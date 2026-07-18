# Agent Design

The runtime does not give an LLM write access to memory. Agent outputs should be converted into
events first. Derivation rules and lifecycle reducers decide whether those events produce memory.

Expected integration pattern:

```text
Agent output
 -> structured event
 -> runtime.ingest(event)
 -> retrieve/project on the next turn
```

Prompt text can ask an LLM to summarize or propose a memory, but production writes should still
pass through a typed event, a registered derivation rule, write guard, and reducer.

The context projection is intentionally lossy. It is a safe read model for the next agent turn,
not a state object.

