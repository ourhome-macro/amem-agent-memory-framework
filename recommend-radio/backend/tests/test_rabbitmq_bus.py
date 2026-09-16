from __future__ import annotations

from types import SimpleNamespace

import pytest
from amem_bridge import AmemBridge
from rabbitmq_bus import (
    RabbitMQBehaviorBridge,
    RabbitMQSettings,
    declare_topology,
)


def _settings() -> RabbitMQSettings:
    return RabbitMQSettings(
        host="rabbitmq",
        port=5672,
        user="radio",
        password="secret",
        virtual_host="/",
        exchange="events",
        routing_key="behavior.music",
        queue="behavior",
        dead_letter_exchange="events.dlx",
        dead_letter_queue="behavior.dlq",
        delivery_limit=8,
        prefetch_count=16,
    )


@pytest.mark.parametrize("value", ["oops", "0", "65536"])
def test_invalid_broker_port_is_rejected(monkeypatch, value):
    monkeypatch.setenv("RABBITMQ_PORT", value)
    with pytest.raises(ValueError):
        RabbitMQSettings.from_env()


def test_host_validation_accepts_dns_names_but_not_whitespace():
    assert RabbitMQSettings(host='localhost').host == 'localhost'
    assert RabbitMQSettings(host='mq.service').host == 'mq.service'
    with pytest.raises(ValueError):
        RabbitMQSettings(host='bad host')


class _Channel:
    is_open = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.published: dict | None = None

    def exchange_declare(self, **kwargs) -> None:
        self.calls.append(("exchange", kwargs))

    def queue_declare(self, **kwargs) -> None:
        self.calls.append(("queue", kwargs))

    def queue_bind(self, **kwargs) -> None:
        self.calls.append(("bind", kwargs))

    def confirm_delivery(self) -> None:
        self.calls.append(("confirm", {}))

    def basic_publish(self, **kwargs) -> bool:
        self.published = kwargs
        return True


def test_topology_uses_quorum_queue_delivery_limit_and_dlq() -> None:
    channel = _Channel()
    declare_topology(channel, _settings())

    queue_calls = [kwargs for kind, kwargs in channel.calls if kind == "queue"]
    assert queue_calls[0]["arguments"] == {"x-queue-type": "quorum"}
    assert queue_calls[1]["arguments"] == {
        "x-queue-type": "quorum",
        "x-delivery-limit": 8,
        "x-dead-letter-exchange": "events.dlx",
        "x-dead-letter-routing-key": "behavior.dlq",
    }


def test_behavior_bridge_queues_writes_and_delegates_reads() -> None:
    publisher = SimpleNamespace(
        publish=lambda payload: "evt-2",
        health=lambda: True,
        close=lambda: None,
    )
    delegate = SimpleNamespace(project=lambda: "profile")
    bridge = RabbitMQBehaviorBridge(delegate, publisher=publisher)

    result = bridge.record_behavior({"event": "played"})

    assert result == {
        "enabled": True,
        "queued": True,
        "eventId": "evt-2",
        "memoryIds": [],
        "source": "rabbitmq",
    }
    assert bridge.project() == "profile"
    assert bridge.behavior_bus_health() is True


def test_legacy_drain_is_durable_and_deduplicated(tmp_path):
    from behavior_worker import ingest_legacy_message
    from database import get_connection, init_db

    path = tmp_path / "radio.sqlite3"
    init_db(path)
    body = b'{"event_id":"v1-id","userId":"owner","event":"liked"}'
    with get_connection(path) as conn:
        first = ingest_legacy_message(conn, body)
    with get_connection(path) as conn:
        assert ingest_legacy_message(conn, body) == first
    with get_connection(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM durable_jobs").fetchone()[0] == 1


def test_amem_redelivery_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMEM_EMBEDDING_MODEL", "")
    bridge = AmemBridge(str(tmp_path / "amem.sqlite3"))
    payload = {
        "event_id": "evt-redelivered",
        "occurred_at": "2026-09-15T00:00:00+00:00",
        "event": "liked",
        "userId": "user-1",
    }

    first = bridge.record_behavior(payload)
    second = bridge.record_behavior(payload)

    assert first["eventId"] == "evt-redelivered"
    assert second["eventId"] == "evt-redelivered"
    assert len(bridge.handle.runtime.event_store.list_events()) == 1
