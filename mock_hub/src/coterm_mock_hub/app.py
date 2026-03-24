from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable
from concurrent.futures import Future
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.legacy.server import WebSocketServerProtocol, serve

from .models import dumps, make_ws_message, new_id
from .store import InMemoryStore


class HubAPIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class MockHubApp:
    def __init__(
        self,
        *,
        ws_host: str,
        ws_port: int,
        http_host: str,
        http_port: int,
        logger: logging.Logger,
    ) -> None:
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.http_host = http_host
        self.http_port = http_port
        self.logger = logger
        self.store = InMemoryStore()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ws_server = None

    async def start_ws(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._ws_server = await serve(self._handle_ws_connection, self.ws_host, self.ws_port)
        self.logger.info("mock hub websocket listening on ws://%s:%s", self.ws_host, self.ws_port)

    async def stop_ws(self) -> None:
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()

    async def _handle_ws_connection(self, websocket: WebSocketServerProtocol, path: str) -> None:
        if path == "/ws/cli":
            await self._handle_cli(websocket)
            return
        if path == "/ws/client":
            await self._handle_client(websocket)
            return
        await websocket.close(code=1008, reason="unknown path")

    async def _handle_cli(self, websocket: WebSocketServerProtocol) -> None:
        session_id: str | None = None
        try:
            async for raw_message in websocket:
                if not isinstance(raw_message, str):
                    continue
                data = json.loads(raw_message)
                msg_type = str(data["type"])
                payload = self._coerce_dict(data.get("payload"))
                session_id = str(data["session_id"])

                if msg_type == "register":
                    await self._handle_cli_register(websocket, session_id, payload)
                    continue
                if msg_type == "heartbeat":
                    await self._handle_cli_heartbeat(session_id, payload)
                    continue
                if msg_type == "agent_output":
                    await self._handle_agent_output(session_id, data)
                    continue
                if msg_type == "permission_request":
                    await self._handle_permission_request(session_id, data)
                    continue
                if msg_type == "session_state":
                    await self._handle_session_state(session_id, payload)
                    continue
                if msg_type == "error":
                    await self._broadcast_clients(session_id, data)
                    continue
        except ConnectionClosed:
            self.logger.info("cli websocket closed: session_id=%s", session_id)
        finally:
            if session_id is not None and self.store.detach_cli(session_id, websocket):
                snapshot = self.store.mark_session_disconnected(session_id)
                if snapshot is not None:
                    await self._broadcast_session_snapshot(snapshot.to_dict())

    async def _handle_client(self, websocket: WebSocketServerProtocol) -> None:
        subscribed_sessions: set[str] = set()
        try:
            async for raw_message in websocket:
                if not isinstance(raw_message, str):
                    continue
                data = json.loads(raw_message)
                msg_type = str(data["type"])
                session_id = str(data["session_id"])

                if msg_type == "subscribe_session":
                    self.store.attach_client(session_id, websocket)
                    subscribed_sessions.add(session_id)
                    session = self.store.get_session(session_id)
                    payload = {
                        "state": session.state if session else "INIT",
                        "agent_type": session.agent_type if session else "claude_code",
                        "connected": session.connected if session else False,
                        "pending_request_id": session.pending_request_id if session else None,
                    }
                    await self._send_json(
                        websocket,
                        make_ws_message(
                            message_type="session_snapshot",
                            session_id=session_id,
                            payload=payload,
                        ),
                    )
                    continue

                if msg_type == "unsubscribe_session":
                    self.store.detach_client(session_id, websocket)
                    subscribed_sessions.discard(session_id)
                    continue
        except ConnectionClosed:
            self.logger.info("client websocket closed")
        finally:
            for session_id in subscribed_sessions:
                self.store.detach_client(session_id, websocket)

    async def _handle_cli_register(
        self,
        websocket: WebSocketServerProtocol,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        device_id = str(payload.get("device_id", "unknown-device"))
        agent_type = str(payload.get("agent_type", "claude_code"))
        previous = self.store.attach_cli(session_id, websocket)
        if previous is not None and previous is not websocket:
            await previous.close(code=4000, reason="replaced by newer cli connection")

        session = self.store.upsert_session_from_register(session_id, device_id, agent_type)
        await self._send_json(
            websocket,
            make_ws_message(
                message_type="register_ack",
                session_id=session_id,
                payload={
                    "accepted": True,
                    "state": session.state,
                    "heartbeat_interval_sec": 10,
                },
            ),
        )
        await self._broadcast_session_snapshot(session.to_dict())

    async def _handle_cli_heartbeat(self, session_id: str, payload: dict[str, Any]) -> None:
        state = payload.get("state")
        session = self.store.update_session_heartbeat(session_id, str(state) if state else None)
        if session is not None:
            await self._broadcast_session_snapshot(session.to_dict())

    async def _handle_agent_output(self, session_id: str, data: dict[str, Any]) -> None:
        payload = self._coerce_dict(data["payload"])
        message_id = str(payload["message_id"])
        chunk = str(payload.get("chunk", ""))
        final = bool(payload.get("final", False))
        self.store.append_agent_output(session_id, message_id, chunk, final)
        await self._broadcast_clients(session_id, data)

        if final:
            await self._broadcast_clients(
                session_id,
                make_ws_message(
                    message_type="message_done",
                    session_id=session_id,
                    payload={"message_id": message_id},
                    trace_id=data.get("trace_id"),
                ),
            )

    async def _handle_permission_request(self, session_id: str, data: dict[str, Any]) -> None:
        payload = self._coerce_dict(data["payload"])
        self.store.create_permission(
            request_id=str(payload["request_id"]),
            session_id=session_id,
            message_id=str(payload["message_id"]),
            tool_name=str(payload["tool_name"]),
            summary=str(payload.get("summary", "")),
            payload=self._coerce_dict(payload.get("payload")),
        )
        await self._broadcast_clients(session_id, data)

    async def _handle_session_state(self, session_id: str, payload: dict[str, Any]) -> None:
        session = self.store.update_session_state(
            session_id,
            state=str(payload["state"]),
            active_message_id=self._coerce_optional_str(payload.get("active_message_id")),
            pending_request_id=self._coerce_optional_str(payload.get("pending_request_id")),
        )
        if session is not None:
            await self._broadcast_clients(
                session_id,
                make_ws_message(
                    message_type="session_state",
                    session_id=session_id,
                    payload={
                        "state": session.state,
                        "active_message_id": session.active_message_id,
                        "pending_request_id": session.pending_request_id,
                    },
                ),
            )

    def create_session(self, agent_type: str, device_id: str) -> dict[str, Any]:
        session_id = new_id("sess")
        session = self.store.create_session(session_id, device_id, agent_type)
        return {
            "session_id": session.session_id,
            "state": session.state,
            "ws_cli_url": f"ws://{self.ws_host}:{self.ws_port}/ws/cli",
            "ws_client_url": f"ws://{self.ws_host}:{self.ws_port}/ws/client",
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise HubAPIError(404, "HUB_SESSION_NOT_FOUND", f"session not found: {session_id}")
        return session.to_dict()

    def list_messages(self, session_id: str, limit: int, before: str | None) -> dict[str, Any]:
        self.get_session(session_id)
        return {"items": self.store.list_messages(session_id, limit=limit, before=before), "next_cursor": None}

    def list_permissions(self, session_id: str) -> dict[str, Any]:
        self.get_session(session_id)
        return {"items": self.store.list_permissions(session_id)}

    def post_user_message(self, session_id: str, message_id: str, content: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise HubAPIError(404, "HUB_SESSION_NOT_FOUND", f"session not found: {session_id}")
        if not session.connected or self.store.get_cli(session_id) is None:
            raise HubAPIError(503, "HUB_DOWNSTREAM_UNAVAILABLE", "cli is offline")
        if session.state not in {"ACTIVE", "INIT"}:
            raise HubAPIError(409, "HUB_INVALID_STATE", f"session is not ready: {session.state}")

        self.store.add_user_message(session_id, message_id, content)
        self._submit_coro(
            self._send_to_cli(
                session_id,
                make_ws_message(
                    message_type="user_message",
                    session_id=session_id,
                    payload={"message_id": message_id, "content": content},
                ),
            )
        )
        return {"accepted": True, "message_id": message_id, "state": "PROCESSING"}

    def post_permission_decision(self, request_id: str, decision: str, resolved_by: str) -> dict[str, Any]:
        permission = self.store.get_permission(request_id)
        if permission is None:
            raise HubAPIError(404, "HUB_PERMISSION_NOT_FOUND", f"permission not found: {request_id}")
        if permission.status != "PENDING":
            raise HubAPIError(409, "HUB_PERMISSION_RESOLVED", f"permission already resolved: {request_id}")

        resolved = self.store.resolve_permission(request_id, decision, resolved_by)
        assert resolved is not None

        self._submit_coro(
            self._send_to_cli(
                resolved.session_id,
                make_ws_message(
                    message_type="permission_decision",
                    session_id=resolved.session_id,
                    payload={"request_id": request_id, "decision": decision, "reason": resolved_by},
                ),
            )
        )
        self._submit_coro(
            self._broadcast_clients(
                resolved.session_id,
                make_ws_message(
                    message_type="permission_update",
                    session_id=resolved.session_id,
                    payload={
                        "request_id": request_id,
                        "status": resolved.status,
                        "resolved_by": resolved.resolved_by,
                    },
                ),
            )
        )
        return {"accepted": True, "request_id": request_id, "status": resolved.status}

    async def _send_to_cli(self, session_id: str, message: dict[str, Any]) -> None:
        websocket = self.store.get_cli(session_id)
        if websocket is None:
            raise HubAPIError(503, "HUB_DOWNSTREAM_UNAVAILABLE", "cli is offline")
        await self._send_json(websocket, message)

    async def _broadcast_clients(self, session_id: str, message: dict[str, Any]) -> None:
        clients = self.store.get_clients(session_id)
        if not clients:
            return
        await asyncio.gather(*(self._send_json(client, message) for client in clients), return_exceptions=True)

    async def _broadcast_session_snapshot(self, session: dict[str, Any]) -> None:
        await self._broadcast_clients(
            str(session["session_id"]),
            make_ws_message(
                message_type="session_state",
                session_id=str(session["session_id"]),
                payload={
                    "state": session["state"],
                    "active_message_id": session.get("active_message_id"),
                    "pending_request_id": session.get("pending_request_id"),
                },
            ),
        )

    async def _send_json(self, websocket: WebSocketServerProtocol, message: dict[str, Any]) -> None:
        await websocket.send(dumps(message))

    def _submit_coro(self, coro: Awaitable[None]) -> Future[None]:
        if self.loop is None:
            raise RuntimeError("mock hub event loop is not ready")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _coerce_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    def _coerce_optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        return str(value)
