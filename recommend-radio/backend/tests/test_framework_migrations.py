from __future__ import annotations

import asyncio
import sqlite3

import pytest


def test_embedding_requests_are_bounded_and_preserve_order(monkeypatch):
    from content_embeddings import ContentEmbeddingService
    from types import SimpleNamespace
    import content_embeddings
    batches = []
    def post(url, *, json, **kwargs):
        batches.append(json['input'])
        return SimpleNamespace(raise_for_status=lambda:None,
            json=lambda:{'data':[{'index':i,'embedding':[float(text),1.0]}
                                  for i,text in enumerate(json['input'])]})
    monkeypatch.setattr(content_embeddings.requests,'post',post)
    monkeypatch.setenv('RECOMMEND_EMBEDDING_BATCH_SIZE','8')
    service=ContentEmbeddingService.__new__(ContentEmbeddingService)
    service.text_base_url='http://embedding/v1'
    service.text_model='test'
    service.timeout_seconds=1
    vectors=service._embed_texts([str(i) for i in range(19)])
    assert [len(batch) for batch in batches]==[8,8,3]
    assert len(vectors)==19


def test_profile_projector_drain_precedes_channel_close(monkeypatch):
    import amem_grpc_bridge
    from amem_grpc_bridge import GrpcProfileProjector
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Timer
    from types import SimpleNamespace
    from music_profile import MusicProfile
    started, release, finished = Event(),Event(),Event()
    def refresh(**kwargs):
        started.set()
        release.wait(2)
        finished.set()
        return MusicProfile.empty()
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(amem_grpc_bridge,'_PROFILE_REFRESH_EXECUTOR',executor)
        projector=GrpcProfileProjector(SimpleNamespace(get_music_profile=refresh,timeout_seconds=2))
        projector.project(user_id='u',scene='home',fallback_profile=MusicProfile.empty())
        assert started.wait(1)
        timer=Timer(0.03,release.set)
        timer.start()
        projector.close()
        timer.join()
        assert finished.is_set()
from database import init_db
from schema_migrations import HEAD, current_revision, ensure_database


def test_existing_v25_data_is_adopted_by_alembic(tmp_path):
    from legacy_schema_v25 import upgrade_legacy_v25

    path = (tmp_path / "radio.sqlite3").resolve()
    upgrade_legacy_v25(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO settings(user_id,key,value,updated_at) VALUES "
            "('legacy-owner','migration-proof','preserved','now')"
        )
    ensure_database(path, migrate=True)
    assert current_revision(path) == HEAD
    ensure_database(path, migrate=True)
    with sqlite3.connect(path) as conn:
        assert (
            conn.execute("SELECT value FROM settings WHERE key='migration-proof'").fetchone()[0]
            == "preserved"
        )
        assert {"trace_context", "agent_run_id"} <= {
            r[1] for r in conn.execute("PRAGMA table_info(durable_jobs)")
        }


def test_worker_refuses_unmigrated_database(tmp_path):
    with pytest.raises(RuntimeError, match="migrate"):
        ensure_database(tmp_path / "missing.sqlite3", migrate=False)


def test_legacy_orphan_history_is_preserved_with_audited_parent_recovery(tmp_path):
    from legacy_schema_v25 import upgrade_legacy_v25

    path = (tmp_path / "legacy.sqlite3").resolve()
    upgrade_legacy_v25(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO agent_dialogue_checkpoints "
            "(checkpoint_id,session_id,user_id,reason,snapshot_json,created_at) "
            "VALUES ('cp','missing','legacy-owner','old','{}','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO agent_dialogue_turns(session_id,role,content,created_at) "
            "VALUES ('missing','user','preserve-history','2026-01-01')"
        )
        assert len(conn.execute("PRAGMA foreign_key_check").fetchall()) == 2
    ensure_database(path, migrate=True)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            conn.execute("SELECT content FROM agent_dialogue_turns").fetchone()[0]
            == "preserve-history"
        )
        assert conn.execute("SELECT COUNT(*) FROM migration_repairs").fetchone()[0] == 1


def test_w3c_context_survives_transactional_outbox(tmp_path):
    import json

    from database import get_connection
    from durable_jobs import enqueue

    from agent_memory_runtime.telemetry import configure, span, trace_id

    path = tmp_path / "traced.sqlite3"
    init_db(path)
    configure("test-tracing")
    with span("request"):
        original = trace_id()
        with get_connection(path) as conn:
            enqueue(conn, kind="probe", user_id="user", payload={}, job_id="job")
    with get_connection(path) as conn:
        parent = json.loads(conn.execute("SELECT trace_context FROM durable_jobs").fetchone()[0])
    with span("worker", parent=parent):
        assert trace_id() == original and original


