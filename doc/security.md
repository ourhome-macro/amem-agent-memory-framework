# Security

Security is enforced through identity-aware retrieval, deterministic write
policy, sanitization, and audit records.

## Module Roles

- `Principal`: represents tenant, user, and agent identity for access checks.
- `AccessChecker`: authorizes memory records before context projection.
- `structured_memory_where`: applies tenant, user, session, scope, type, layer,
  tag, and ACL filters in SQLite queries.
- `QdrantVectorIndex`: applies equivalent payload filters before vector top-k.
- `sanitize_event`: removes sensitive payload fields from legacy event audit.
- `sanitize_context`: removes forged memory-context fence markers.
- `RiskGuard`: routes sensitive or high-risk writes to review.
