from __future__ import annotations

from conversation_memory import ConversationMemoryService
from database import get_connection, init_db
from experiments import ExperimentAssignments


def _insert_session(db_path, session_id: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO agent_dialogue_sessions (
                session_id, user_id, state, focus, created_at, updated_at
            ) VALUES (?, 'legacy-owner', 'active', 'test',
                      '2026-09-11T00:00:00+00:00', '2026-09-11T00:00:00+00:00')
            """,
            (session_id,),
        )
        conn.execute(
            """
            INSERT INTO agent_dialogue_turns (session_id, role, content, created_at)
            VALUES (?, 'user', '想听舒缓的英文歌', '2026-09-11T00:00:00+00:00')
            """,
            (session_id,),
        )


def test_warm_memories_keep_types_separate_across_scene_scope(tmp_path) -> None:
    db_path = tmp_path / "radio.sqlite3"
    init_db(db_path)
    _insert_session(db_path, "session-1")
    service = ConversationMemoryService(str(db_path), user_id="legacy-owner")
    service.refresh_warm(
        session_id="session-1",
        topic="英文歌",
        memory_type="request_summary",
        scope_type="scene",
        scope_key="music_conversation",
    )
    service.refresh_warm(
        session_id="session-1",
        topic="放松",
        memory_type="emotion_state",
        scope_type="scene",
        scope_key="music_conversation",
    )

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT memory_type, memory_key, status
            FROM conversation_warm_memories ORDER BY memory_type
            """
        ).fetchall()
    assert [(row["memory_type"], row["status"]) for row in rows] == [
        ("emotion_state", "active"),
        ("request_summary", "active"),
    ]


def test_experiment_assignment_is_stable(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "radio.sqlite3"
    init_db(db_path)
    monkeypatch.setenv("RECOMMEND_MEMORY_AB_ENABLED", "true")
    first = ExperimentAssignments(str(db_path), user_id="legacy-owner").memory_variant()
    second = ExperimentAssignments(str(db_path), user_id="legacy-owner").memory_variant()
    assert first in {"control", "treatment"}
    assert second == first
