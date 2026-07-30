# Module Responsibilities

| Module | Role |
| --- | --- |
| `agent` | Runs agent requests, model calls, tool loops, checkpoints, and conversation compaction. |
| `tools` | Registers and executes explicit tools, including memory save, revise, forget, and search. |
| `memory.intake` | Builds `MemoryProposal` objects from explicit tools and Auto Dream output. |
| `memory.write_policy` | Enforces deterministic schema, access, version, and risk checks. |
| `memory.service` | Applies accepted proposals to `MemoryRecord`, tombstones, and audit logs. |
| `memory.intake.dream` | Produces maintenance proposals for duplicates, state conflicts, and missing derived memory. |
| `memory.intake.worker` | Schedules, leases, runs, retries, and checkpoints Auto Dream jobs. |
| `memory.retrieval` | Routes queries, gathers FTS5/Qdrant candidates, fuses, reranks, filters, and selects results. |
| `memory.embeddings` | Manages embedding providers, generations, outbox jobs, workers, SQLite vectors, and Qdrant vectors. |
| `memory.stores` | Provides SQLite, JSONL, and in-memory stores for events, memory, snapshots, tombstones, audit, jobs, and state. |
| `audit` | Records audit envelopes, LLM traces, memory write logs, and audit replay input. |
| `access` | Applies principal-based record access and sensitive payload sanitization. |
| `context` | Builds model-facing memory context, structured projections, and personalization snippets. |
| `llm` | Normalizes chat provider requests, responses, streaming events, usage, and errors. |
| `config` | Defines runtime, retrieval, rerank, query-router, worker, LLM, and token-budget settings. |

## Data Contracts

- `MemoryQuery`: read request with identity and retrieval constraints.
- `MemoryProposal`: write request with action, target, identity, evidence, and version.
- `MemoryRecord`: durable memory state.
- `MemoryAuditLog`: durable write history and replay input.
- `MemoryTombstone`: durable deletion watermark.
