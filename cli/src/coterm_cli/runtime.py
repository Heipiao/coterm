from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from .adapter import AgentAdapter
from .hub_client import HubClient
from .models import (
    AgentEvent,
    AgentEventType,
    HubMessage,
    PermissionDecision,
    RenderBlock,
    SessionState,
    utc_now_ms,
)
from .session import InvalidSessionTransition, SessionManager


class AgentRuntime:
    def __init__(
        self,
        session_manager: SessionManager,
        hub_client: HubClient,
        adapter: AgentAdapter,
        logger: logging.Logger,
    ) -> None:
        self._session = session_manager
        self._hub = hub_client
        self._adapter = adapter
        self._logger = logger
        self._adapter_events_task: asyncio.Task[None] | None = None
        self._output_index = 0
        self._active_text_block_created_at: int | None = None
        self._shutdown_event = asyncio.Event()

        self._hub.on_message(self.handle_hub_message)
        self._hub.on_disconnected(self.handle_disconnected)
        self._hub.set_state_provider(lambda: self._session.state.value)

    @property
    def shutdown_event(self) -> asyncio.Event:
        return self._shutdown_event

    async def start(self) -> None:
        await self._adapter.start()
        self._adapter_events_task = asyncio.create_task(self._consume_adapter_events())

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._adapter_events_task is not None:
            self._adapter_events_task.cancel()
            await asyncio.gather(self._adapter_events_task, return_exceptions=True)
            self._adapter_events_task = None
        await self._adapter.stop()

    async def handle_hub_message(self, message: HubMessage) -> None:
        if message.type == "error":
            self._log_hub_error(message)
        else:
            self._logger.info(
                "received hub message: type=%s session_id=%s trace_id=%s",
                message.type,
                message.session_id,
                message.trace_id or "-",
            )
        if message.type == "register_ack":
            await self._handle_register_ack()
            return
        if message.type == "user_message":
            await self._handle_user_message(message)
            return
        if message.type == "permission_decision":
            await self._handle_permission_decision(message)
            return
        if message.type == "cancel_message":
            await self._handle_cancel_message()
            return
        if message.type == "shutdown":
            await self.stop()
            return

    async def handle_disconnected(self) -> None:
        self._logger.warning("hub disconnected")
        self._session.mark_disconnected()

    def _log_hub_error(self, message: HubMessage) -> None:
        code = str(message.payload.get("code", "UNKNOWN"))
        error_message = str(message.payload.get("message", ""))
        fatal = bool(message.payload.get("fatal", False))
        log_method = self._logger.error if fatal else self._logger.warning
        log_method(
            "received hub error: code=%s fatal=%s session_id=%s trace_id=%s message=%s payload=%r",
            code,
            fatal,
            message.session_id,
            message.trace_id or "-",
            error_message,
            message.payload,
        )

    async def _handle_register_ack(self) -> None:
        if self._session.state == SessionState.INIT:
            self._session.transition(SessionState.ACTIVE)
        elif self._session.state == SessionState.DISCONNECTED:
            self._session.restore_connection_state()

        await self._sync_session_state()

    async def _handle_user_message(self, message: HubMessage) -> None:
        if self._session.state != SessionState.ACTIVE:
            await self._safe_send(
                self._hub.send_error(
                    code="CLI_INVALID_STATE",
                    message=f"session is not ACTIVE: {self._session.state.value}",
                    fatal=False,
                    trace_id=message.trace_id,
                )
            )
            return

        message_id = str(message.payload["message_id"])
        content = str(message.payload["content"])

        self._session.set_active_message(message_id)
        self._session.transition(SessionState.PROCESSING)
        self._output_index = 0
        self._active_text_block_created_at = None
        await self._sync_session_state()

        try:
            await self._adapter.send_user_message(message_id, content)
        except Exception as exc:
            self._session.record_error("CLI_SEND_MESSAGE_FAILED")
            self._session.set_active_message(None)
            self._session.force_state(SessionState.ACTIVE)
            await self._safe_send(
                self._hub.send_error(
                    code="CLI_SEND_MESSAGE_FAILED",
                    message=str(exc),
                    fatal=False,
                    trace_id=message.trace_id,
                )
            )
            await self._sync_session_state()

    async def _handle_permission_decision(self, message: HubMessage) -> None:
        if self._session.state != SessionState.WAITING_PERMISSION:
            await self._safe_send(
                self._hub.send_error(
                    code="CLI_INVALID_STATE",
                    message=f"session is not WAITING_PERMISSION: {self._session.state.value}",
                    fatal=False,
                    trace_id=message.trace_id,
                )
            )
            return

        request_id = str(message.payload["request_id"])
        if request_id != self._session.pending_permission_id:
            await self._safe_send(
                self._hub.send_error(
                    code="CLI_PERMISSION_MISMATCH",
                    message=f"unexpected permission request id: {request_id}",
                    fatal=False,
                    trace_id=message.trace_id,
                )
            )
            return

        decision = PermissionDecision(str(message.payload["decision"]))
        self._session.set_pending_permission(None)
        self._session.transition(SessionState.PROCESSING)
        await self._sync_session_state()
        await self._adapter.send_permission_decision(request_id, decision)

    async def _handle_cancel_message(self) -> None:
        await self._adapter.cancel_current_turn()
        self._session.set_active_message(None)
        self._session.set_pending_permission(None)
        self._active_text_block_created_at = None
        if self._session.state != SessionState.ENDED:
            self._session.force_state(SessionState.ACTIVE)
        await self._sync_session_state()

    async def _consume_adapter_events(self) -> None:
        async for event in self._adapter.events():
            await self._handle_adapter_event(event)

    async def _handle_adapter_event(self, event: AgentEvent) -> None:
        if event.type == AgentEventType.MESSAGE_START:
            if event.message_id:
                await self._safe_send(
                    self._hub.send_message_started(
                        message_id=event.message_id,
                        role="agent",
                    )
                )
            if self._session.state == SessionState.ACTIVE:
                self._session.transition(SessionState.PROCESSING)
                await self._sync_session_state()
            return

        if event.type == AgentEventType.OUTPUT:
            if event.message_id and event.content:
                render_block = self._make_text_block(event.message_id, event.content)
                await self._safe_send(
                    self._hub.send_message_block(
                        message_id=event.message_id,
                        block=render_block.to_dict(),
                    )
                )
                await self._safe_send(
                    self._hub.send_agent_output(
                        message_id=event.message_id,
                        chunk=event.content,
                        final=False,
                        index=self._output_index,
                    )
                )
                self._output_index += 1
            return

        if event.type == AgentEventType.TOOL_USE:
            request_id = event.request_id or f"perm_{uuid4().hex[:12]}"
            self._session.set_pending_permission(request_id)
            try:
                self._session.transition(SessionState.WAITING_PERMISSION)
            except InvalidSessionTransition:
                self._session.force_state(SessionState.WAITING_PERMISSION)
            await self._safe_send(
                self._hub.send_permission_request(
                    request_id=request_id,
                    message_id=event.message_id or self._session.active_message_id or "",
                    tool_name=event.tool_name or "unknown",
                    summary=f"tool request: {event.tool_name or 'unknown'}",
                    payload=event.tool_input,
                )
            )
            await self._sync_session_state()
            return

        if event.type == AgentEventType.MESSAGE_STOP:
            message_id = event.message_id or self._session.active_message_id
            if message_id:
                if self._active_text_block_created_at is not None:
                    await self._safe_send(
                        self._hub.send_message_block_done(
                            message_id=message_id,
                            block_id=self._text_block_id(message_id),
                        )
                    )
                await self._safe_send(
                    self._hub.send_agent_output(
                        message_id=message_id,
                        chunk="",
                        final=True,
                        index=self._output_index,
                    )
                )
            self._session.set_active_message(None)
            self._session.set_pending_permission(None)
            self._active_text_block_created_at = None
            if self._session.state != SessionState.ENDED:
                self._session.force_state(SessionState.ACTIVE)
                await self._sync_session_state()
            return

        if event.type == AgentEventType.ERROR:
            code = event.error_code or "CLI_RUNTIME_ERROR"
            self._session.record_error(code)
            await self._safe_send(
                self._hub.send_error(
                    code=code,
                    message=event.content or "runtime error",
                    fatal=event.fatal,
                )
            )
            if event.fatal:
                self._session.force_state(SessionState.ENDED)
                self._active_text_block_created_at = None
                await self._sync_session_state()
            return

    def _text_block_id(self, message_id: str) -> str:
        return f"{message_id}:block:text:0"

    def _make_text_block(self, message_id: str, text: str) -> RenderBlock:
        now = utc_now_ms()
        created_at = self._active_text_block_created_at or now
        if self._active_text_block_created_at is None:
            self._active_text_block_created_at = created_at
        return RenderBlock(
            block_id=self._text_block_id(message_id),
            message_id=message_id,
            kind="text",
            status="STREAMING",
            sequence=0,
            created_at=created_at,
            updated_at=now,
            text=text,
            format="plain",
        )

    async def _sync_session_state(self) -> None:
        if not self._hub.is_connected:
            return
        await self._safe_send(
            self._hub.send_session_state(
                state=self._session.state.value,
                active_message_id=self._session.active_message_id,
                pending_request_id=self._session.pending_permission_id,
            )
        )

    async def _safe_send(self, operation) -> None:
        try:
            await operation
        except Exception:
            self._logger.warning("failed to send message to hub", exc_info=True)
