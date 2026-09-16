from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from candidate_pool import CandidatePool
from content_embeddings import ContentEmbeddingService
from database import get_connection
from durable_jobs import enqueue
from discovery_planner import DiscoveryPlanner
from experiments import ExperimentAssignments
from full_trace import FullTrace, current_trace_id
from keyword_evolution import KeywordEvolutionService
from keyword_governance import KeywordGovernance
from models import Track
from music_profile import MusicProfile
from request_spec import RequestSpec



class DiscoveryService:
    """Produces and admits candidates. Recommendation serving never calls Bilibili through this class."""

    def __init__(
        self,
        db_path: str,
        *,
        user_id: str,
        bili_client: Any,
        planner: DiscoveryPlanner | None = None,
    ) -> None:
        self.db_path = db_path
        self.user_id = user_id
        self.bili_client = bili_client
        self.planner = planner or DiscoveryPlanner()
        self.pool = CandidatePool(db_path, user_id=user_id)
        self.content_embeddings = ContentEmbeddingService(db_path, user_id=user_id)
        self.keyword_governance = KeywordGovernance(db_path, user_id=user_id)
        self.experiments = ExperimentAssignments(db_path, user_id=user_id)
        self.keyword_evolution = KeywordEvolutionService(self.keyword_governance)

    def enqueue(
        self,
        *,
        profile: MusicProfile,
        request_spec: RequestSpec,
        scene: str,
        limit: int,
        parent_trace_id: str | None = None,
    ) -> str | None:
        plan = self.planner.plan(profile=profile, request_spec=request_spec, scene=scene)
        if not plan.search_queries and not plan.negative_queries:
            return None
        job_id = f"discovery:{uuid4().hex}"
        trace = FullTrace(
            self.db_path,
            trace_type="discovery",
            trace_id=job_id,
            parent_trace_id=parent_trace_id or current_trace_id(),
            user_id=self.user_id,
            attributes={"scene": scene, "mode": "async"},
            initial_status="queued",
        )
        trace.event(
            "discovery.queued",
            {"queryCount": len(plan.search_queries), "limit": limit},
        )
        now = _utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO discovery_jobs (job_id, user_id, scene, request_spec_json, plan_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    job_id,
                    self.user_id,
                    scene,
                    json.dumps(request_spec.to_dict(), ensure_ascii=False),
                    json.dumps(
                        {
                            "queries": plan.search_queries,
                            "semanticQueries": plan.semantic_queries,
                            "keywordSpecs": plan.keyword_specs,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            enqueue(conn, kind='discovery', job_id=job_id, user_id=self.user_id,
                    lane=f'discovery:{self.user_id}', payload={
                        'queries': plan.search_queries,
                        'negative_queries': plan.negative_queries,
                        'keyword_specs': plan.keyword_specs,
                        'negative_keyword_specs': plan.negative_keyword_specs,
                        'request_spec': request_spec.to_dict(), 'limit': limit,
                    })
        return job_id

    def discover_now(
        self,
        *,
        profile: MusicProfile,
        request_spec: RequestSpec,
        scene: str,
        limit: int,
        parent_trace_id: str | None = None,
    ) -> dict[str, Any]:
        plan = self.planner.plan(profile=profile, request_spec=request_spec, scene=scene)
        trace = FullTrace(
            self.db_path,
            trace_type="discovery",
            parent_trace_id=parent_trace_id or current_trace_id(),
            user_id=self.user_id,
            attributes={"scene": scene, "mode": "synchronous"},
        )
        with trace:
            result = self._discover(
                plan.search_queries,
                request_spec,
                limit,
                trace_id=trace.trace_id,
                negative_queries=plan.negative_queries,
                keyword_specs=plan.keyword_specs,
                negative_keyword_specs=plan.negative_keyword_specs,
                full_trace=trace,
            )
            result["fullTraceId"] = trace.trace_id
            return result

    def job_status(self, job_id: str) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM discovery_jobs WHERE job_id = ? AND user_id = ?",
                (job_id, self.user_id),
            ).fetchone()
        if row is None:
            return {"jobId": job_id, "available": False}
        with get_connection(self.db_path) as conn:
            execution = conn.execute('SELECT status,error FROM durable_jobs WHERE job_id=?',
                                     (job_id,)).fetchone()
        terminal_failure = execution and execution['status'] in {'failed','needs_reconciliation'}
        return {
            "jobId": row["job_id"],
            "fullTraceId": row["job_id"],
            "available": True,
            "status": execution['status'] if terminal_failure else row["status"],
            "result": _json_object(row["result_json"]),
            "error": execution['error'] if terminal_failure else row["error"],
        }

    def _run_job(
        self,
        job_id: str,
        queries: list[str],
        negative_queries: list[str],
        keyword_specs: dict[str, dict[str, object]],
        negative_keyword_specs: dict[str, dict[str, object]],
        spec: RequestSpec,
        limit: int,
    ) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE discovery_jobs SET status = 'running', updated_at = ? WHERE job_id = ?",
                (_utc_now(), job_id),
            )
        try:
            trace = FullTrace.resume(self.db_path, job_id)
        except Exception:
            trace = FullTrace(
                self.db_path,
                trace_type="discovery",
                trace_id=job_id,
                user_id=self.user_id,
                attributes={"mode": "async_recovered"},
            )
        trace.record_span(
            "discovery.queue_wait",
            trace.elapsed_ms(),
            kind="queue",
        )
        try:
            with trace:
                result = self._discover(
                    queries,
                    spec,
                    limit,
                    trace_id=job_id,
                    negative_queries=negative_queries,
                    keyword_specs=keyword_specs,
                    negative_keyword_specs=negative_keyword_specs,
                    full_trace=trace,
                )
        except Exception as exc:
            with get_connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE discovery_jobs SET status = 'failed', error = ?, updated_at = ? WHERE job_id = ?",
                    (str(exc)[:300], _utc_now(), job_id),
                )
            return
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE discovery_jobs SET status = 'completed', result_json = ?, updated_at = ? WHERE job_id = ?",
                (json.dumps(result, ensure_ascii=False), _utc_now(), job_id),
            )
        if self.keyword_governance.evolution_due():
            with get_connection(self.db_path) as conn:
                enqueue(conn, kind='evolution', user_id=self.user_id,
                        lane=f'evolution:{self.user_id}', job_id=f'evolution:{job_id}',
                        payload={'blocked_topics': list(spec.excluded_topics)})

    def _run_evolution(self, blocked_topics: list[str]) -> None:
        try:
            self.keyword_evolution.run(blocked_topics=blocked_topics)
        except Exception as exc:
            self.keyword_governance.record_evolution_run(status="failed", error=str(exc))
            raise

    def _discover(
        self,
        queries: list[str],
        spec: RequestSpec,
        limit: int,
        *,
        trace_id: str,
        negative_queries: list[str] | None = None,
        keyword_specs: dict[str, dict[str, object]] | None = None,
        negative_keyword_specs: dict[str, dict[str, object]] | None = None,
        full_trace: FullTrace | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        keyword_specs = dict(keyword_specs or {})
        governance_started = time.perf_counter()
        governance_variant = self.experiments.keyword_governance_variant()
        apply_governance = governance_variant != "control"
        if not spec.constrained and apply_governance:
            for reusable in self.keyword_governance.reusable_keywords(limit=32):
                query = str(reusable.get("query") or "").strip()
                if not query:
                    continue
                if query not in queries:
                    queries.append(query)
                canonical = reusable.get("canonicalSpec")
                if isinstance(canonical, dict):
                    keyword_specs.setdefault(query, canonical)
        governed = self.keyword_governance.prepare(
            queries,
            source="request" if spec.constrained else "profile",
            preserve_order=spec.constrained,
            family_specs=keyword_specs,
            limit=self.planner.search_budget,
            apply_governance=apply_governance,
        )
        queries = [item["query"] for item in governed]
        keyword_ids = {item["query"]: item["keywordId"] for item in governed}
        query_pages = {
            item["query"]: int(item.get("searchCount") or 0) % 10 + 1 for item in governed
        }
        negative_governed = self.keyword_governance.prepare(
            negative_queries or [],
            source="negative_probe",
            preserve_order=True,
            family_specs=negative_keyword_specs,
            limit=1,
            apply_governance=apply_governance,
        )
        negative_queries = [item["query"] for item in negative_governed]
        negative_keyword_ids = {item["query"]: item["keywordId"] for item in negative_governed}
        negative_query_pages = {
            item["query"]: int(item.get("searchCount") or 0) % 10 + 1 for item in negative_governed
        }
        if full_trace is not None:
            full_trace.record_span(
                "keyword_governance.select",
                (time.perf_counter() - governance_started) * 1000,
                kind="governance",
                outputs={
                    "selectedQueries": queries,
                    "negativeQueries": negative_queries,
                    "variant": governance_variant,
                },
                metrics={"selectedQueryCount": len(queries)},
            )
        per_query = max(4, min(16, max(limit, 1) * 2))
        total = {"enqueued": 0, "admitted": 0}
        query_timings = []

        def search(kind: str, query: str) -> tuple[str, str, int, list[Track], float, str | None]:
            query_started = time.perf_counter()
            page_size = min(per_query, 8) if kind == "negative" else per_query
            page = negative_query_pages[query] if kind == "negative" else query_pages[query]
            tracks, error_type = self._safe_search_result(query, page_size, page=page)
            return (
                kind,
                query,
                page,
                tracks,
                (time.perf_counter() - query_started) * 1000,
                error_type,
            )

        search_tasks = [("positive", query) for query in queries] + [
            ("negative", query) for query in negative_queries
        ]
        search_results: list[tuple[str, str, int, list[Track], float, str | None]] = []
        with ThreadPoolExecutor(
            max_workers=min(4, len(search_tasks) or 1), thread_name_prefix="music-search"
        ) as executor:
            futures = [executor.submit(search, kind, query) for kind, query in search_tasks]
            for future in as_completed(futures):
                search_results.append(future.result())
        negative_sample_count = 0
        admitted_tracks: list[Track] = []
        for kind, query, page, tracks, search_ms, error_type in search_results:
            if full_trace is not None:
                full_trace.record_span(
                    "bilibili.search",
                    search_ms,
                    kind="tool",
                    status="failed" if error_type else "completed",
                    inputs={"query": query, "page": page, "queryKind": kind},
                    outputs={"resultCount": len(tracks)},
                    error_type=error_type,
                )
            admit_started = time.perf_counter()
            if kind == "negative":
                recorded = self.pool.record_negative_samples(tracks, query=query)
                self.keyword_governance.record_discovery(
                    negative_keyword_ids[query],
                    tracks=tracks,
                    admitted_count=0,
                    discovery_job_id=trace_id,
                    admitted_track_ids=[],
                )
                negative_sample_count += recorded
                query_timings.append(
                    {
                        "query": query,
                        "kind": kind,
                        "page": page,
                        "searchMs": round(search_ms, 2),
                        "admissionMs": round((time.perf_counter() - admit_started) * 1000, 2),
                        "resultCount": len(tracks),
                        "errorType": error_type,
                    }
                )
                continue
            result = self.pool.admit(
                tracks, source="discovery_search", request_spec=spec, query=query
            )
            admitted_ids = set(result.get("admittedTrackIds") or [])
            admitted_tracks.extend(track for track in tracks if track.track_id in admitted_ids)
            self.keyword_governance.record_discovery(
                keyword_ids[query],
                tracks=tracks,
                admitted_count=result["admitted"],
                discovery_job_id=trace_id,
                admitted_track_ids=result.get("admittedTrackIds") or [],
                scope_kind=str(result.get("scopeKind") or "default"),
                scope_key=str(result.get("scopeKey") or ""),
            )
            admit_ms = (time.perf_counter() - admit_started) * 1000
            total["enqueued"] += result["enqueued"]
            total["admitted"] += result["admitted"]
            query_timings.append(
                {
                    "query": query,
                    "kind": kind,
                    "page": page,
                    "searchMs": round(search_ms, 2),
                    "admissionMs": round(admit_ms, 2),
                    "resultCount": len(tracks),
                    "errorType": error_type,
                }
            )
            if full_trace is not None:
                full_trace.record_span(
                    "candidate.admit",
                    admit_ms,
                    kind="retrieval",
                    inputs={"source": "discovery_search", "query": query},
                    outputs={
                        "resultCount": len(tracks),
                        "admittedCount": result["admitted"],
                    },
                    metrics={"invalidRecallCount": max(len(tracks) - result["admitted"], 0)},
                )
        supply_lanes = []
        for source, query, tracks in self._supply_lane_tracks(
            limit=max(limit * 2, 8),
            full_trace=full_trace,
        ):
            admit_started = time.perf_counter()
            result = self.pool.admit(
                tracks,
                source=source,
                request_spec=spec,
                query=query,
            )
            admitted_ids = set(result.get("admittedTrackIds") or [])
            admitted_tracks.extend(track for track in tracks if track.track_id in admitted_ids)
            total["enqueued"] += result["enqueued"]
            total["admitted"] += result["admitted"]
            supply_lanes.append(
                {
                    "source": source,
                    "query": query,
                    "resultCount": len(tracks),
                    "admittedCount": result["admitted"],
                    "admissionMs": round((time.perf_counter() - admit_started) * 1000, 2),
                }
            )
            if full_trace is not None:
                full_trace.record_span(
                    "candidate.admit",
                    supply_lanes[-1]["admissionMs"],
                    kind="retrieval",
                    inputs={"source": source, "query": query},
                    outputs={
                        "resultCount": len(tracks),
                        "admittedCount": result["admitted"],
                    },
                    metrics={"invalidRecallCount": max(len(tracks) - result["admitted"], 0)},
                )
        embedding_started = time.perf_counter()
        embedding_result = self.content_embeddings.ensure_text_embeddings(admitted_tracks[:64])
        audio_queued = self.content_embeddings.enqueue_audio_embeddings(admitted_tracks[:64])
        embedding_ms = round((time.perf_counter() - embedding_started) * 1000, 2)
        available = self.pool.availability(spec)
        if full_trace is not None:
            full_trace.record_span(
                "candidate.embedding.persist",
                embedding_ms,
                kind="embedding",
                outputs={**embedding_result, "audioQueued": audio_queued},
            )
            full_trace.event(
                "discovery.completed",
                {
                    "admitted": total["admitted"],
                    "enqueued": total["enqueued"],
                    "available": available,
                },
            )
        return {
            "traceId": trace_id,
            "queries": queries,
            "negativeQueries": negative_queries or [],
            "negativeSampleCount": negative_sample_count,
            "keywords": [*governed, *negative_governed],
            "keywordGovernanceVariant": governance_variant,
            "supplyLanes": supply_lanes,
            **total,
            "available": available,
            "embedding": {**embedding_result, "audioQueued": audio_queued},
            "timing": {
                "queries": query_timings,
                "embeddingPersistMs": embedding_ms,
                "totalMs": round((time.perf_counter() - started) * 1000, 2),
            },
        }

    def _supply_lane_tracks(
        self,
        *,
        limit: int,
        full_trace: FullTrace | None = None,
    ) -> list[tuple[str, str, list[Track]]]:
        with get_connection(self.db_path) as conn:
            uploader_rows = conn.execute(
                """
                SELECT owner_mid, COUNT(*) AS evidence_count
                FROM (
                    SELECT t.owner_mid
                    FROM likes l JOIN tracks t ON t.track_id=l.track_id
                    WHERE l.user_id=? AND t.owner_mid IS NOT NULL
                    UNION ALL
                    SELECT t.owner_mid
                    FROM playback_recent r JOIN tracks t ON t.track_id=r.track_id
                    WHERE r.user_id=? AND r.completed=1 AND t.owner_mid IS NOT NULL
                )
                GROUP BY owner_mid ORDER BY evidence_count DESC LIMIT 2
                """,
                (self.user_id, self.user_id),
            ).fetchall()
            seed_rows = conn.execute(
                """
                SELECT bvid FROM (
                    SELECT t.bvid, l.created_at AS evidence_at
                    FROM likes l JOIN tracks t ON t.track_id=l.track_id
                    WHERE l.user_id=?
                    UNION ALL
                    SELECT t.bvid, r.last_played_at AS evidence_at
                    FROM playback_recent r JOIN tracks t ON t.track_id=r.track_id
                    WHERE r.user_id=? AND r.completed=1
                ) ORDER BY evidence_at DESC LIMIT 2
                """,
                (self.user_id, self.user_id),
            ).fetchall()

        lanes: list[tuple[str, str, list[Track]]] = []
        for row in uploader_rows:
            mid = int(row["owner_mid"])
            tool_started = time.perf_counter()
            error_type = None
            try:
                payload = self.bili_client.list_user_tracks(
                    mid,
                    page=1,
                    page_size=min(limit, 20),
                    order="pubdate",
                )
                tracks = [Track.from_dict(item) for item in payload.get("tracks") or []]
            except Exception as exc:
                tracks = []
                error_type = type(exc).__name__
            if full_trace is not None:
                full_trace.record_span(
                    "bilibili.uploader_supply",
                    (time.perf_counter() - tool_started) * 1000,
                    kind="tool",
                    status="failed" if error_type else "completed",
                    inputs={"uploaderMid": mid},
                    outputs={"resultCount": len(tracks)},
                    error_type=error_type,
                )
            if tracks:
                lanes.append(("preferred_uploader_supply", f"uploader:{mid}", tracks))

        for row in seed_rows:
            bvid = str(row["bvid"])
            tool_started = time.perf_counter()
            error_type = None
            try:
                tracks = self.bili_client.list_related_tracks(bvid, limit=min(limit, 20))
            except Exception as exc:
                tracks = []
                error_type = type(exc).__name__
            if full_trace is not None:
                full_trace.record_span(
                    "bilibili.related_supply",
                    (time.perf_counter() - tool_started) * 1000,
                    kind="tool",
                    status="failed" if error_type else "completed",
                    inputs={"seedBvid": bvid},
                    outputs={"resultCount": len(tracks)},
                    error_type=error_type,
                )
            if tracks:
                lanes.append(("related_supply", f"related:{bvid}", tracks))

        for media_id in _favorite_media_ids():
            tool_started = time.perf_counter()
            error_type = None
            try:
                payload = self.bili_client.list_favorite_tracks(
                    media_id,
                    page=1,
                    page_size=min(limit, 20),
                )
                tracks = [Track.from_dict(item) for item in payload.get("tracks") or []]
            except Exception as exc:
                tracks = []
                error_type = type(exc).__name__
            if full_trace is not None:
                full_trace.record_span(
                    "bilibili.favorite_supply",
                    (time.perf_counter() - tool_started) * 1000,
                    kind="tool",
                    status="failed" if error_type else "completed",
                    inputs={"mediaId": media_id},
                    outputs={"resultCount": len(tracks)},
                    error_type=error_type,
                )
            if tracks:
                lanes.append(("favorite_supply", f"favorite:{media_id}", tracks))
        return lanes

    def _safe_search(self, query: str, page_size: int, *, page: int = 1) -> list[Track]:
        values, _error_type = self._safe_search_result(query, page_size, page=page)
        return values

    def _safe_search_result(
        self,
        query: str,
        page_size: int,
        *,
        page: int = 1,
    ) -> tuple[list[Track], str | None]:
        try:
            values = self.bili_client.search(query, page=max(int(page), 1), page_size=page_size)
        except Exception as exc:
            return [], type(exc).__name__
        result = []
        for value in values or []:
            try:
                result.append(value if isinstance(value, Track) else Track.from_dict(value))
            except Exception:
                continue
        return result, None


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _favorite_media_ids() -> tuple[int, ...]:
    values = []
    for raw in os.getenv("RECOMMEND_DISCOVERY_FAVORITE_MEDIA_IDS", "").split(","):
        try:
            media_id = int(raw.strip())
        except ValueError:
            continue
        if media_id > 0:
            values.append(media_id)
    return tuple(dict.fromkeys(values))[:4]
