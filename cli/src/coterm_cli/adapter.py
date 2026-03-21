from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from .models import AgentEvent, PermissionDecision


class AgentAdapter(ABC):
    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send_user_message(self, message_id: str, content: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send_permission_decision(self, request_id: str, decision: PermissionDecision) -> None:
        raise NotImplementedError

    @abstractmethod
    async def cancel_current_turn(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def events(self) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError
