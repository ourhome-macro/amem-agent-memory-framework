from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass

from agent_memory_runtime.governance.pii.token import PiiToken


@dataclass(frozen=True)
class _VaultRecord:
    token: PiiToken
    owner_id: str
    salt: bytes
    value_hash: str


class SaltedHashPiiVault:
    """Local irreversible PII vault that stores per-token salted hashes only."""

    def __init__(self, *, secret_key: str = "", salt_bytes: int = 16) -> None:
        if salt_bytes < 16:
            raise ValueError("salt_bytes must be at least 16")
        self._pepper = secret_key.encode("utf-8")
        self._salt_bytes = salt_bytes
        self._records: dict[str, _VaultRecord] = {}
        self._counter = 0

    def store(self, *, raw_value: str, pii_type: str, owner_id: str, field_path: str) -> PiiToken:
        self._counter += 1
        salt = os.urandom(self._salt_bytes)
        token = PiiToken(
            token_id=f"PII_{self._counter:06d}",
            pii_type=pii_type,
            field_path=field_path,
        )
        self._records[token.token_id] = _VaultRecord(
            token=token,
            owner_id=owner_id,
            salt=salt,
            value_hash=self._hash(raw_value, pii_type=pii_type, salt=salt),
        )
        return token

    def resolve(self, token_id: str, *, owner_id: str) -> str | None:
        """Salted hashes are irreversible; use ``matches`` for equality checks."""

        record = self._records.get(token_id)
        if record is None or record.owner_id != owner_id:
            return None
        return None

    def matches(self, token_id: str, raw_value: str, *, owner_id: str) -> bool:
        record = self._records.get(token_id)
        if record is None or record.owner_id != owner_id:
            return False
        return hmac.compare_digest(
            record.value_hash,
            self._hash(raw_value, pii_type=record.token.pii_type, salt=record.salt),
        )

    def list_tokens(self, *, owner_id: str | None = None) -> list[PiiToken]:
        return [
            record.token
            for record in self._records.values()
            if owner_id is None or record.owner_id == owner_id
        ]

    def _hash(self, value: str, *, pii_type: str, salt: bytes) -> str:
        payload = _canonical_value(value, pii_type=pii_type).encode("utf-8")
        return hashlib.sha256(salt + b"\0" + self._pepper + b"\0" + payload).hexdigest()


class SimpleEncryptedPiiVault(SaltedHashPiiVault):
    """Backward-compatible name for the local salted-hash PII vault."""


def _canonical_value(value: str, *, pii_type: str) -> str:
    if pii_type == "payment_card":
        return re.sub(r"\D", "", value)
    if pii_type == "email":
        return value.strip().casefold()
    return value