def test_session_analysis_runs_after_business_transaction_commits(tmp_path):
    from types import SimpleNamespace

    from dialogue_service import MusicDialogueService

    path = tmp_path / "session.sqlite3"
    init_db(path)

    def analysis():
        # Acquiring the writer here would fail if create_session still held its transaction.
        connection = sqlite3.connect(path, timeout=0.05)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.commit()
        finally:
            connection.close()
        return {"verified": True}

    service = MusicDialogueService(db_path=path, recommendation_service=SimpleNamespace())
    service._safe_analysis = analysis
    value = service.create_session()
    assert value["analysis"] == {"verified": True}
    assert "_deferred_analysis" not in value


def test_langgraph_approval_resume_and_completed_replay(tmp_path):
    from agent_memory_runtime.agent.models import AgentRequest, ModelResponse, ModelToolCall
    from agent_memory_runtime.agent.runtime import BusinessAgentRuntime
    from agent_memory_runtime.agent.stores.sqlite import SQLiteAgentStateStore
    from agent_memory_runtime.agent.tool_runtime import AgentFunctionTool
    from agent_memory_runtime.tools.registry import ToolRegistry

    calls = []

    class Gateway:
        async def complete(self, *, messages, **_kwargs):
            if not any(m.role == "tool" for m in messages):
                return ModelResponse(
                    content="", tool_calls=(ModelToolCall("approved-call", "write", {}),)
                )
            return ModelResponse(content="done")

    registry = ToolRegistry()
    registry.register(
        AgentFunctionTool(
            "write",
            lambda a, c: calls.append(c.call_id) or {"ok": True},
            side_effects=True,
            idempotent=False,
        )
    )
    store = SQLiteAgentStateStore(tmp_path / "agent.sqlite3")

    async def scenario():
        runtime = BusinessAgentRuntime(
            model_gateway=Gateway(), state_store=store, tool_registry=registry
        )
        events = [
            e
            async for e in runtime.run(
                AgentRequest("agent", "write", tenant_id="tenant", user_id="user")
            )
        ]
        approval = next(e for e in events if e.type == "approval.required")
        assert calls == []
        await runtime.decide_approval(
            approval.data["approval_id"], tenant_id="tenant", reviewer_id="user", approved=True
        )
        # New runtime instance simulates a new worker resuming persisted graph state.
        runtime = BusinessAgentRuntime(
            model_gateway=Gateway(), state_store=store, tool_registry=registry
        )
        events = [
            e async for e in runtime.resume(approval.run_id, tenant_id="tenant", user_id="user")
        ]
        assert events[-1].type == "run.completed"
        assert calls == ["approved-call"]
        again = [
            e async for e in runtime.resume(approval.run_id, tenant_id="tenant", user_id="user")
        ]
        assert again[-1].type == "run.completed"
        assert calls == ["approved-call"]

    asyncio.run(scenario())
    with sqlite3.connect(str(store.path) + ".langgraph") as conn:
        assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] > 0


def test_music_operation_uses_common_graph(tmp_path):
    from music_agent import music_operation

    path = tmp_path / "radio.sqlite3"
    init_db(path)

    class Service:
        db_path = path
        user_id = "legacy-owner"

        @music_operation("probe")
        def operation(self, value):
            return {"result": value + 1}

    assert Service().operation(4) == {"result": 5}
    with sqlite3.connect(tmp_path / "agent-runs.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 1


def test_langgraph_schedules_parallel_agents_and_dependency_join(tmp_path):
    from agent_memory_runtime.agent.models import ModelResponse
    from agent_memory_runtime.agent.orchestration.models import (
        AgentGraph,
        DelegatedTask,
        OrchestrationRequest,
    )
    from agent_memory_runtime.agent.orchestration.registry import (
        AgentDefinition,
        AgentDefinitionRegistry,
    )
    from agent_memory_runtime.agent.orchestration.runtime import AgentOrchestrator
    from agent_memory_runtime.agent.runtime import BusinessAgentRuntime
    from agent_memory_runtime.memory.stores import SQLiteStoreBundle

    bundle = SQLiteStoreBundle(tmp_path / "dag.sqlite3")
    registry = AgentDefinitionRegistry()
    seen = []

    class Gateway:
        async def complete(self, *, messages, **kwargs):
            text = messages[-1].content
            seen.append(text)
            await asyncio.sleep(0.02)
            return ModelResponse(content="completed:" + text[:20])

    registry.register(
        AgentDefinition(
            "worker",
            BusinessAgentRuntime(model_gateway=Gateway(), state_store=bundle.agent_state_store),
        )
    )
    runtime = AgentOrchestrator(registry=registry, state_store=bundle.orchestration_store)
    plan = AgentGraph(
        (
            DelegatedTask("a", "worker", "first"),
            DelegatedTask("b", "worker", "second"),
            DelegatedTask("c", "worker", "combine", depends_on=("a", "b")),
        )
    )

    async def scenario():
        events = [event async for event in runtime.run(OrchestrationRequest(plan))]
        assert events[-1].type == "orchestration.completed", [(e.type, e.data) for e in events]
        assert set(events[-1].data["outputs"]) == {"c"}

    asyncio.run(scenario())
    assert len(seen) == 3
    assert "combine" in seen[-1]
