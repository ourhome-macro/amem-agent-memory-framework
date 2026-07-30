from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from hashlib import sha256
from time import monotonic, perf_counter
from typing import Any

from agent_memory_runtime.config import HybridRetrievalConfig, QueryRouterConfig
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.exceptions import (
    EmbeddingConfigurationError,
    SemanticCircuitOpenError,
)
from agent_memory_runtime.memory.embeddings import (
    EmbeddingProvider,
    VectorIndex,
    validate_vector,
)
from agent_memory_runtime.memory.retrieval.candidates import CandidateBatch, CandidateHit
from agent_memory_runtime.memory.retrieval.query_router import route_hybrid_config, route_query


class StoreLexicalRetriever:
    def __init__(self, memory_store: Any) -> None:
        self.memory_store = memory_store

    def retrieve(self, query: MemoryQuery, *, limit: int) -> tuple[CandidateHit, ...]:
        search = getattr(self.memory_store, "search_lexical", None)
        if callable(search):
            return tuple(search(query, limit=limit))
        records = self.memory_store.query_records(query, limit=limit)
        return tuple(
            CandidateHit(
                memory_id=record.memory_id,
                sources=("lexical",),
                lexical_rank=rank,
            )
            for rank, record in enumerate(records, start=1)
        )


class SemanticRetriever:
    def __init__(
        self,
        *,
        provider: EmbeddingProvider,
        vector_index: VectorIndex,
        config: HybridRetrievalConfig,
    ) -> None:
        self.provider = provider
        self.vector_index = vector_index
        self.config = config
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._cache_lock = threading.RLock()
        self._breaker_lock = threading.RLock()
        self._coverage_lock = threading.RLock()
        self._coverage_value: float | None = None
        self._coverage_checked_at = 0.0
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        if config.min_semantic_similarity is None and not config.allow_uncalibrated_semantic:
            raise EmbeddingConfigurationError(
                "semantic retrieval requires a model-calibrated "
                "min_semantic_similarity; set allow_uncalibrated_semantic only "
                "for offline evaluation"
            )

    def retrieve(
        self,
        query: MemoryQuery,
        *,
        limit: int,
    ) -> tuple[tuple[CandidateHit, ...], float, float, float]:
        started = perf_counter()
        vector = self._query_vector(query.text)
        embedding_ms = _elapsed_ms(started)
        started = perf_counter()
        vector_hits = self.vector_index.search(
            list(vector),
            query,
            spec=self.provider.spec,
            limit=limit,
        )
        vector_search_ms = _elapsed_ms(started)
        threshold = self.config.min_semantic_similarity
        if threshold is not None:
            vector_hits = [hit for hit in vector_hits if hit.similarity >= threshold]
        hits = tuple(
            CandidateHit(
                memory_id=hit.memory_id,
                sources=("semantic",),
                semantic_rank=rank,
                semantic_similarity=hit.similarity,
            )
            for rank, hit in enumerate(vector_hits, start=1)
        )
        coverage = self._coverage()
        return hits, embedding_ms, vector_search_ms, coverage

    def _query_vector(self, text: str) -> tuple[float, ...]:
        key = sha256(f"{self.provider.spec.generation}\0{text.strip()}".encode()).hexdigest()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        self._before_provider_call()
        try:
            vector = tuple(self.provider.embed_query(text))
            validate_vector(vector, self.provider.spec)
        except Exception:
            self._record_provider_failure()
            raise
        self._record_provider_success()
        if self.config.query_cache_size:
            with self._cache_lock:
                self._cache[key] = vector
                self._cache.move_to_end(key)
                while len(self._cache) > self.config.query_cache_size:
                    self._cache.popitem(last=False)
        return vector

    def _before_provider_call(self) -> None:
        with self._breaker_lock:
            if monotonic() < self._circuit_open_until:
                raise SemanticCircuitOpenError("semantic provider circuit is open")

    def _record_provider_failure(self) -> None:
        with self._breaker_lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.config.semantic_failure_threshold:
                self._circuit_open_until = monotonic() + self.config.semantic_cooldown_seconds

    def _record_provider_success(self) -> None:
        with self._breaker_lock:
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0

    def _coverage(self) -> float:
        now = monotonic()
        with self._coverage_lock:
            if (
                self._coverage_value is not None
                and now - self._coverage_checked_at
                < self.config.embedding_coverage_cache_seconds
            ):
                return self._coverage_value
        value = self.vector_index.coverage(generation=self.provider.spec.generation)
        with self._coverage_lock:
            self._coverage_value = value
            self._coverage_checked_at = now
        return value


