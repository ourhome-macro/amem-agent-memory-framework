from __future__ import annotations

from pathlib import Path

from agent_memory_runtime.audit.dashboard import generate_audit_dashboard_html
from agent_memory_runtime.audit.stores import InMemoryAuditStore
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.runtime import AgentMemoryRuntime
from agent_memory_runtime.tools import (
    FileReadTool,
    FileWriteTool,
    FunctionTool,
    MemorySearchTool,
    StaticWebSearchProvider,
    ToolExecutor,
    ToolPolicy,
    ToolRegistry,
    ToolRequest,
    WebSearchTool,
)


def test_function_tool_call_is_audited_and_normalized_to_event() -> None:
    audit_store = InMemoryAuditStore()
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="math.add",
            description="Add two numbers.",
            handler=lambda args: {"sum": int(args["a"]) + int(args["b"])},
        )
    )
    executor = ToolExecutor(registry=registry, audit_store=audit_store)

    execution = executor.execute(
        ToolRequest(
            tool_name="math.add",
            arguments={"a": 2, "b": 3},
            actor_id="user",
            agent_id="assistant",
            session_id="s1",
        )
    )

    assert execution.result.status == "succeeded"
    assert execution.result.output == {"sum": 5}
    assert execution.event.kind == "tool.result"
    assert execution.event.payload["tool_name"] == "math.add"
    assert execution.event.payload["result_status"] == "succeeded"
    assert execution.event.payload["output_hash"] == execution.result.output_hash
    assert execution.event.payload["summary"] == "Tool math.add succeeded."

    audit = audit_store.list_envelopes()[0]
    assert audit.audit_type == "tool_call"
    assert audit.decision == "allow"
    assert audit.payload["input_hash"]
    assert audit.payload["output_hash"] == execution.result.output_hash
    assert '"sum": 5' not in str(audit.to_dict())


def test_file_tools_are_sandboxed_to_configured_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.txt").write_text("local note", encoding="utf-8")
    audit_store = InMemoryAuditStore()
    registry = ToolRegistry()
    registry.register(FileReadTool(root=root))
    registry.register(FileWriteTool(root=root))
    executor = ToolExecutor(registry=registry, audit_store=audit_store)

    read = executor.execute(
        ToolRequest(
            tool_name="file.read",
            arguments={"path": "note.txt"},
            actor_id="user",
            agent_id="assistant",
            session_id="s1",
        )
    )
    write = executor.execute(
        ToolRequest(
            tool_name="file.write",
            arguments={"path": "out.txt", "content": "stored"},
            actor_id="user",
            agent_id="assistant",
            session_id="s1",
        )
    )
    blocked = executor.execute(
        ToolRequest(
            tool_name="file.read",
            arguments={"path": "..\\outside.txt"},
            actor_id="user",
            agent_id="assistant",
            session_id="s1",
        )
    )

    assert read.result.output["content"] == "local note"
    assert write.result.output["bytes_written"] == 6
    assert (root / "out.txt").read_text(encoding="utf-8") == "stored"
    assert blocked.result.status == "blocked"
    assert blocked.result.error_type == "ToolPolicyError"
    assert len(audit_store.list_envelopes()) == 3
    assert audit_store.list_envelopes()[-1].decision == "block"


def test_web_search_tool_uses_provider_and_never_audits_raw_query() -> None:
    audit_store = InMemoryAuditStore()
    registry = ToolRegistry()
    registry.register(
        WebSearchTool(
            provider=StaticWebSearchProvider(
                results=[
                    {
                        "title": "Agent memory",
                        "url": "https://example.com/memory",
                        "snippet": "A result about agent memory.",
                    }
                ]
            )
        )
    )
    executor = ToolExecutor(registry=registry, audit_store=audit_store)

    execution = executor.execute(
        ToolRequest(
            tool_name="web.search",
            arguments={"query": "private refund status", "max_results": 1},
            actor_id="user",
            agent_id="assistant",
            session_id="s1",
        )
    )

    assert execution.result.status == "succeeded"
    assert execution.result.output["results"][0]["url"] == "https://example.com/memory"
    audit_text = str(audit_store.list_envelopes()[0].to_dict())
    assert "private refund status" not in audit_text
    assert "input_hash" in audit_text


