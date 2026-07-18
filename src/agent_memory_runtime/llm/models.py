from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ChatClient(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Return one non-streaming assistant completion."""
