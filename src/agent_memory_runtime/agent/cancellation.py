from __future__ import annotations

import asyncio

from agent_memory_runtime.agent.errors import AgentCancelledError


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason = "agent run was cancelled"

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "agent run was cancelled") -> None:
        if not self._event.is_set():
            self._reason = reason
            self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise AgentCancelledError(self._reason)
