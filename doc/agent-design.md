# Agent Runtime

The agent runtime coordinates model calls, tool execution, checkpoints, and
memory context injection.

## Module Roles

- `BusinessAgentRuntime`: executes `AgentRequest`, prepares model input, runs
  tool loops, and records run state.
- `AgentMemoryRuntime`: provides memory retrieval, projection, proposal
  application, audit recording, and Auto Dream scheduling.
- `AgentPolicy`: defines token budgets, tool policy, retry bounds, approval
  rules, and compaction thresholds.
- `AgentCheckpoint`: stores model-facing messages, tool call state, usage, and
  compaction metadata.
- `ToolExecutionContext`: carries tenant, user, agent, session, run, and call
  identity into tool handlers.
- `ModelGateway`: isolates provider-specific chat, streaming, usage, and tool
  call protocol handling.

## Memory Boundary

The agent runtime reads memory through `AgentMemoryRuntime.project()` and writes
memory through explicit tools that create `MemoryProposal` objects. Model text
does not mutate memory state directly.
