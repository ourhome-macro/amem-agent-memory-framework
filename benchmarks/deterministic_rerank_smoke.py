from __future__ import annotations

# ruff: noqa: E501
import json
from dataclasses import dataclass
from pathlib import Path

from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.memory.retrieval.candidates import CandidateBatch, CandidateHit
from agent_memory_runtime.memory.retrieval.pipeline import RetrievalPipeline

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "benchmarks" / "results" / "deterministic-rerank-smoke-results.json"


@dataclass(frozen=True)
class SmokeCase:
    case_id: str
    category: str
    query: str
    expected_memory_id: str | None
    records: tuple[MemoryRecord, ...]


def main() -> None:
    cases = _cases()
    pipeline = RetrievalPipeline(RuntimeConfig())
    results = []
    for case in cases:
        selected, trace = pipeline.retrieve(
            list(case.records),
            MemoryQuery(
                agent_id="assistant",
                text=case.query,
                tenant_id="tenant-smoke",
                user_id="user-smoke",
                session_id="s1",
                session_policy="profile",
                limit=5,
            ),
            candidate_batch=_candidate_batch(case.records),
        )
        selected_ids = [record.memory_id for record in selected]
        passed = (
            selected_ids == []
            if case.expected_memory_id is None
            else selected_ids[:1] == [case.expected_memory_id]
        )
        results.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "query": case.query,
                "expected_memory_id": case.expected_memory_id,
                "selected_memory_ids": selected_ids,
                "trace_memory_ids": [result.memory_id for result in trace.results],
                "passed": passed,
            }
        )
    summary = {
        "case_count": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "pass_rate": round(sum(1 for item in results if item["passed"]) / len(results), 4),
        "category_counts": _category_counts(results),
        "category_passed": _category_passed(results),
        "results": results,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: summary[key] for key in summary if key != "results"}, indent=2))
    print(f"Report written to: {REPORT_PATH}")


