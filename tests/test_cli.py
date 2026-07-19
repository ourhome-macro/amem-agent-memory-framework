from __future__ import annotations

import json

from typer.testing import CliRunner

import agent_memory_runtime.cli.app as cli_app
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.llm import LLMResponse
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
    assert (data_dir / "audit.jsonl").exists()
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
    assert (data_dir / "derivation_queue.jsonl").exists()
    ingest = runner.invoke(
        cli_app.app,
        ["ingest", str(events), "--async-derive", "--data-dir", str(data_dir)],
    )

    assert ingest.exit_code == 0
    assert "pending_derivation_jobs=1" in ingest.output
    assert json.loads((data_dir / "memories.jsonl").read_text(encoding="utf-8") or "[]") == []

    status = runner.invoke(cli_app.app, ["queue", "--data-dir", str(data_dir)])
    assert status.exit_code == 0
    assert '"status": "pending"' in status.output

    run_once = runner.invoke(cli_app.app, ["queue", "run-once", "--data-dir", str(data_dir)])
    assert run_once.exit_code == 0
    assert '"status": "succeeded"' in run_once.output
    assert "episodic:cli-s1:evt-async-1" in (data_dir / "memories.jsonl").read_text(
        encoding="utf-8"
    )


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


class _FakeChatClient:
    def complete(self, *, system_prompt: str, user_prompt: str) -> LLMResponse:
        return LLMResponse(content="Gateway confirmation is pending.", model="test-model")
