from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_memory_runtime.audit.hashing import secure_hash


@dataclass(frozen=True)
class LLMCallTrace:
    trace_id: str
    occurred_at: str
    outcome: str
    agent_id: str
    provider: str
    model: str
    selected_memory_ids: tuple[str, ...]
    blocked_memory_count: int
    rule_version: str
    config_hash: str
    last_event_sequence: int
    state_hash: str
    request_hash: str
    response_hash: str | None = None
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_type: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "occurred_at": self.occurred_at,
            "outcome": self.outcome,
            "agent_id": self.agent_id,
            "provider": self.provider,
            "model": self.model,
            "selected_memory_ids": list(self.selected_memory_ids),
            "blocked_memory_count": self.blocked_memory_count,
            "rule_version": self.rule_version,
            "config_hash": self.config_hash,
            "last_event_sequence": self.last_event_sequence,
            "state_hash": self.state_hash,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
            "response_id": self.response_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error_type": self.error_type,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LLMCallTrace:
        return cls(
            trace_id=str(value["trace_id"]),
            occurred_at=str(value["occurred_at"]),
            outcome=str(value["outcome"]),
            agent_id=str(value["agent_id"]),
            provider=str(value["provider"]),
            model=str(value["model"]),
            selected_memory_ids=tuple(str(item) for item in value.get("selected_memory_ids", ())),
            blocked_memory_count=int(value.get("blocked_memory_count", 0)),
            rule_version=str(value.get("rule_version", "")),
            config_hash=str(value.get("config_hash", "")),
            last_event_sequence=int(value.get("last_event_sequence", 0)),
            state_hash=str(value.get("state_hash", "")),
            request_hash=str(value.get("request_hash", "")),
            response_hash=_optional_str(value.get("response_hash")),
            response_id=_optional_str(value.get("response_id")),
            input_tokens=_optional_int(value.get("input_tokens")),
            output_tokens=_optional_int(value.get("output_tokens")),
            error_type=_optional_str(value.get("error_type")),
            metadata=dict(value.get("metadata", {})),
        )


def build_llm_call_trace(
    *,
    agent_id: str,
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    selected_memory_ids: tuple[str, ...],
    blocked_memory_count: int,
    rule_version: str,
    config_hash: str,
    last_event_sequence: int,
    state_hash: str,
    response_content: str | None = None,
    response_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    error: BaseException | None = None,
) -> LLMCallTrace:
    # 审计只持久化可验证指纹，绝不保存提示词或回答正文。
    return LLMCallTrace(
        trace_id=str(uuid4()),
        occurred_at=datetime.now(UTC).isoformat(),
        outcome="failed" if error is not None else "completed",
        agent_id=agent_id,
        provider=provider,
        model=model,
        selected_memory_ids=selected_memory_ids,
        blocked_memory_count=blocked_memory_count,
        rule_version=rule_version,
        config_hash=config_hash,
        last_event_sequence=last_event_sequence,
        state_hash=state_hash,
        request_hash=secure_hash({"system_prompt": system_prompt, "user_prompt": user_prompt}),
        response_hash=secure_hash(response_content) if response_content is not None else None,
        response_id=response_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error_type=type(error).__name__ if error is not None else None,
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
