from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class LLMStreamEvent:
    type: str
    delta: str = ""
    model: str | None = None
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ChatClient(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Return one non-streaming assistant completion."""


class StreamingChatClient(Protocol):
    def stream_complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> Iterator[LLMStreamEvent]:
        """Yield assistant deltas from a streaming completion."""
