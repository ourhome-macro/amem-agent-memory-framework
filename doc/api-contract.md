# API Contract

This document lists the public data contracts used by runtime modules.

## MemoryQuery

`MemoryQuery` describes a retrieval request.

- Identity fields: `tenant_id`, `user_id`, `agent_id`, `session_id`.
- Search fields: `text`, `limit`, `tags`, `memory_types`, `layers`, `scopes`.
- Session behavior: `session_policy` controls exact-session, profile, or broad recall.

## MemoryProposal

`MemoryProposal` is the write intent accepted by `MemoryService`.

- Identity fields: `actor_id`, `tenant_id`, `user_id`, `agent_id`, `session_id`.
- Write target: `action`, `target_memory_id`, `subject_id`, `key`, `content`.
- Memory shape: `memory_type`, `layer`, `scope`, `visible_to`, `labels`, `tags`.
- Evidence: `source_message_ids`, `source_memory_ids`, `evidence_text`, `reason`.
- Safety and idempotency: `proposal_id`, `expected_version`, `source`.
- Auto Dream metadata: `dream_run_id`, `dream_version`.

## Runtime Methods

- `retrieve(query)`: returns selected memory records and retrieval trace.
- `project(query)`: builds model-facing memory context.
- `apply_memory_proposal(proposal)`: validates and applies a memory write.
- `replay_memory_audit()`: rebuilds memory state from `MemoryAuditLog`.
- `schedule_auto_dream(...)`: enqueues semantic maintenance work.
- `run_auto_dream_once(...)`: processes one Auto Dream job.
