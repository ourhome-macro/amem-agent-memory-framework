import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from amem_bridge import _normalize_event, _retrieval_route
from music_profile import MusicProfile, RelevantMemory
from profile_projector import ProfileProjector


def test_behavior_event_aliases_match_amem_memory_semantics():
    assert _normalize_event({"event": "play"}) == "played"
    assert _normalize_event({"event": "skip"}) == "skipped"
    assert _normalize_event({"event": "complete"}) == "completed"
    assert _normalize_event({"event": "like"}) == "liked"
    assert _normalize_event({"event": "recommendation.clicked"}) == "accepted"


def test_retrieval_route_is_scene_scoped(monkeypatch):
    monkeypatch.delenv("AMEM_RETRIEVAL_ROUTE_MUSIC_RECOMMENDATION", raising=False)
    monkeypatch.delenv("AMEM_RETRIEVAL_ROUTE_CONVERSATION", raising=False)
    assert _retrieval_route("music_recommendation") == "lexical_first"
    assert _retrieval_route("conversation") == "hybrid"
    monkeypatch.setenv("AMEM_RETRIEVAL_ROUTE_MUSIC_RECOMMENDATION", "hybrid")
    assert _retrieval_route("music_recommendation") == "hybrid"


def test_fallback_projection_builds_positive_and_negative_interest_texts():
    memories = [
        RelevantMemory(
            memory_id="mem-1",
            content="User explicitly states a music preference for topic: Mandopop.",
            layer="working",
            memory_type="preference",
            salience=0.9,
            confidence=0.9,
            metadata={"signal": "profile_statement_positive_topic", "topic": "Mandopop"},
        ),
        RelevantMemory(
            memory_id="mem-2",
            content="User explicitly states a negative music preference for topic: noisy EDM.",
            layer="working",
            memory_type="preference",
            salience=0.8,
            confidence=0.8,
            metadata={"signal": "profile_statement_negative_topic", "topic": "noisy EDM"},
        ),
    ]
    profile = ProfileProjector._fallback_with_memories(MusicProfile.empty(), memories)

    assert profile.positive_topics["Mandopop"] == 0.9
    assert profile.negative_topics["noisy EDM"] == 0.8
    assert any("Mandopop" in text for text in profile.positive_interest_texts)
    assert any("noisy EDM" in text for text in profile.negative_interest_texts)
