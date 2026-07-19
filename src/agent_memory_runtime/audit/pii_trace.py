from __future__ import annotations

import re
from dataclasses import dataclass

from agent_memory_runtime.audit.decision import AuditDecision
from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.hashing import secure_hash
from agent_memory_runtime.audit.subject import AuditSubject
from agent_memory_runtime.domain.enums import MemoryLabel
from agent_memory_runtime.domain.event import Event

_CARD_NUMBER_PATTERN = re.compile(r"(?:\d[ -]?){13,19}")
_SENSITIVE_FIELD_MARKERS = {
    "authorization": "credential",
    "bankaccount": "bank_account",
    "card": "card_number",
    "credential": "credential",
    "cvc": "card_verification_code",
    "cvv": "card_verification_code",
    "password": "credential",
    "pin": "pin",
    "secret": "secret",
    "ssn": "ssn",
    "token": "credential",
}
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
    "visible_to",
}


@dataclass(frozen=True)
class PiiFinding:
    field_path: str
    pii_type: str
    value_hash: str | None = None
    action: str = AuditDecision.REDACT.value

    def to_dict(self) -> dict[str, object]:
        return {
            "field_path": self.field_path,
            "pii_type": self.pii_type,
            "value_hash": self.value_hash,
            "action": self.action,
        }


@dataclass(frozen=True)
class PiiTrace:
    event_id: str
    actor_id: str
    findings: tuple[PiiFinding, ...]

    @classmethod
    def from_event(cls, event: Event) -> PiiTrace:
        return cls(
            event_id=event.event_id,
            actor_id=event.actor_id,
            findings=tuple(_find_pii(event.payload, sensitive_event=_is_sensitive(event))),
        )

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def to_envelope(
        self,
        *,
        rule_version: str,
        config_hash: str,
        last_event_sequence: int,
        state_hash: str,
    ) -> AuditEnvelope:
        return AuditEnvelope(
            audit_type="pii",
            actor_id=self.actor_id,
            action="sanitize_event",
            outcome="redacted" if self.findings else "allowed",
            decision=(
                AuditDecision.REDACT.value if self.findings else AuditDecision.ALLOW.value
            ),
            subject=AuditSubject(subject_type="event", subject_id=self.event_id),
            rule_version=rule_version,
            config_hash=config_hash,
            last_event_sequence=last_event_sequence,
            state_hash=state_hash,
            payload={
                "finding_count": len(self.findings),
                "findings": [finding.to_dict() for finding in self.findings],
            },
        )


def _find_pii(
    value: object,
    *,
    field_path: str = "payload",
    field_name: str | None = None,
    sensitive_event: bool = False,
) -> list[PiiFinding]:
    findings: list[PiiFinding] = []
    pii_type = _sensitive_field_type(field_name)
    if pii_type is not None:
        findings.append(
            PiiFinding(field_path=field_path, pii_type=pii_type, value_hash=_hash(value))
        )
        return findings
    if isinstance(value, str) and _CARD_NUMBER_PATTERN.search(value):
        findings.append(
            PiiFinding(
                field_path=field_path,
                pii_type="card_number",
                value_hash=secure_hash(value),
            )
        )
    if isinstance(value, dict):
        for key, item in value.items():
            child_key = str(key)
            findings.extend(
                _find_pii(
                    item,
                    field_path=f"{field_path}.{child_key}",
                    field_name=child_key.casefold(),
                    sensitive_event=sensitive_event,
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(
                _find_pii(
                    item,
                    field_path=f"{field_path}[{index}]",
                    field_name=field_name,
                    sensitive_event=sensitive_event,
                )
            )
    elif sensitive_event and field_name not in _ROUTING_FIELD_NAMES:
        findings.append(
            PiiFinding(
                field_path=field_path,
                pii_type="sensitive_event_payload",
                value_hash=_hash(value),
            )
        )
    return findings


def _is_sensitive(event: Event) -> bool:
    return MemoryLabel.SENSITIVE.value in set(event.labels)


def _sensitive_field_type(field_name: str | None) -> str | None:
    if field_name is None:
        return None
    normalized = re.sub(r"[^a-z0-9]", "", field_name.casefold())
    for marker, pii_type in _SENSITIVE_FIELD_MARKERS.items():
        if marker in normalized:
            return pii_type
    return None


def _hash(value: object) -> str | None:
    if value is None:
        return None
    return secure_hash(value)