def _cases() -> list[SmokeCase]:
    cases: list[SmokeCase] = []
    state_rows = [
        ("renewal", "账单自动续费已经关闭。", "账单自动续费仍然开启。", "自动续费现在关闭了吗"),
        ("sms", "生产告警短信通知已经开启。", "生产告警短信通知已经关闭。", "生产告警会发短信吗"),
        ("mfa", "Admin account MFA is enabled.", "Admin account MFA is disabled.", "Is MFA enabled on the admin account?"),
        ("invoice", "发票 INV-42 已经支付完成。", "发票 INV-42 仍然未支付。", "INV-42 现在付款了吗"),
        ("ticket", "支持工单 T-900 已经解决。", "支持工单 T-900 仍未解决。", "T-900 现在解决了吗"),
        ("deploy", "Production deployment is approved by security.", "Production deployment is blocked by security.", "Can production deployment proceed?"),
        ("webhook", "Stripe webhook is enabled for payment callbacks.", "Stripe webhook is disabled for payment callbacks.", "Is the Stripe callback webhook on?"),
        ("lambda", "Java 示例不要使用 Lambda，优先写显式循环。", "Java 示例允许使用 Lambda。", "Java 示例不要用 Lambda 吗"),
        ("backup", "昨晚夜间备份任务成功完成。", "昨晚夜间备份任务失败。", "昨晚备份成功了吗"),
        ("license", "License LIC-88 is active for the workspace.", "License LIC-88 is inactive for the workspace.", "Is LIC-88 active?"),
    ]
    for index, (name, good, bad, query) in enumerate(state_rows, start=1):
        cases.append(
            SmokeCase(
                f"state_{index:02d}",
                "state",
                query,
                f"{name}-good",
                (_record(f"{name}-bad", bad), _record(f"{name}-good", good)),
            )
        )

    temporal_rows = [
        ("db", "过去订单系统使用 MySQL。", "当前订单系统使用 PostgreSQL。", "计划明年评估是否回迁 MySQL。", "订单系统现在用什么数据库"),
        ("runtime", "以前主线是 Event Sourcing。", "当前主线是 MemoryRecord 加 AuditLog。", "未来 Event 可能只作外部审计输入。", "现在主线还是 Event Sourcing 吗"),
        ("queue", "之前 embedding 同步写 Qdrant。", "现在 embedding 通过 outbox 异步重试。", "后续考虑多 worker 处理 outbox。", "embedding 现在怎么写入"),
        ("dream", "Auto Dream 最初是同步 Analyzer。", "现在 Auto Dream 由后台 job 触发。", "未来可能按活跃度调度。", "Auto Dream 现在怎么触发"),
        ("langgraph", "以前考虑直接接 LangGraph。", "现在 LangGraph 不进入 memory runtime 核心。", "未来编排层可能接 LangGraph。", "LangGraph 现在在核心 runtime 里吗"),
        ("visible", "之前 visible_to 可以由模型建议扩大。", "现在扩大 visible_to 必须进入审核。", "未来可能增加管理员批准流。", "现在模型能直接扩大 visible_to 吗"),
        ("ui", "去年后台 UI 使用深蓝主题。", "现在后台 UI 使用中性色和少量绿色强调。", "下季度可能引入品牌红。", "后台现在是什么视觉主题"),
        ("api", "过去错误响应只返回 message。", "当前错误响应包含 code 和 retryable。", "下一版会补 trace_id。", "现在 API 错误里有什么字段"),
        ("cache", "过去推荐 Redis 缓存用户画像。", "现在用户画像直接读 SQLite 真实状态。", "未来可投影热点画像到 Redis。", "用户画像现在从哪里读"),
        ("deploy", "以前周五下午允许发布。", "现在周五下午禁止生产发布。", "以后节假日发布需要额外审批。", "现在周五下午能发布生产吗"),
    ]
    for index, (name, past, current, future, query) in enumerate(temporal_rows, start=1):
        cases.append(
            SmokeCase(
                f"temporal_{index:02d}",
                "temporal",
                query,
                f"{name}-current",
                (
                    _record(f"{name}-past", past),
                    _record(f"{name}-future", future),
                    _record(f"{name}-current", current),
                ),
            )
        )

    entity_rows = [
        ("pay", "支付项目负责人是张敏。", "订单项目负责人是李航。", "支付项目测试负责人是王琦。", "支付项目负责人是谁"),
        ("invoice", "发票开具负责人是孙悦。", "合同审核负责人是周可。", "发票报销负责人是顾晨。", "发票开具找谁"),
        ("eu", "欧洲团队项目联系人是 Maya。", "北美团队项目联系人是 Noah。", "欧洲团队财务接口人是 Eva。", "欧洲团队项目联系人是谁"),
        ("api", "API 兼容性评审负责人是 Alex。", "权限密钥审查负责人是何川。", "API 文档负责人是 Grace。", "API 兼容性评审找谁"),
        ("release", "灰度发布窗口协调人是韩雪。", "事故复盘负责人是陈思。", "灰度发布文案负责人是赵宁。", "灰度发布窗口谁协调"),
        ("acl", "ACL 过滤安全评审负责人是何川。", "审计日志字段核对负责人是顾晨。", "ACL UI 文案负责人是赵宁。", "ACL 过滤安全评审找谁"),
        ("k8s", "Kubernetes 集群告警处理人是刘洋。", "数据库慢查询告警处理人是许睿。", "Kubernetes 文档负责人是 Grace。", "Kubernetes 告警找谁"),
        ("mobile", "移动端回归测试负责人是王琦。", "Web 组件视觉回归负责人是唐佳。", "移动端埋点负责人是林岚。", "移动端回归测试找谁"),
        ("billing", "订阅计费产品策略负责人是张敏。", "收银台转化实验负责人是林岚。", "订阅计费测试负责人是王琦。", "订阅计费产品策略找谁"),
        ("search", "前端搜索组件库维护人是唐佳。", "后台图表视觉规范维护人是赵宁。", "搜索索引优化负责人是许睿。", "搜索组件库谁维护"),
    ]
    for index, (name, good, bad_a, bad_b, query) in enumerate(entity_rows, start=1):
        cases.append(
            SmokeCase(
                f"entity_{index:02d}",
                "entity",
                query,
                f"{name}-good",
                (
                    _record(f"{name}-bad-a", bad_a),
                    _record(f"{name}-bad-b", bad_b),
                    _record(f"{name}-good", good),
                ),
            )
        )

    no_answer_queries = [
        "用户护照号码是什么",
        "火星基地发布窗口是哪天",
        "量子航运许可证由谁审批",
        "银行卡 CVV 是多少",
        "北京办公室门禁密码是什么",
        "项目 Zephyr 的根密钥在哪里",
        "无人机巡检合同签署日是哪天",
        "CRM 沙箱超级管理员密码是什么",
        "月球仓库库存盘点负责人是谁",
        "明年春节机票订单号是什么",
    ]
    unrelated_records = (
        _record("unrelated-a", "回答要简短，直接给结论。"),
        _record("unrelated-b", "MemoryRecord 是当前事实源，AuditLog 是变更历史。"),
        _record("unrelated-c", "Qdrant 是向量投影，不是真实状态源。"),
    )
    for index, query in enumerate(no_answer_queries, start=1):
        cases.append(
            SmokeCase(
                f"none_{index:02d}",
                "no_answer",
                query,
                None,
                unrelated_records,
            )
        )
    return cases


def _record(memory_id: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        memory_type="belief",
        scope="private",
        layer="working",
        session_id="s1",
        subject_id="user-smoke",
        content=content,
        source_event_ids=(f"event-{memory_id}",),
        rule_id="smoke",
        owner_id="assistant",
        visible_to=("assistant",),
        labels=("private",),
        salience=0.7,
        confidence=0.9,
        tenant_id="tenant-smoke",
        user_id="user-smoke",
        agent_id="assistant",
        created_at="2026-07-29T00:00:00+00:00",
        updated_at="2026-07-29T00:00:00+00:00",
    )


def _candidate_batch(records: tuple[MemoryRecord, ...]) -> CandidateBatch:
    return CandidateBatch(
        hits=tuple(
            CandidateHit(
                memory_id=record.memory_id,
                sources=("lexical", "semantic"),
                lexical_rank=index + 1,
                semantic_rank=index + 1,
                semantic_similarity=0.72 if not record.memory_id.startswith("unrelated") else 0.12,
                lexical_relevance=0.2,
                semantic_relevance=0.2,
                fusion_score=0.5 if not record.memory_id.startswith("unrelated") else 0.05,
            )
            for index, record in enumerate(records)
        ),
        retrieval_legs=("lexical", "semantic"),
        lexical_candidate_count=len(records),
        semantic_candidate_count=len(records),
    )


def _category_counts(results: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        category = str(result["category"])
        counts[category] = counts.get(category, 0) + 1
    return counts


def _category_passed(results: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        if not result["passed"]:
            continue
        category = str(result["category"])
        counts[category] = counts.get(category, 0) + 1
    return counts


if __name__ == "__main__":
    main()
