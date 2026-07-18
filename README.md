# Agent Memory Runtime

Agent Memory Runtime is an event-sourced memory framework for stateful agents.
Ordinary RAG retrieves external knowledge. This runtime maintains an agent's long-term
interaction state, proves where each memory came from, governs memory lifecycle, and safely
projects selected memory into an agent context.

The first version intentionally does not call an LLM and does not require a vector database.
It proves the hard part first: memories are produced from events, typed, scoped, access checked,
retrieved, compressed, snapshotted, and replayed.

## Core Flows

```text
Event
 -> EventStore
 -> Derivation
 -> Lifecycle
 -> MemoryStore
```

```text
Query
 -> Retrieval
 -> Access
 -> Compression
 -> Context
```

```text
EventStore
 -> Replay
 -> RuntimeSnapshot
 -> Consistency Check
```

## Install

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## CLI

```powershell
amem init
amem ingest examples/data/customer_support_events.jsonl
amem derive
amem retrieve --agent support_agent --query "refund status"
amem project --agent support_agent --query "refund status"
amem replay
amem eval examples/evals/retrieval_cases.yml
amem demo customer-support
amem demo personal-assistant
amem demo mock-interviewer
```

CLI trace output includes selected memory ids, score breakdown, blocked memory count,
`rule_version`, `config_hash`, `last_event_sequence`, and `state_hash`.

## Python

```python
from agent_memory_runtime import AgentMemoryRuntime
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.query import MemoryQuery

runtime = AgentMemoryRuntime()
runtime.ingest(Event(
    event_id="evt-1",
    kind="message.created",
    actor_id="user",
    session_id="s1",
    labels=("private",),
    payload={
        "agent_id": "assistant",
        "subject_id": "user",
        "text": "User prefers concise status updates.",
    },
))

context = runtime.project(MemoryQuery(agent_id="assistant", text="status updates"))
print(context.projected_context)
```

## Repository Layout

```text
src/agent_memory_runtime/
  runtime.py
  config.py
  domain/
  memory/
    derivation/
    lifecycle/
    retrieval/
    compression/
    stores/
  access/
  context/
  audit/
  evals/
  cli/
```

## Design Lineage

This project extracts the useful production boundary from the adjacent mystery-agent system:
LLMs can produce expression or intent, but durable state changes must be rule-derived,
source-linked, replayable, and access checked before they enter context.

