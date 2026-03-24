from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from websockets.legacy.server import WebSocketServerProtocol

from .models import MessageRecord, PermissionRecord, SessionRecord, utc_now_ms


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionRecord] = {}
        self._messages: dict[str, list[MessageRecord]] = defaultdict(list)
        self._permissions: dict[str, PermissionRecord] = {}
        self._cli_connections: dict[str, WebSocketServerProtocol] = {}
        self._client_connections: dict[str, set[WebSocketServerProtocol]] = defaultdict(set)
        self._agent_messages: dict[tuple[str, str], MessageRecord] = {}

    def create_session(self, session_id: str, device_id: str, agent_type: str) -> SessionRecord:
        with self._lock:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                session.device_id = device_id
                session.agent_type = agent_type
                session.updated_at = utc_now_ms()
                return session

            session = SessionRecord(session_id=session_id, device_id=device_id, agent_type=agent_type)
            self._sessions[session_id] = session
            return session

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            return SessionRecord(**session.to_dict())

    def upsert_session_from_register(self, session_id: str, device_id: str, agent_type: str) -> SessionRecord:
        with self._lock:
            session = self._sessions.get(session_id)
            now = utc_now_ms()
            if session is None:
                session = SessionRecord(
                    session_id=session_id,
                    device_id=device_id,
                    agent_type=agent_type,
                    state="ACTIVE",
                    connected=True,
                    created_at=now,
                    updated_at=now,
                    last_heartbeat_at=now,
                )
                self._sessions[session_id] = session
            else:
                session.device_id = device_id
                session.agent_type = agent_type
                session.connected = True
                session.state = "ACTIVE" if session.state == "INIT" else session.state
                session.updated_at = now
                session.last_heartbeat_at = now
            return SessionRecord(**session.to_dict())

    def update_session_heartbeat(self, session_id: str, state: str | None = None) -> SessionRecord | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.last_heartbeat_at = utc_now_ms()
            session.updated_at = session.last_heartbeat_at
            session.connected = True
            if state:
                session.state = state
            return SessionRecord(**session.to_dict())

    def update_session_state(
        self,
        session_id: str,
        *,
        state: str,
        active_message_id: str | None,
        pending_request_id: str | None,
    ) -> SessionRecord | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.state = state
            session.active_message_id = active_message_id
            session.pending_request_id = pending_request_id
            session.updated_at = utc_now_ms()
            return SessionRecord(**session.to_dict())

    def mark_session_disconnected(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.connected = False
            if session.state != "ENDED":
                session.state = "DISCONNECTED"
            session.updated_at = utc_now_ms()
            return SessionRecord(**session.to_dict())

    def attach_cli(self, session_id: str, websocket: WebSocketServerProtocol) -> WebSocketServerProtocol | None:
        with self._lock:
            previous = self._cli_connections.get(session_id)
            self._cli_connections[session_id] = websocket
            return previous

    def detach_cli(self, session_id: str, websocket: WebSocketServerProtocol) -> bool:
        with self._lock:
            current = self._cli_connections.get(session_id)
            if current is websocket:
                self._cli_connections.pop(session_id, None)
                return True
            return False

    def get_cli(self, session_id: str) -> WebSocketServerProtocol | None:
        with self._lock:
            return self._cli_connections.get(session_id)

    def attach_client(self, session_id: str, websocket: WebSocketServerProtocol) -> None:
        with self._lock:
            self._client_connections[session_id].add(websocket)

    def detach_client(self, session_id: str, websocket: WebSocketServerProtocol) -> None:
        with self._lock:
            clients = self._client_connections.get(session_id)
            if not clients:
                return
            clients.discard(websocket)
            if not clients:
                self._client_connections.pop(session_id, None)

    def get_clients(self, session_id: str) -> list[WebSocketServerProtocol]:
        with self._lock:
            return list(self._client_connections.get(session_id, set()))

    def add_user_message(self, session_id: str, message_id: str, content: str) -> MessageRecord:
        with self._lock:
            now = utc_now_ms()
            for record in self._messages[session_id]:
                if record.message_id == message_id and record.role == "user":
                    record.content = content
                    record.status = "DONE"
                    record.updated_at = now
                    return MessageRecord(**record.to_dict())

            record = MessageRecord(
                message_id=message_id,
                session_id=session_id,
                role="user",
                status="DONE",
                content=content,
                created_at=now,
                updated_at=now,
            )
            self._messages[session_id].append(record)
            return MessageRecord(**record.to_dict())

    def append_agent_output(self, session_id: str, message_id: str, chunk: str, final: bool) -> MessageRecord:
        with self._lock:
            key = (session_id, message_id)
            record = self._agent_messages.get(key)
            now = utc_now_ms()
            if record is None:
                record = MessageRecord(
                    message_id=f"{message_id}:agent",
                    session_id=session_id,
                    role="agent",
                    status="STREAMING",
                    content=chunk,
                    created_at=now,
                    updated_at=now,
                )
                self._messages[session_id].append(record)
                self._agent_messages[key] = record
            else:
                record.content += chunk
                record.updated_at = now
            if final:
                record.status = "DONE"
            return MessageRecord(**record.to_dict())

    def list_messages(self, session_id: str, limit: int = 20, before: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [MessageRecord(**record.to_dict()) for record in self._messages.get(session_id, [])]

        if before:
            filtered: list[MessageRecord] = []
            for item in items:
                if item.message_id == before:
                    break
                filtered.append(item)
            items = filtered

        return [item.to_dict() for item in items[-limit:]]

    def create_permission(
        self,
        request_id: str,
        session_id: str,
        message_id: str,
        tool_name: str,
        summary: str,
        payload: dict[str, Any],
    ) -> PermissionRecord:
        with self._lock:
            record = PermissionRecord(
                request_id=request_id,
                session_id=session_id,
                message_id=message_id,
                tool_name=tool_name,
                status="PENDING",
                summary=summary,
                payload=payload,
            )
            self._permissions[request_id] = record
            session = self._sessions.get(session_id)
            if session is not None:
                session.pending_request_id = request_id
                session.state = "WAITING_PERMISSION"
                session.updated_at = utc_now_ms()
            return PermissionRecord(**record.to_dict())

    def resolve_permission(self, request_id: str, decision: str, resolved_by: str) -> PermissionRecord | None:
        with self._lock:
            record = self._permissions.get(request_id)
            if record is None:
                return None

            if record.status != "PENDING":
                return PermissionRecord(**record.to_dict())

            if decision == "APPROVE":
                record.status = "APPROVED"
            elif decision == "DENY":
                record.status = "DENIED"
            else:
                record.status = "TIMEOUT"

            record.resolved_at = utc_now_ms()
            record.resolved_by = resolved_by

            session = self._sessions.get(record.session_id)
            if session is not None:
                session.pending_request_id = None
                if session.state == "WAITING_PERMISSION":
                    session.state = "PROCESSING"
                session.updated_at = utc_now_ms()

            return PermissionRecord(**record.to_dict())

    def get_permission(self, request_id: str) -> PermissionRecord | None:
        with self._lock:
            record = self._permissions.get(request_id)
            if record is None:
                return None
            return PermissionRecord(**record.to_dict())

    def list_permissions(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            items = [
                PermissionRecord(**record.to_dict()).to_dict()
                for record in self._permissions.values()
                if record.session_id == session_id
            ]
        items.sort(key=lambda item: item["created_at"])
        return items
