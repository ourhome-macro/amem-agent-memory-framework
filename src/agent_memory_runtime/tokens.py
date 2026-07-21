from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


class TokenEstimator(Protocol):
    """Model-aware token estimation boundary used before provider calls."""

    def count_text(self, text: str, *, model: str | None = None) -> int:
        ...

    def count_messages(
        self,
        messages: Sequence[object],
        *,
        tools: Sequence[object] = (),
        model: str | None = None,
    ) -> int:
        ...


@dataclass(frozen=True)
class CallableTokenEstimator:
    """Adapter for a provider-native tokenizer supplied by an application."""

    counter: Callable[[str, str | None], int]

    def count_text(self, text: str, *, model: str | None = None) -> int:
        return max(0, int(self.counter(text, model)))

    def count_messages(
        self,
        messages: Sequence[object],
        *,
        tools: Sequence[object] = (),
        model: str | None = None,
    ) -> int:
        payload = {
            "messages": [_serializable(item) for item in messages],
            "tools": [_serializable(item) for item in tools],
        }
        return self.count_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            model=model,
        )


class AdaptiveTokenEstimator:
    """Uses a registered/provider tokenizer and a conservative Unicode fallback.

    Tokenization differs across providers. Applications can register an exact counter
    per model or inject ``CallableTokenEstimator``. The fallback deliberately counts
    CJK characters individually instead of using whitespace, avoiding severe budget
    underestimation for Chinese prompts.
    """

    def __init__(
        self,
        counters: dict[str, Callable[[str], int]] | None = None,
        *,
        safety_factor: float = 1.12,
    ) -> None:
        if safety_factor < 1:
            raise ValueError("token estimator safety_factor must be at least 1")
        self._counters = dict(counters or {})
        self.safety_factor = safety_factor

    def register(self, model: str, counter: Callable[[str], int]) -> None:
        if not model.strip():
            raise ValueError("token estimator model cannot be empty")
        self._counters[model] = counter

    def count_text(self, text: str, *, model: str | None = None) -> int:
        counter = self._counters.get(model or "")
        if counter is not None:
            return max(0, int(counter(text)))
        return _fallback_token_count(text, safety_factor=self.safety_factor)

    def count_messages(
        self,
        messages: Sequence[object],
        *,
        tools: Sequence[object] = (),
        model: str | None = None,
    ) -> int:
        # Message framing and tool schema framing consume provider tokens in addition
        # to visible content. Keep this overhead explicit and conservative.
        total = 3
        for message in messages:
            value = _serializable(message)
            total += 4 + self.count_text(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                model=model,
            )
        if tools:
            total += 8 + self.count_text(
                json.dumps(
                    [_serializable(item) for item in tools],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                model=model,
            )
        return total


def _fallback_token_count(text: str, *, safety_factor: float) -> int:
    if not text:
        return 0
    ascii_characters = 0
    cjk_characters = 0
    other_characters = 0
    for character in text:
        if character.isspace():
            continue
        if _is_cjk(character):
            cjk_characters += 1
        elif ord(character) < 128:
            ascii_characters += 1
        else:
            other_characters += 1
    ascii_words = len(_ASCII_WORD_RE.findall(text))
    raw = (
        cjk_characters
        + math.ceil(other_characters / 2)
        + math.ceil(ascii_characters / 4)
        + math.ceil(ascii_words / 3)
    )
    return max(1, math.ceil(raw * safety_factor))


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _serializable(value: object) -> Any:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    normalized = unicodedata.normalize("NFKC", str(value))
    return normalized
