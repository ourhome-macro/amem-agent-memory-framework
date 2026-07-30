# World State

World state is the durable memory state managed by the runtime.

## Module Roles

- `MemoryRecord`: authoritative memory item with type, layer, scope, ownership,
  visibility, confidence, salience, version, and source metadata.
- `MemoryAuditLog`: ordered write history with before/after records and evidence.
- `MemoryTombstone`: deletion watermark used by read paths and replay recovery.
- `RuntimeSnapshot`: state digest used for tracing and fast response metadata.
- `EmbeddingJob`: outbox item for asynchronous vector projection.
- `DreamJob`: queued Auto Dream maintenance unit with lease and checkpoint state.

## Memory Layers

- `core`: stable facts, preferences, and durable profile memory.
- `working`: task or session-local memory.
- `archival`: archived memory that is recalled only when query policy allows it.
