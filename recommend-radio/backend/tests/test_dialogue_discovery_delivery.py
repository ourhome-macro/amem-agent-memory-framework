from __future__ import annotations

from types import SimpleNamespace

from database import get_connection, init_db
from dialogue_repository import DialogueRepository
from dialogue_service import MusicDialogueService
from dialogue_task_service import DialogueTaskService


def test_watcher_publishes_completed_recommendation_card(monkeypatch):
    monkeypatch.setattr("dialogue_task_service.DISCOVERY_POLL_SECONDS", 0)
    card = {
        "cardId": "card-1",
        "kind": "recommendation_carousel",
        "discoveryJobId": "discovery-1",
        "discoveryStatus": "queued",
        "recommendations": [],
    }
    initial = {"cards": [card]}
    complete = {"cards": [{**card, "discoveryStatus": "completed", "recommendations": [{"track": {"trackId": "song-1"}}]}]}
    events = []
    publisher = SimpleNamespace(publish=lambda **event: events.append(event))
    service = SimpleNamespace(refresh_recommendation_card=lambda _card_id: complete)

    DialogueTaskService(publisher)._watch_discovery(
        service, "legacy-owner", "dialogue-1", "session-1", initial
    )

    assert [event["event_type"] for event in events] == ["session", "discovery"]
    assert events[0]["payload"]["session"]["cards"][0]["recommendations"]
    assert events[1]["status"] == "completed"


def test_reconciliation_required_marks_card_failed(tmp_path):
    db_path = tmp_path / "radio.sqlite3"
    init_db(db_path)
    repo = DialogueRepository(db_path, "legacy-owner")
    with get_connection(db_path) as conn:
        session = repo._create_session(conn)
        card = repo._insert_card(
            conn,
            session["session_id"],
            kind="recommendation_carousel",
            title="测试推荐",
            prompt="正在找歌",
            statement="",
            topic="测试",
            polarity="neutral",
            source_text="测试",
            payload={"discoveryJobId": "discovery-1", "recommendations": []},
        )
    recommendations = SimpleNamespace(
        discovery_status=lambda _job_id: {
            "available": True,
            "status": "needs_reconciliation",
            "error": "worker lost",
        },
        music_profile_analysis=lambda **_kwargs: {},
    )
    service = MusicDialogueService(
        db_path=db_path, recommendation_service=recommendations
    )
    result = service.refresh_recommendation_card(card["card_id"])
    assert result["cards"][0]["discoveryStatus"] == "failed"
    assert result["cards"][0]["error"] == "worker lost"
