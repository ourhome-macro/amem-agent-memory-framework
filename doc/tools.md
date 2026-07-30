# Tools

The tool runtime exposes explicit agent capabilities and records tool execution
through typed context objects.

## Module Roles

- `ToolRegistry`: registers callable tools and their schemas.
- `ToolExecutor`: validates tool arguments, invokes handlers, and records tool
  audit data.
- `ToolExecutionContext`: passes tenant, user, agent, session, run, and call
  identity to handlers.
- `MemoryIntakeService`: implements `save_memory`, `revise_memory`, and
  `forget_memory` by creating `MemoryProposal` objects.
- `MemorySearchTool`: exposes authorized memory search through the runtime
  projection path.

## Memory Tool Semantics

- `save_memory`: creates or reinforces memory.
- `revise_memory`: updates a targeted memory using optimistic version checks.
- `forget_memory`: archives or deletes memory and records tombstone/audit data.
