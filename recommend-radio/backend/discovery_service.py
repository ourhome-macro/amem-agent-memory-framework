from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import requests

from candidate_pool import CandidatePool
from database import get_connection
from discovery_planner import DiscoveryPlanner
from keyword_governance import KeywordGovernance
from models import Track
from music_profile import MusicProfile
from request_spec import RequestSpec


_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="music-discovery")


class DiscoveryService:
    """Produces and admits candidates. Recommendation serving never calls Bilibili through this class."""

    def __init__(self, db_path: str, *, user_id: str, bili_client: Any, planner: DiscoveryPlanner | None = None) -> None:
        self.db_path = db_path
        self.user_id = user_id
        self.bili_client = bili_client
        self.planner = planner or DiscoveryPlanner()
        self.pool = CandidatePool(db_path, user_id=user_id)
        self.keyword_governance = KeywordGovernance(db_path, user_id=user_id)

    def enqueue(self, *, profile: MusicProfile, request_spec: RequestSpec, scene: str, limit: int) -> str | None:
        plan = self.planner.plan(profile=profile, request_spec=request_spec, scene=scene)
        if not plan.search_queries and not plan.negative_queries:
            return None
        job_id = f"discovery:{uuid4().hex}"
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
                        {"queries": plan.search_queries, "keywordSpecs": plan.keyword_specs},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
        _EXECUTOR.submit(
            self._run_job,
            job_id,
            plan.search_queries,
            plan.negative_queries,
            plan.keyword_specs,
            plan.negative_keyword_specs,
            request_spec,
            limit,
        )
        return job_id

    def discover_now(self, *, profile: MusicProfile, request_spec: RequestSpec, scene: str, limit: int) -> dict[str, Any]:
        plan = self.planner.plan(profile=profile, request_spec=request_spec, scene=scene)
        return self._discover(
            plan.search_queries,
            request_spec,
            limit,
            trace_id=plan.trace_id,
            negative_queries=plan.negative_queries,
            keyword_specs=plan.keyword_specs,
            negative_keyword_specs=plan.negative_keyword_specs,
        )

    def job_status(self, job_id: str) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM discovery_jobs WHERE job_id = ? AND user_id = ?", (job_id, self.user_id)).fetchone()
        if row is None:
            return {"jobId": job_id, "available": False}
        return {
            "jobId": row["job_id"],
            "available": True,
            "status": row["status"],
            "result": _json_object(row["result_json"]),
            "error": row["error"],
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
            conn.execute("UPDATE discovery_jobs SET status = 'running', updated_at = ? WHERE job_id = ?", (_utc_now(), job_id))
        try:
            result = self._discover(
                queries,
                spec,
                limit,
                trace_id=job_id,
                negative_queries=negative_queries,
                keyword_specs=keyword_specs,
                negative_keyword_specs=negative_keyword_specs,
            )
        except Exception as exc:
            with get_connection(self.db_path) as conn:
                conn.execute("UPDATE discovery_jobs SET status = 'failed', error = ?, updated_at = ? WHERE job_id = ?", (str(exc)[:300], _utc_now(), job_id))
            return
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE discovery_jobs SET status = 'completed', result_json = ?, updated_at = ? WHERE job_id = ?",
                (json.dumps(result, ensure_ascii=False), _utc_now(), job_id),
            )

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
    ) -> dict[str, Any]:
        started = time.perf_counter()
        governed = self.keyword_governance.prepare(
            queries,
            source="request" if spec.constrained else "profile",
            preserve_order=spec.constrained,
            family_specs=keyword_specs,
            limit=self.planner.search_budget,
        )
        queries = [item["query"] for item in governed]
        keyword_ids = {item["query"]: item["keywordId"] for item in governed}
        query_pages = {item["query"]: int(item.get("searchCount") or 0) % 10 + 1 for item in governed}
        negative_governed = self.keyword_governance.prepare(
            negative_queries or [],
            source="negative_probe",
            preserve_order=True,
            family_specs=negative_keyword_specs,
            limit=1,
        )
        negative_queries = [item["query"] for item in negative_governed]
        negative_keyword_ids = {item["query"]: item["keywordId"] for item in negative_governed}
        negative_query_pages = {
            item["query"]: int(item.get("searchCount") or 0) % 10 + 1 for item in negative_governed
        }
        per_query = max(4, min(16, max(limit, 1) * 2))
        total = {"enqueued": 0, "admitted": 0}
        query_timings = []
        def search(kind: str, query: str) -> tuple[str, str, int, list[Track], float]:
            query_started = time.perf_counter()
            page_size = min(per_query, 8) if kind == "negative" else per_query
            page = negative_query_pages[query] if kind == "negative" else query_pages[query]
            tracks = self._safe_search(query, page_size, page=page)
            return kind, query, page, tracks, (time.perf_counter() - query_started) * 1000

        search_tasks = [("positive", query) for query in queries] + [("negative", query) for query in negative_queries]
        search_results: list[tuple[str, str, int, list[Track], float]] = []
        with ThreadPoolExecutor(max_workers=min(4, len(search_tasks) or 1), thread_name_prefix="music-search") as executor:
            futures = [executor.submit(search, kind, query) for kind, query in search_tasks]
            for future in as_completed(futures):
                search_results.append(future.result())
        negative_sample_count = 0
        embedding_warm_texts: list[str] = []
        for kind, query, page, tracks, search_ms in search_results:
            admit_started = time.perf_counter()
            if kind == "negative":
                recorded = self.pool.record_negative_samples(tracks, query=query)
                self.keyword_governance.record_discovery(
                    negative_keyword_ids[query], tracks=tracks, admitted_count=0
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
                    }
                )
                continue
            embedding_warm_texts.extend(
                f"{track.title[:180]} {track.owner[:80]}" for track in tracks
            )
            result = self.pool.admit(tracks, source="discovery_search", request_spec=spec, query=query)
            self.keyword_governance.record_discovery(keyword_ids[query], tracks=tracks, admitted_count=result["admitted"])
            admit_ms = (time.perf_counter() - admit_started) * 1000
            total["enqueued"] += result["enqueued"]
            total["admitted"] += result["admitted"]
            query_timings.append({"query": query, "kind": kind, "page": page, "searchMs": round(search_ms, 2), "admissionMs": round(admit_ms, 2), "resultCount": len(tracks)})
        warm_started = time.perf_counter()
        warmed_count = self._warm_embedding_cache(embedding_warm_texts[:64])
        return {"traceId": trace_id, "queries": queries, "negativeQueries": negative_queries or [], "negativeSampleCount": negative_sample_count, "keywords": [*governed, *negative_governed], **total, "available": self.pool.availability(spec), "timing": {"queries": query_timings, "embeddingWarmCount": warmed_count, "embeddingWarmMs": round((time.perf_counter() - warm_started) * 1000, 2), "totalMs": round((time.perf_counter() - started) * 1000, 2)}}

    @staticmethod
    def _warm_embedding_cache(texts: list[str]) -> int:
        values = list(dict.fromkeys(text for text in texts if text.strip()))
        base_url = os.getenv("AMEM_EMBEDDING_BASE_URL", "").rstrip("/")
        if not values or not base_url:
            return 0
        try:
            response = requests.post(
                f"{base_url}/embeddings",
                json={"model": os.getenv("AMEM_EMBEDDING_MODEL", "bge-m3"), "input": values},
                headers={"Authorization": f"Bearer {os.getenv('BGE_M3_API_KEY', 'local-embedding')}"},
                timeout=max(float(os.getenv("AMEM_EMBEDDING_TIMEOUT_SECONDS", "15")) * 2, 30.0),
            )
            response.raise_for_status()
            return len(response.json().get("data") or [])
        except Exception:
            return 0

    def _safe_search(self, query: str, page_size: int, *, page: int = 1) -> list[Track]:
        try:
            values = self.bili_client.search(query, page=max(int(page), 1), page_size=page_size)
        except Exception:
            return []
        result = []
        for value in values or []:
            try:
                result.append(value if isinstance(value, Track) else Track.from_dict(value))
            except Exception:
                continue
        return result


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
