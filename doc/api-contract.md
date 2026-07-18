# API Contract

## Python Runtime

### `AgentMemoryRuntime.ingest(event)`

Input: `Event` or event-shaped dict.

Side effects:

- appends event to `EventStore`
- derives memory candidates
- validates source and information flow
- upserts official `MemoryRecord` objects
- saves a `RuntimeSnapshot`

Output: `IngestResult`.

### `AgentMemoryRuntime.retrieve(query)`

Input: `MemoryQuery` or query-shaped dict.

Output: `(list[MemoryRecord], RuntimeTrace)`.

No state mutation except updating `runtime.last_trace`.

### `AgentMemoryRuntime.project(query)`

Input: `MemoryQuery` or query-shaped dict.

Output: `AgentContext` with selected memory ids, blocked count, projected context, projected
memory payloads, and retrieval trace.

### `AgentMemoryRuntime.replay(events=None)`

Input: optional event list. Defaults to current `EventStore`.

Side effects:

- clears `MemoryStore`
- re-applies events through derivation/lifecycle
- saves snapshot

Output: `RuntimeSnapshot`.

## CLI Contract

The `amem` CLI exposes `init`, `ingest`, `derive`, `retrieve`, `project`, `replay`, `eval`, and
three demos. Trace output must include:

- `selected_memory_ids`
- score breakdown
- blocked memory count
- `rule_version`
- `config_hash`
- `last_event_sequence`
- `state_hash`

