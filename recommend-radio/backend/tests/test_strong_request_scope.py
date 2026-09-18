from __future__ import annotations

from candidate_pool import CandidatePool
from database import init_db
from dialogue_rules import _canonical_route, _route_message
from discovery_planner import DiscoveryPlanner
from models import Track
from music_profile import MusicProfile
from recommendation_contracts import RecommendationCandidate, UserProfile
from recommendation_engine import RecommendationEngine, RecommendationRequest
from request_spec import RequestInterpreter, RequestSpec


def test_dance_stage_request_rejects_personal_favorites_outside_the_scene(tmp_path):
    spec = RequestInterpreter().interpret("给我推荐一些唱跳舞台")
    assert spec.required_scenes == ("dance_stage",)
    assert spec.constrained is True
    assert RequestSpec.from_dict(spec.to_dict()).required_scenes == ("dance_stage",)

    plan = DiscoveryPlanner(search_budget=8).plan(
        profile=MusicProfile(positive_topics={"周杰伦": 1.0}),
        request_spec=spec,
        scene="conversation",
    )
    assert plan.search_queries
    assert all("舞台" in query or "现场" in query for query in plan.search_queries)
    assert all("周杰伦" not in query for query in plan.search_queries)

    db_path = tmp_path / "radio.sqlite3"
    init_db(db_path)
    pool = CandidatePool(str(db_path), user_id="legacy-owner")
    favorite = Track(bvid="BV1234567890", title="周杰伦 稻香 音乐", duration=180)
    stage = Track(bvid="BV1234567891", title="K-Pop 唱跳舞台 现场版", duration=180)
    pool.admit([favorite, stage], source="discovery_search", request_spec=RequestSpec(), query="音乐")
    ready = pool.list_ready(spec)
    assert [item.track.track_id for item in ready] == [stage.track_id]


def test_agent_route_preserves_strong_stage_context():
    message = "给我推荐一些唱跳舞台"
    route = _canonical_route(
        _route_message(message, None), message, source="rule", confidence=1.0
    )
    assert route.tool == "recommend_music"
    assert route.request_spec.required_scenes == ("dance_stage",)


def test_negated_dance_stage_is_not_required():
    spec = RequestInterpreter().interpret("不要唱跳舞台，来点安静的")
    assert spec.required_scenes == ()


def test_strong_scene_beats_high_scoring_personal_favorite():
    spec = RequestInterpreter().interpret("给我推荐一些唱跳舞台")
    favorite = RecommendationCandidate(
        track=Track(bvid="BV1234567890", title="周杰伦 稻香 音乐", duration=180).to_dict(),
        score=100.0,
        source="library",
        reason="长期偏好",
    )
    stage = RecommendationCandidate(
        track=Track(bvid="BV1234567891", title="K-Pop 唱跳舞台 音乐现场", duration=180).to_dict(),
        score=1.0,
        source="library",
        reason="本轮请求",
    )
    profile = MusicProfile(positive_topics={"周杰伦": 1.0})
    legacy = UserProfile()
    request = RecommendationRequest(
        scene="conversation",
        limit=2,
        request_spec=spec,
        profile=profile,
        exclude_track_ids=set(),
        recent_context={},
    )
    _reranked, selected, _diagnostics = RecommendationEngine().rank_and_select(
        [favorite, stage], request=request, legacy_profile=legacy
    )
    assert [item.track["trackId"] for item in selected] == [stage.track["trackId"]]
