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
            "disabled",
            "turned off",
            "inactive",
            "cancelled",
            "canceled",
        ),
    },
    "allowed": {
        "yes": ("允许", "准许", "可用", "allowed", "permitted", "approved"),
        "no": ("禁止", "不允许", "拒绝", "forbidden", "blocked", "denied", "rejected"),
    },
    "success": {
        "yes": ("成功", "通过", "完成", "succeeded", "successful", "passed", "completed"),
        "no": ("失败", "未通过", "没通过", "failed", "unsuccessful", "did not pass"),
    },
    "resolved": {
        "yes": ("已解决", "解决了", "已关闭", "resolved", "closed", "fixed"),
        "no": ("未解决", "仍未解决", "仍然开启", "unresolved", "still open", "open"),
    },
    "paid": {
        "yes": ("已支付", "已付款", "paid", "payment completed"),
        "no": ("未支付", "未付款", "unpaid", "payment pending"),
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
    return _topic_similarity(query, record) >= 0.25


def _signals(text: str) -> set[tuple[str, str]]:
    signals: set[tuple[str, str]] = set()
    for family, values in _STATE_MARKERS.items():
        for value, markers in values.items():
            if any(marker in text for marker in markers):
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


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()
