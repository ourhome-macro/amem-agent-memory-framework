from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from agent_memory_runtime.agent.models import AgentRun, AgentRunEvent


class AgentObserver(Protocol):
    async def on_event(self, event: AgentRunEvent) -> None:
        ...


class AgentRunEvaluator(Protocol):
    async def evaluate(self, run: AgentRun) -> EvaluationResult:
        ...


@dataclass(frozen=True)
class EvaluationResult:
    evaluator: str
    score: float
    passed: bool
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("evaluation score must be between 0 and 1")


class RuntimeMetrics:
    """Dependency-free process metrics; exporters can consume snapshot()."""

    def __init__(self) -> None:
        self._counters: defaultdict[str, int] = defaultdict(int)
        self._totals: defaultdict[str, float] = defaultdict(float)
        self._samples: defaultdict[str, int] = defaultdict(int)
        self._lock = RLock()

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._totals[name] += value
            self._samples[name] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            averages = {
                name: self._totals[name] / count
                for name, count in sorted(self._samples.items())
                if count > 0
            }
            return {
                "counters": dict(sorted(self._counters.items())),
                "averages": averages,
                "samples": dict(sorted(self._samples.items())),
            }
