from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from typer.testing import CliRunner

import agent_memory_runtime.cli.app as cli_app
import agent_memory_runtime.cli.chat as cli_chat
from agent_memory_runtime.agent import AgentRunEvent
from agent_memory_runtime.config import LLMConfig
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.llm import LLMResponse
from agent_memory_runtime.memory.stores import SQLiteStoreBundle
from agent_memory_runtime.runtime import AgentMemoryRuntime


def test_cli_ingest_retrieve_project_and_replay(tmp_path) -> None:
    runner = CliRunner()
    data_dir = tmp_path / ".amem"
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event_id": "evt-cli-1",
                "kind": "message.created",
                "actor_id": "customer",
                "session_id": "cli-s1",
                "labels": ["private"],
                "tags": ["refund"],
                "payload": {
                    "agent_id": "support_agent",
                    "subject_id": "order",
                    "text": "Refund status needs gateway confirmation.",
                    "salience": 0.8,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert runner.invoke(cli_app.app, ["init", "--path", str(data_dir)]).exit_code == 0
    assert (data_dir / "runtime.sqlite").exists()
    assert (
        runner.invoke(cli_app.app, ["ingest", str(events), "--data-dir", str(data_dir)]).exit_code
        == 0
    )

    retrieve = runner.invoke(
        cli_app.app,
        [
            "retrieve",
            "--agent",
            "support_agent",
            "--query",
            "refund status",
            "--session",
            "cli-s1",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert retrieve.exit_code == 0
    assert "episodic:cli-s1:evt-cli-1" in retrieve.output
    assert "score_breakdown" in retrieve.output

    project = runner.invoke(
        cli_app.app,
        [
            "project",
            "--agent",
            "support_agent",
            "--query",
            "refund status",
            "--session",
            "cli-s1",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert project.exit_code == 0
    assert "projected_context" not in project.output
    assert "last_event_sequence" in project.output

    replay = runner.invoke(cli_app.app, ["replay", "--data-dir", str(data_dir)])
    assert replay.exit_code == 0
    assert "state_hash" in replay.output

    audit = runner.invoke(cli_app.app, ["audit", "--data-dir", str(data_dir)])
    assert audit.exit_code == 0
    assert "llm_call_traces" in audit.output
    access_audit = runner.invoke(
        cli_app.app,
        ["audit", "--type", "access", "--data-dir", str(data_dir)],
    )
    assert access_audit.exit_code == 0
    assert '"audit_type": "access"' in access_audit.output


def test_cli_cross_session_profile_retention_worker_and_eval_gate(tmp_path) -> None:
    runner = CliRunner()
    data_dir = tmp_path / ".amem"
    events = tmp_path / "profile-events.jsonl"
    cases = tmp_path / "failing-eval.yml"
    events.write_text(
        json.dumps(
            {
                "event_id": "profile-cli-1",
                "kind": "preference.updated",
                "actor_id": "user-1",
                "session_id": "old-session",
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "agent_id": "assistant",
                "labels": ["private"],
                "payload": {
                    "agent_id": "assistant",
                    "subject_id": "user-1",
                    "key": "response_style",
                    "preference": "Keep answers concise.",
                    "value": "concise",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cases.write_text(
        "cases:\n"
        "  - id: expected-failure\n"
        "    agent: assistant\n"
        "    query: missing\n"
        "    expected_memory_ids: [missing-memory]\n",
        encoding="utf-8",
    )
    runner.invoke(cli_app.app, ["--no-banner", "init", "--path", str(data_dir)])
    ingest = runner.invoke(
        cli_app.app,
        ["--no-banner", "ingest", str(events), "--data-dir", str(data_dir)],
    )
    assert ingest.exit_code == 0

    retrieve = runner.invoke(
        cli_app.app,
        [
            "--no-banner",
            "retrieve",
            "--agent",
            "assistant",
            "--query",
            "my response style",
            "--session",
            "new-session",
            "--tenant",
            "tenant-1",
            "--user",
            "user-1",
            "--session-policy",
            "profile",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert retrieve.exit_code == 0
    assert "v3:belief:tenant-1:user-1:assistant:response_style" in retrieve.output

    retention = runner.invoke(
        cli_app.app,
        ["--no-banner", "retention", "worker", "--data-dir", str(data_dir)],
    )
    assert retention.exit_code == 0
    assert '"planned_actions"' in retention.output

    evaluation = runner.invoke(
        cli_app.app,
        ["--no-banner", "eval", str(cases), "--data-dir", str(data_dir)],
    )
    assert evaluation.exit_code == 1
    assert '"failed": 1' in evaluation.output


def test_cli_async_ingest_and_queue_run_once(tmp_path) -> None:
    runner = CliRunner()
    data_dir = tmp_path / ".amem"
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event_id": "evt-async-1",
                "kind": "message.created",
                "actor_id": "customer",
                "session_id": "cli-s1",
                "labels": ["private"],
                "tags": ["refund"],
                "payload": {
                    "agent_id": "support_agent",
                    "subject_id": "order",
                    "text": "Async refund status memory.",
                    "salience": 0.8,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert runner.invoke(cli_app.app, ["init", "--path", str(data_dir)]).exit_code == 0
    assert (data_dir / "runtime.sqlite").exists()
    ingest = runner.invoke(
        cli_app.app,
        ["ingest", str(events), "--async-derive", "--data-dir", str(data_dir)],
    )

    assert ingest.exit_code == 0
    assert "pending_derivation_jobs=1" in ingest.output
    assert SQLiteStoreBundle(data_dir / "runtime.sqlite").memory_store.list_records() == []

    status = runner.invoke(cli_app.app, ["queue", "--data-dir", str(data_dir)])
    assert status.exit_code == 0
    assert '"status": "pending"' in status.output

    run_once = runner.invoke(cli_app.app, ["queue", "run-once", "--data-dir", str(data_dir)])
    assert run_once.exit_code == 0
    assert '"status": "succeeded"' in run_once.output
    assert (
        SQLiteStoreBundle(data_dir / "runtime.sqlite").memory_store.get(
            "episodic:cli-s1:evt-async-1"
        )
        is not None
    )


def test_cli_worker_and_audit_dashboard(tmp_path) -> None:
    runner = CliRunner()
    data_dir = tmp_path / ".amem"
    dashboard = tmp_path / "audit.html"
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event_id": "evt-worker-1",
                "kind": "message.created",
                "actor_id": "customer",
                "session_id": "cli-s1",
                "labels": ["private"],
                "payload": {
                    "agent_id": "support_agent",
                    "subject_id": "order",
                    "text": "Worker generated memory.",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    runner.invoke(cli_app.app, ["init", "--path", str(data_dir)])
    runner.invoke(
        cli_app.app,
        ["ingest", str(events), "--async-derive", "--data-dir", str(data_dir)],
    )
    worker = runner.invoke(cli_app.app, ["worker", "--data-dir", str(data_dir)])

    assert worker.exit_code == 0
    assert '"processed": 1' in worker.output
    assert '"succeeded": 1' in worker.output

    dashboard_result = runner.invoke(
        cli_app.app,
        ["audit-dashboard", "--out", str(dashboard), "--data-dir", str(data_dir)],
    )

    assert dashboard_result.exit_code == 0
    html = dashboard.read_text(encoding="utf-8")
    assert "Agent Memory Runtime 审计面板" in html
    assert "governance_job" in html


def test_cli_embedding_status_reports_sqlite_vec_lexical_only_mode(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AMEM_EMBEDDING_MODEL", "")
    data_dir = tmp_path / ".amem"
    runner = CliRunner()

    assert (
        runner.invoke(
            cli_app.app,
            ["--no-banner", "init", "--path", str(data_dir)],
        ).exit_code
        == 0
    )
    status = runner.invoke(
        cli_app.app,
        ["--no-banner", "embedding", "status", "--data-dir", str(data_dir)],
    )

    assert status.exit_code == 0
    assert '"sqlite_vec_loaded": true' in status.output
    assert '"semantic_available": false' in status.output


def test_cli_respond_uses_injected_llm_client(monkeypatch, tmp_path) -> None:
    runtime = AgentMemoryRuntime(llm_client=_FakeChatClient())
    runtime.ingest(
        Event(
            event_id="evt-response-1",
            kind="message.created",
            actor_id="customer",
            session_id="cli-s1",
            labels=("private",),
            payload={
                "agent_id": "support_agent",
                "subject_id": "order",
                "text": "Refund requires gateway confirmation.",
            },
        )
    )
    received_configs = []

    def fake_runtime(_data_dir, *, config=None):
        received_configs.append(config)
        return runtime

    monkeypatch.setattr(cli_app, "_runtime", fake_runtime)

    response = CliRunner().invoke(
        cli_app.app,
        [
            "respond",
            "--agent",
            "support_agent",
            "--query",
            "What should I do?",
            "--session",
            "cli-s1",
            "--provider",
            "kimi",
            "--data-dir",
            str(tmp_path / ".amem"),
        ],
    )

    assert response.exit_code == 0
    assert "Gateway confirmation is pending." in response.output
    assert "selected_memory_ids" in response.output
    assert "test-model" in response.output
    assert received_configs[-1].llm.provider == "kimi"
    assert received_configs[-1].llm.model == "kimi-k2.6"


def test_cli_lists_supported_openai_compatible_providers() -> None:
    result = CliRunner().invoke(cli_app.app, ["providers"])

    assert result.exit_code == 0
    assert result.output.count("Agent Memory Runtime") == 1
    assert "Event-sourced memory for stateful agents." in result.output
    assert "Safe context. Deterministic replay." in result.output
    for provider in ("deepseek", "openai", "gemini", "qwen", "zai", "kimi", "custom"):
        assert provider in result.output


def test_cli_chat_jsonl_mode_is_banner_free_and_machine_readable(
    monkeypatch,
    tmp_path,
) -> None:
    runtime = _FakeBusinessRuntime()
    context = _fake_chat_context(runtime)
    monkeypatch.setattr(cli_chat, "build_chat_context", lambda data_dir, config: context)

    result = CliRunner().invoke(
        cli_app.app,
        [
            "chat",
            "--prompt",
            "hello",
            "--mode",
            "jsonl",
            "--no-remember",
            "--session",
            "session-jsonl",
        ],
    )

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert [line["type"] for line in lines] == [
        "model.output.delta",
        "run.completed",
    ]
    assert "Agent Memory Runtime" not in result.output
    assert runtime.requests[0].session_id == "session-jsonl"


def test_cli_chat_interactive_commands_switch_session_and_show_status(
    monkeypatch,
    tmp_path,
) -> None:
    runtime = _FakeBusinessRuntime()
    context = _fake_chat_context(runtime)
    monkeypatch.setattr(cli_chat, "build_chat_context", lambda data_dir, config: context)

    result = CliRunner().invoke(
        cli_app.app,
        [
            "--no-banner",
            "chat",
            "--no-remember",
            "--session",
            "session-one",
        ],
        input="/status\n/new session-two\nhello\n/exit\n",
    )

    assert result.exit_code == 0
    assert "provider" in result.output
    assert "session=session-two" in result.output
    assert "done" in result.output
    assert "[completed]" in result.output
    assert runtime.requests[0].session_id == "session-two"


def test_cli_chat_interactively_decides_child_tool_approval(
    monkeypatch,
    tmp_path,
) -> None:
    runtime = _FakeApprovalRuntime()
    context = _fake_chat_context(runtime)
    monkeypatch.setattr(cli_chat, "build_chat_context", lambda data_dir, config: context)

    result = CliRunner().invoke(
        cli_app.app,
        ["--no-banner", "chat", "--no-remember", "--session", "approval-session"],
        input="save it\ny\n/exit\n",
    )

    assert result.exit_code == 0
    assert "approval required" in result.output
    assert "saved" in result.output
    assert runtime.approvals == [("approval-1", True)]
    assert runtime.resumed == ["run-approval"]


def test_cli_chat_remembers_completed_turn_atomically(monkeypatch, tmp_path) -> None:
    runtime = _FakeBusinessRuntime()
    bundle = SQLiteStoreBundle(tmp_path / "chat.sqlite")
    memory_runtime = AgentMemoryRuntime(
        event_store=bundle.event_store,
        memory_store=bundle.memory_store,
        snapshot_store=bundle.snapshot_store,
        audit_store=bundle.audit_store,
        derivation_queue=bundle.derivation_queue,
        transaction_manager=bundle,
    )
    context = SimpleNamespace(
        config=LLMConfig.for_provider("deepseek", model="test-model"),
        runtime=runtime,
        bundle=bundle,
        memory_runtime=memory_runtime,
        reconfigure=lambda new_config: None,
    )
    monkeypatch.setattr(cli_chat, "build_chat_context", lambda data_dir, config: context)

    result = CliRunner().invoke(
        cli_app.app,
        [
            "--no-banner",
            "chat",
            "--prompt",
            "remember this",
            "--mode",
            "text",
            "--session",
            "remember-session",
        ],
    )

    assert result.exit_code == 0
    events = bundle.event_store.list_events()
    assert [event.actor_id for event in events] == ["user", "assistant"]
    assert all("cli-chat" in event.tags for event in events)
    assert events[1].caused_by_event_id == events[0].event_id


class _FakeChatClient:
    def complete(self, *, system_prompt: str, user_prompt: str) -> LLMResponse:
        return LLMResponse(content="Gateway confirmation is pending.", model="test-model")


class _FakeBusinessRuntime:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def run(self, request: Any) -> Any:
        self.requests.append(request)
        yield AgentRunEvent(
            type="model.output.delta",
            run_id="run-chat",
            sequence=1,
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            data={"delta": "done"},
        )
        yield AgentRunEvent(
            type="run.completed",
            run_id="run-chat",
            sequence=2,
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            data={"output": "done", "input_tokens": 3, "output_tokens": 1},
        )


class _FakeApprovalRuntime:
    def __init__(self) -> None:
        self.approvals: list[tuple[str, bool]] = []
        self.resumed: list[str] = []

    async def run(self, request: Any) -> Any:
        yield AgentRunEvent(
            type="approval.required",
            run_id="run-approval",
            sequence=1,
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            data={
                "approval_id": "approval-1",
                "call_id": "call-1",
                "tool_name": "record.write",
            },
        )

    async def decide_approval(self, approval_id: str, **values: Any) -> None:
        self.approvals.append((approval_id, bool(values["approved"])))

    async def resume(self, run_id: str, **identity: Any) -> Any:
        self.resumed.append(run_id)
        yield AgentRunEvent(
            type="model.output.delta",
            run_id=run_id,
            sequence=2,
            tenant_id=str(identity["tenant_id"]),
            agent_id="assistant",
            session_id="approval-session",
            data={"delta": "saved"},
        )
        yield AgentRunEvent(
            type="run.completed",
            run_id=run_id,
            sequence=3,
            tenant_id=str(identity["tenant_id"]),
            agent_id="assistant",
            session_id="approval-session",
            data={"output": "saved", "input_tokens": 4, "output_tokens": 1},
        )


def _fake_chat_context(runtime: Any) -> Any:
    config = LLMConfig.for_provider("deepseek", model="test-model")
    return SimpleNamespace(
        config=config,
        runtime=runtime,
        reconfigure=lambda new_config: None,
    )
