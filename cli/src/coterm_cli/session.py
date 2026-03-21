from __future__ import annotations

from dataclasses import asdict

from .models import SessionContext, SessionState, utc_now_ms


class InvalidSessionTransition(RuntimeError):
    """Raised when a state transition is not allowed."""


VALID_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.INIT: {SessionState.ACTIVE, SessionState.ENDED},
    SessionState.ACTIVE: {SessionState.PROCESSING, SessionState.DISCONNECTED, SessionState.ENDED},
    SessionState.PROCESSING: {
        SessionState.WAITING_PERMISSION,
        SessionState.ACTIVE,
        SessionState.DISCONNECTED,
        SessionState.ENDED,
    },
    SessionState.WAITING_PERMISSION: {
        SessionState.PROCESSING,
        SessionState.DISCONNECTED,
        SessionState.ENDED,
    },
    SessionState.DISCONNECTED: {SessionState.ACTIVE, SessionState.ENDED},
    SessionState.ENDED: set(),
}


class SessionManager:
    def __init__(self, session_id: str, device_id: str, agent_type: str) -> None:
        self._context = SessionContext(
            session_id=session_id,
            device_id=device_id,
            agent_type=agent_type,
        )
        self._resume_state = SessionState.ACTIVE

    @property
    def state(self) -> SessionState:
        return self._context.state

    @property
    def session_id(self) -> str:
        return self._context.session_id

    @property
    def active_message_id(self) -> str | None:
        return self._context.active_message_id

    @property
    def pending_permission_id(self) -> str | None:
        return self._context.pending_permission_id

    def snapshot(self) -> dict[str, object]:
        return asdict(self._context)

    def transition(self, new_state: SessionState) -> None:
        current_state = self._context.state
        if new_state == current_state:
            return

        if new_state not in VALID_TRANSITIONS[current_state]:
            raise InvalidSessionTransition(f"cannot transition {current_state.value} -> {new_state.value}")

        self._context.state = new_state

    def force_state(self, new_state: SessionState) -> None:
        self._context.state = new_state

    def mark_disconnected(self) -> None:
        if self._context.state not in {SessionState.DISCONNECTED, SessionState.ENDED}:
            self._resume_state = self._context.state
            self._context.state = SessionState.DISCONNECTED

    def restore_connection_state(self) -> None:
        if self._context.state == SessionState.DISCONNECTED:
            self._context.state = self._resume_state

    def set_active_message(self, message_id: str | None) -> None:
        self._context.active_message_id = message_id

    def set_pending_permission(self, request_id: str | None) -> None:
        self._context.pending_permission_id = request_id

    def record_heartbeat(self) -> None:
        self._context.last_heartbeat_ts = utc_now_ms()

    def record_error(self, error_code: str) -> None:
        self._context.last_error_code = error_code
