from __future__ import annotations

import json
from typing import Any, Protocol

from agent_memory_runtime.exceptions import StoreError


class StateCodec(Protocol):
    """Serialization boundary for encrypting durable agent state at rest."""

    def encode(self, value: dict[str, Any]) -> str:
        ...

    def decode(self, payload: str) -> dict[str, Any]:
        ...


class JsonStateCodec:
    """Plain JSON codec intended for tests or encrypted storage volumes."""

    def encode(self, value: dict[str, Any]) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise StoreError("agent state is not JSON serializable") from error

    def decode(self, payload: str) -> dict[str, Any]:
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as error:
            raise StoreError("agent state payload cannot be decoded") from error
        if not isinstance(value, dict):
            raise StoreError("agent state payload must decode to an object")
        return {str(key): item for key, item in value.items()}
