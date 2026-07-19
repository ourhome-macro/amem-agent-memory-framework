from __future__ import annotations

from agent_memory_runtime.audit.envelope import AuditEnvelope


def filter_audit_records(
    records: list[AuditEnvelope],
    *,
    audit_type: str | None = None,
    outcome: str | None = None,
    subject: str | None = None,
) -> list[AuditEnvelope]:
    selected = records
    if audit_type:
        selected = [record for record in selected if record.audit_type == audit_type]
    if outcome:
        selected = [record for record in selected if record.outcome == outcome]
    if subject:
        subject_type, subject_id = _parse_subject(subject)
        selected = [
            record
            for record in selected
            if (
                record.subject.subject_type == subject_type
                and record.subject.subject_id == subject_id
            )
        ]
    return selected


def _parse_subject(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise ValueError("Subject filter must use '<type>:<id>'.")
    subject_type, subject_id = value.split(":", 1)
    if not subject_type or not subject_id:
        raise ValueError("Subject filter must use '<type>:<id>'.")
    return subject_type, subject_id
