from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


def utc_now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    device_id: str
    agent_type: str
    state: str = "INIT"
    connected: bool = False
    created_at: int = field(default_factory=utc_now_ms)
    updated_at: int = field(default_factory=utc_now_ms)
    last_heartbeat_at: int | None = None
    active_message_id: str | None = None
    pending_request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MessageRecord:
    message_id: str
    session_id: str
    role: str
    status: str
    content: str
    created_at: int = field(default_factory=utc_now_ms)
    updated_at: int = field(default_factory=utc_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PermissionRecord:
    request_id: str
    session_id: str
    message_id: str
    tool_name: str
    status: str
    summary: str
    payload: dict[str, Any]
    created_at: int = field(default_factory=utc_now_ms)
    resolved_at: int | None = None
    resolved_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_ws_message(
    *,
    message_type: str,
    session_id: str,
    payload: dict[str, Any],
    trace_id: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": "1.0",
        "type": message_type,
        "session_id": session_id,
        "ts": utc_now_ms(),
        "payload": payload,
    }
    if trace_id:
        data["trace_id"] = trace_id
    return data


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)
