# Audit

The audit module records memory changes, access decisions, model-call metadata,
and legacy event observations.

## Module Roles

- `MemoryAuditLog`: stores before/after memory state for proposal writes.
- `AuditEnvelope`: stores normalized audit records for access, model, and event
  observation flows.
- `AuditStore`: persists audit envelopes, LLM traces, and memory audit logs.
- `replay_memory_audit_logs`: rebuilds memory state from ordered
  `MemoryAuditLog` entries.
- `RuntimeTrace`: exposes retrieval, projection, and model-call metadata for one
  runtime request.

## Memory Replay Semantics

- `after_record` present: replay upserts that record as memory state.
- `after_record` absent: replay deletes that memory id.
- delete logs rebuild tombstones from `before_record` and audit metadata.
- replay replaces retrieval projections through the memory store.
