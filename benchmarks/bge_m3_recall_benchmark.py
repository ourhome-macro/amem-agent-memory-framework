#!/usr/bin/env python3
# ruff: noqa: E402, E501
"""
BGE-M3 Recall Benchmark — FTS5-only vs Vector-only vs Hybrid-RRF.

Uses real BGE-M3 embeddings (1024-dim, cosine) via sentence-transformers (CPU).
Generates an expanded test set (~60 memories, ~35 cases) and evaluates
Recall@K, Precision@K, MRR, nDCG@K, and forbidden-hit count across three
retrieval modes and multiple similarity thresholds.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sentence_transformers import SentenceTransformer

from agent_memory_runtime.config import HybridRetrievalConfig, RuntimeConfig
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.evals import evaluate_retrieval
from agent_memory_runtime.memory.embeddings import (
    CallableEmbeddingProvider,
    VectorRecord,
    canonical_memory_text,
    embedding_content_hash,
)
from agent_memory_runtime.memory.embeddings.models import EmbeddingSpec
from agent_memory_runtime.memory.intake import MemoryIntakeService, MemoryToolIdentity
from agent_memory_runtime.memory.retrieval import (
    HybridCandidateRetriever,
    SemanticRetriever,
    StoreLexicalRetriever,
)
from agent_memory_runtime.memory.retrieval.candidates import (
    CandidateBatch,
    CandidateHit,
)
from agent_memory_runtime.memory.retrieval.pipeline import RetrievalPipeline
from agent_memory_runtime.memory.stores import SQLiteStoreBundle
from agent_memory_runtime.runtime import AgentMemoryRuntime

# ══════════════════════════════════════════════════════════════
# BGE-M3 embedding spec
# ══════════════════════════════════════════════════════════════

DIMENSIONS = 1024
SPEC = EmbeddingSpec(
    provider="bge-m3-local",
    model_id="BAAI/bge-m3",
    dimensions=DIMENSIONS,
    distance_metric="cosine",
    normalized=True,
)
RECALL_DATASET_PATH = Path(
    os.environ.get(
        "AMEM_RECALL_DATASET",
        ROOT / "benchmarks" / "data" / "recall_250_balanced_v1.json",
    )
)
BENCHMARK_REPORT_PATH = Path(
    os.environ.get("AMEM_BENCHMARK_REPORT", ROOT / "doc" / "bge-m3-benchmark-results.json")
)

# ══════════════════════════════════════════════════════════════
# Expanded event data — mix of target + distractor memories
# ══════════════════════════════════════════════════════════════

def _load_existing_events() -> list[dict[str, Any]]:
    """Load all existing JSONL event files."""
    events: list[dict[str, Any]] = []
    data_dir = ROOT / "examples" / "data"
    for jsonl_file in sorted(data_dir.glob("*.jsonl")):
        for line in jsonl_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _load_recall_dataset() -> dict[str, Any] | None:
    if not RECALL_DATASET_PATH.exists():
        return None
    return json.loads(RECALL_DATASET_PATH.read_text(encoding="utf-8"))


def _dataset_events(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    memories = dataset.get("memories") or dataset.get("items", [])
    for item in memories:
        if not item.get("content"):
            continue
        events.append(
            {
                "event_id": str(item["memory_id"]),
                "kind": "belief.stated",
                "actor_id": "alice",
                "session_id": str(item.get("session_id") or "recall-v1"),
                "tenant_id": "eval-tenant",
                "user_id": "alice",
                "agent_id": "companion",
                "labels": ["private"],
                "payload": {
                    "agent_id": "companion",
                    "subject_id": "alice",
                    "key": str(item["memory_id"]),
                    "belief": str(item["content"]),
                    "salience": float(item.get("salience") or 0.7),
                    "confidence": float(item.get("confidence") or 0.9),
                },
            }
        )
    return events


def _dataset_cases(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    if dataset.get("cases"):
        for item in dataset["cases"]:
            cases.append(
                {
                    "id": str(item["case_id"]),
                    "category": str(item["category"]),
                    "query": str(item["query"]),
                    "expected_memory_ids": list(item.get("ground_truth_memory_ids") or []),
                    "forbidden_memory_ids": list(item.get("forbidden_memory_ids") or []),
                    "session": str(item.get("session_id") or "recall-v1"),
                    "k": int(item.get("k") or 5),
                }
            )
        return cases
    for item in dataset.get("items", []):
        cases.append(
            {
                "id": f"case_{item['memory_id']}",
                "category": str(item["category"]),
                "query": str(item["query"]),
                "expected_content": str(item["content"]),
                "forbidden_memory_ids": list(item.get("forbidden_memory_ids") or []),
                "session": str(item.get("session_id") or "recall-v1"),
                "k": int(item.get("k") or 5),
            }
        )
    return cases


# New distractor + target events to expand the test set.
# These add "haystack" noise and a few new "needles" for eval cases.
NEW_EVENTS: list[dict[str, Any]] = [
    # ── Music distractors ──
    {"event_id": "d-music-1", "kind": "message.created", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "播放列表添加了十首爵士乐曲目用于夜间放松", "salience": 0.5}},
    {"event_id": "d-music-2", "kind": "preference.updated", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "music_genre", "preference": "下班后听爵士乐帮助专注", "value": "jazz", "salience": 0.7}},
    {"event_id": "d-music-3", "kind": "message.created", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "音响设备低音炮音量调节到百分之四十比较合适", "salience": 0.4}},

    # ── Travel distractors ──
    {"event_id": "d-travel-1", "kind": "message.created", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "十月份去东京旅行计划住在新宿附近交通便利的酒店", "salience": 0.6}},
    {"event_id": "d-travel-2", "kind": "preference.updated", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "travel_seat", "preference": "长途飞行偏好靠走道座位方便活动", "value": "aisle", "salience": 0.75}},
    {"event_id": "d-travel-3", "kind": "message.created", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "机场快线乘坐指南已保存到备忘录", "salience": 0.3}},

    # ── Cooking distractors ──
    {"event_id": "d-cook-1", "kind": "message.created", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "红烧肉需要小火慢炖两个小时才能入味", "salience": 0.4}},
    {"event_id": "d-cook-2", "kind": "message.created", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "橄榄油和蒜末是意大利面的基础调料组合", "salience": 0.35}},

    # ── Work distractors ──
    {"event_id": "d-work-1", "kind": "message.created", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "项目周定于每周一上午十点召开需要准备进度汇报", "salience": 0.55}},
    {"event_id": "d-work-2", "kind": "task.outcome", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "task": "project meeting", "result": "success", "outcome": "会议纪要提前发送给参会者可以提升讨论效率", "salience": 0.6}},

    # ── Health distractors ──
    {"event_id": "d-health-1", "kind": "message.created", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "体检报告显示维生素D偏低需要补充", "salience": 0.5}},
    {"event_id": "d-health-2", "kind": "preference.updated", "actor_id": "alice", "session_id": "distractor-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "exercise_time", "preference": "早晨七点跑步三公里比较舒服", "value": "morning", "salience": 0.65}},

    # ── Additional semantic target events ──
    {"event_id": "t-semantic-1", "kind": "message.created", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "服务器磁盘空间不足需要清理日志文件", "salience": 0.7}},
    {"event_id": "t-semantic-2", "kind": "message.created", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "团队协作工具切换到了飞书集成文档功能", "salience": 0.6}},
    {"event_id": "t-semantic-3", "kind": "message.created", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "快递包裹放在了门口鞋柜旁边的储物箱里", "salience": 0.55}},
    {"event_id": "t-semantic-4", "kind": "message.created", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "电费缴纳方式从线下营业厅改成了手机自动扣款", "salience": 0.5}},
    {"event_id": "t-semantic-5", "kind": "message.created", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "学习计划安排了每天背五十个英语单词", "salience": 0.5}},

    # ── Cross-tenant distractor (for authorization cases) ──
    {"event_id": "t-auth-1", "kind": "message.created", "actor_id": "mallory", "session_id": "semantic-session", "tenant_id": "other-tenant", "user_id": "mallory", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "mallory", "text": "跨租户敏感代号 ORBIT-8842 标记为机密", "salience": 0.99}},

    # ── Cross-agent distractor ──
    {"event_id": "t-auth-2", "kind": "message.created", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "other-agent", "labels": ["private"], "payload": {"agent_id": "other-agent", "subject_id": "alice", "text": "代理私有标记 AGENT-7711 仅限内部使用", "salience": 0.99}},

    # ── Cross-session distractor ──
    {"event_id": "t-auth-3", "kind": "message.created", "actor_id": "alice", "session_id": "other-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "会话隔离口令 SESSION-6633 不应跨会话可见", "salience": 0.99}},

    # ── Negation pair ──
    {"event_id": "t-neg-1", "kind": "message.created", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "自动续费已经关闭", "salience": 0.8}},
    {"event_id": "t-neg-2", "kind": "message.created", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "text": "自动续费仍然开启", "salience": 0.8}},
]

HARD_NEGATIVE_EVENTS: list[dict[str, Any]] = [
    {"event_id": "hn-renewal-off", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "automatic_renewal_off", "belief": "Automatic renewal is turned off."}},
    {"event_id": "hn-renewal-on", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "automatic_renewal_on", "belief": "Automatic renewal is still on."}},
    {"event_id": "hn-mfa-enabled", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "mfa_enabled", "belief": "Two factor authentication is enabled."}},
    {"event_id": "hn-mfa-disabled", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "mfa_disabled", "belief": "Two factor authentication is disabled."}},
    {"event_id": "hn-backup-success", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "backup_success", "belief": "Nightly backup completed successfully."}},
    {"event_id": "hn-backup-failed", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "backup_failed", "belief": "Nightly backup failed."}},
    {"event_id": "hn-invoice-paid", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "invoice_paid", "belief": "Invoice INV-42 is paid."}},
    {"event_id": "hn-invoice-unpaid", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "invoice_unpaid", "belief": "Invoice INV-42 is unpaid."}},
    {"event_id": "hn-deploy-approved", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "deploy_approved", "belief": "Production deployment is approved."}},
    {"event_id": "hn-deploy-blocked", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "deploy_blocked", "belief": "Production deployment is blocked."}},
    {"event_id": "hn-ticket-resolved", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "ticket_resolved", "belief": "Support ticket T-900 is resolved."}},
    {"event_id": "hn-ticket-unresolved", "kind": "belief.stated", "actor_id": "alice", "session_id": "semantic-session", "tenant_id": "eval-tenant", "user_id": "alice", "agent_id": "companion", "labels": ["private"], "payload": {"agent_id": "companion", "subject_id": "alice", "key": "ticket_unresolved", "belief": "Support ticket T-900 is unresolved."}},
]


# ══════════════════════════════════════════════════════════════
# Expanded eval cases
# ══════════════════════════════════════════════════════════════
# Each case matches expected/forbidden by content substring (more robust than hard IDs).

# Each case is a plain dict with content-substring matching (more robust than hard IDs).
CASES: list[dict[str, Any]] = [
    # ── 1. Semantic paraphrase (Chinese→Chinese, zero/minimal lexical overlap) ──
    {"id": "para_1_release_schedule", "category": "semantic_paraphrase", "query": "产品何时可以发布", "expected_content": "上线窗口顺延至本周五", "k": 3},
    {"id": "para_2_disk_space", "category": "semantic_paraphrase", "query": "硬盘容量不够了怎么办", "expected_content": "服务器磁盘空间不足需要清理日志文件", "k": 3},
    {"id": "para_3_delivery_location", "category": "semantic_paraphrase", "query": "快递放哪了", "expected_content": "快递包裹放在了门口鞋柜旁边的储物箱里", "k": 3},
    {"id": "para_4_utility_payment", "category": "semantic_paraphrase", "query": "水电费怎么交的", "expected_content": "电费缴纳方式从线下营业厅改成了手机自动扣款", "k": 3},
    {"id": "para_5_study_plan", "category": "semantic_paraphrase", "query": "语言学习安排", "expected_content": "学习计划安排了每天背五十个英语单词", "k": 3},

    # ── 2. Cross-lingual (Chinese↔English) ──
    {"id": "xlang_1_car_service", "category": "cross_language", "query": "Where is my car service appointment?", "expected_content": "车辆保养预约在北辰维修中心", "k": 3},
    {"id": "xlang_2_release", "category": "cross_language", "query": "When can the product be released?", "expected_content": "上线窗口顺延至本周五", "k": 3},
    {"id": "xlang_3_disk", "category": "cross_language", "query": "Server storage is running low", "expected_content": "服务器磁盘空间不足需要清理日志文件", "k": 3},
    {"id": "xlang_4_music", "category": "cross_language", "query": "What music do I listen to after work?", "expected_content": "下班后听爵士乐帮助专注", "k": 3},

    # ── 3. Exact identifier (FTS5 should excel) ──
    {"id": "exact_1_order_id", "category": "exact_identifier", "query": "ZX-49271", "expected_content": "订单 ZX-49271", "k": 1},
    {"id": "exact_2_orbit", "category": "exact_identifier", "query": "ORBIT-8842", "expect_empty": True, "forbidden_content": "ORBIT-8842"},
    {"id": "exact_3_agent", "category": "exact_identifier", "query": "AGENT-7711", "expect_empty": True, "forbidden_content": "AGENT-7711"},
    {"id": "exact_4_session", "category": "exact_identifier", "query": "SESSION-6633", "expect_empty": True, "forbidden_content": "SESSION-6633"},

    # ── 4. Negation hard negative ──
    {"id": "neg_1_renewal_off", "category": "hard_negative", "query": "自动续费已经关闭", "expected_content": "自动续费已经关闭", "forbidden_content": "自动续费仍然开启", "k": 1},
    {"id": "neg_2_renewal_on", "category": "hard_negative", "query": "自动续费仍然开启", "expected_content": "自动续费仍然开启", "forbidden_content": "自动续费已经关闭", "k": 1},

    # ── 5. Authorization boundaries ──
    {"id": "auth_1_cross_tenant", "category": "authorization", "query": "ORBIT-8842", "expect_empty": True, "forbidden_content": "ORBIT-8842"},
    {"id": "auth_2_cross_agent", "category": "authorization", "query": "AGENT-7711", "expect_empty": True, "forbidden_content": "AGENT-7711"},
    {"id": "auth_3_cross_session", "category": "authorization", "query": "SESSION-6633", "expect_empty": True, "forbidden_content": "SESSION-6633"},

    # ── 6. No-result calibration ──
    {"id": "none_1_quantum", "category": "no_result", "query": "不存在的量子航运许可证", "expect_empty": True, "k": 3},
    {"id": "none_2_mars", "category": "no_result", "query": "火星殖民地建设进度报告", "expect_empty": True, "k": 3},
    {"id": "none_3_ancient", "category": "no_result", "query": "古巴比伦空中花园修复方案", "expect_empty": True, "k": 3},

    # ── 7. Preference recall (core layer, cross-session) ──
    {"id": "pref_1_response_style", "category": "preference_recall", "query": "请按我的偏好回答，简洁一点", "expected_content": "回答保持简洁", "forbidden_content": "回答需要非常详细", "k": 5, "session": "current-session"},
    {"id": "pref_2_music_genre", "category": "preference_recall", "query": "我下班后喜欢听什么", "expected_content": "下班后听爵士乐", "k": 5, "session": "distractor-session"},
    {"id": "pref_3_travel_seat", "category": "preference_recall", "query": "我坐飞机有什么偏好", "expected_content": "长途飞行偏好靠走道座位", "k": 5, "session": "distractor-session"},

    # ── 8. Strategy recall (core layer) ──
    {"id": "strat_1_refund", "category": "strategy_recall", "query": "退款处理有什么经验教训", "expected_content": "Checking payment gateway settlement", "k": 5, "agent": "support_agent", "tenant": "default", "user": "", "session": "support-001", "session_policy": "exact"},

    # ── 9. Episodic recall ──
    {"id": "epi_1_refund_progress", "category": "episodic_recall", "query": "退款进度怎么样", "expected_content": "退款进度已经进入银行处理阶段", "k": 3, "session": "current-session"},
    {"id": "epi_2_concert", "category": "episodic_recall", "query": "还记得上次演唱会订票吗", "expected_content": "演唱会订票选择了靠近舞台左侧", "k": 5, "session": "current-session"},

    # ── 10. Semantic synonym ──
    {"id": "syn_1_payment_method", "category": "semantic_synonym", "query": "付款方式偏好", "expected_content": "refund updates by email", "k": 5, "agent": "support_agent", "tenant": "default", "user": "", "session": "support-001", "session_policy": "exact"},
    {"id": "syn_2_collaboration_tool", "category": "semantic_synonym", "query": "团队协作平台选了什么", "expected_content": "团队协作工具切换到了飞书", "k": 3},
    {"id": "syn_3_workout_time", "category": "semantic_synonym", "query": "运动时间安排", "expected_content": "早晨七点跑步三公里", "k": 3, "session": "distractor-session"},
]

HARD_NEGATIVE_CASES: list[dict[str, Any]] = [
    {"id": "hn_renewal_off", "category": "hard_negative", "query": "Automatic renewal is turned off", "expected_content": "Automatic renewal is turned off.", "forbidden_content": "Automatic renewal is still on.", "k": 1},
    {"id": "hn_renewal_on", "category": "hard_negative", "query": "Automatic renewal is still on", "expected_content": "Automatic renewal is still on.", "forbidden_content": "Automatic renewal is turned off.", "k": 1},
    {"id": "hn_mfa_enabled", "category": "hard_negative", "query": "Two factor authentication is enabled", "expected_content": "Two factor authentication is enabled.", "forbidden_content": "Two factor authentication is disabled.", "k": 1},
    {"id": "hn_mfa_disabled", "category": "hard_negative", "query": "Two factor authentication is disabled", "expected_content": "Two factor authentication is disabled.", "forbidden_content": "Two factor authentication is enabled.", "k": 1},
    {"id": "hn_backup_success", "category": "hard_negative", "query": "Nightly backup completed successfully", "expected_content": "Nightly backup completed successfully.", "forbidden_content": "Nightly backup failed.", "k": 1},
    {"id": "hn_backup_failed", "category": "hard_negative", "query": "Nightly backup failed", "expected_content": "Nightly backup failed.", "forbidden_content": "Nightly backup completed successfully.", "k": 1},
    {"id": "hn_invoice_paid", "category": "hard_negative", "query": "Invoice INV-42 is paid", "expected_content": "Invoice INV-42 is paid.", "forbidden_content": "Invoice INV-42 is unpaid.", "k": 1},
    {"id": "hn_invoice_unpaid", "category": "hard_negative", "query": "Invoice INV-42 is unpaid", "expected_content": "Invoice INV-42 is unpaid.", "forbidden_content": "Invoice INV-42 is paid.", "k": 1},
    {"id": "hn_deploy_approved", "category": "hard_negative", "query": "Production deployment is approved", "expected_content": "Production deployment is approved.", "forbidden_content": "Production deployment is blocked.", "k": 1},
    {"id": "hn_deploy_blocked", "category": "hard_negative", "query": "Production deployment is blocked", "expected_content": "Production deployment is blocked.", "forbidden_content": "Production deployment is approved.", "k": 1},
    {"id": "hn_ticket_resolved", "category": "hard_negative", "query": "Support ticket T-900 is resolved", "expected_content": "Support ticket T-900 is resolved.", "forbidden_content": "Support ticket T-900 is unresolved.", "k": 1},
    {"id": "hn_ticket_unresolved", "category": "hard_negative", "query": "Support ticket T-900 is unresolved", "expected_content": "Support ticket T-900 is unresolved.", "forbidden_content": "Support ticket T-900 is resolved.", "k": 1},
]

CASES.extend(HARD_NEGATIVE_CASES)


# ══════════════════════════════════════════════════════════════
# Vector-only retriever wrapper
# ══════════════════════════════════════════════════════════════

class VectorOnlyRetriever:
    """Wraps SemanticRetriever to implement the CandidateRetriever protocol."""

    def __init__(self, semantic: SemanticRetriever) -> None:
        self._semantic = semantic
        self._rrf_k = 60

    def retrieve(self, query: MemoryQuery, *, limit: int) -> CandidateBatch:
        if not query.text.strip():
            return CandidateBatch(hits=())
        hits, emb_ms, vec_ms, coverage = self._semantic.retrieve(query, limit=min(limit, 64))
        max_rel = (self._rrf_k + 1) / (self._rrf_k + 1)  # = 1.0 for rank 1
        candidate_hits = tuple(
            CandidateHit(
                memory_id=hit.memory_id,
                sources=("semantic",),
                semantic_rank=i + 1,
                semantic_similarity=hit.semantic_similarity,
                semantic_relevance=(self._rrf_k + 1) / (self._rrf_k + i + 1),
                fusion_score=((self._rrf_k + 1) / (self._rrf_k + i + 1)) / max_rel if max_rel > 0 else 0.0,
            )
            for i, hit in enumerate(hits)
        )
        return CandidateBatch(
            hits=candidate_hits,
            retrieval_legs=("semantic",),
            semantic_candidate_count=len(candidate_hits),
            embedding_ms=emb_ms,
            vector_search_ms=vec_ms,
            embedding_coverage=coverage,
        )

    def close(self) -> None:
        pass


# ══════════════════════════════════════════════════════════════
# Benchmark runner
# ══════════════════════════════════════════════════════════════

def build_query(case: dict[str, Any]) -> MemoryQuery:
    return MemoryQuery(
        agent_id=str(case.get("agent") or "companion"),
        text=str(case["query"]),
        session_id=case.get("session") or "semantic-session",
        tenant_id=str(case.get("tenant") or "eval-tenant"),
        user_id=case.get("user") or "alice",
        session_policy=str(case.get("session_policy") or "profile"),
        limit=case.get("k", 8),
    )


def seed_memory_from_event(runtime: AgentMemoryRuntime, event_dict: dict[str, Any]) -> None:
    event = Event.from_dict(event_dict)
    runtime.ingest(event)
    payload = dict(event.payload)
    content = str(
        payload.get("belief")
        or payload.get("preference")
        or payload.get("outcome")
        or payload.get("result")
        or payload.get("text")
        or ""
    ).strip()
    if not content:
        return
    kind = event.kind if event.kind in {"belief.stated", "preference.updated", "task.outcome"} else "belief.stated"
    identity = MemoryToolIdentity(
        actor_id=event.actor_id,
        agent_id=str(event.agent_id or payload.get("agent_id") or "companion"),
        session_id=event.session_id,
        tenant_id=event.tenant_id,
        user_id=event.user_id,
        labels=tuple(str(item) for item in event.labels),
    )
    MemoryIntakeService(runtime).save_memory(
        {
            "kind": kind,
            "key": str(payload.get("key") or event.event_id),
            "content": content,
            "subject_id": str(payload.get("subject_id") or event.user_id or event.actor_id),
            "salience": float(payload.get("salience") or 0.6),
            "confidence": float(payload.get("confidence") or 0.9),
            "evidence_event_ids": (event.event_id,),
            "evidence_text": content,
        },
        identity=identity,
        idempotency_key=event.event_id,
    )


def resolve_ids(
    records: list,
    cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match expected_content/forbidden_content substrings to real memory_ids."""
    by_logical_id = {}
    for record in records:
        key = str(getattr(record, "metadata", {}).get("key") or "")
        if key:
            by_logical_id[key] = record.memory_id
    resolved = []
    for case in cases:
        rc = dict(case)
        expected_ids: list[str] = []
        forbidden_ids: list[str] = []
        for logical_id in case.get("expected_memory_ids", ()):
            if logical_id in by_logical_id:
                expected_ids.append(by_logical_id[logical_id])
        if case.get("expected_content"):
            for r in records:
                if case["expected_content"] in r.content:
                    expected_ids.append(r.memory_id)
        for logical_id in case.get("forbidden_memory_ids", ()):
            if logical_id in by_logical_id:
                forbidden_ids.append(by_logical_id[logical_id])
        if case.get("forbidden_content"):
            for r in records:
                if case["forbidden_content"] in r.content:
                    forbidden_ids.append(r.memory_id)
        rc["expected_memory_ids"] = list(dict.fromkeys(expected_ids))
        rc["forbidden_memory_ids"] = list(dict.fromkeys(forbidden_ids))
        resolved.append(rc)
    return resolved


