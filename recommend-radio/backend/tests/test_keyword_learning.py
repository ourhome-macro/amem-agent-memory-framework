from __future__ import annotations

from datetime import datetime, timedelta, timezone

from database import get_connection, init_db
from keyword_governance import KeywordGovernance, _decayed_search_metrics
from library_service import LibraryService
from models import Track


def test_search_observations_drive_time_decayed_yield(tmp_path) -> None:
    db_path = tmp_path / "radio.sqlite3"
    init_db(db_path)
    library = LibraryService(db_path)
    tracks = [
        Track(bvid="BV1234567890", title="Artist - First"),
        Track(bvid="BV1234567891", title="Artist - Second"),
    ]
    library.upsert_tracks(tracks)
    governance = KeywordGovernance(str(db_path), user_id="legacy-owner")
    keyword = governance.prepare(
        ["R&B 音乐"],
        source="profile",
        preserve_order=True,
        limit=1,
    )[0]
    governance.record_discovery(
        keyword["keywordId"],
        tracks=tracks,
        admitted_count=2,
        discovery_job_id="job-1",
        admitted_track_ids=[track.track_id or "" for track in tracks],
    )

    with get_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM discovery_search_observations").fetchone()[0] == 1
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn.execute("UPDATE discovery_search_observations SET observed_at=?", (old,))
        old_metrics = _decayed_search_metrics(conn, keyword["keywordId"])
        conn.execute(
            "UPDATE discovery_search_observations SET observed_at=?",
            (datetime.now(timezone.utc).isoformat(),),
        )
        fresh_metrics = _decayed_search_metrics(conn, keyword["keywordId"])
    assert fresh_metrics["new_candidate_count"] > old_metrics["new_candidate_count"]
