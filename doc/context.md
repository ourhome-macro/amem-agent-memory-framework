# Context

The context module builds model-facing memory input from selected records.

## Module Roles

- `ContextBuilder`: selects records under `context_token_budget`, sanitizes
  projections, and builds `AgentContext`.
- `select_under_budget`: keeps selected memory within the configured token
  budget.
- `project_record`: converts `MemoryRecord` into structured model-facing fields.
- `sanitize_context`: removes forged memory fence markers from recalled text.
- `build_personalization_profile`: derives compact preference/profile snippets
  from selected records.
- `compact_checkpoint`: compresses older agent messages while preserving system
  rules, pinned facts, original task, and recent turns.
- `AdaptiveTokenEstimator`: estimates text, message, and tool-schema token usage.

## Budget

`RuntimeConfig.context_token_budget` defaults to `1000` for memory injection.
Conversation compaction uses separate `AgentPolicy` settings.
