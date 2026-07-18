# Architecture

## Positioning

Agent Memory Runtime is a production-oriented runtime for agent state, not a prompt helper.
The runtime stores original events, derives typed memory candidates, reduces those candidates
into official memory records, and only then lets retrieval project memory into context.

The root boundary is:

```text
event log is authority
memory records are derived state
context is a temporary projection
```

## Write Path

```text
Event
 -> EventStore.append
 -> DerivationEngine.derive
 -> WriteGuard.validate
 -> LifecycleReducer.reduce
 -> MemoryStore.upsert
 -> RuntimeSnapshot
```

The system rejects candidates without source events. Private memories cannot be promoted to
shared/global memory by later writes, and sensitive labels cannot be silently dropped.

## Read Path

```text
MemoryQuery
 -> RetrievalPipeline
 -> hard filters
 -> AccessChecker
 -> scoring/rerank/budget
 -> ContextBuilder
```

Hard filters remove wrong session/type/scope/layer/tag/status records. Access checking then
blocks unauthorized private and sensitive records. Scoring combines keyword overlap, recency,
salience, confidence, type boost, reinforcement, and source-link signals.

## Replay Path

```text
EventStore.list_events
 -> MemoryStore.clear
 -> apply each event through derivation and lifecycle
 -> RuntimeSnapshot
 -> consistency comparison
```

Replay snapshots include `rule_version`, `config_hash`, `last_event_sequence`, and `state_hash`.
Changing rules or configuration changes the state hash and is detectable.

## Stores

The store interfaces are intentionally split:

- `EventStore`: original events only.
- `MemoryStore`: official derived `MemoryRecord` objects.
- `SnapshotStore`: runtime snapshots and replay checkpoints.

In-memory, JSONL, and SQLite implementations are available. SQLite can be the backing database
for all three roles, but the runtime still calls separate interfaces.

