from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

DATASET_PATH = Path("benchmarks/data/recall_100_v1.json")


def test_recall_100_dataset_has_expected_manual_labels() -> None:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
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
