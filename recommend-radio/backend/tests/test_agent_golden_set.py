from __future__ import annotations

import json

from run_agent_evals import (
    DEFAULT_GATES,
    DEFAULT_GOLDEN_DIR,
    _load_cases,
    evaluate_cases,
    evaluate_gates,
)


def test_agent_golden_set_passes_regression_gates() -> None:
    cases, golden_hash = _load_cases(DEFAULT_GOLDEN_DIR)
    report = evaluate_cases(cases, golden_hash=golden_hash)
    gates = json.loads(DEFAULT_GATES.read_text(encoding="utf-8"))
    gate_result = evaluate_gates(report, gates)
    assert len(cases) == 27
    assert report["failedCount"] == 0
    assert gate_result["passed"] is True
