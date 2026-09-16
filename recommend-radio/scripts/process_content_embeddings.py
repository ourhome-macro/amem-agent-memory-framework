"""Backfill persisted text vectors and process opt-in CLAP audio jobs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from bili_client import BiliClient  # noqa: E402
from content_embeddings import ContentEmbeddingService  # noqa: E402
from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID, get_connection, init_db  # noqa: E402
from library_service import LibraryService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--user-id", default=LEGACY_OWNER_USER_ID)
    parser.add_argument("--mode", choices=("text", "audio", "all"), default="all")
    parser.add_argument("--limit", type=int, default=64)
    args = parser.parse_args()

    db_path = Path(args.db_path).resolve()
    init_db(db_path)
    service = ContentEmbeddingService(str(db_path), user_id=args.user_id)
    result: dict[str, object] = {}
    if args.mode in {"text", "all"}:
        tracks = _ready_tracks(db_path, user_id=args.user_id, limit=args.limit)
        result["text"] = service.ensure_text_embeddings(tracks)
        result["audioQueued"] = service.enqueue_audio_embeddings(tracks)
    if args.mode in {"audio", "all"}:
        client = BiliClient()
        try:
            result["audio"] = service.process_audio_jobs(client, limit=args.limit)
        finally:
            client.close()
    result["coverage"] = _coverage(db_path, user_id=args.user_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _ready_tracks(db_path: Path, *, user_id: str, limit: int):
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT track_id FROM content_cache
            WHERE user_id=? AND status='ready'
            ORDER BY updated_at DESC LIMIT ?
            """,
            (user_id, max(1, min(int(limit), 1000))),
        ).fetchall()
    library = LibraryService(db_path, user_id=user_id)
    return [track for row in rows if (track := library.get_track(str(row["track_id"]))) is not None]


def _coverage(db_path: Path, *, user_id: str) -> dict[str, int]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT c.track_id) AS candidates,
                   COUNT(DISTINCT CASE WHEN text_e.recording_id IS NOT NULL
                                      THEN c.track_id END) AS text_vectors,
                   COUNT(DISTINCT CASE WHEN audio_e.recording_id IS NOT NULL
                                      THEN c.track_id END) AS audio_vectors
            FROM content_cache c
            JOIN tracks t ON t.track_id=c.track_id
            LEFT JOIN content_embeddings text_e
              ON text_e.recording_id=t.recording_id
             AND text_e.modality='text' AND text_e.status='ready'
            LEFT JOIN content_embeddings audio_e
              ON audio_e.recording_id=t.recording_id
             AND audio_e.modality='audio' AND audio_e.status='ready'
            WHERE c.user_id=? AND c.status='ready'
            """,
            (user_id,),
        ).fetchone()
    return {
        "candidateCount": int(row["candidates"] or 0),
        "textVectorCount": int(row["text_vectors"] or 0),
        "audioVectorCount": int(row["audio_vectors"] or 0),
    }


if __name__ == "__main__":
    main()
