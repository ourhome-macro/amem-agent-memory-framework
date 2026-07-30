from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from agent_memory_runtime.config import HybridRetrievalConfig, QueryRouterConfig
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.memory.retrieval.lexical import lexical_tokens
from agent_memory_runtime.memory.semantic_state import extract_query_state_intent

RetrievalMode = Literal[
    "lexical_heavy",
    "vector_heavy",
    "hybrid",
    "state_aware",
    "temporal_aware",
    "strict_no_answer",
]


@dataclass(frozen=True)
class QueryRoute:
    mode: RetrievalMode
    confidence: float
    reasons: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


def route_query(query: MemoryQuery, config: QueryRouterConfig | None = None) -> QueryRoute:
    router_config = config or QueryRouterConfig()
    if not router_config.enabled:
        return QueryRoute("hybrid", 0.0, ("router_disabled",))

    text = _normalize(query.text)
    if not text:
        return QueryRoute("strict_no_answer", 1.0, ("empty_query",))

    reasons: list[str] = []
    exact_score = _exact_score(text, reasons)
    semantic_score = _semantic_score(text, reasons)
    state_score = _state_score(text, reasons)
    temporal_score = _temporal_score(text, reasons)
    no_answer_score = _no_answer_score(text, reasons)

    if state_score >= 2.0 and semantic_score < 1.5:
        return QueryRoute("state_aware", _confidence(state_score, 3.5), tuple(reasons))
    if temporal_score >= 2.0 and exact_score < 3.0:
        return QueryRoute("temporal_aware", _confidence(temporal_score, 3.0), tuple(reasons))
    if no_answer_score >= 2.0 and exact_score == 0 and semantic_score <= 1.0:
        return QueryRoute("strict_no_answer", _confidence(no_answer_score, 3.5), tuple(reasons))
    if exact_score >= 3.0 and "entity_lookup" in reasons:
        return QueryRoute("lexical_heavy", _confidence(exact_score, 4.0), tuple(reasons))
    if exact_score >= 3.0 and semantic_score < 2.0:
        return QueryRoute("lexical_heavy", _confidence(exact_score, 4.0), tuple(reasons))
    if exact_score >= 1.5 and semantic_score <= 0.5:
        return QueryRoute("lexical_heavy", _confidence(exact_score + 1.0, 4.0), tuple(reasons))
    if semantic_score >= 1.5 and exact_score < 2.0:
        return QueryRoute("vector_heavy", _confidence(semantic_score, 4.0), tuple(reasons))
    return QueryRoute("hybrid", 0.55, tuple(reasons or ["default_hybrid"]))


def route_hybrid_config(
    base: HybridRetrievalConfig,
    route: QueryRoute,
    router_config: QueryRouterConfig,
) -> HybridRetrievalConfig:
    if not router_config.enabled:
        return base
    if route.mode == "lexical_heavy":
        return _replace_hybrid(
            base,
            lexical_weight=router_config.lexical_heavy_lexical_weight,
            semantic_weight=router_config.lexical_heavy_semantic_weight,
            semantic_candidate_limit=_scaled_limit(
                base.semantic_candidate_limit,
                router_config.lexical_heavy_semantic_limit_factor,
            ),
        )
    if route.mode == "vector_heavy":
        return _replace_hybrid(
            base,
            lexical_weight=router_config.vector_heavy_lexical_weight,
            semantic_weight=router_config.vector_heavy_semantic_weight,
            lexical_candidate_limit=_scaled_limit(
                base.lexical_candidate_limit,
                router_config.vector_heavy_lexical_limit_factor,
            ),
        )
    if route.mode == "state_aware":
        return _replace_hybrid(
            base,
            lexical_weight=router_config.state_lexical_weight,
            semantic_weight=router_config.state_semantic_weight,
        )
    if route.mode == "temporal_aware":
        return _replace_hybrid(
            base,
            lexical_weight=router_config.temporal_lexical_weight,
            semantic_weight=router_config.temporal_semantic_weight,
        )
    if route.mode == "strict_no_answer":
        return _replace_hybrid(
            base,
            lexical_weight=0.6,
            semantic_weight=0.8,
            lexical_candidate_limit=_scaled_limit(base.lexical_candidate_limit, 0.5),
            semantic_candidate_limit=_scaled_limit(base.semantic_candidate_limit, 0.5),
        )
    return _replace_hybrid(
        base,
        lexical_weight=router_config.hybrid_lexical_weight,
        semantic_weight=router_config.hybrid_semantic_weight,
    )


