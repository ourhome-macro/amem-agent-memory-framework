from __future__ import annotations

import argparse
import json
import math
import platform
import tempfile
from pathlib import Path
from statistics import mean, median
from time import perf_counter

import yaml

from agent_memory_runtime.agent.context_window import compact_checkpoint
from agent_memory_runtime.agent.models import AgentCheckpoint, ModelMessage
from agent_memory_runtime.agent.policy import AgentPolicy
from agent_memory_runtime.config import HybridRetrievalConfig, RuntimeConfig
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.evals import evaluate_retrieval
from agent_memory_runtime.memory.embeddings import (
    CallableEmbeddingProvider,
    EmbeddingSpec,
    VectorRecord,
    embedding_content_hash,
)
from agent_memory_runtime.memory.retrieval import (
    HybridCandidateRetriever,
    SemanticRetriever,
    StoreLexicalRetriever,
)
from agent_memory_runtime.memory.retrieval.pipeline import RetrievalPipeline
from agent_memory_runtime.memory.stores import SQLiteStoreBundle
from agent_memory_runtime.runtime import AgentMemoryRuntime
from agent_memory_runtime.tokens import AdaptiveTokenEstimator

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce v0.6 retrieval validation metrics.")
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--dimensions", type=int, default=1_024)
    args = parser.parse_args()
    if args.records < 100 or args.iterations < 10 or args.dimensions < 2:
        parser.error(
            "--records must be >= 100, --iterations must be >= 10, "
            "and --dimensions must be >= 2"
        )

    result = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "retrieval_eval": retrieval_eval(),
        "sqlite_fts5_retrieval": sqlite_retrieval_benchmark(
            record_count=args.records,
            iterations=args.iterations,
        ),
        "sqlite_vec_hybrid_retrieval": sqlite_vec_hybrid_benchmark(
            record_count=args.records,
            iterations=args.iterations,
            dimensions=args.dimensions,
        ),
        "checkpoint_compaction": checkpoint_compaction_benchmark(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def retrieval_eval() -> dict[str, object]:
    runtime = AgentMemoryRuntime()
    for source in (
        ROOT / "examples" / "data" / "customer_support_events.jsonl",
        ROOT / "examples" / "data" / "memory_eval_events.jsonl",
    ):
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.strip():
                runtime.ingest(Event.from_dict(json.loads(line)))

    suite = yaml.safe_load(
        (ROOT / "examples" / "evals" / "retrieval_cases.yml").read_text(encoding="utf-8")
    )
    results = []
    for case in suite["cases"]:
        query = MemoryQuery(
            agent_id=str(case["agent"]),
            text=str(case["query"]),
            session_id=case.get("session_id"),
            tenant_id=str(case.get("tenant_id") or "default"),
            user_id=case.get("user_id"),
            session_policy=str(case.get("session_policy") or "exact"),
            limit=case.get("limit"),
        )
        context = runtime.project(query)
        result = evaluate_retrieval(
            str(case["id"]),
            list(case.get("expected_memory_ids", [])),
            list(context.selected_memory_ids),
            forbidden=list(case.get("forbidden_memory_ids", [])),
            relevance=case.get("relevance"),
            k=int(case.get("k") or query.limit or runtime.config.max_retrieval_results),
        )
        results.append(result)

    return {
        "cases": len(results),
        "passed": sum(result.passed for result in results),
        "pass_rate": _rounded(mean(result.passed for result in results)),
        "mean_recall_at_k": _rounded(mean(result.recall_at_k for result in results)),
        "mean_mrr": _rounded(mean(result.reciprocal_rank for result in results)),
        "mean_ndcg_at_k": _rounded(mean(result.ndcg_at_k for result in results)),
        "case_ids": [result.case_id for result in results],
    }


def sqlite_retrieval_benchmark(
    *,
    record_count: int,
    iterations: int,
) -> dict[str, object]:
    target_id = f"memory-{record_count - 1:08d}"
    records = [_benchmark_record(index, record_count=record_count) for index in range(record_count)]
    query = MemoryQuery(
        agent_id="assistant",
        text="退款进度银行确认",
        session_id="benchmark-session",
        tenant_id="benchmark-tenant",
        user_id="benchmark-user",
        layers=("working",),
        limit=8,
    )
    config = RuntimeConfig(hybrid_retrieval=HybridRetrievalConfig(enable_semantic=False))
    pipeline = RetrievalPipeline(config)

    with tempfile.TemporaryDirectory(prefix="amem-v05-benchmark-") as temporary:
        database = Path(temporary) / "runtime.sqlite"
        bundle = SQLiteStoreBundle(database)
        started = perf_counter()
        bundle.memory_store.replace_all(records)
        index_build_ms = _elapsed_ms(started)

        retriever = HybridCandidateRetriever(
            lexical=StoreLexicalRetriever(bundle.memory_store),
            semantic=None,
            config=config.hybrid_retrieval,
        )
        for _ in range(5):
            batch = retriever.retrieve(query, limit=256)
            candidates = bundle.memory_store.get_many([hit.memory_id for hit in batch.hits])
            selected, _ = pipeline.retrieve(
                candidates,
                query,
                candidate_batch=batch,
            )
        indexed_samples = []
        for _ in range(iterations):
            started = perf_counter()
            batch = retriever.retrieve(query, limit=256)
            candidates = bundle.memory_store.get_many([hit.memory_id for hit in batch.hits])
            selected, _ = pipeline.retrieve(
                candidates,
                query,
                candidate_batch=batch,
            )
            indexed_samples.append(_elapsed_ms(started))
        if not selected or selected[0].memory_id != target_id:
            raise RuntimeError("indexed benchmark did not rank the expected memory first")

        materialized = bundle.memory_store.list_records()
        full_scan_iterations = max(10, iterations // 10)
        full_scan_samples = []
        for _ in range(full_scan_iterations):
            started = perf_counter()
            full_selected, _ = pipeline.retrieve(materialized, query)
            full_scan_samples.append(_elapsed_ms(started))
        if not full_selected or full_selected[0].memory_id != target_id:
            raise RuntimeError("full-scan benchmark did not rank the expected memory first")

        indexed_p50 = median(indexed_samples)
        full_scan_p50 = median(full_scan_samples)
        retriever.close(wait=True)
        return {
            "records": record_count,
            "candidate_limit": 256,
            "lexical_candidate_count": len(batch.hits),
            "candidate_reduction": _rounded(1 - len(batch.hits) / record_count),
            "index_build_ms": _rounded(index_build_ms),
            "database_bytes": database.stat().st_size,
            "indexed_iterations": iterations,
            "indexed_p50_ms": _rounded(indexed_p50),
            "indexed_p95_ms": _rounded(_percentile(indexed_samples, 0.95)),
            "indexed_p99_ms": _rounded(_percentile(indexed_samples, 0.99)),
            "materialized_full_scan_iterations": full_scan_iterations,
            "materialized_full_scan_p50_ms": _rounded(full_scan_p50),
            "materialized_full_scan_p95_ms": _rounded(_percentile(full_scan_samples, 0.95)),
            "p50_speedup_vs_materialized_full_scan": _rounded(
                full_scan_p50 / max(indexed_p50, 0.000001)
            ),
            "expected_top1": target_id,
        }


def sqlite_vec_hybrid_benchmark(
    *,
    record_count: int,
    iterations: int,
    dimensions: int,
) -> dict[str, object]:
    target_id = f"memory-{record_count - 1:08d}"
    records = [_benchmark_record(index, record_count=record_count) for index in range(record_count)]
    spec = EmbeddingSpec(
        provider="benchmark",
        model_id="deterministic-v1",
        dimensions=dimensions,
    )
    query_vector = [1.0, *([0.0] * (dimensions - 1))]
    background_vector = [0.0, 1.0, *([0.0] * (dimensions - 2))]
    provider = CallableEmbeddingProvider(
        spec,
        query_embedder=lambda _text: list(query_vector),
        document_embedder=lambda texts: [list(background_vector) for _ in texts],
    )
    query = MemoryQuery(
        agent_id="assistant",
        text="退款进度银行确认",
        session_id="benchmark-session",
        tenant_id="benchmark-tenant",
        user_id="benchmark-user",
        layers=("working",),
        limit=8,
    )
    config = HybridRetrievalConfig(min_semantic_similarity=0.5)

    with tempfile.TemporaryDirectory(prefix="amem-v06-vector-benchmark-") as temporary:
        database = Path(temporary) / "runtime.sqlite"
        bundle = SQLiteStoreBundle(database)
        bundle.memory_store.replace_all(records)
        bundle.embedding_generations.register(spec, status="active")
        started = perf_counter()
        with bundle.transaction():
            for record in records:
                bundle.vector_index.upsert(
                    VectorRecord(
                        memory_id=record.memory_id,
                        spec=spec,
                        content_hash=embedding_content_hash(record, spec),
                        source_sequence=record.last_event_sequence,
                        vector=tuple(
                            query_vector if record.memory_id == target_id else background_vector
                        ),
                    )
                )
        vector_build_ms = _elapsed_ms(started)
        semantic = SemanticRetriever(
            provider=provider,
            vector_index=bundle.vector_index,
            config=config,
        )
        hybrid = HybridCandidateRetriever(
            lexical=StoreLexicalRetriever(bundle.memory_store),
            semantic=semantic,
            config=config,
        )

        semantic_samples = []
        hybrid_samples = []
        for _ in range(5):
            semantic.retrieve(query, limit=64)
        for _ in range(iterations):
            started = perf_counter()
            semantic_hits, *_ = semantic.retrieve(query, limit=64)
            semantic_samples.append(_elapsed_ms(started))

        semantic_timeout_count = 0
        semantic_completed_count = 0
        semantic_bulkhead_rejection_count = 0
        for _ in range(iterations):
            started = perf_counter()
            hybrid_batch = hybrid.retrieve(query, limit=256)
            hybrid_samples.append(_elapsed_ms(started))
            semantic_timeout_count += int(hybrid_batch.semantic_timed_out)
            semantic_completed_count += int(
                "semantic" in hybrid_batch.retrieval_legs
            )
            semantic_bulkhead_rejection_count += int(
                hybrid_batch.semantic_error_type == "SemanticBulkheadRejected"
            )
        if not semantic_hits or semantic_hits[0].memory_id != target_id:
            raise RuntimeError("sqlite-vec benchmark did not rank the expected memory first")
        if not hybrid_batch.hits or hybrid_batch.hits[0].memory_id != target_id:
            raise RuntimeError("hybrid benchmark did not rank the expected memory first")
        hybrid.close(wait=True)
        return {
            "records": record_count,
            "dimensions": dimensions,
            "distance": "cosine",
            "search": "exact filtered scan",
            "vector_build_ms": _rounded(vector_build_ms),
            "embedding_coverage": bundle.vector_index.coverage(generation=spec.generation),
            "database_bytes": database.stat().st_size,
            "iterations": iterations,
            "semantic_p50_ms": _rounded(median(semantic_samples)),
            "semantic_p95_ms": _rounded(_percentile(semantic_samples, 0.95)),
            "semantic_p99_ms": _rounded(_percentile(semantic_samples, 0.99)),
            "hybrid_p50_ms": _rounded(median(hybrid_samples)),
            "hybrid_p95_ms": _rounded(_percentile(hybrid_samples, 0.95)),
            "hybrid_p99_ms": _rounded(_percentile(hybrid_samples, 0.99)),
            "hybrid_semantic_completed_count": semantic_completed_count,
            "hybrid_semantic_timeout_count": semantic_timeout_count,
            "hybrid_semantic_bulkhead_rejection_count": (
                semantic_bulkhead_rejection_count
            ),
            "expected_top1": target_id,
        }


def checkpoint_compaction_benchmark() -> dict[str, object]:
    estimator = AdaptiveTokenEstimator(safety_factor=1.0)
    messages = [
        ModelMessage(role="system", content="immutable production policy"),
        ModelMessage(role="user", content="complete the original business task"),
    ]
    messages.extend(
        ModelMessage(
            role="assistant",
            content=f"completed historical step {index}: " + "x" * 400,
        )
        for index in range(40)
    )
    checkpoint = AgentCheckpoint(run_id="benchmark-run", messages=tuple(messages))
    compacted, report = compact_checkpoint(
        checkpoint,
        tools=(),
        estimator=estimator,
        policy=AgentPolicy(
            model_context_tokens=4_000,
            reserved_output_tokens=500,
            context_compaction_ratio=0.5,
            context_keep_recent_messages=6,
            context_summary_max_tokens=400,
        ),
        model=None,
    )
    if report is None:
        raise RuntimeError("checkpoint benchmark did not trigger compaction")
    return {
        "messages_before": len(checkpoint.messages),
        "messages_after": len(compacted.messages),
        "tokens_before": report.before_tokens,
        "tokens_after": report.after_tokens,
        "token_reduction": _rounded(1 - report.after_tokens / report.before_tokens),
        "removed_messages": report.removed_messages,
        "system_and_original_task_preserved": (
            compacted.messages[0] == checkpoint.messages[0]
            and compacted.messages[1] == checkpoint.messages[1]
        ),
    }


def _benchmark_record(index: int, *, record_count: int) -> MemoryRecord:
    is_target = index == record_count - 1
    return MemoryRecord(
        memory_id=f"memory-{index:08d}",
        memory_type="episodic",
        scope="private",
        layer="working",
        session_id="benchmark-session",
        subject_id="benchmark-user",
        content=(
            "退款进度正在等待银行确认，预计两个工作日到账"
            if is_target
            else f"播放列表状态记录 {index}，当前音乐播放正常"
        ),
        source_event_ids=(f"event-{index:08d}",),
        rule_id="benchmark.v1",
        owner_id="assistant",
        visible_to=("assistant",),
        labels=("private",),
        tags=("target",) if is_target else ("background",),
        salience=0.95 if is_target else 0.5,
        confidence=0.9,
        status="active",
        created_at="2026-07-21T00:00:00+00:00",
        updated_at="2026-07-21T00:00:00+00:00",
        last_event_sequence=index + 1,
        tenant_id="benchmark-tenant",
        user_id="benchmark-user",
        agent_id="assistant",
    )


def _elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1_000


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def _rounded(value: float) -> float:
    return round(float(value), 4)


if __name__ == "__main__":
    main()
