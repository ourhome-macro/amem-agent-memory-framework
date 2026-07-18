from __future__ import annotations

import json

import pytest

from agent_memory_runtime.access.write_guard import WriteGuard
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryCandidate
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.exceptions import WriteGuardError
from agent_memory_runtime.llm import LLMResponse
from agent_memory_runtime.memory.stores import (
    InMemoryAuditStore,
    JsonlAuditStore,
    SQLiteStoreBundle,
)
from agent_memory_runtime.runtime import AgentMemoryRuntime


def test_sensitive_payload_is_redacted_before_event_and_memory_persistence() -> None:
    card_number = "4111 1111 1111 1111"
    cvv = "123"
    runtime = AgentMemoryRuntime()

    result = runtime.ingest(
        Event(
            event_id="evt-sensitive-1",
            kind="message.created",
            actor_id="customer",
            session_id="sensitive-session",
            labels=("sensitive",),
            payload={
                "agent_id": "support_agent",
                "subject_id": "payment",
                "text": f"Customer supplied card {card_number} for verification.",
                "card_number": card_number,
                "verification": {"cvv": cvv},
            },
        )
    )

    persisted = json.dumps(
        [
            result.event.to_dict(),
            *(record.to_dict() for record in runtime.memory_store.list_records()),
        ],
        ensure_ascii=True,
    )

    assert card_number not in persisted
    assert cvv not in persisted
    assert "[redacted]" in persisted


def test_detected_card_number_is_upgraded_to_sensitive_and_redacted() -> None:
    card_number = "5555-5555-5555-4444"
    runtime = AgentMemoryRuntime()

    result = runtime.ingest(
        Event(
            event_id="evt-detected-sensitive-1",
            kind="message.created",
            actor_id="customer",
            session_id="detected-sensitive-session",
            labels=("public",),
            payload={
                "agent_id": "support_agent",
                "subject_id": "payment",
                "text": f"Use card {card_number} for the refund.",
            },
        )
    )

    persisted = json.dumps(result.event.to_dict(), ensure_ascii=True)
    assert "sensitive" in result.event.labels
    assert card_number not in persisted
    assert "[redacted]" in persisted


def test_sensitive_label_redacts_unknown_nested_payload_fields() -> None:
    secret_note = "Customer recovery phrase: cobalt-harbor-71"
    runtime = AgentMemoryRuntime()

    result = runtime.ingest(
        Event(
            event_id="evt-sensitive-unknown-fields",
            kind="message.created",
            actor_id="customer",
            session_id="sensitive-session",
            labels=("sensitive",),
            payload={
                "agent_id": "support_agent",
                "subject_id": "payment",
                "internal_note": secret_note,
                "nested": {"operator_note": secret_note},
                "pin_code": 123456,
            },
        )
    )

    persisted = json.dumps(result.event.to_dict(), ensure_ascii=True)
    assert secret_note not in persisted
    assert "123456" not in persisted
    assert result.event.payload["internal_note"] == "[redacted]"
    assert result.event.payload["nested"] == {"operator_note": "[redacted]"}
    assert result.event.payload["pin_code"] == "[redacted]"


def test_sensitive_memory_cannot_be_global() -> None:
    runtime = AgentMemoryRuntime()

    with pytest.raises(WriteGuardError, match="cannot use global scope"):
        runtime.ingest(
            Event(
                event_id="evt-sensitive-global",
                kind="message.created",
                actor_id="customer",
                session_id="sensitive-session",
                labels=("sensitive",),
                payload={
                    "agent_id": "support_agent",
                    "subject_id": "payment",
                    "scope": "global",
                    "text": "Sensitive payment detail.",
                },
            )
        )

    assert runtime.memory_store.list_records() == []


def test_shared_sensitive_memory_requires_explicit_visibility() -> None:
    candidate = MemoryCandidate(
        memory_id="sensitive-shared-without-visibility",
        memory_type="episodic",
        scope="shared",
        layer="working",
        session_id="sensitive-session",
        subject_id="payment",
        content="[redacted]",
        source_event_ids=("evt-sensitive-shared",),
        rule_id="test.rule",
        owner_id="support_agent",
        labels=("sensitive",),
    )

    with pytest.raises(WriteGuardError, match="requires visible_to"):
        WriteGuard().validate(candidate, source_event_exists=True)


