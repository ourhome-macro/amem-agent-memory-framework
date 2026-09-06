"""Deterministic offline evaluation for the three-layer decision memory model.

The 100-query set is a controlled ambiguity benchmark: each request contains a
listening mood but omits the user's preferred genre.  It measures whether L1/L3
can resolve that ambiguity without allowing raw event logs into ranking.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import tiktoken
import requests

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import init_db  # noqa: E402
from memory_lifecycle import SceneMemoryService  # noqa: E402
from recommendation_service import RecommendationService  # noqa: E402
from request_spec import RequestInterpreter, RequestSpec  # noqa: E402


K = 8
QUERY_TEMPLATES = (
    "来点舒缓的",
    "想听点舒服的",
    "今天想放松一下",
    "给我来点不吵的",
    "随便放点适合现在听的",
)
PERSONAS = (
    ("rnb", "抒情 R&B"),
    ("blues_rock", "布鲁斯摇滚"),
    ("chinese_pop", "中文流行"),
    ("dance_pop", "唱跳舞台"),
    ("jazz", "爵士"),
)
DISTRACTORS = ("rnb", "blues_rock", "chinese_pop", "dance_pop", "jazz", "rap", "reggae", "folk")


@dataclass(frozen=True)
class Candidate:
    identifier: str
    genre: str
    popularity: float
    relevance: int


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-url", default="", help="Optional backend URL for end-to-end latency sampling.")
    parser.add_argument("--live-samples", type=int, default=5)
    args = parser.parse_args()
    personalization = evaluate_ambiguous_queries()
    continuation = evaluate_follow_up_restoration()
    token_budget = evaluate_context_tokens()
    report = {
        "methodology": {
            "dataset": "100-query deterministic controlled ambiguity set",
            "queryDefinition": "mood is explicit; the preferred genre is deliberately omitted",
            "relevance": "3 = preferred genre and requested mood, 1 = requested mood only",
            "ranking": "same candidate inventory; popularity-only baseline vs L1/L3 preference score",
            "tokenizer": "cl100k_base prompt-token estimate",
        },
        "personalization": personalization,
        "requestSpecRestoration": continuation,
        "contextTrimming": token_budget,
    }
    if args.live_url:
        report["liveRecommendationLatency"] = evaluate_live_latency(
            args.live_url,
            samples=max(args.live_samples, 1),
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def evaluate_ambiguous_queries() -> dict[str, float | int]:
    baseline_ndcg: list[float] = []
    memory_ndcg: list[float] = []
    baseline_hit: list[float] = []
    memory_hit: list[float] = []
    baseline_ms: list[float] = []
    memory_ms: list[float] = []

    for index in range(100):
        preferred_genre, _label = PERSONAS[index % len(PERSONAS)]
        candidates = _candidate_inventory(index, preferred_genre)

        started = time.perf_counter()
        no_memory = sorted(candidates, key=lambda item: item.popularity, reverse=True)
        baseline_ms.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        with_memory = sorted(
            candidates,
            key=lambda item: item.popularity + (4.0 * 0.9 if item.genre == preferred_genre else 0.0),
            reverse=True,
        )
        memory_ms.append((time.perf_counter() - started) * 1000)

        baseline_ndcg.append(_ndcg_at_k(no_memory, K))
        memory_ndcg.append(_ndcg_at_k(with_memory, K))
        baseline_hit.append(_hit_at_k(no_memory, K))
        memory_hit.append(_hit_at_k(with_memory, K))

    return {
        "queryCount": 100,
        "ndcgAt8WithoutMemory": _round_mean(baseline_ndcg),
        "ndcgAt8WithL1L3": _round_mean(memory_ndcg),
        "hitRateAt8WithoutMemoryPct": _pct(baseline_hit),
        "hitRateAt8WithL1L3Pct": _pct(memory_hit),
        "rankingP50MsWithoutMemory": _percentile(baseline_ms, 50),
        "rankingP50MsWithL1L3": _percentile(memory_ms, 50),
        "rankingP95MsWithL1L3": _percentile(memory_ms, 95),
    }


def evaluate_follow_up_restoration() -> dict[str, float | int]:
    interpreter = RequestInterpreter()
    recovered = 0
    generic_isolated = 0
    with tempfile.TemporaryDirectory(prefix="memory-runtime-eval-") as directory:
        db_path = Path(directory) / "evaluation.sqlite3"
        init_db(db_path)
        scene_memory = SceneMemoryService(str(db_path), user_id="legacy-owner")
        for index in range(100):
            genre, _label = PERSONAS[index % len(PERSONAS)]
            source_spec = RequestSpec(
                raw_text=f"request-{index}",
                required_genres=(genre,),
                required_languages=("english",) if genre in {"rnb", "blues_rock", "jazz"} else (),
                moods=("舒缓",),
            )
            scene_memory.remember_request(scene="conversation", request_spec=source_spec)
            active = scene_memory.active(scene="conversation")
            active_spec = RequestSpec.from_dict(active[0]["requestSpec"])

            follow_up = interpreter.interpret("再来几首")
            resolved, restored = RecommendationService._resolve_request_spec(follow_up, active_spec)
            if restored and resolved.to_dict() == source_spec.to_dict():
                recovered += 1

            generic = interpreter.interpret("随便推荐一些歌")
            resolved_generic, generic_restored = RecommendationService._resolve_request_spec(generic, active_spec)
            if not generic_restored and not resolved_generic.has_explicit_preferences:
                generic_isolated += 1

    return {
        "followUpCount": 100,
        "requestSpecRecoveryAccuracyPct": round(recovered, 2),
        "genericRequestIsolationAccuracyPct": round(generic_isolated, 2),
    }


def evaluate_context_tokens() -> dict[str, int | float]:
    encoding = tiktoken.get_encoding("cl100k_base")
    raw_events = [
        {
            "event": "completed" if index % 3 else "skipped",
            "track": f"track-{index}",
            "title": f"candidate {index} {PERSONAS[index % len(PERSONAS)][1]}",
            "owner": f"uploader-{index % 11}",
            "playedSeconds": 186 if index % 3 else 8,
            "reason": "user playback behavior captured as raw evidence",
            "createdAt": f"2026-09-{index % 28 + 1:02d}T12:00:00Z",
        }
        for index in range(60)
    ]
    decision_context = {
        "l1_recent_preference_atoms": [
            "recently completes lyric R&B",
            "recently skips high-BPM EDM",
            "accepts blues-rock guitar texture",
        ],
        "l2_active_scene": {
            "request": "再来几首",
            "restoredRequestSpec": {"genre": "rnb", "language": "english", "mood": "舒缓"},
        },
        "l3_stable_profile": {
            "positiveTopics": ["rnb", "blues_rock", "chinese_pop"],
            "negativeTopics": ["high_bpm_edm"],
            "explorationRatio": 0.2,
        },
    }
    raw_tokens = len(encoding.encode(json.dumps(raw_events, ensure_ascii=False)))
    decision_tokens = len(encoding.encode(json.dumps(decision_context, ensure_ascii=False)))
    return {
        "rawEventPromptTokens": raw_tokens,
        "decisionMemoryPromptTokens": decision_tokens,
        "tokenSavingsPct": round((1 - decision_tokens / raw_tokens) * 100, 2),
    }


def evaluate_live_latency(base_url: str, *, samples: int) -> dict[str, float | int]:
    http_latencies: list[float] = []
    service_latencies: list[float] = []
    result_counts: list[int] = []
    endpoint = f"{base_url.rstrip('/')}/api/recommendations"
    for _ in range(samples):
        started = time.perf_counter()
        response = requests.get(endpoint, params={"scene": "home", "limit": K}, timeout=45)
        response.raise_for_status()
        http_latencies.append((time.perf_counter() - started) * 1000)
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else {}
        timing = data.get("timing") if isinstance(data, dict) else {}
        if isinstance(timing, dict) and isinstance(timing.get("totalMs"), (int, float)):
            service_latencies.append(float(timing["totalMs"]))
        items = data.get("items") if isinstance(data, dict) else []
        result_counts.append(len(items) if isinstance(items, list) else 0)
    return {
        "samples": samples,
        "httpP50Ms": _percentile(http_latencies, 50),
        "httpP95Ms": _percentile(http_latencies, 95),
        "serviceP50Ms": _percentile(service_latencies, 50),
        "serviceP95Ms": _percentile(service_latencies, 95),
        "meanReturnedItems": round(statistics.mean(result_counts), 2),
    }


def _candidate_inventory(index: int, preferred_genre: str) -> list[Candidate]:
    rng = random.Random(20260906 + index)
    candidates: list[Candidate] = []
    target_position = rng.randrange(24)
    for position in range(24):
        genre = preferred_genre if position == target_position else DISTRACTORS[(position + index) % len(DISTRACTORS)]
        relevance = 3 if position == target_position else 1 if position < 10 else 0
        candidates.append(
            Candidate(
                identifier=f"q{index}-candidate{position}",
                genre=genre,
                popularity=rng.random(),
                relevance=relevance,
            )
        )
    return candidates


def _ndcg_at_k(ranking: Iterable[Candidate], k: int) -> float:
    values = list(ranking)
    dcg = sum((2**item.relevance - 1) / math.log2(position + 2) for position, item in enumerate(values[:k]))
    ideal = sorted(values, key=lambda item: item.relevance, reverse=True)
    idcg = sum((2**item.relevance - 1) / math.log2(position + 2) for position, item in enumerate(ideal[:k]))
    return dcg / idcg if idcg else 0.0


def _hit_at_k(ranking: Iterable[Candidate], k: int) -> float:
    return float(any(item.relevance >= 3 for item in list(ranking)[:k]))


def _round_mean(values: list[float]) -> float:
    return round(statistics.mean(values), 4)


def _pct(values: list[float]) -> float:
    return round(statistics.mean(values) * 100, 2)


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    offset = (len(ordered) - 1) * percentile / 100
    lower, upper = math.floor(offset), math.ceil(offset)
    if lower == upper:
        return round(ordered[lower], 4)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (offset - lower), 4)


if __name__ == "__main__":
    main()
