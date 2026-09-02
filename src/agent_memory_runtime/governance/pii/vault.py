from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

from agent_memory_runtime.governance.pii.token import PiiToken


@dataclass(frozen=True)
class _VaultRecord:
    token: PiiToken
    tenant_id: str
    owner_id: str
    value_digest: str


class HmacPiiVault:
    """Local irreversible PII vault that stores keyed HMAC digests only."""

    def __init__(self, *, secret_key: str) -> None:
        if not secret_key:
            raise ValueError("secret_key is required for HMAC PII vault")
        self._secret_key = secret_key.encode("utf-8")
        self._records: dict[str, _VaultRecord] = {}
        self._counter = 0

    def store(
        self,
        *,
        raw_value: str,
        pii_type: str,
        owner_id: str,
        field_path: str,
        tenant_id: str = "default",
    ) -> PiiToken:
        self._counter += 1
        token = PiiToken(
            token_id=f"PII_{self._counter:06d}",
            pii_type=pii_type,
            field_path=field_path,
        )
        self._records[token.token_id] = _VaultRecord(
            token=token,
            tenant_id=tenant_id,
            owner_id=owner_id,
            value_digest=self._digest(raw_value, pii_type=pii_type, tenant_id=tenant_id),
        )
        return token

    def resolve(self, token_id: str, *, owner_id: str) -> str | None:
        """HMAC digests are irreversible; use ``matches`` for equality checks."""

        record = self._records.get(token_id)
        if record is None or record.owner_id != owner_id:
            return None
        return None

    def matches(
        self,
        token_id: str,
        raw_value: str,
        *,
        owner_id: str,
        tenant_id: str = "default",
    ) -> bool:
        record = self._records.get(token_id)
        if record is None or record.owner_id != owner_id or record.tenant_id != tenant_id:
            return False
        return hmac.compare_digest(
            record.value_digest,
            self._digest(raw_value, pii_type=record.token.pii_type, tenant_id=tenant_id),
        )

    def find_tokens(
        self,
        raw_value: str,
        *,
        pii_type: str,
        tenant_id: str = "default",
        owner_id: str | None = None,
    ) -> list[PiiToken]:
        digest = self._digest(raw_value, pii_type=pii_type, tenant_id=tenant_id)
        return [
            record.token
            for record in self._records.values()
            if record.tenant_id == tenant_id
            and (owner_id is None or record.owner_id == owner_id)
            and hmac.compare_digest(record.value_digest, digest)
        ]

    def list_tokens(
        self,
        *,
        tenant_id: str | None = None,
        owner_id: str | None = None,
    ) -> list[PiiToken]:
        return [
            record.token
            for record in self._records.values()
            if (tenant_id is None or record.tenant_id == tenant_id)
            and (owner_id is None or record.owner_id == owner_id)
        ]

    def _digest(self, value: str, *, pii_type: str, tenant_id: str) -> str:
        payload = "\0".join(
            (tenant_id, pii_type, _canonical_value(value, pii_type=pii_type))
        ).encode("utf-8")
        return hmac.new(self._secret_key, payload, hashlib.sha256).hexdigest()


class SaltedHashPiiVault(HmacPiiVault):
    """Backward-compatible alias for the local HMAC PII vault."""


class SimpleEncryptedPiiVault(SaltedHashPiiVault):
    """Backward-compatible name for the local HMAC PII vault."""


def _canonical_value(value: str, *, pii_type: str) -> str:
    if pii_type == "payment_card":
        return re.sub(r"\D", "", value)
    if pii_type == "email":
        return value.strip().casefold()
    return value