class HybridCandidateRetriever:
    def __init__(
        self,
        *,
        lexical: StoreLexicalRetriever | None,
        config: HybridRetrievalConfig,
        semantic: SemanticRetriever | None = None,
        router_config: QueryRouterConfig | None = None,
    ) -> None:
        self.lexical = lexical
        self.semantic = semantic
        self.config = config
        self.router_config = router_config or QueryRouterConfig(enabled=False)
        self._semantic_executor = ThreadPoolExecutor(
            max_workers=config.semantic_max_concurrency,
            thread_name_prefix="amem-semantic",
        )
        self._semantic_slots = threading.BoundedSemaphore(config.semantic_max_concurrency)

    def retrieve(self, query: MemoryQuery, *, limit: int) -> CandidateBatch:
        submitted_at = perf_counter()
        route = route_query(query, self.router_config)
        retrieval_config = route_hybrid_config(self.config, route, self.router_config)
        lexical_limit = min(limit, retrieval_config.lexical_candidate_limit)
        semantic_limit = min(limit, retrieval_config.semantic_candidate_limit)
        semantic_future = None
        semantic_error_type = None
        if self.semantic is not None and retrieval_config.enable_semantic and query.text.strip():
            if self._semantic_slots.acquire(blocking=False):
                try:
                    semantic_future = self._semantic_executor.submit(
                        self._retrieve_semantic,
                        query,
                        limit=semantic_limit,
                    )
                except BaseException:
                    self._semantic_slots.release()
                    raise
            else:
                semantic_error_type = "SemanticBulkheadRejected"

        lexical_enabled = self.lexical is not None and retrieval_config.enable_lexical
        lexical_hits = (
            () if not lexical_enabled else self.lexical.retrieve(query, limit=lexical_limit)
        )
        semantic_hits: tuple[CandidateHit, ...] = ()
        embedding_ms = 0.0
        vector_search_ms = 0.0
        embedding_coverage = None
        semantic_timed_out = False
        semantic_completed = False
        if semantic_future is not None:
            try:
                deadline_seconds = max(0, retrieval_config.semantic_timeout_ms) / 1000
                remaining_seconds = max(
                    0.0,
                    deadline_seconds - (perf_counter() - submitted_at),
                )
                semantic_hits, embedding_ms, vector_search_ms, embedding_coverage = (
                    semantic_future.result(timeout=remaining_seconds)
                )
                semantic_completed = True
            except FutureTimeoutError:
                semantic_timed_out = True
                if semantic_future.cancel():
                    self._semantic_slots.release()
            except Exception as error:
                semantic_error_type = type(error).__name__

        started = perf_counter()
        fused = _rrf_fuse(
            lexical_hits,
            semantic_hits,
            config=retrieval_config,
            drop_lexical_only=route.mode == "vector_heavy" and bool(semantic_hits),
        )
        return CandidateBatch(
            hits=tuple(fused[:limit]),
            retrieval_legs=(
                (("lexical",) if lexical_enabled else ())
                + (("semantic",) if semantic_completed else ())
            ),
            lexical_candidate_count=len(lexical_hits),
            semantic_candidate_count=len(semantic_hits),
            semantic_generation=(
                self.semantic.provider.spec.generation if self.semantic is not None else None
            ),
            embedding_ms=round(embedding_ms, 4),
            vector_search_ms=round(vector_search_ms, 4),
            fusion_ms=round(_elapsed_ms(started), 4),
            semantic_timed_out=semantic_timed_out,
            semantic_error_type=semantic_error_type,
            embedding_coverage=embedding_coverage,
            query_route=route.to_metadata(),
        )

    def close(self, *, wait: bool = False) -> None:
        self._semantic_executor.shutdown(wait=wait, cancel_futures=True)

    def _retrieve_semantic(
        self,
        query: MemoryQuery,
        *,
        limit: int,
    ) -> tuple[tuple[CandidateHit, ...], float, float, float]:
        try:
            if self.semantic is None:
                return (), 0.0, 0.0, 0.0
            return self.semantic.retrieve(query, limit=limit)
        finally:
            self._semantic_slots.release()


def _rrf_fuse(
    lexical_hits: tuple[CandidateHit, ...],
    semantic_hits: tuple[CandidateHit, ...],
    *,
    config: HybridRetrievalConfig,
    drop_lexical_only: bool = False,
) -> list[CandidateHit]:
    values: dict[str, dict[str, object]] = {}
    for hit in lexical_hits:
        values[hit.memory_id] = {
            "sources": {"lexical"},
            "lexical_rank": hit.lexical_rank,
            "semantic_rank": None,
            "lexical_raw_score": hit.lexical_raw_score,
            "semantic_similarity": None,
        }
    for hit in semantic_hits:
        value = values.setdefault(
            hit.memory_id,
            {
                "sources": set(),
                "lexical_rank": None,
                "semantic_rank": None,
                "lexical_raw_score": None,
                "semantic_similarity": None,
            },
        )
        value["sources"].add("semantic")
        value["semantic_rank"] = hit.semantic_rank
        value["semantic_similarity"] = hit.semantic_similarity

    active_max = 0.0
    if lexical_hits and config.lexical_weight:
        active_max += config.lexical_weight / (config.rrf_k + 1)
    if semantic_hits and config.semantic_weight:
        active_max += config.semantic_weight / (config.rrf_k + 1)
    active_max = max(active_max, 1e-12)

    fused = []
    for memory_id, value in values.items():
        lexical_rank = _optional_int(value["lexical_rank"])
        semantic_rank = _optional_int(value["semantic_rank"])
        if drop_lexical_only and semantic_rank is None:
            continue
        lexical_component = (
            config.lexical_weight / (config.rrf_k + lexical_rank)
            if lexical_rank is not None
            else 0.0
        )
        semantic_component = (
            config.semantic_weight / (config.rrf_k + semantic_rank)
            if semantic_rank is not None
            else 0.0
        )
        fused.append(
            CandidateHit(
                memory_id=memory_id,
                sources=tuple(sorted(value["sources"])),
                lexical_rank=lexical_rank,
                semantic_rank=semantic_rank,
                lexical_raw_score=_optional_float(value["lexical_raw_score"]),
                semantic_similarity=_optional_float(value["semantic_similarity"]),
                lexical_relevance=(
                    round((config.rrf_k + 1) / (config.rrf_k + lexical_rank), 6)
                    if lexical_rank is not None
                    else 0.0
                ),
                semantic_relevance=(
                    round((config.rrf_k + 1) / (config.rrf_k + semantic_rank), 6)
                    if semantic_rank is not None
                    else 0.0
                ),
                fusion_score=round(
                    (lexical_component + semantic_component) / active_max,
                    6,
                ),
            )
        )
    return sorted(fused, key=lambda hit: (hit.fusion_score, hit.memory_id), reverse=True)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000
