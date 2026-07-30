# Governance

Governance is the deterministic write boundary for memory changes.

## Module Roles

- `MemoryValidator`: validates required fields, enums, confidence, salience, and
  action shape.
- `AccessPolicy`: rejects cross-tenant, cross-user, cross-agent, and
  cross-subject writes; enforces optimistic version checks.
- `RiskGuard`: routes deletion, sensitive content, sensitive labels, and
  visibility expansion into review.
- `MemoryWritePolicy`: runs validator, access policy, and risk guard in order.
- `MemoryService`: applies only allowed proposals and writes audit records.

## Boundary

Semantic grouping and conflict proposal generation belong to Auto Dream.
Permission, schema, and risk decisions belong to deterministic policy code.