def run_mode(
    mode: str,
    cases: list[dict[str, Any]],
    runtime: AgentMemoryRuntime,
    pipeline: RetrievalPipeline,
    k_override: int | None = None,
) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        query = build_query(case)
        k = k_override or case.get("k", 8)
        started = perf_counter()
        try:
            selected_records, _trace = runtime.retrieve(query)
            selected_ids = [r.memory_id for r in selected_records][:max(k, 8)]
        except Exception:
            selected_ids = []
        elapsed_ms = (perf_counter() - started) * 1000
        eval_result = evaluate_retrieval(
            case["id"],
            case["expected_memory_ids"],
            selected_ids,
            forbidden=case["forbidden_memory_ids"],
            k=k,
        )
        results.append({
            "case_id": case["id"],
            "category": case["category"],
            "passed": eval_result.passed,
            "recall_at_k": eval_result.recall_at_k,
            "precision_at_k": eval_result.precision_at_k,
            "mrr": eval_result.reciprocal_rank,
            "ndcg_at_k": eval_result.ndcg_at_k,
                "forbidden_hits": eval_result.forbidden_hit_count,
                "no_result_case": eval_result.no_result_case,
                "no_result_correct": eval_result.no_result_correct,
                "selected_ids": selected_ids[:5],
                "expected_ids": case["expected_memory_ids"],
                "elapsed_ms": round(elapsed_ms, 1),
        })
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    no_result_cases = [r for r in results if r["no_result_case"]]
    return {
        "cases": n,
        "passed": sum(r["passed"] for r in results),
        "pass_rate": round(sum(r["passed"] for r in results) / n, 4) if n else 0,
        "mean_recall": round(mean(r["recall_at_k"] for r in results), 4) if n else 0,
        "mean_precision": round(mean(r["precision_at_k"] for r in results), 4) if n else 0,
        "mean_mrr": round(mean(r["mrr"] for r in results), 4) if n else 0,
        "mean_ndcg": round(mean(r["ndcg_at_k"] for r in results), 4) if n else 0,
        "forbidden_hits_total": sum(r["forbidden_hits"] for r in results),
        "no_result_cases": len(no_result_cases),
        "no_result_correct": sum(r["no_result_correct"] for r in no_result_cases),
        "no_result_accuracy": round(
            sum(r["no_result_correct"] for r in no_result_cases) / len(no_result_cases), 4
        )
        if no_result_cases
        else 0,
        "mean_latency_ms": round(mean(r["elapsed_ms"] for r in results), 1) if n else 0,
    }


