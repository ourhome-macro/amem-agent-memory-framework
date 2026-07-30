# Storage

The storage module separates durable state from retrieval projections.

## Module Roles

- `SQLiteStoreBundle`: wires SQLite-backed stores, embedding queues, vector
  index, audit store, dream store, agent state, and orchestration state.
- `SQLiteMemoryStore`: stores `MemoryRecord` payloads and maintains FTS5, tag,
  ACL, and embedding-job projections.
- `SQLiteAuditStore`: stores audit envelopes, LLM traces, and memory audit logs.
- `SQLiteTombstoneStore`: stores memory deletion watermarks.
- `SQLiteEmbeddingJobStore`: stores asynchronous embedding outbox jobs.
- `SQLiteDreamStore`: stores Auto Dream jobs, leases, checkpoints, and reviews.
- `QdrantVectorIndex`: stores rebuildable vector points and retrieval payloads.
- `InMemory*Store`: provides test and embedded runtime stores.
- `Jsonl*Store`: provides appendable local persistence stores.

## Storage Boundary

SQLite memory records, audit logs, tombstones, jobs, and checkpoints are durable
runtime state. FTS5, SQLite vectors, and Qdrant points are rebuildable retrieval
projections.
