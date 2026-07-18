# Memory State Model

## Core Types

- `Event`: immutable source input with `event_id`, `sequence`, `kind`, `actor_id`,
  `session_id`, `payload`, labels, tags, and timestamp.
- `MemoryCandidate`: rule-derived proposed memory. It is not authoritative until it passes
  write guard and lifecycle reduction.
- `MemoryRecord`: official retrievable memory with type, scope, layer, owner, visibility,
  labels, source links, salience, confidence, status, and lifecycle metadata.
- `RuntimeSnapshot`: replay checkpoint with rule/config hashes and state hash.

## Memory Types

- `episodic`: concrete interaction or observation.
- `belief`: preference, stated belief, inferred stable user/agent belief.
- `relationship`: relationship signal between principals.
- `strategy`: task outcome or learned execution heuristic.

## Scope And Layer

Scopes:

- `private`: owned by one agent or explicitly visible principals.
- `shared`: visible to configured principals.
- `global`: broadly available memory.

Layers:

- `core`: stable long-term memory.
- `working`: active session memory.
- `archival`: retained for audit/replay but normally excluded from context.

## Source Links

Every active memory must carry `source_event_ids`. Derived strategy memories may also carry
`source_memory_ids` to preserve reasoning chains.

