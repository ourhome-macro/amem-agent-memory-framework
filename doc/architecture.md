# Architecture

AMEM is a long-term memory runtime for stateful AI agents. SQLite stores durable
memory state. FTS5 and Qdrant provide retrieval projections. Agent code accesses
memory through runtime APIs instead of reading or mutating storage directly.

## Main Write Path

```text
save/revise/forget tools or Auto Dream
  -> MemoryProposal
  -> MemoryWritePolicy
  -> MemoryService
  -> MemoryRecord
  -> MemoryAuditLog
  -> embedding outbox
  -> Qdrant vector projection
```

## Main Read Path

```text
MemoryQuery
  -> query router
  -> FTS5 and/or Qdrant candidates
  -> RRF fusion
  -> deterministic rerank and final filter
  -> AccessChecker
  -> ContextBuilder
```

## Core Modules

- `memory.intake`: converts tools and Auto Dream output into proposals.
- `memory.write_policy`: validates schema, identity, version, and write risk.
- `memory.service`: applies accepted proposals and writes audit records.
- `memory.retrieval`: selects authorized records for a query.
- `context`: renders memory into model-facing context.
- `audit`: records write history and replay input.
- `agent`: coordinates model calls, tools, checkpoints, and memory projection.
