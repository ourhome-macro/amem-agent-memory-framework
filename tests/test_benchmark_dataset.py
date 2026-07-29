from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

LEGACY_DATASET_PATH = Path("benchmarks/data/recall_100_v1.json")
MAIN_DATASET_PATH = Path("benchmarks/data/recall_250_v1.json")
BALANCED_DATASET_PATH = Path("benchmarks/data/recall_250_balanced_v1.json")
HOLDOUT_DATASET_PATH = Path("benchmarks/data/recall_holdout_50_v1.json")


def test_recall_100_dataset_has_expected_manual_labels() -> None:
    dataset = json.loads(LEGACY_DATASET_PATH.read_text(encoding="utf-8"))
    items = dataset["items"]
    memory_ids = [item["memory_id"] for item in items]
    memory_id_set = set(memory_ids)

    assert len(items) == 100
    assert len(memory_id_set) == 100
    assert Counter(item["category"] for item in items) == {
        "semantic_preference": 30,
        "relationship": 20,
        "episodic": 20,
        "profile": 20,
        "temporal": 10,
    }

    for item in items:
        assert item["query"].strip()
        assert item["content"].strip()
        assert item["ground_truth_memory_id"] == item["memory_id"]
        assert item.get("k", 5) == 5

        for forbidden_memory_id in item.get("forbidden_memory_ids", []):
            assert forbidden_memory_id in memory_id_set
            assert forbidden_memory_id != item["memory_id"]


def test_recall_250_dataset_has_harder_coverage() -> None:
    dataset = json.loads(MAIN_DATASET_PATH.read_text(encoding="utf-8"))

    _assert_dataset_shape(
        dataset,
        expected_cases=250,
        expected_counts={
            "semantic_preference": 30,
            "relationship": 20,
            "episodic": 20,
            "profile": 20,
            "temporal": 10,
            "hard_negative_state": 40,
            "temporal_shift": 25,
            "near_entity_scope": 25,
            "cross_lingual": 20,
            "semantic_paraphrase_hard": 20,
            "no_answer": 20,
        },
        min_forbidden_cases=90,
        expected_no_answer=20,
        max_near_copy_cases=5,
    )


def test_holdout_dataset_is_separate_and_hard() -> None:
    dataset = json.loads(HOLDOUT_DATASET_PATH.read_text(encoding="utf-8"))

    _assert_dataset_shape(
        dataset,
        expected_cases=50,
        expected_counts={
            "hard_negative_state": 12,
            "temporal_shift": 8,
            "near_entity_scope": 8,
            "cross_lingual": 8,
            "semantic_paraphrase_hard": 8,
            "no_answer": 6,
        },
        min_forbidden_cases=25,
        expected_no_answer=6,
        max_near_copy_cases=1,
    )


def test_recall_250_balanced_dataset_mixes_easy_medium_hard_cases() -> None:
    dataset = json.loads(BALANCED_DATASET_PATH.read_text(encoding="utf-8"))

    _assert_dataset_shape(
        dataset,
        expected_cases=250,
        expected_counts={
            "semantic_preference": 30,
            "relationship": 20,
            "episodic": 20,
            "profile": 20,
            "temporal": 10,
            "natural_rewrite": 30,
            "cross_lingual": 20,
            "semantic_paraphrase_hard": 20,
            "hard_negative_state": 30,
            "temporal_shift": 20,
            "near_entity_scope": 15,
            "no_answer": 15,
        },
        min_forbidden_cases=65,
        expected_no_answer=15,
        max_near_copy_cases=5,
    )
    assert dataset["difficulty_counts"] == {
        "simple": 100,
        "medium": 70,
        "hard": 65,
        "no_answer": 15,
    }


def _assert_dataset_shape(
    dataset: dict,
    *,
    expected_cases: int,
    expected_counts: dict[str, int],
    min_forbidden_cases: int,
    expected_no_answer: int,
    max_near_copy_cases: int,
) -> None:
    memories = dataset["memories"]
    cases = dataset["cases"]
    memory_ids = {memory["memory_id"] for memory in memories}

    assert len(cases) == expected_cases
    assert len(memory_ids) == len(memories)
    assert Counter(case["category"] for case in cases) == expected_counts
    assert sum(bool(case.get("forbidden_memory_ids")) for case in cases) >= min_forbidden_cases
    assert sum(not case.get("ground_truth_memory_ids") for case in cases) == expected_no_answer
    assert _near_copy_case_count(dataset) <= max_near_copy_cases

    for memory in memories:
        assert memory["content"].strip()

    for case in cases:
        assert case["query"].strip()
        assert case.get("k", 5) == 5
        for memory_id in case.get("ground_truth_memory_ids", []):
            assert memory_id in memory_ids
        for forbidden_memory_id in case.get("forbidden_memory_ids", []):
            assert forbidden_memory_id in memory_ids
            assert forbidden_memory_id not in case.get("ground_truth_memory_ids", [])


def _near_copy_case_count(dataset: dict) -> int:
    memories = {memory["memory_id"]: memory["content"] for memory in dataset["memories"]}
    count = 0
    for case in dataset["cases"]:
        expected = case.get("ground_truth_memory_ids") or []
        if not expected:
            continue
        query = _compact_text(case["query"])
        content = _compact_text(" ".join(memories[memory_id] for memory_id in expected))
        if query and (query in content or content in query):
            count += 1
    return count


def _compact_text(value: str) -> str:
    return "".join(re.findall(r"[\w\u4e00-\u9fff]", value.casefold()))
