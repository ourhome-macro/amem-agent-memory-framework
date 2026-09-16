from __future__ import annotations

from types import SimpleNamespace

from content_embeddings import ContentEmbeddingService
from database import get_connection, init_db
from library_service import LibraryService
from models import Track
from music_entity import resolve_track_entity
from recommendation_engine import RecommendationEngine, RecommendationRequest
from request_spec import RequestSpec


def test_entity_model_separates_work_recording_and_asset() -> None:
    studio = Track(
        bvid="BV1234567890",
        cid=1,
        title="Artist - Song",
        duration=201,
    )
    live = Track(
        bvid="BV1234567891",
        cid=2,
        title="Artist - Song (Live)",
        duration=244,
    )
    duplicate_asset = Track(
        bvid="BV1234567892",
        cid=3,
        title="Artist - Song",
        duration=206,
    )

    studio_entity = resolve_track_entity(studio)
    live_entity = resolve_track_entity(live)
    duplicate_entity = resolve_track_entity(duplicate_asset)

    assert studio_entity.work_id == live_entity.work_id == duplicate_entity.work_id
    assert studio_entity.recording_id == duplicate_entity.recording_id
    assert studio_entity.recording_id != live_entity.recording_id
    assert studio.track_id != duplicate_asset.track_id


def test_unknown_artist_does_not_merge_same_title() -> None:
    first = resolve_track_entity(Track(bvid="BV1234567890", title="同名歌曲"))
    second = resolve_track_entity(Track(bvid="BV1234567891", title="同名歌曲"))
    assert first.work_id != second.work_id


def test_library_persists_entity_and_text_embedding(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "radio.sqlite3"
    init_db(db_path)
    library = LibraryService(db_path)
    track = Track(
        bvid="BV1234567890",
        cid=1,
        title="Artist - Song",
        owner="Uploader",
        duration=201,
        description="calm night music",
        tags=("R&B", "chill"),
        type_name="音乐",
    )
    library.upsert_track(track)
    loaded = library.get_track(track.track_id or "")
    assert loaded is not None
    assert loaded.work_id
    assert loaded.recording_id

    service = ContentEmbeddingService(str(db_path), user_id="legacy-owner")
    service.text_base_url = "http://embedding.invalid"
    monkeypatch.setattr(service, "_embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    report = service.ensure_text_embeddings([loaded])
    assert report["embedded"] == 1
    assert service.text_vectors([loaded.track_id or ""])[loaded.track_id or ""] == [1.0, 0.0]

    with get_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM song_works").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM song_recordings").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM track_entity_links").fetchone()[0] == 1


def test_mmr_uses_persisted_text_relevance_and_audio_diversity() -> None:
    embedding_service = SimpleNamespace(
        text_model="bge-m3",
        embed_query=lambda text: [1.0, 0.0],
    )
    engine = RecommendationEngine(embedding_service=embedding_service)
    candidates = [
        SimpleNamespace(
            score=2.0,
            text_embedding=[1.0, 0.0],
            audio_embedding=[1.0, 0.0],
            track={"trackId": "one", "title": "one", "owner": "artist"},
            tags=[],
        ),
        SimpleNamespace(
            score=1.0,
            text_embedding=[0.8, 0.2],
            audio_embedding=[0.0, 1.0],
            track={"trackId": "two", "title": "two", "owner": "artist"},
            tags=[],
        ),
    ]
    request = RecommendationRequest(
        scene="home",
        limit=2,
        request_spec=RequestSpec(raw_text="calm music"),
        profile=SimpleNamespace(),
        exclude_track_ids=set(),
        recent_context={},
    )
    ranked, diagnostics = engine._vector_mmr(candidates, request, output_limit=2)
    assert [item.track["trackId"] for item in ranked] == ["one", "two"]
    assert diagnostics["candidateVectors"] == "persisted"
    assert diagnostics["diversitySpace"] == "audio"
