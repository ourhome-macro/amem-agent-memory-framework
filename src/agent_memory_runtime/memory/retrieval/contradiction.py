from __future__ import annotations

import re
import unicodedata

_STATE_MARKERS: dict[str, dict[str, tuple[str, ...]]] = {
    "enabled": {
        "on": (
            "开启",
            "打开",
            "启用",
            "仍然开启",
            "已经开启",
            "已经打开",
            "enabled",
            "turned on",
            "still on",
            "active",
        ),
        "off": (
            "关闭",
            "关掉",
            "禁用",
            "停用",
            "取消",
            "已经关闭",
            "已经禁用",
            "disabled",
            "turned off",
            "inactive",
            "cancelled",
            "canceled",
        ),
    },
    "allowed": {
        "yes": (
            "允许",
            "准许",
            "可以",
            "可用",
            "能用",
            "allowed",
            "permitted",
            "approved",
            "已经批准",
        ),
        "no": (
            "禁止",
            "不允许",
            "不要",
            "不要用",
            "不能",
            "不可",
            "拒绝",
            "forbidden",
            "blocked",
            "被阻止",
            "denied",
            "rejected",
        ),
    },
    "success": {
        "yes": (
            "成功",
            "成功完成",
            "已经完成",
            "通过",
            "完成",
            "succeeded",
            "successful",
            "passed",
            "completed",
        ),
        "no": (
            "失败",
            "任务失败",
            "未完成",
            "未通过",
            "没通过",
            "failed",
            "unsuccessful",
            "did not pass",
        ),
    },
    "resolved": {
        "yes": (
            "已解决",
            "已经解决",
            "解决了",
            "已关闭",
            "resolved",
            "closed",
            "fixed",
        ),
        "no": ("未解决", "仍未解决", "仍然开启", "unresolved", "still open", "open"),
    },
    "paid": {
        "yes": ("已支付", "已经支付", "支付完成", "已付款", "paid", "payment completed"),
        "no": ("未支付", "仍然未支付", "未付款", "unpaid", "payment pending"),
    },
    "temporal_scope": {
        "current": ("当前", "现在", "如今", "current", "currently"),
        "past": ("过去", "曾经", "以前", "previously", "historical"),
    },
}

_STOPWORDS = {
    "is",
    "are",
    "was",
    "were",
    "the",
    "a",
    "an",
    "has",
    "have",
    "been",
    "still",
    "now",
    "already",
    "currently",
    "not",
    "no",
    "longer",
    "已经",
    "仍然",
    "还是",
    "当前",
    "现在",
}


def has_state_conflict(query_text: str, record_text: str) -> bool:
    query = _normalize(query_text)
    record = _normalize(record_text)
    query_signals = _signals(query)
    record_signals = _signals(record)
    if not query_signals or not record_signals:
        return False
    if not any(
        family == other_family and value != other_value
        for family, value in query_signals
        for other_family, other_value in record_signals
    ):
        return False
    return _topic_similarity(query, record) >= 0.25 or bool(
        _identifier_tokens(query) & _identifier_tokens(record)
    )


def _signals(text: str) -> set[tuple[str, str]]:
    signals: set[tuple[str, str]] = set()
    for family, values in _STATE_MARKERS.items():
        hits: list[tuple[str, tuple[int, int]]] = []
        for value, markers in values.items():
            for marker in markers:
                for match in re.finditer(re.escape(marker), text):
                    hits.append((value, match.span()))
        for value, span in hits:
            if any(
                other_value != value
                and other_span != span
                and other_span[0] <= span[0]
                and span[1] <= other_span[1]
                for other_value, other_span in hits
            ):
                continue
            signals.add((family, value))
    return signals


def _topic_similarity(left: str, right: str) -> float:
    left_tokens = _topic_tokens(left)
    right_tokens = _topic_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _topic_tokens(text: str) -> set[str]:
    cleaned = text
    for values in _STATE_MARKERS.values():
        for markers in values.values():
            for marker in markers:
                cleaned = cleaned.replace(marker, " ")
    words = {
        token
        for token in re.findall(r"[a-z0-9_]{2,}", cleaned)
        if token not in _STOPWORDS
    }
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", cleaned))
    words.update(
        token
        for token in (cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
        if token and token not in _STOPWORDS
    )
    return words


def _identifier_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]{3,}", text)
        if token not in _STOPWORDS
    }


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()
