from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from database import get_connection, init_db
from music_agent import OperationStage, _execute


def test_single_line_tool_result_obeys_token_budget_without_changing_journal_value():
    from agent_memory_runtime.agent.context_window import compact_tool_output_for_model
    from agent_memory_runtime.tokens import AdaptiveTokenEstimator

    estimator = AdaptiveTokenEstimator()
    output = {"large": "内容" * 100000, "nested": [{"value": "x" * 10000}] * 20}
    preview = compact_tool_output_for_model(
        output, estimator=estimator, model=None, max_tokens=800, head_lines=40, tail_lines=20
    )
    assert (
        estimator.count_text(
            json.dumps(preview, ensure_ascii=False, sort_keys=True, indent=2), model=None
        )
        <= 800
    )
    assert len(output["large"]) == 200000
    assert preview["raw_output_hash"]


def test_step_journal_resumes_after_process_exit_without_replaying_committed_stage(tmp_path):
    path = tmp_path / "radio.sqlite3"
    init_db(path)
    script = r"""
import asyncio, os, sys
from pathlib import Path
from types import SimpleNamespace
from music_agent import OperationStage, _execute
from agent_memory_runtime.agent.runtime import BusinessAgentRuntime
path=Path(sys.argv[1])
original=BusinessAgentRuntime._publish
async def publish(self, event):
    result=await original(self, event)
    if event.type=='tool.completed': os._exit(91)
    return result
BusinessAgentRuntime._publish=publish
asyncio.run(_execute(SimpleNamespace(db_path=path,user_id='legacy-owner'),
    'recovery', {'input':1}, 'stable-request', None, None,
    stages=(OperationStage('first', lambda _: {'large':'x'*200000}),
            OperationStage('second', lambda value: {'size':len(value['large'])}))))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[1]), *sys.path])
    child = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert child.returncode == 91, child.stderr
    # Simulate lease expiry after worker death, without waiting a production lease interval.
    with sqlite3.connect(tmp_path / "agent-runs.sqlite3") as conn:
        run_id, payload = conn.execute("SELECT run_id,payload FROM agent_runs").fetchone()
        run = json.loads(payload)
        run["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
        conn.execute("UPDATE agent_runs SET payload=? WHERE run_id=?", (json.dumps(run), run_id))
    calls = []

    def first(_):
        pytest.fail("committed side effect was replayed")

    def second(value):
        calls.append("second")
        return {"size": len(value["large"])}

    service = SimpleNamespace(db_path=path, user_id="legacy-owner")
    stages = (OperationStage("first", first), OperationStage("second", second))
    result = asyncio.run(
        _execute(service, "recovery", {"input": 1}, "stable-request", None, None, stages=stages)
    )
    assert result == {"size": 200000}  # Full journal output, not model-context truncation.
    assert calls == ["second"]
    assert (
        asyncio.run(
            _execute(service, "recovery", {"input": 1}, "stable-request", None, None, stages=stages)
        )
        == result
    )
    assert calls == ["second"]


def test_unknown_stage_outcome_stops_workflow_and_requires_reconciliation(tmp_path):
    from job_errors import JobReconciliationRequired
    from durable_jobs import claim, enqueue, finish

    path = tmp_path / "radio.sqlite3"
    init_db(path)
    calls = []

    def unsafe(_):
        calls.append("effect")
        raise RuntimeError("unknown outcome")

    stages = (
        OperationStage("unsafe", unsafe),
        OperationStage("later", lambda _: calls.append("later") or {}),
    )
    service = SimpleNamespace(db_path=path, user_id="legacy-owner")
    for _ in range(2):
        with pytest.raises(JobReconciliationRequired):
            asyncio.run(_execute(service, "unsafe", {}, "same", None, None, stages=stages))
    assert calls == ["effect"]
    with get_connection(path) as conn:
        enqueue(
            conn, kind="discovery", user_id="legacy-owner", job_id="j", payload={}, retry_safe=True
        )
    with get_connection(path) as conn:
        job = claim(conn, "j")
    with get_connection(path) as conn:
        finish(conn, job, error=JobReconciliationRequired("inspect"))
    with get_connection(path) as conn:
        assert (
            conn.execute("SELECT status FROM durable_jobs").fetchone()[0] == "needs_reconciliation"
        )


def test_service_factory_closes_owned_resources_in_order_and_preserves_borrowed(monkeypatch):
    import amem_runtime
    from service_factory import MusicServices

    calls = []
    bridge = SimpleNamespace(close=lambda: calls.append("bridge"))
    projector = SimpleNamespace(close=lambda: calls.append("projector"))
    monkeypatch.setattr(amem_runtime, "build_amem_runtime", lambda: (bridge, projector))
    with MusicServices() as services:
        assert services.memory is services.memory
    assert calls == ["projector", "bridge"]
    with MusicServices(amem_runtime=(bridge, projector)) as services:
        assert services.memory == (bridge, projector)
    assert calls == ["projector", "bridge"]


def test_model_transport_reuses_clients_and_separates_credentials(monkeypatch):
    import openai
    from agent_memory_runtime.llm import transport

    created = []

    def client(**kwargs):
        value = SimpleNamespace(close=lambda: None)
        created.append(value)
        return value

    monkeypatch.setattr(transport, "_clients", {})
    monkeypatch.setattr(openai, "OpenAI", client)
    monkeypatch.setenv("TEST_MODEL_KEY", "a")
    args = dict(
        base_url="https://example.invalid/v1", api_key_env="TEST_MODEL_KEY", timeout_seconds=8
    )
    assert transport.get_openai_client(**args) is transport.get_openai_client(**args)
    monkeypatch.setenv("TEST_MODEL_KEY", "b")
    assert transport.get_openai_client(**args) is not created[0]
    assert len(created) == 2
    transport.close_clients()


def test_trace_batches_projection_writes_and_flushes_on_error(tmp_path, monkeypatch):
    import full_trace

    path = tmp_path / "radio.sqlite3"
    init_db(path)
    trace = full_trace.FullTrace(str(path), trace_type="test", user_id="legacy-owner")
    opened = []
    original = full_trace.get_connection

    def connection(*args, **kwargs):
        opened.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(full_trace, "get_connection", connection)
    with pytest.raises(ValueError):
        with trace:
            for i in range(20):
                trace.record_span("step", 1, inputs={"cookie": "secret"})
                trace.event("step.done", {"index": i})
            assert opened == []
            raise ValueError("stop")
    assert len(opened) == 2  # One batch transaction, one final status update.
    with get_connection(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evaluation_trace_events").fetchone()[0] == 20
        assert conn.execute("SELECT COUNT(*) FROM evaluation_trace_spans").fetchone()[0] == 20
        assert conn.execute("SELECT status FROM evaluation_traces").fetchone()[0] == "failed"
        assert (
            "secret"
            not in conn.execute("SELECT input_json FROM evaluation_trace_spans LIMIT 1").fetchone()[
                0
            ]
        )


def test_dialogue_repository_keeps_user_boundary_and_undo(tmp_path):
    from dialogue_service import MusicDialogueService

    path = tmp_path / "radio.sqlite3"
    service = MusicDialogueService(db_path=path, recommendation_service=SimpleNamespace())
    session = service.create_session()
    repo = service.repository
    with get_connection(path) as conn:
        repo._save_checkpoint(conn, session["sessionId"], reason="test")
        repo._insert_turn(conn, session["sessionId"], "user", "temporary")
    result = service.undo_last_message(session_id=session["sessionId"])
    assert not any(m["content"] == "temporary" for m in result["messages"])
    from dialogue_repository import DialogueRepository

    with get_connection(path) as conn:
        assert (
            DialogueRepository(path, "other-user")._load_session(conn, session["sessionId"]) is None
        )