def test_llm_audit_trace_records_provenance_without_prompt_or_response_content() -> None:
    audit_store = InMemoryAuditStore()
    runtime = AgentMemoryRuntime(llm_client=_SuccessfulChatClient(), audit_store=audit_store)
    runtime.ingest(_message_event())

    runtime.respond(
        MemoryQuery(agent_id="support_agent", text="What is the refund status?", session_id="s1")
    )

    traces = audit_store.list_traces()
    assert len(traces) == 1
    trace = traces[0]
    assert trace.outcome == "completed"
    assert trace.provider == "deepseek"
    assert trace.model == "test-model"
    assert trace.response_id == "response-1"
    assert trace.selected_memory_ids == ("episodic:s1:evt-1",)
    assert trace.input_tokens == 11
    assert trace.output_tokens == 7

    serialized = json.dumps(trace.to_dict(), ensure_ascii=True)
    assert "What is the refund status?" not in serialized
    assert "Refund gateway confirmation is pending." not in serialized
    assert "Customer asked about refund status." not in serialized
    assert trace.request_hash
    assert trace.response_hash


def test_failed_llm_call_is_audited_without_raw_exception_message() -> None:
    audit_store = InMemoryAuditStore()
    runtime = AgentMemoryRuntime(llm_client=_FailingChatClient(), audit_store=audit_store)
    runtime.ingest(_message_event())

    with pytest.raises(RuntimeError, match="provider secret"):
        runtime.respond(
            MemoryQuery(agent_id="support_agent", text="refund status", session_id="s1")
        )

    trace = audit_store.list_traces()[0]
    assert trace.outcome == "failed"
    assert trace.error_type == "RuntimeError"
    assert "provider secret" not in json.dumps(trace.to_dict(), ensure_ascii=True)


def test_jsonl_audit_store_persists_only_llm_trace_metadata(tmp_path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    runtime = AgentMemoryRuntime(
        llm_client=_SuccessfulChatClient(),
        audit_store=JsonlAuditStore(audit_path),
    )
    runtime.ingest(_message_event())

    runtime.respond(
        MemoryQuery(agent_id="support_agent", text="What is the refund status?", session_id="s1")
    )

    persisted = audit_path.read_text(encoding="utf-8")
    assert "response-1" in persisted
    assert "What is the refund status?" not in persisted
    assert "Refund gateway confirmation is pending." not in persisted


def test_sqlite_ingest_rolls_back_event_memory_and_snapshot_when_validation_fails(tmp_path) -> None:
    stores = SQLiteStoreBundle(tmp_path / "runtime.sqlite")
    runtime = AgentMemoryRuntime(
        event_store=stores.event_store,
        memory_store=stores.memory_store,
        snapshot_store=stores.snapshot_store,
        transaction_manager=stores,
        write_guard=_RejectingWriteGuard(),
    )

    with pytest.raises(WriteGuardError, match="forced rejection"):
        runtime.ingest(_message_event())

    assert stores.event_store.list_events() == []
    assert stores.memory_store.list_records() == []
    assert stores.snapshot_store.latest() is None


def test_sqlite_ingest_commits_event_memory_and_snapshot_together(tmp_path) -> None:
    stores = SQLiteStoreBundle(tmp_path / "runtime.sqlite")
    runtime = AgentMemoryRuntime(
        event_store=stores.event_store,
        memory_store=stores.memory_store,
        snapshot_store=stores.snapshot_store,
        transaction_manager=stores,
    )

    runtime.ingest(_message_event())

    assert [event.event_id for event in stores.event_store.list_events()] == ["evt-1"]
    assert [record.memory_id for record in stores.memory_store.list_records()] == [
        "episodic:s1:evt-1"
    ]
    assert stores.snapshot_store.latest() is not None


def _message_event() -> Event:
    return Event(
        event_id="evt-1",
        kind="message.created",
        actor_id="customer",
        session_id="s1",
        labels=("private",),
        payload={
            "agent_id": "support_agent",
            "subject_id": "order",
            "text": "Customer asked about refund status.",
        },
    )


class _SuccessfulChatClient:
    def complete(self, *, system_prompt: str, user_prompt: str) -> LLMResponse:
        return LLMResponse(
            content="Refund gateway confirmation is pending.",
            model="test-model",
            response_id="response-1",
            input_tokens=11,
            output_tokens=7,
        )


class _FailingChatClient:
    def complete(self, *, system_prompt: str, user_prompt: str) -> LLMResponse:
        raise RuntimeError("provider secret must not be persisted")


class _RejectingWriteGuard(WriteGuard):
    def validate(
        self,
        candidate,
        *,
        source_event_exists: bool,
        current=None,
    ) -> None:
        raise WriteGuardError("forced rejection")
