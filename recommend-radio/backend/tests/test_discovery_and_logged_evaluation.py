from __future__ import annotations

import json

from candidate_pool import CandidatePool
from database import get_connection, init_db
from discovery_planner import DiscoveryPlanner
from discovery_service import DiscoveryService
from evaluate_memory_runtime import evaluate_logged_impressions
from library_service import LibraryService
from models import Track
from music_profile import MusicProfile
from request_spec import RequestSpec


class _SupplyClient:
    def list_user_tracks(self, mid, **kwargs):
        return {"tracks": [Track(bvid="BV1234567893", title="Artist - New").to_dict()]}

    def list_related_tracks(self, bvid, **kwargs):
        return [Track(bvid="BV1234567894", title="Artist - Related")]

    def list_favorite_tracks(self, media_id, **kwargs):
        return {"tracks": [Track(bvid="BV1234567895", title="Artist - Favorite").to_dict()]}


def test_discovery_limits_semantic_queries_to_one_external_probe() -> None:
    profile = MusicProfile.empty()
    profile.mood_weights = {"calm": 0.8, "chill": 0.7, "治愈": 0.6}
    plan = DiscoveryPlanner(search_budget=8).plan(
        profile=profile,
        request_spec=RequestSpec(),
        scene="home",
    )
    assert len(plan.semantic_queries) >= 2
    assert sum(query in plan.semantic_queries for query in plan.search_queries) == 1


def test_candidate_inventory_keeps_default_and_request_scopes(tmp_path) -> None:
    db_path = tmp_path / "radio.sqlite3"
    init_db(db_path)
    pool = CandidatePool(str(db_path), user_id="legacy-owner")
    track = Track(bvid="BV1234567890", title="R&B music", duration=180)
    pool.admit(
        [track],
        source="discovery_search",
        request_spec=RequestSpec(),
        query="R&B 音乐",
    )
    pool.admit(
        [track],
        source="discovery_search",
        request_spec=RequestSpec(required_genres=("rnb",)),
        query="英文 R&B 音乐",
    )
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT scope_kind FROM content_cache WHERE track_id=? ORDER BY scope_kind",
            (track.track_id,),
        ).fetchall()
    assert [row["scope_kind"] for row in rows] == ["default", "request"]


def test_logged_evaluation_uses_post_impression_feedback(tmp_path) -> None:
    db_path = tmp_path / "radio.sqlite3"
    init_db(db_path)
    library = LibraryService(db_path)
    tracks = [
        Track(bvid="BV1234567890", cid=1, title="Artist - First"),
        Track(bvid="BV1234567891", cid=2, title="Artist - Second"),
    ]
    library.upsert_tracks(tracks)
    shown_at = "2026-09-01T00:00:00+00:00"
    with get_connection(db_path) as conn:
        for rank, track in enumerate(tracks, start=1):
            conn.execute(
                """
                INSERT INTO recommendation_impressions (
                    recommendation_trace_id, user_id, track_id, rank_position,
                    score, score_signals_json, candidate_snapshot_json,
                    policy_json, shown_at
                ) VALUES ('trace-1', 'legacy-owner', ?, ?, ?, ?, '{}', ?, ?)
                """,
                (
                    track.track_id,
                    rank,
                    3 - rank,
                    json.dumps({"profile_match": 1 if rank == 1 else 0}),
                    json.dumps({"experiments": {"memory_context_v1": "treatment"}}),
                    shown_at,
                ),
            )
        conn.execute(
            """
            INSERT INTO recommendation_events (
                user_id, track_id, event, scene, source, reason, score,
                recommendation_trace_id, source_keyword_ids_json, created_at
            ) VALUES ('legacy-owner', ?, 'completed', 'home', 'test', '', 1,
                      'trace-1', '[]', '2026-09-01T00:10:00+00:00')
            """,
            (tracks[0].track_id,),
        )
    report = evaluate_logged_impressions(db_path)
    assert report["available"] is True
    assert report["traceCount"] == 1
    assert report["hitRateAt8Pct"] == 100.0
    assert report["completionRatePct"] == 50.0


def test_supply_lanes_use_likes_related_and_configured_favorites(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "radio.sqlite3"
    init_db(db_path)
    library = LibraryService(db_path)
    seed = Track(
        bvid="BV1234567890",
        cid=1,
        title="Artist - Seed",
        owner="Artist",
        owner_mid=42,
    )
    library.add_like(seed)
    monkeypatch.setenv("RECOMMEND_DISCOVERY_FAVORITE_MEDIA_IDS", "7")
    service = DiscoveryService(
        str(db_path),
        user_id="legacy-owner",
        bili_client=_SupplyClient(),
    )
    lanes = service._supply_lane_tracks(limit=8)
    assert {source for source, _query, _tracks in lanes} == {
        "preferred_uploader_supply",
        "related_supply",
        "favorite_supply",
    }
