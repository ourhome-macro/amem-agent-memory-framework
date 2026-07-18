from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.audit.snapshot import RuntimeSnapshot


@dataclass(frozen=True)
class ConsistencyReport:
    ok: bool
    expected: dict[str, object] | None
    actual: dict[str, object]
    reason: str | None = None


def compare_snapshots(
    expected: RuntimeSnapshot | dict[str, object] | None,
    actual: RuntimeSnapshot,
) -> ConsistencyReport:
    actual_dict = actual.to_dict()
    if expected is None:
        return ConsistencyReport(ok=True, expected=None, actual=actual_dict)
    expected_dict = expected.to_dict() if isinstance(expected, RuntimeSnapshot) else dict(expected)
    keys = ("rule_version", "config_hash", "last_event_sequence", "state_hash")
    for key in keys:
        if expected_dict.get(key) != actual_dict.get(key):
            return ConsistencyReport(
                ok=False,
                expected=expected_dict,
                actual=actual_dict,
                reason=f"{key}_mismatch",
            )
    return ConsistencyReport(ok=True, expected=expected_dict, actual=actual_dict)

