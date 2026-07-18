from __future__ import annotations


def replay_is_consistent(report: object) -> bool:
    return bool(getattr(report, "ok", False))