def test_memory_search_tool_projects_authorized_memories() -> None:
    memory_runtime = AgentMemoryRuntime()
    memory_runtime.memory_store.upsert(
        _memory_record(
            "memory-db",
            "The user prefers PostgreSQL for analytics workloads.",
        )
    )
    memory_runtime.memory_store.upsert(
        _memory_record(
            "other-tenant",
            "Other tenant private memory.",
            tenant_id="tenant-b",
        )
    )
    audit_store = InMemoryAuditStore()
    registry = ToolRegistry()
    registry.register(
        MemorySearchTool(
            runtime=memory_runtime,
            default_agent_id="assistant",
            default_tenant_id="tenant-a",
            default_user_id="user-a",
            default_session_id="s1",
        )
    )
    executor = ToolExecutor(registry=registry, audit_store=audit_store)

    execution = executor.execute(
        ToolRequest(
            tool_name="memory.search",
            arguments={"text": "PostgreSQL analytics", "limit": 5},
            actor_id="user",
            agent_id="assistant",
            session_id="s1",
            tenant_id="tenant-a",
            user_id="user-a",
        )
    )

    assert execution.result.status == "succeeded"
    assert execution.result.output["selected_memory_ids"] == ["memory-db"]
    assert execution.result.output["memories"][0]["content"].startswith("The user prefers")
    assert execution.result.output["trace"]["candidate_count"] == 1
    audit_text = str(audit_store.list_envelopes()[0].to_dict())
    assert "PostgreSQL analytics" not in audit_text
    assert "PostgreSQL" not in audit_text


def test_tool_policy_blocks_unregistered_or_disallowed_tool_without_raw_arguments() -> None:
    audit_store = InMemoryAuditStore()
    registry = ToolRegistry()
    registry.register(FunctionTool(name="math.add", handler=lambda args: {"sum": 0}))
    executor = ToolExecutor(
        registry=registry,
        audit_store=audit_store,
        policy=ToolPolicy(allowed_tools={"math.add"}),
    )

    execution = executor.execute(
        ToolRequest(
            tool_name="file.write",
            arguments={"path": "secret.txt", "content": "do not leak"},
            actor_id="user",
            agent_id="assistant",
            session_id="s1",
        )
    )

    assert execution.result.status == "blocked"
    audit = audit_store.list_envelopes()[0]
    assert audit.audit_type == "tool_call"
    assert audit.decision == "block"
    assert audit.payload["error_type"] == "ToolPolicyError"
    assert "do not leak" not in str(audit.to_dict())


def test_audit_dashboard_renders_counts_and_excludes_raw_sensitive_payload() -> None:
    audit_store = InMemoryAuditStore()
    registry = ToolRegistry()
    registry.register(FunctionTool(name="secret.echo", handler=lambda args: {"secret": "raw"}))
    ToolExecutor(registry=registry, audit_store=audit_store).execute(
        ToolRequest(
            tool_name="secret.echo",
            arguments={"secret": "raw prompt"},
            actor_id="user",
            agent_id="assistant",
            session_id="s1",
        )
    )

    html = generate_audit_dashboard_html(audit_store.list_envelopes())

    assert "<!doctype html>" in html.casefold()
    assert "tool_call" in html
    assert "raw prompt" not in html
    assert '"secret": "raw"' not in html


def _memory_record(
    memory_id: str,
    content: str,
    *,
    tenant_id: str = "tenant-a",
    user_id: str | None = "user-a",
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        memory_type="belief",
        scope="private",
        layer="core",
        session_id="s1",
        subject_id="user-a",
        content=content,
        source_event_ids=("event-1",),
        rule_id="test",
        owner_id="assistant",
        visible_to=("assistant",),
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id="assistant",
        created_at="2026-07-29T00:00:00+00:00",
        updated_at="2026-07-29T00:00:00+00:00",
    )