def main() -> None:
    print("=" * 70)
    print("BGE-M3 Recall Benchmark")
    print("Model: BAAI/bge-m3 (1024-dim, cosine, L2-normalized)")
    print("Mode: CPU (sentence-transformers)")
    print("=" * 70)

    # ── 1. Load BGE-M3 ──
    print("\n[1/5] Loading BGE-M3 model...")
    started = perf_counter()
    # Use local snapshot path directly to avoid transformers _patch_mistral_regex
    # which tries to call model_info() on the hub even with local_files_only=True
    import os
    _local_model_path = os.path.expanduser(
        "~/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/"
        "5617a9f61b028005a4858fdac845db406aefb181"
    )
    model = SentenceTransformer(
        _local_model_path,
        device="cpu",
    )
    print(f"  Loaded in {perf_counter() - started:.1f}s")
    print(f"  Dim: {model.get_sentence_embedding_dimension()}")

    # ── 2. Create embedding provider ──
    def _embed_query(text: str) -> list[float]:
        vec = model.encode(text, normalize_embeddings=True, convert_to_numpy=True)
        return vec.tolist()

    def _embed_docs(texts: list[str]) -> list[list[float]]:
        vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return [v.tolist() for v in vecs]

    provider = CallableEmbeddingProvider(
        SPEC,
        query_embedder=_embed_query,
        document_embedder=_embed_docs,
    )

    # ── 3. Create store + ingest events ──
    print("\n[2/5] Creating SQLite store and ingesting events...")
    tmpdir = tempfile.TemporaryDirectory(prefix="amem-bge3-bench-")
    db_path = Path(tmpdir.name) / "benchmark.sqlite"
    bundle = SQLiteStoreBundle(db_path)

    config_fts = RuntimeConfig(
        hybrid_retrieval=HybridRetrievalConfig(enable_semantic=False),
    )
    runtime = AgentMemoryRuntime(
        config=config_fts,
        event_store=bundle.event_store,
        memory_store=bundle.memory_store,
        snapshot_store=bundle.snapshot_store,
        tombstone_store=bundle.tombstone_store,
        transaction_manager=bundle._manager,
        audit_store=bundle.audit_store,
    )

    dataset = _load_recall_dataset()
    benchmark_cases = CASES
    if dataset is None:
        all_events = _load_existing_events() + NEW_EVENTS + HARD_NEGATIVE_EVENTS
    else:
        all_events = _dataset_events(dataset)
        benchmark_cases = _dataset_cases(dataset)
    print(f"  Total events: {len(all_events)}")
    started = perf_counter()
    for ev_dict in all_events:
        seed_memory_from_event(runtime, ev_dict)
    print(f"  Ingested in {perf_counter() - started:.1f}s")

    records = bundle.memory_store.list_records()
    print(f"  Total memories: {len(records)}")

    # ── 4. Backfill embeddings ──
    print("\n[3/5] Backfilling BGE-M3 embeddings...")
    texts = [canonical_memory_text(r) for r in records]
    started = perf_counter()
    vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    print(f"  Encoded {len(texts)} memories in {perf_counter() - started:.1f}s")

    bundle.embedding_generations.register(SPEC, status="active")
    with bundle.transaction():
        for record, vec in zip(records, vectors, strict=True):
            bundle.vector_index.upsert(
                VectorRecord(
                    memory_id=record.memory_id,
                    spec=SPEC,
                    content_hash=embedding_content_hash(record, SPEC),
                    source_sequence=record.last_event_sequence,
                    vector=tuple(vec.tolist()),
                )
            )
    coverage = bundle.vector_index.coverage(generation=SPEC.generation)
    print(f"  Vector coverage: {coverage:.2%}")

    # ── 5. Resolve eval cases to real memory IDs ──
    cases = resolve_ids(records, benchmark_cases)
    # Verify all expected IDs were found
    for c in cases:
        if c.get("expected_content") and not c["expected_memory_ids"]:
            print(f"  WARNING: case {c['id']} expected_content not found: {c['expected_content']}")
        if c.get("forbidden_content") and not c["forbidden_memory_ids"]:
            print(f"  NOTE: case {c['id']} forbidden_content not found (may be OK for auth cases): {c['forbidden_content']}")

    # ── 6. Run benchmarks ──
    print(f"\n[4/5] Running benchmarks ({len(cases)} cases per mode)...")
    all_results: dict[str, dict[str, Any]] = {}

    # --- Mode 1: FTS5-only ---
    print("\n  --- FTS5-only ---")
    runtime_fts = AgentMemoryRuntime(
        config=config_fts,
        event_store=bundle.event_store,
        memory_store=bundle.memory_store,
        snapshot_store=bundle.snapshot_store,
        tombstone_store=bundle.tombstone_store,
        transaction_manager=bundle._manager,
        audit_store=bundle.audit_store,
        candidate_retriever=HybridCandidateRetriever(
            lexical=StoreLexicalRetriever(bundle.memory_store),
            semantic=None,
            config=HybridRetrievalConfig(enable_semantic=False),
        ),
    )
    fts_results = run_mode("fts5", cases, runtime_fts, pipeline=runtime_fts.retrieval)
    fts_summary = summarize(fts_results)
    all_results["fts5_only"] = {"summary": fts_summary, "details": fts_results}
    _print_summary("FTS5-only", fts_summary)

    # --- Mode 2 & 3: Vector-only and Hybrid at multiple thresholds ---
    for threshold in [0.0, 0.3, 0.5, 0.6, 0.7]:
        sem_config = HybridRetrievalConfig(
            enable_semantic=True,
            min_semantic_similarity=threshold if threshold > 0 else None,
            allow_uncalibrated_semantic=threshold == 0,
            semantic_timeout_ms=30000,  # generous timeout for CPU
            semantic_max_concurrency=2,
        )
        sem_retriever = SemanticRetriever(
            provider=provider,
            vector_index=bundle.vector_index,
            config=sem_config,
        )

        # --- Vector-only ---
        vo_retriever = VectorOnlyRetriever(sem_retriever)
        runtime_vo = AgentMemoryRuntime(
            config=RuntimeConfig(hybrid_retrieval=sem_config),
            event_store=bundle.event_store,
            memory_store=bundle.memory_store,
            snapshot_store=bundle.snapshot_store,
            tombstone_store=bundle.tombstone_store,
            transaction_manager=bundle._manager,
            audit_store=bundle.audit_store,
            candidate_retriever=vo_retriever,
        )
        vo_results = run_mode(f"vector_only_{threshold}", cases, runtime_vo, pipeline=runtime_vo.retrieval)
        vo_summary = summarize(vo_results)
        all_results[f"vector_only_t{threshold}"] = {"summary": vo_summary, "details": vo_results}
        _print_summary(f"Vector-only (t={threshold})", vo_summary)

        # --- Hybrid-RRF ---
        hybrid_retriever = HybridCandidateRetriever(
            lexical=StoreLexicalRetriever(bundle.memory_store),
            semantic=sem_retriever,
            config=sem_config,
        )
        runtime_hy = AgentMemoryRuntime(
            config=RuntimeConfig(hybrid_retrieval=sem_config),
            event_store=bundle.event_store,
            memory_store=bundle.memory_store,
            snapshot_store=bundle.snapshot_store,
            tombstone_store=bundle.tombstone_store,
            transaction_manager=bundle._manager,
            audit_store=bundle.audit_store,
            candidate_retriever=hybrid_retriever,
        )
        hy_results = run_mode(f"hybrid_{threshold}", cases, runtime_hy, pipeline=runtime_hy.retrieval)
        hy_summary = summarize(hy_results)
        all_results[f"hybrid_rrf_t{threshold}"] = {"summary": hy_summary, "details": hy_results}
        _print_summary(f"Hybrid-RRF (t={threshold})", hy_summary)

    # ── 7. Per-category breakdown for best modes ──
    print("\n[5/5] Per-category breakdown (FTS5 vs best vector vs best hybrid)")
    best_vo_key = max(
        (k for k in all_results if k.startswith("vector_only")),
        key=lambda k: all_results[k]["summary"]["mean_recall"],
    )
    best_hy_key = max(
        (k for k in all_results if k.startswith("hybrid")),
        key=lambda k: all_results[k]["summary"]["mean_recall"],
    )
    print(f"  Best vector-only: {best_vo_key}")
    print(f"  Best hybrid: {best_hy_key}")

    _print_category_breakdown("FTS5-only", all_results["fts5_only"]["details"])
    _print_category_breakdown(best_vo_key, all_results[best_vo_key]["details"])
    _print_category_breakdown(best_hy_key, all_results[best_hy_key]["details"])

    # ── 8. Write JSON report ──
    report_path = BENCHMARK_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {}
    for key, val in all_results.items():
        serializable[key] = {
            "summary": val["summary"],
            "details": val["details"],
        }
    report_path.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nReport written to: {report_path}")

    # ── 9. Failed case analysis ──
    print("\n--- Failed cases (best hybrid) ---")
    for r in all_results[best_hy_key]["details"]:
        if not r["passed"]:
            print(f"  {r['case_id']}: recall={r['recall_at_k']}, expected={r['expected_ids']}, got={r['selected_ids']}")

    # Cleanup
    hybrid_retriever.close(wait=True)
    tmpdir.cleanup()


def _print_summary(label: str, s: dict[str, Any]) -> None:
    print(f"  {label:30s} | pass={s['pass_rate']:.1%} recall={s['mean_recall']:.3f} "
          f"precision={s['mean_precision']:.3f} MRR={s['mean_mrr']:.3f} "
          f"nDCG={s['mean_ndcg']:.3f} forbidden={s['forbidden_hits_total']} "
          f"p50={s['mean_latency_ms']:.0f}ms")


def _print_category_breakdown(label: str, details: list[dict[str, Any]]) -> None:
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in details:
        by_cat[d["category"]].append(d)
    print(f"\n  {label}:")
    for cat in sorted(by_cat):
        items = by_cat[cat]
        r = mean(d["recall_at_k"] for d in items)
        p = sum(d["passed"] for d in items)
        print(f"    {cat:25s} pass={p}/{len(items)} recall={r:.3f}")


if __name__ == "__main__":
    main()
