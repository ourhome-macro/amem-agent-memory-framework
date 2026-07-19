from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from agent_memory_runtime.governance.pii.token import PiiToken


@dataclass(frozen=True)
class _VaultRecord:
    token: PiiToken
    owner_id: str
    ciphertext: str


class SimpleEncryptedPiiVault:
    """Small local vault for tests and demos; production deployments should swap in KMS/HSM."""

    def __init__(self, *, secret_key: str) -> None:
        self._secret_key = secret_key.encode("utf-8")
        self._records: dict[str, _VaultRecord] = {}
        self._counter = 0

    def store(self, *, raw_value: str, pii_type: str, owner_id: str, field_path: str) -> PiiToken:
        self._counter += 1
        token = PiiToken(
            token_id=f"PII_{self._counter:06d}",
            pii_type=pii_type,
            field_path=field_path,
        )
        self._records[token.token_id] = _VaultRecord(
            token=token,
            owner_id=owner_id,
            ciphertext=self._crypt(raw_value),
        )
        return token

    def resolve(self, token_id: str, *, owner_id: str) -> str | None:
        record = self._records.get(token_id)
        if record is None or record.owner_id != owner_id:
            return None
        return self._crypt(record.ciphertext, decrypt=True)

    def list_tokens(self, *, owner_id: str | None = None) -> list[PiiToken]:
        return [
            record.token
            for record in self._records.values()
            if owner_id is None or record.owner_id == owner_id
        ]

    def _crypt(self, value: str, *, decrypt: bool = False) -> str:
        key = hashlib.sha256(self._secret_key).digest()
        if decrypt:
            data = base64.urlsafe_b64decode(value.encode("ascii"))
        else:
            data = value.encode("utf-8")
        transformed = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
        if decrypt:
            return transformed.decode("utf-8")
        return base64.urlsafe_b64encode(transformed).decode("ascii")
