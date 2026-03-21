from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def utc_now_ms() -> int:
    return int(time.time() * 1000)


class SessionState(str, Enum):
    INIT = "INIT"
    ACTIVE = "ACTIVE"
    PROCESSING = "PROCESSING"
    WAITING_PERMISSION = "WAITING_PERMISSION"
    DISCONNECTED = "DISCONNECTED"
    ENDED = "ENDED"


class AgentEventType(str, Enum):
    MESSAGE_START = "message_start"
    OUTPUT = "output"
    MESSAGE_STOP = "message_stop"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    AGENT_STATE = "agent_state"


class PermissionDecision(str, Enum):
    APPROVE = "APPROVE"
    DENY = "DENY"
    TIMEOUT = "TIMEOUT"


@dataclass(slots=True)
class RenderBlock:
    block_id: str
    message_id: str
    kind: str
    status: str
    sequence: int
    created_at: int
    updated_at: int
    text: str | None = None
    format: str | None = None
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "block_id": self.block_id,
            "message_id": self.message_id,
            "kind": self.kind,
            "status": self.status,
            "sequence": self.sequence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.text is not None:
            data["text"] = self.text
        if self.format is not None:
            data["format"] = self.format
        if self.meta is not None:
            data["meta"] = self.meta
        return data


@dataclass(slots=True)
class AgentEvent:
    type: AgentEventType
    message_id: str | None = None
    request_id: str | None = None
    content: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None
    error_code: str | None = None
    fatal: bool = False


@dataclass(slots=True)
class HubMessage:
    type: str
    session_id: str
    payload: dict[str, Any]
    version: str = "1.0"
    trace_id: str | None = None
    ts: int = field(default_factory=utc_now_ms)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": self.version,
            "type": self.type,
            "session_id": self.session_id,
            "ts": self.ts,
            "payload": self.payload,
        }
        if self.trace_id:
            data["trace_id"] = self.trace_id
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HubMessage":
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        return cls(
            version=str(data.get("version", "1.0")),
            type=str(data["type"]),
            session_id=str(data["session_id"]),
            trace_id=str(data["trace_id"]) if data.get("trace_id") is not None else None,
            ts=int(data.get("ts", utc_now_ms())),
            payload=payload,
        )

    @classmethod
    def from_json(cls, raw: str) -> "HubMessage":
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("hub message must be an object")
        return cls.from_dict(data)


@dataclass(slots=True)
class SessionContext:
    session_id: str
    device_id: str
    agent_type: str
    state: SessionState = SessionState.INIT
    active_message_id: str | None = None
    pending_permission_id: str | None = None
    last_heartbeat_ts: int | None = None
    last_error_code: str | None = None
