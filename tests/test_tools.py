from __future__ import annotations

from pathlib import Path

from agent_memory_runtime.audit.dashboard import generate_audit_dashboard_html
from agent_memory_runtime.audit.stores import InMemoryAuditStore
from agent_memory_runtime.tools import (
    FileReadTool,
    FileWriteTool,
    FunctionTool,
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
