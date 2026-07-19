from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent_memory_runtime.governance.pii.token import PiiToken
from agent_memory_runtime.governance.pii.vault import SimpleEncryptedPiiVault

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SENSITIVE_KEYS = ("email", "card", "phone", "credential", "secret", "password", "token")


@dataclass(frozen=True)
class ProtectedPayload:
    payload: dict[str, Any]
    tokens: tuple[PiiToken, ...]


class PiiProtector:
    def __init__(self, *, vault: SimpleEncryptedPiiVault) -> None:
        self.vault = vault

    def protect_payload(self, payload: dict[str, Any], *, owner_id: str) -> ProtectedPayload:
        tokens: list[PiiToken] = []
        protected = self._protect_value(payload, owner_id=owner_id, path="$", tokens=tokens)
        return ProtectedPayload(payload=dict(protected), tokens=tuple(tokens))

    def _protect_value(
        self,
        value: Any,
        *,
        owner_id: str,
        path: str,
        tokens: list[PiiToken],
    ) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._protect_value(
                    item,
                    owner_id=owner_id,
                    path=f"{path}.{key}",
                    tokens=tokens,
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._protect_value(item, owner_id=owner_id, path=f"{path}[{index}]", tokens=tokens)
                for index, item in enumerate(value)
            ]
        if isinstance(value, str):
            if _is_sensitive_path(path) and value:
                return self._tokenize(
                    value,
                    pii_type=_pii_type_from_path(path),
                    owner_id=owner_id,
                    path=path,
                    tokens=tokens,
                )
            return self._protect_text(value, owner_id=owner_id, path=path, tokens=tokens)
        return value

    def _protect_text(
        self,
        text: str,
        *,
        owner_id: str,
        path: str,
        tokens: list[PiiToken],
    ) -> str:
        protected = _EMAIL_RE.sub(
            lambda match: self._tokenize(
                match.group(0),
                pii_type="email",
                owner_id=owner_id,
                path=path,
                tokens=tokens,
            ),
            text,
        )

        def replace_card(match: re.Match[str]) -> str:
            candidate = match.group(0)
            digits = re.sub(r"\D", "", candidate)
            if len(digits) < 13 or len(digits) > 19:
                return candidate
            return self._tokenize(
                candidate,
                pii_type="payment_card",
                owner_id=owner_id,
                path=path,
                tokens=tokens,
            )

        return _CARD_RE.sub(replace_card, protected)

    def _tokenize(
        self,
        raw_value: str,
        *,
        pii_type: str,
        owner_id: str,
        path: str,
        tokens: list[PiiToken],
    ) -> str:
        token = self.vault.store(
            raw_value=raw_value,
            pii_type=pii_type,
            owner_id=owner_id,
            field_path=path,
        )
        tokens.append(token)
        return token.placeholder


def _is_sensitive_path(path: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", path.casefold())
    return any(marker in normalized for marker in _SENSITIVE_KEYS)


def _pii_type_from_path(path: str) -> str:
    normalized = path.casefold()
    if "email" in normalized:
        return "email"
    if "card" in normalized:
        return "payment_card"
    if "phone" in normalized:
        return "phone"
    return "secret"
