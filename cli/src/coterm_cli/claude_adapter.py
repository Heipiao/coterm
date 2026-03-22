from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    TaskStartedMessage,
    TextBlock,
)

from .adapter import AgentAdapter
from .config import CLIConfig
from .models import AgentEvent, AgentEventType, PermissionDecision
from .process import ProcessManager


class ClaudeRemoteAdapter(AgentAdapter):
    def __init__(self, config: CLIConfig, process_manager: ProcessManager, logger: logging.Logger) -> None:
        self._config = config
        self._process_manager = process_manager
        self._logger = logger
        self._event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._client: ClaudeSDKClient | None = None
        self._consume_task: asyncio.Task[None] | None = None
        self._active_message_id: str | None = None
        self._active_task_id: str | None = None
        self._pending_permission_request_id: str | None = None
        self._pending_permission_future: asyncio.Future[PermissionDecision] | None = None
        self._discard_current_response = False
        self._stopped = False

    async def start(self) -> None:
        self._stopped = False
        if self._client is not None:
            return

        options = ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code"},
            permission_mode=self._config.permission_mode or "default",
            allowed_tools=list(self._config.allowed_tools),
            cwd=self._config.working_dir,
            cli_path=self._config.claude_bin,
            can_use_tool=self._handle_permission_request,
            include_partial_messages=True,
            setting_sources=["user", "project"],
        )
        self._client = ClaudeSDKClient(options)
        await self._client.connect()

    async def stop(self) -> None:
        self._stopped = True
        self._resolve_pending_permission(PermissionDecision.TIMEOUT)
        if self._consume_task is not None:
            self._consume_task.cancel()
            await asyncio.gather(self._consume_task, return_exceptions=True)
            self._consume_task = None
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
        self._active_message_id = None
        self._active_task_id = None
        self._discard_current_response = False

    async def send_user_message(self, message_id: str, content: str) -> None:
        if self._client is None:
            raise RuntimeError("Claude SDK client is not started")
        if self._consume_task is not None:
            raise RuntimeError("a message is already being processed")

        self._active_message_id = message_id
        self._active_task_id = None
        self._discard_current_response = False
        await self._event_queue.put(AgentEvent(type=AgentEventType.MESSAGE_START, message_id=message_id))
        await self._client.query(content, session_id=self._config.session_id)
        self._consume_task = asyncio.create_task(self._consume_response(message_id))

    async def send_permission_decision(self, request_id: str, decision: PermissionDecision) -> None:
        future = self._pending_permission_future
        if (
            future is None
            or future.done()
            or request_id != self._pending_permission_request_id
        ):
            await self._event_queue.put(
                AgentEvent(
                    type=AgentEventType.ERROR,
                    message_id=self._active_message_id,
                    request_id=request_id,
                    error_code="CLI_PERMISSION_MISMATCH",
                    content=f"unexpected permission request id: {request_id}",
                    fatal=False,
                    raw={"decision": decision.value},
                )
            )
            return

        self._pending_permission_request_id = None
        future.set_result(decision)

    async def cancel_current_turn(self) -> None:
        if self._client is None or self._consume_task is None:
            return
        self._discard_current_response = True
        self._resolve_pending_permission(PermissionDecision.TIMEOUT)
        try:
            if self._active_task_id:
                await self._client.stop_task(self._active_task_id)
            else:
                await self._client.interrupt()
        except Exception:
            self._logger.warning("failed to interrupt active Claude task", exc_info=True)

    async def events(self):
        while True:
            yield await self._event_queue.get()

    async def _handle_permission_request(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: Any,
    ) -> PermissionResultAllow | PermissionResultDeny:
        if self._stopped:
            return PermissionResultDeny(message="coterm cli is stopping", interrupt=True)

        request_id = f"perm_{uuid4().hex[:12]}"
        self._pending_permission_request_id = request_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionDecision] = loop.create_future()
        self._pending_permission_future = future

        await self._event_queue.put(
            AgentEvent(
                type=AgentEventType.TOOL_USE,
                message_id=self._active_message_id,
                request_id=request_id,
                tool_name=tool_name,
                tool_input=input_data,
                raw={
                    "tool_name": tool_name,
                    "input": input_data,
                    "suggestions": getattr(context, "suggestions", []),
                },
            )
        )

        try:
            decision = await future
        except asyncio.CancelledError:
            return PermissionResultDeny(message="permission request cancelled", interrupt=True)
        finally:
            if self._pending_permission_future is future:
                self._pending_permission_future = None
            self._pending_permission_request_id = None

        if decision == PermissionDecision.APPROVE:
            return PermissionResultAllow()
        if decision == PermissionDecision.DENY:
            return PermissionResultDeny(message="request denied by user", interrupt=False)
        return PermissionResultDeny(message="request timed out", interrupt=True)

    async def _consume_response(self, message_id: str) -> None:
        assert self._client is not None
        saw_stream_output = False
        emitted_outputs: set[str] = set()
        try:
            async for message in self._client.receive_response():
                if isinstance(message, TaskStartedMessage):
                    self._active_task_id = message.task_id
                    continue

                if isinstance(message, StreamEvent):
                    if self._discard_current_response:
                        continue
                    text = self._extract_stream_text(message)
                    if text:
                        saw_stream_output = True
                        emitted_outputs.add(self._normalize_output(text))
                        await self._emit_output(message_id, text, raw=message.event)
                    continue

                if isinstance(message, AssistantMessage):
                    handled = await self._consume_message_blocks(
                        message,
                        message_id,
                        saw_stream_output=saw_stream_output,
                        emitted_outputs=emitted_outputs,
                    )
                    if handled:
                        continue
                    if self._discard_current_response:
                        continue
                    if saw_stream_output:
                        continue
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            emitted_outputs.add(self._normalize_output(block.text))
                            await self._emit_output(message_id, block.text)
                    continue

                handled_generic_blocks = await self._consume_message_blocks(
                    message,
                    message_id,
                    saw_stream_output=saw_stream_output,
                    emitted_outputs=emitted_outputs,
                )
                if handled_generic_blocks:
                    continue

                if isinstance(message, RateLimitEvent):
                    self._logger.warning(
                        "Claude rate limit event: status=%s type=%s",
                        message.rate_limit_info.status,
                        message.rate_limit_info.rate_limit_type,
                    )
                    continue

                if isinstance(message, ResultMessage):
                    if self._discard_current_response:
                        return
                    if message.is_error:
                        await self._event_queue.put(
                            AgentEvent(
                                type=AgentEventType.ERROR,
                                message_id=message_id,
                                error_code="CLI_AGENT_ERROR",
                                content=message.result or message.stop_reason or "Claude returned an error",
                                fatal=True,
                                raw={
                                    "subtype": message.subtype,
                                    "stop_reason": message.stop_reason,
                                    "result": message.result,
                                },
                            )
                        )
                        return
                    fallback_text = (message.result or "").strip()
                    if fallback_text and self._normalize_output(fallback_text) not in emitted_outputs:
                        emitted_outputs.add(self._normalize_output(fallback_text))
                        await self._emit_output(
                            message_id,
                            fallback_text,
                            raw={
                                "subtype": message.subtype,
                                "stop_reason": message.stop_reason,
                                "result": message.result,
                            },
                        )
                    await self._event_queue.put(
                        AgentEvent(type=AgentEventType.MESSAGE_STOP, message_id=message_id)
                    )
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._event_queue.put(
                AgentEvent(
                    type=AgentEventType.ERROR,
                    message_id=message_id,
                    error_code="CLI_AGENT_ERROR",
                    content=str(exc),
                    fatal=True,
                )
            )
        finally:
            self._resolve_pending_permission(PermissionDecision.TIMEOUT)
            self._active_message_id = None
            self._active_task_id = None
            self._discard_current_response = False
            self._consume_task = None

    def _resolve_pending_permission(self, decision: PermissionDecision) -> None:
        future = self._pending_permission_future
        if future is None or future.done():
            self._pending_permission_future = None
            self._pending_permission_request_id = None
            return
        future.set_result(decision)
        self._pending_permission_future = None
        self._pending_permission_request_id = None

    def _extract_stream_text(self, message: StreamEvent) -> str | None:
        event = message.event
        if not isinstance(event, dict):
            return None
        if event.get("type") != "content_block_delta":
            return None
        delta = event.get("delta")
        if not isinstance(delta, dict):
            return None
        if delta.get("type") != "text_delta":
            return None
        text = delta.get("text")
        if isinstance(text, str) and text:
            return text
        return None

    async def _emit_output(self, message_id: str, content: str, raw: dict[str, Any] | None = None) -> None:
        if not content:
            return
        await self._event_queue.put(
            AgentEvent(
                type=AgentEventType.OUTPUT,
                message_id=message_id,
                content=content,
                raw=raw,
            )
        )

    async def _consume_message_blocks(
        self,
        message: Any,
        message_id: str,
        *,
        saw_stream_output: bool,
        emitted_outputs: set[str],
    ) -> bool:
        if self._discard_current_response:
            return False

        content = self._extract_message_content(message)
        if not content:
            return False

        handled = False
        for block in content:
            if isinstance(block, TextBlock):
                if saw_stream_output or not block.text:
                    handled = True
                    continue
                handled = True
                normalized = self._normalize_output(block.text)
                if normalized and normalized not in emitted_outputs:
                    emitted_outputs.add(normalized)
                    await self._emit_output(message_id, block.text)
                continue

            block_type = self._read_value(block, "type")
            if block_type == "text":
                text = self._read_value(block, "text")
                handled = True
                if saw_stream_output or not isinstance(text, str) or not text:
                    continue
                normalized = self._normalize_output(text)
                if normalized and normalized not in emitted_outputs:
                    emitted_outputs.add(normalized)
                    await self._emit_output(message_id, text)
                continue

            if block_type == "tool_use":
                handled = True
                tool_name = self._read_value(block, "name")
                tool_input = self._coerce_dict(self._read_value(block, "input"))
                block_id = self._read_value(block, "id")
                summary = (
                    str(tool_input.get("description"))
                    if isinstance(tool_input.get("description"), str) and tool_input.get("description")
                    else f"tool call: {tool_name or 'unknown'}"
                )
                await self._event_queue.put(
                    AgentEvent(
                        type=AgentEventType.TOOL_CALL,
                        message_id=message_id,
                        request_id=str(block_id or f"tool_{uuid4().hex[:12]}"),
                        tool_name=str(tool_name or "unknown"),
                        tool_input=tool_input,
                        content=summary,
                    )
                )
                continue

            if block_type == "tool_result":
                handled = True
                text = self._stringify_tool_result(self._read_value(block, "content"))
                normalized = self._normalize_output(text)
                if normalized and normalized not in emitted_outputs:
                    emitted_outputs.add(normalized)
                    await self._emit_output(message_id, text)
                continue

        return handled

    def _extract_message_content(self, message: Any) -> list[Any]:
        direct_content = getattr(message, "content", None)
        if isinstance(direct_content, list):
            return direct_content

        inner_message = getattr(message, "message", None)
        nested_content = getattr(inner_message, "content", None)
        if isinstance(nested_content, list):
            return nested_content

        return []

    def _read_value(self, value: Any, field: str) -> Any:
        if isinstance(value, dict):
            return value.get(field)
        return getattr(value, field, None)

    def _stringify_tool_result(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                text = self._read_value(item, "text")
                if isinstance(text, str) and text:
                    parts.append(text)
                    continue
                content = self._read_value(item, "content")
                if isinstance(content, str) and content:
                    parts.append(content)
            return "\n".join(part for part in parts if part).strip()
        return ""

    def _coerce_dict(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _normalize_output(self, content: str) -> str:
        return " ".join(content.split())
