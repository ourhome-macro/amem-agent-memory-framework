from __future__ import annotations

import re

_SENSITIVE_KEY_MARKERS = (
    "api",
    "authorization",
    "card",
    "credential",
    "cvc",
    "cvv",
    "password",
    "pin",
    "prompt",
    "query",
    "response",
    "secret",
    "token",
)
_FENCE_TAG_RE = re.compile(r"</?\s*memory\s*[-_]\s*context\s*>", re.IGNORECASE)


def redact_audit_payload(value: object, *, key: str | None = None) -> object:
    if _is_sensitive_key(key):
        return "[audit-redacted]"
    if isinstance(value, str):
        return _FENCE_TAG_RE.sub("", value)
    if isinstance(value, dict):
        return {
            str(item_key): redact_audit_payload(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_audit_payload(item) for item in value]
    if isinstance(value, tuple):
        return [redact_audit_payload(item) for item in value]
    return value


def _is_sensitive_key(key: str | None) -> bool:
    if key is None:
        return False
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    if normalized.endswith(("hash", "id", "ids", "tokens")) or normalized in {
        "firsttokenms",
    }:
        return False
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)
