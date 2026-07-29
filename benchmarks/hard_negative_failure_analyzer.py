from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_memory_runtime.memory.semantic_state import (
    StateFact,
    extract_query_state_intent,
    extract_state_fact,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = Path(
    os.environ.get(
        "AMEM_RECALL_DATASET",
        ROOT / "benchmarks" / "data" / "recall_250_balanced_v1.json",
    )
)
REPORT_PATH = Path(
    os.environ.get(
        "AMEM_BENCHMARK_REPORT",
        ROOT / "doc" / "bge-m3-balanced-rerank-benchmark-results.json",
    )
)
ANALYSIS_PATH = Path(
    os.environ.get(
        "AMEM_HARD_NEGATIVE_ANALYSIS",
        ROOT / "doc" / "hard-negative-state-analysis.json",
    )
)
MODE = os.environ.get("AMEM_ANALYZE_MODE", "hybrid_rrf_t0.0")


def main() -> None:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    memories = {str(memory["memory_id"]): str(memory["content"]) for memory in dataset["memories"]}
    cases = {str(case["case_id"]): case for case in dataset["cases"]}

    details = report[MODE]["details"]
    rows: list[dict[str, Any]] = []
    for detail in details:
        case = cases.get(str(detail["case_id"]))
        if case is None or case.get("category") != "hard_negative_state":
            continue
        if detail.get("passed") and not detail.get("forbidden_hits"):
            continue
        rows.append(_analyze_case(case, detail, memories))

    summary = {
        "dataset": str(DATASET_PATH),
        "report": str(REPORT_PATH),
        "mode": MODE,
        "failed_or_forbidden_cases": len(rows),
        "missing_query_attribute": sum(row["query_intent"]["attribute"] is None for row in rows),
        "missing_query_value": sum(row["query_intent"]["expected_value"] is None for row in rows),
        "missing_expected_state": sum(
            any(item["state"] is None for item in row["expected"])
            for row in rows
        ),
        "missing_forbidden_state": sum(
            any(item["state"] is None for item in row["forbidden"])
            for row in rows
        ),
        "cases": rows,
    }
    ANALYSIS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}, indent=2))
    print(f"Analysis written to: {ANALYSIS_PATH}")


def _analyze_case(
    case: dict[str, Any],
    detail: dict[str, Any],
    memories: dict[str, str],
) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "query": case["query"],
        "passed": detail["passed"],
        "forbidden_hits": detail["forbidden_hits"],
        "selected_ids": detail["selected_ids"],
        "query_intent": extract_query_state_intent(str(case["query"])).__dict__,
        "expected": [
            _state_item(memory_id, memories)
            for memory_id in case["ground_truth_memory_ids"]
        ],
        "forbidden": [
            _state_item(memory_id, memories)
            for memory_id in case["forbidden_memory_ids"]
        ],
        "selected": [
            _state_item(_logical_id(memory_id), memories)
            for memory_id in detail["selected_ids"]
        ],
    }


def _state_item(memory_id: str, memories: dict[str, str]) -> dict[str, Any]:
    content = memories.get(memory_id)
    fact = None if content is None else extract_state_fact(content)
    return {
        "memory_id": memory_id,
        "content": content,
        "state": _fact_dict(fact),
    }


def _fact_dict(fact: StateFact | None) -> dict[str, Any] | None:
    if fact is None:
        return None
    return {
        "entity": fact.entity_key,
        "attribute": fact.attribute,
        "value": fact.value,
        "temporal_scope": fact.temporal_scope,
    }


def _logical_id(memory_id: str) -> str:
    return memory_id.rsplit(":", 1)[-1]


if __name__ == "__main__":
    main()