def _replace_hybrid(base: HybridRetrievalConfig, **updates: object) -> HybridRetrievalConfig:
    values = {
        "enable_lexical": base.enable_lexical,
        "enable_semantic": base.enable_semantic,
        "lexical_candidate_limit": base.lexical_candidate_limit,
        "semantic_candidate_limit": base.semantic_candidate_limit,
        "rrf_k": base.rrf_k,
        "lexical_weight": base.lexical_weight,
        "semantic_weight": base.semantic_weight,
        "semantic_timeout_ms": base.semantic_timeout_ms,
        "query_cache_size": base.query_cache_size,
        "embedding_coverage_cache_seconds": base.embedding_coverage_cache_seconds,
        "min_semantic_similarity": base.min_semantic_similarity,
        "semantic_failure_threshold": base.semantic_failure_threshold,
        "semantic_cooldown_seconds": base.semantic_cooldown_seconds,
        "semantic_max_concurrency": base.semantic_max_concurrency,
        "allow_uncalibrated_semantic": base.allow_uncalibrated_semantic,
    }
    values.update(updates)
    return HybridRetrievalConfig(**values)


def _scaled_limit(value: int, factor: float) -> int:
    return max(1, int(round(value * factor)))


def _exact_score(text: str, reasons: list[str]) -> float:
    score = 0.0
    if re.search(r"\b[A-Z]{1,8}-\d{2,}\b", text, flags=re.IGNORECASE):
        score += 3.0
        reasons.append("exact_identifier")
    if re.search(r"\b[A-Z][A-Za-z0-9_]{2,}\b", text):
        score += 1.0
        reasons.append("symbol_token")
    if any(marker in text for marker in ("负责人", "找谁", "谁负责", "联系人", "owner")):
        score += 3.0
        reasons.append("entity_lookup")
    if any(marker in text for marker in ("k 值", "k值", "字段", "函数", "class", "method")):
        score += 1.5
        reasons.append("field_or_code_lookup")
    return score


def _semantic_score(text: str, reasons: list[str]) -> float:
    score = 0.0
    ascii_count = sum(1 for char in text if char.isascii() and char.isalpha())
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    if ascii_count >= 8 and cjk_count >= 2:
        score += 2.0
        reasons.append("cross_lingual_mix")
    if ascii_count >= 12 and cjk_count == 0 and not _has_exact_identifier(text):
        score += 1.4
        reasons.append("english_natural_query")
    tokens = lexical_tokens(text)
    if len(tokens) <= 3 and len(text) >= 8:
        score += 1.0
        reasons.append("low_keyword_density")
    if any(
        marker in text
        for marker in (
            "为什么",
            "怎么",
            "如何",
            "区别",
            "是不是",
            "能不能",
            "到底",
            "靠哪个",
            "哪个字段",
            "由模型",
            "怎么办",
            "别把",
            "what protects",
            "how does",
            "why",
            "explain",
        )
    ):
        score += 1.5
        reasons.append("semantic_question")
    if len(text) >= 28 and not re.search(r"\b[A-Z]{1,8}-\d{2,}\b", text, flags=re.IGNORECASE):
        score += 0.8
        reasons.append("natural_language_long_form")
    return score


def _state_score(text: str, reasons: list[str]) -> float:
    intent = extract_query_state_intent(text)
    score = 0.0
    if intent.attribute is not None:
        score += 2.0
        reasons.append("state_attribute")
    if intent.expected_value is not None:
        score += 0.8
        reasons.append("state_value")
    if any(
        marker in text
        for marker in (
            "现在",
            "当前",
            "仍然",
            "已经",
            "是否",
            "了吗",
            "开着",
            "关闭",
            "开启",
            "能继续",
            "active",
            "inactive",
            "enabled",
            "disabled",
        )
    ):
        score += 0.8
        reasons.append("state_cue")
    return score


def _temporal_score(text: str, reasons: list[str]) -> float:
    score = 0.0
    if any(
        marker in text
        for marker in (
            "现在",
            "当前",
            "过去",
            "以前",
            "曾经",
            "未来",
            "以后",
            "明年",
            "上次",
            "什么时候",
            "后续",
            "计划",
            "when",
            "before",
            "after",
            "currently",
            "previously",
            "future",
        )
    ):
        score += 2.0
        reasons.append("temporal_cue")
    return score


def _no_answer_score(text: str, reasons: list[str]) -> float:
    score = 0.0
    if any(
        marker in text
        for marker in (
            "cvv",
            "密码",
            "密钥",
            "根密钥",
            "许可证",
            "火星",
            "月球",
            "量子航运",
            "passport",
            "secret",
        )
    ):
        score += 2.0
        reasons.append("no_answer_or_sensitive_cue")
    if len(lexical_tokens(text)) <= 2:
        score += 0.7
        reasons.append("too_few_terms")
    return score


def _confidence(score: float, ceiling: float) -> float:
    return round(min(0.95, max(0.1, score / ceiling)), 4)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _has_exact_identifier(text: str) -> bool:
    return bool(re.search(r"\b[A-Z]{1,8}-\d{2,}\b", text, flags=re.IGNORECASE))
