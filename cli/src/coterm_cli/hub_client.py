from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable

import websockets

from .config import CLIConfig
from .models import HubMessage, utc_now_ms


MessageHandler = Callable[[HubMessage], Awaitable[None]]
LifecycleHandler = Callable[[], Awaitable[None]]
StateProvider = Callable[[], str]


class HubClient:
    def __init__(self, config: CLIConfig, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self._message_handler: MessageHandler | None = None
        self._connected_handler: LifecycleHandler | None = None
        self._disconnected_handler: LifecycleHandler | None = None
        self._state_provider: StateProvider | None = None
        self._stop_event = asyncio.Event()
        self._connected = asyncio.Event()
        self._register_ack = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._websocket: websockets.WebSocketClientProtocol | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._message_handler = handler

    def on_connected(self, handler: LifecycleHandler) -> None:
        self._connected_handler = handler

    def on_disconnected(self, handler: LifecycleHandler) -> None:
        self._disconnected_handler = handler

    def set_state_provider(self, provider: StateProvider) -> None:
        self._state_provider = provider

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._websocket is not None:
            await self._websocket.close()

    async def run_forever(self) -> None:
        backoff = self._config.reconnect_base_sec
        headers = {
            "Authorization": f"Bearer {self._config.auth_token}",
            "X-Device-Id": self._config.device_id,
            "X-Client-Version": "0.1.0",
        }

        while not self._stop_event.is_set():
            try:
                connect_kwargs: dict[str, object] = {
                    "max_size": None,
                    "proxy": None,
                }
                if "additional_headers" in inspect.signature(websockets.connect).parameters:
                    connect_kwargs["additional_headers"] = headers
                else:
                    connect_kwargs["extra_headers"] = headers

                async with websockets.connect(self._config.hub_url, **connect_kwargs) as websocket:
                    self._websocket = websocket
                    self._connected.set()
                    self._logger.info("connected to hub: %s", self._config.hub_url)
                    await self.send_register()

                    if self._connected_handler is not None:
                        await self._connected_handler()

                    recv_task = asyncio.create_task(self._recv_loop())
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    done, pending = await asyncio.wait(
                        [recv_task, heartbeat_task],
                        return_when=asyncio.FIRST_EXCEPTION,
                    )

                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

                    for task in done:
                        exc = task.exception()
                        if exc is not None:
                            raise exc

                backoff = self._config.reconnect_base_sec
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("hub connection loop failed")
            finally:
                self._websocket = None
                self._connected.clear()
                self._register_ack.clear()
                if self._disconnected_handler is not None and not self._stop_event.is_set():
                    await self._disconnected_handler()

            if self._stop_event.is_set():
                break

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._config.reconnect_max_sec)

    async def send_register(self) -> None:
        await self.send_message(
            HubMessage(
                type="register",
                session_id=self._config.session_id,
                payload={
                    "device_id": self._config.device_id,
                    "agent_type": self._config.agent_type,
                    "client_version": "0.1.0",
                    "capabilities": ["stream_output", "permission_request", "message_blocks"],
                },
            )
        )

    async def send_heartbeat(self, state: str) -> None:
        await self.send_message(
            HubMessage(
                type="heartbeat",
                session_id=self._config.session_id,
                payload={"state": state},
            )
        )

    async def send_session_state(
        self,
        state: str,
        active_message_id: str | None,
        pending_request_id: str | None,
    ) -> None:
        await self.send_message(
            HubMessage(
                type="session_state",
                session_id=self._config.session_id,
                payload={
                    "state": state,
                    "active_message_id": active_message_id,
                    "pending_request_id": pending_request_id,
                },
            )
        )

    async def send_agent_output(
        self,
        *,
        message_id: str,
        chunk: str,
        final: bool,
        trace_id: str | None = None,
        index: int | None = None,
    ) -> None:
        payload = {"message_id": message_id, "chunk": chunk, "final": final}
        if index is not None:
            payload["index"] = index
        await self.send_message(
            HubMessage(
                type="agent_output",
                session_id=self._config.session_id,
                trace_id=trace_id,
                payload=payload,
            )
        )

    async def send_message_started(
        self,
        *,
        message_id: str,
        role: str,
        trace_id: str | None = None,
    ) -> None:
        await self.send_message(
            HubMessage(
                type="message_started",
                session_id=self._config.session_id,
                trace_id=trace_id,
                payload={
                    "message_id": message_id,
                    "role": role,
                },
            )
        )

    async def send_message_block(
        self,
        *,
        message_id: str,
        block: dict[str, object],
        trace_id: str | None = None,
    ) -> None:
        await self.send_message(
            HubMessage(
                type="message_block",
                session_id=self._config.session_id,
                trace_id=trace_id,
                payload={
                    "message_id": message_id,
                    "block": block,
                },
            )
        )

    async def send_message_block_done(
        self,
        *,
        message_id: str,
        block_id: str,
        trace_id: str | None = None,
    ) -> None:
        await self.send_message(
            HubMessage(
                type="message_block_done",
                session_id=self._config.session_id,
                trace_id=trace_id,
                payload={
                    "message_id": message_id,
                    "block_id": block_id,
                },
            )
        )

    async def send_permission_request(
        self,
        *,
        request_id: str,
        message_id: str,
        tool_name: str,
        summary: str,
        payload: dict[str, object] | None,
        trace_id: str | None = None,
    ) -> None:
        await self.send_message(
            HubMessage(
                type="permission_request",
                session_id=self._config.session_id,
                trace_id=trace_id,
                payload={
                    "request_id": request_id,
                    "message_id": message_id,
                    "tool_name": tool_name,
                    "summary": summary,
                    "payload": payload or {},
                },
            )
        )

    async def send_error(
        self,
        *,
        code: str,
        message: str,
        fatal: bool,
        trace_id: str | None = None,
    ) -> None:
        await self.send_message(
            HubMessage(
                type="error",
                session_id=self._config.session_id,
                trace_id=trace_id,
                payload={
                    "code": code,
                    "message": message,
                    "fatal": fatal,
                },
            )
        )

    async def send_message(self, message: HubMessage) -> None:
        if self._websocket is None:
            raise RuntimeError("hub websocket is not connected")

        async with self._send_lock:
            await self._websocket.send(message.to_json())

    async def _recv_loop(self) -> None:
        assert self._websocket is not None
        async for raw_message in self._websocket:
            if not isinstance(raw_message, str):
                self._logger.warning("ignoring non-text websocket frame")
                continue

            message = HubMessage.from_json(raw_message)
            if message.type == "register_ack":
                self._register_ack.set()

            if self._message_handler is not None:
                await self._message_handler(message)

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._config.heartbeat_interval_sec)
            if self._websocket is None:
                return
            state = self._state_provider() if self._state_provider is not None else "ACTIVE"
            await self.send_heartbeat(state)

    async def wait_until_registered(self) -> None:
        await self._register_ack.wait()
