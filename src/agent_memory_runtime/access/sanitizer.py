from __future__ import annotations

import re

from agent_memory_runtime.access.principal import Principal
from agent_memory_runtime.domain.enums import MemoryLabel
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord

_SENSITIVE_FIELD_NAMES = {
    "access_token",
    "api_key",
    "authorization",
    "bank_account",
    "card_number",
    "credential",
    "cvv",
    "cvc",
    "id_number",
    "password",
    "refresh_token",
    "secret",
    "ssn",
}
_SENSITIVE_FIELD_MARKERS = {
    "authorization",
    "bankaccount",
    "card",
    "credential",
    "cvc",
    "cvv",
    "password",
    "pin",
    "secret",
    "ssn",
    "token",
}
# 这些字段用于派生和访问控制；事件被判定为敏感后，其余字段默认拒绝保留。
_ROUTING_FIELD_NAMES = {
    "agent_id",
    "confidence",
    "layer",
    "operation",
    "salience",
    "scope",
    "source_id",
    "source_memory_ids",
    "subject_id",
    "target_id",
    "tenant_id",
    "user_id",
    "visible_to",
}
_FREE_TEXT_FIELDS = {"belief", "content", "outcome", "preference", "summary", "text"}
_CARD_NUMBER_PATTERN = re.compile(r"(?:\d[ -]?){13,19}")


def sanitize(record: MemoryRecord, principal: Principal) -> MemoryRecord:
    if MemoryLabel.SENSITIVE.value not in set(record.labels) or principal.is_auditor:
        return record
    return MemoryRecord.from_dict(
        {
            **record.to_dict(),
            "content": "[sensitive memory redacted]",
            "metadata": {},
        }
    )


def sanitize_event(event: Event) -> Event:
    """Remove sensitive free text and credential-like fields before persistence."""
    detected_sensitive_data = _contains_sensitive_data(event.payload)
    if MemoryLabel.SENSITIVE.value not in set(event.labels) and not detected_sensitive_data:
        return event
    labels = tuple(dict.fromkeys([*event.labels, MemoryLabel.SENSITIVE.value]))
    return Event.from_dict(
        {
            **event.to_dict(),
            "labels": labels,
            # 持久化和派生使用同一份最小化载荷，不能保留原始副本。
            "payload": _sanitize_payload(event.payload, sensitive_event=True),
        }
    )


def _sanitize_payload(
    value: object,
    *,
    field_name: str | None = None,
    sensitive_event: bool = False,
) -> object:
    if _is_sensitive_field(field_name):
        return "[redacted]"
    if isinstance(value, str) and (
        field_name in _FREE_TEXT_FIELDS
        or (sensitive_event and field_name not in _ROUTING_FIELD_NAMES)
    ):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(key): _sanitize_payload(
                item,
                field_name=str(key).casefold(),
                sensitive_event=sensitive_event,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _sanitize_payload(item, field_name=field_name, sensitive_event=sensitive_event)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _sanitize_payload(item, field_name=field_name, sensitive_event=sensitive_event)
            for item in value
        )
    # 敏感事件可能含有未分类的机密字段，包括数值形式的 PIN。
    if sensitive_event and field_name not in _ROUTING_FIELD_NAMES:
        return "[redacted]"
    if isinstance(value, str):
        return _CARD_NUMBER_PATTERN.sub("[redacted]", value)
    return value


def _contains_sensitive_data(value: object, *, field_name: str | None = None) -> bool:
    if _is_sensitive_field(field_name):
        return True
    if isinstance(value, dict):
        return any(
            _contains_sensitive_data(item, field_name=str(key).casefold())
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_data(item) for item in value)
    return isinstance(value, str) and _CARD_NUMBER_PATTERN.search(value) is not None


def _is_sensitive_field(field_name: str | None) -> bool:
    if field_name is None:
        return False
    normalized = re.sub(r"[^a-z0-9]", "", field_name.casefold())
    return normalized in _SENSITIVE_FIELD_NAMES or any(
        marker in normalized for marker in _SENSITIVE_FIELD_MARKERS
    )
