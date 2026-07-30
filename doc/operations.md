# Operations

The operations module is represented by runtime configuration, background
workers, retries, and status APIs.

## Module Roles

- `EmbeddingWorker`: processes embedding outbox jobs and publishes vectors.
- `AutoDreamWorker`: claims dream jobs, runs the analyzer, applies proposals,
  records review items, and advances checkpoints.
- `WorkerConfig`: defines batch size, lease duration, retry delay, and retry
  limits for background work.
- `semantic_status`: reports embedding generation, coverage, ready vectors,
  job counts, and backlog lag.
- `activate_embedding_generation`: switches active embedding generation after
  coverage and pending-job checks.
- `delete_retired_embedding_generation`: removes retired vector generations.

## Failure Boundary

Memory writes commit to SQLite before embedding publication. Vector publication
failures remain in the outbox for retry and do not change stored memory state.
