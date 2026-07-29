from __future__ import annotations

# ruff: noqa: E501
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "benchmarks" / "data"
LEGACY_PATH = DATA_DIR / "recall_100_v1.json"
MAIN_PATH = DATA_DIR / "recall_250_v1.json"
HOLDOUT_PATH = DATA_DIR / "recall_holdout_50_v1.json"


def main() -> None:
    legacy = json.loads(LEGACY_PATH.read_text(encoding="utf-8"))
    main_dataset = _build_main_dataset(legacy["items"])
    holdout_dataset = _build_holdout_dataset()
    _write_dataset(MAIN_PATH, main_dataset)
    _write_dataset(HOLDOUT_PATH, holdout_dataset)


def _build_main_dataset(legacy_items: list[dict[str, Any]]) -> dict[str, Any]:
    memories: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    memory_ids: set[str] = set()

    for item in legacy_items:
        _add_memory(memories, memory_ids, item["memory_id"], item["category"], item["content"])
        cases.append(
            _case(
                f"legacy_{item['memory_id']}",
                item["category"],
                item["query"],
                [item["memory_id"]],
                forbidden=item.get("forbidden_memory_ids", []),
            )
        )

    _add_hard_negative_state(memories, cases, memory_ids, "hn", 40)
    _add_temporal_shift(memories, cases, memory_ids, "ts", 25)
    _add_near_entity_scope(memories, cases, memory_ids, "scope", 25)
    _add_cross_lingual(memories, cases, memory_ids, "xl", 20)
    _add_semantic_paraphrase(memories, cases, memory_ids, "para", 20)
    _add_no_answer_cases(cases, "none", 20)

    return _dataset("recall_250_v1", memories, cases)


def _build_holdout_dataset() -> dict[str, Any]:
    memories: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    memory_ids: set[str] = set()

    _add_hard_negative_state(memories, cases, memory_ids, "hold_hn", 12)
    _add_temporal_shift(memories, cases, memory_ids, "hold_ts", 8)
    _add_near_entity_scope(memories, cases, memory_ids, "hold_scope", 8)
    _add_cross_lingual(memories, cases, memory_ids, "hold_xl", 8)
    _add_semantic_paraphrase(memories, cases, memory_ids, "hold_para", 8)
    _add_no_answer_cases(cases, "hold_none", 6)

    return _dataset("recall_holdout_50_v1", memories, cases)


def _add_hard_negative_state(
    memories: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    memory_ids: set[str],
    prefix: str,
    case_count: int,
) -> None:
    pairs = [
        ("自动续费", "已经关闭", "仍然开启", "用户的账单自动续费已经关闭。", "用户的账单自动续费仍然开启。", "现在还会自动续费吗"),
        ("短信通知", "已经开启", "已经关闭", "生产告警短信通知已经开启。", "生产告警短信通知已经关闭。", "生产告警会发短信吗"),
        ("MFA", "enabled", "disabled", "The admin account MFA is enabled.", "The admin account MFA is disabled.", "Is MFA active on the admin account?"),
        ("发票INV-42", "已支付", "未支付", "发票INV-42已经支付完成。", "发票INV-42仍然未支付。", "INV-42现在付款了吗"),
        ("夜间备份", "成功", "失败", "昨晚夜间备份任务成功完成。", "昨晚夜间备份任务失败。", "昨晚备份结果怎么样"),
        ("工单T-900", "已解决", "未解决", "支持工单T-900已经解决并关闭。", "支持工单T-900仍未解决。", "T-900现在处理完了吗"),
        ("生产发布", "approved", "blocked", "Production deployment is approved by security.", "Production deployment is blocked by security.", "Can production deployment proceed?"),
        ("Webhook", "enabled", "disabled", "Stripe webhook is enabled for payment callbacks.", "Stripe webhook is disabled for payment callbacks.", "Is the Stripe callback webhook on?"),
        ("Lambda", "允许", "不要", "Java示例允许使用Lambda，但要解释可读性取舍。", "Java示例不要使用Lambda，优先写显式循环。", "Java samples may use Lambda?"),
        ("Beta功能", "允许", "禁止", "企业客户允许开启Beta报表功能。", "企业客户禁止开启Beta报表功能。", "Enterprise customers can use beta reports?"),
        ("缓存预热", "完成", "未完成", "订单服务缓存预热已经完成。", "订单服务缓存预热未完成。", "订单服务缓存预热好了吗"),
        ("合同审核", "通过", "未通过", "合同A-17的法务审核已经通过。", "合同A-17的法务审核未通过。", "A-17合同能继续签吗"),
        ("许可证", "active", "inactive", "License LIC-88 is active for the workspace.", "License LIC-88 is inactive for the workspace.", "Is LIC-88 active?"),
        ("退款", "approved", "rejected", "Refund request RF-18 is approved.", "Refund request RF-18 is rejected.", "Can RF-18 be refunded?"),
        ("任务队列", "开启", "关闭", "embedding outbox重试队列已经开启。", "embedding outbox重试队列已经关闭。", "embedding outbox会自动重试吗"),
        ("审计", "启用", "禁用", "内存写入审计日志已经启用。", "内存写入审计日志已经禁用。", "memory audit log is active?"),
        ("告警规则", "打开", "关闭", "CPU告警规则已经打开。", "CPU告警规则已经关闭。", "CPU alert rule is on?"),
        ("索引", "active", "inactive", "Qdrant vector index is active for recall.", "Qdrant vector index is inactive for recall.", "Is the Qdrant recall index active?"),
        ("灰度", "approved", "blocked", "灰度发布窗口已经批准。", "灰度发布窗口被阻止。", "灰度窗口能发布吗"),
        ("会话归档", "完成", "失败", "会话归档任务已经完成。", "会话归档任务失败。", "会话归档成功了吗"),
    ]
    added = 0
    for index, (_, _, _, positive, negative, query) in enumerate(pairs, start=1):
        if added >= case_count:
            return
        yes_id = f"{prefix}_{index:03d}_yes"
        no_id = f"{prefix}_{index:03d}_no"
        _add_memory(memories, memory_ids, yes_id, "hard_negative_state", positive)
        _add_memory(memories, memory_ids, no_id, "hard_negative_state", negative)
        cases.append(_case(f"{prefix}_{index:03d}_yes", "hard_negative_state", query, [yes_id], forbidden=[no_id]))
        added += 1
        if added >= case_count:
            return
        reverse_query = _reverse_state_query(query)
        cases.append(_case(f"{prefix}_{index:03d}_no", "hard_negative_state", reverse_query, [no_id], forbidden=[yes_id]))
        added += 1


def _add_temporal_shift(
    memories: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    memory_ids: set[str],
    prefix: str,
    case_count: int,
) -> None:
    triples = [
        ("db", "过去订单系统使用MySQL。", "当前订单系统使用PostgreSQL。", "计划明年评估是否回迁MySQL。", "订单系统现在用什么数据库"),
        ("rag", "以前项目主线是Event Sourcing。", "当前项目主线是MemoryRecord加AuditLog。", "未来可能只把Event作为外部审计输入。", "现在主线还主打Event Sourcing吗"),
        ("ui", "去年后台UI使用深蓝色主题。", "现在后台UI改为中性色和少量绿色强调。", "下季度可能引入品牌红色。", "后台现在是什么视觉主题"),
        ("queue", "之前embedding任务同步写Qdrant。", "现在embedding通过outbox异步重试。", "后续考虑分布式worker处理outbox。", "embedding现在怎么写入"),
        ("api", "过去错误响应只返回message。", "当前错误响应包含code和retryable。", "下一版会补trace_id。", "现在API错误里有什么字段"),
        ("deploy", "以前周五下午允许发布。", "现在周五下午禁止生产发布。", "以后节假日发布需要额外审批。", "现在周五下午能发生产吗"),
        ("cache", "过去推荐Redis缓存用户画像。", "现在用户画像直接读SQLite真实状态。", "未来可能把热点画像投影到Redis。", "用户画像现在从哪里读"),
        ("auth", "之前visible_to可以由模型建议扩大。", "现在扩大visible_to必须进入审核。", "将来可能增加管理员批准流。", "现在模型能直接扩大visible_to吗"),
        ("dream", "Auto Dream最初是同步Analyzer。", "现在Auto Dream由后台job触发。", "未来可能按用户活跃度动态调度。", "Auto Dream现在怎么触发"),
        ("lang", "以前多Agent方案考虑直接接LangGraph。", "现在LangGraph不进入memory runtime核心。", "未来编排层可能接LangGraph。", "LangGraph现在在核心runtime里吗"),
    ]
    added = 0
    for index, (name, past, current, future, query) in enumerate(triples, start=1):
        ids = [f"{prefix}_{name}_past", f"{prefix}_{name}_current", f"{prefix}_{name}_future"]
        for memory_id, content in zip(ids, [past, current, future], strict=True):
            _add_memory(memories, memory_ids, memory_id, "temporal_shift", content)
        cases.append(_case(f"{prefix}_{index:03d}_current", "temporal_shift", query, [ids[1]], forbidden=[ids[0], ids[2]]))
        added += 1
        if added >= case_count:
            return
        cases.append(_case(f"{prefix}_{index:03d}_past", "temporal_shift", f"{name} 以前是什么状态", [ids[0]], forbidden=[ids[1], ids[2]]))
        added += 1
        if added >= case_count:
            return
        cases.append(_case(f"{prefix}_{index:03d}_future", "temporal_shift", f"{name} 后续计划是什么", [ids[2]], forbidden=[ids[0], ids[1]]))
        added += 1


def _add_near_entity_scope(
    memories: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    memory_ids: set[str],
    prefix: str,
    case_count: int,
) -> None:
    rows = [
        ("pay_owner", "张敏负责支付项目需求确认。", "order_owner", "李航负责订单项目需求确认。", "支付项目需求找谁确认"),
        ("pay_qa", "王琦负责支付项目回归测试。", "pay_ops", "刘洋负责支付项目生产告警。", "支付项目回归测试找谁"),
        ("invoice_finance", "孙悦负责发票开具和财务报销。", "contract_legal", "周可负责合同法务审核。", "发票开具找哪个接口人"),
        ("eu_contact", "Maya负责欧洲团队项目沟通。", "us_contact", "Noah负责北美团队PoC环境。", "欧洲团队项目联系人是谁"),
        ("api_reviewer", "Alex负责API平台兼容性评审。", "security_reviewer", "何川负责权限和密钥审查。", "API兼容性评审找谁"),
        ("doc_owner", "Grace负责开发者指南文档。", "design_owner", "赵宁负责后台工作台视觉规范。", "开发者指南谁负责"),
        ("db_expert", "许睿负责数据库索引优化建议。", "data_analyst", "郑楠负责对账流程梳理。", "索引优化建议找谁"),
        ("release_owner", "韩雪负责灰度发布窗口协调。", "incident_owner", "陈思关注发布风险和事故复盘。", "灰度发布窗口谁协调"),
        ("search_owner", "唐佳维护前端搜索组件库。", "chart_owner", "赵宁维护后台图表视觉规范。", "搜索组件库谁维护"),
        ("billing_pm", "张敏负责订阅计费产品策略。", "checkout_pm", "林岚负责收银台转化实验。", "订阅计费产品策略找谁"),
        ("acl_engineer", "何川负责ACL过滤安全评审。", "audit_engineer", "顾晨负责审计日志字段核对。", "ACL过滤安全评审找谁"),
        ("k8s_ops", "刘洋处理Kubernetes集群告警。", "db_ops", "许睿处理数据库慢查询告警。", "Kubernetes集群告警找谁"),
        ("qa_mobile", "王琦负责移动端回归测试。", "qa_web", "唐佳负责Web组件视觉回归。", "移动端回归测试找谁"),
    ]
    for index, (id_a, content_a, id_b, content_b, query) in enumerate(rows, start=1):
        if len([case for case in cases if case["category"] == "near_entity_scope" and case["case_id"].startswith(prefix)]) >= case_count:
            return
        _add_memory(memories, memory_ids, f"{prefix}_{id_a}", "near_entity_scope", content_a)
        _add_memory(memories, memory_ids, f"{prefix}_{id_b}", "near_entity_scope", content_b)
        cases.append(_case(f"{prefix}_{index:03d}_a", "near_entity_scope", query, [f"{prefix}_{id_a}"], forbidden=[f"{prefix}_{id_b}"]))
        if len([case for case in cases if case["category"] == "near_entity_scope" and case["case_id"].startswith(prefix)]) >= case_count:
            return
        cases.append(_case(f"{prefix}_{index:03d}_b", "near_entity_scope", _swap_scope_query(query), [f"{prefix}_{id_b}"], forbidden=[f"{prefix}_{id_a}"]))


def _add_cross_lingual(
    memories: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    memory_ids: set[str],
    prefix: str,
    case_count: int,
) -> None:
    rows = [
        ("concise", "回答要短，直接给结论，不要铺垫。", "Keep the response focused and concise."),
        ("audit", "MemoryRecord是事实源，AuditLog只记录变更证据。", "Which store is the source of truth for memory?"),
        ("outbox", "Qdrant失败不影响SQLite写入，embedding outbox负责重试。", "What happens if Qdrant indexing fails?"),
        ("acl", "ACL filtering must happen before TopK candidate ranking.", "Where should ACL filtering happen in retrieval?"),
        ("dream", "Auto Dream only proposes memory changes and does not directly mutate records.", "Can Auto Dream directly write memory records?"),
        ("lambda", "Java examples should avoid Lambda unless readability tradeoffs are explained.", "Java示例默认能不能写Lambda"),
        ("rrf", "Hybrid retrieval fuses FTS5 and vector candidates with Reciprocal Rank Fusion.", "FTS5和向量结果怎么融合"),
        ("lock", "Optimistic locking uses the MemoryRecord version to prevent silent overwrite.", "乐观锁靠哪个字段避免静默覆盖"),
        ("scope", "Visible_to expansion is treated as a high-risk write and requires review.", "扩大共享范围是不是低风险写入"),
        ("context_budget", "Context selection prefers score density and diversity rather than exact knapsack search.", "上下文筛选为什么不做精确背包"),
        ("policy", "MemoryWritePolicy handles schema, permissions, scope invariants, and risk gates.", "规则引擎缩小后负责什么"),
        ("checkpoint", "Dream checkpoint advances after proposals for a batch are persisted.", "Auto Dream checkpoint什么时候推进"),
        ("worker", "Dream workers claim jobs with a lease so multiple workers do not process one job.", "多Worker怎么抢Dream任务"),
        ("replay", "Audit replay rebuilds history for inspection, not the primary current-state path.", "审计重放是不是主写入链路"),
        ("rrf_k", "The recall benchmark keeps RRF k at 60 for candidate fusion.", "候选融合实验RRF k是多少"),
        ("qdrant", "Qdrant is a projection index and never the source of truth.", "Qdrant是不是事实源"),
        ("delete", "Forget writes a tombstone or archive audit instead of physical deletion by default.", "forget默认物理删除吗"),
        ("identity", "tenant_id, user_id, agent_id, and session_id come from server-side identity context.", "身份字段由模型提供吗"),
        ("review", "High-confidence conflicts are sent to review instead of being overwritten automatically.", "高置信冲突能不能自动覆盖"),
        ("fts", "FTS5 helps exact terms, identifiers, and lexical fallback when embeddings miss.", "FTS5在混合检索里有什么用"),
    ]
    for index, (name, content, query) in enumerate(rows[:case_count], start=1):
        memory_id = f"{prefix}_{name}"
        _add_memory(memories, memory_ids, memory_id, "cross_lingual", content)
        cases.append(_case(f"{prefix}_{index:03d}", "cross_lingual", query, [memory_id]))


def _add_semantic_paraphrase(
    memories: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    memory_ids: set[str],
    prefix: str,
    case_count: int,
) -> None:
    rows = [
        ("minimal_example", "解释代码问题时先给最小可运行示例，再补上下文。", "别先讲一堆背景，给我能跑的东西"),
        ("review_first", "做代码评审时先列风险和缺陷，再写总结。", "review别先夸，直接指出会坏在哪里"),
        ("no_marketing", "做工具界面时第一屏必须是可用工作台，不要营销落地页。", "我要的是能操作的界面，不是宣传页"),
        ("facts_first", "回答当前代码状态时只说已接通的模块，不把设计稿当实现。", "别把还没连上的东西说成已经有"),
        ("source_truth", "SQLite保存MemoryRecord真实状态，Qdrant只做向量投影。", "哪个库说了算，不要拿索引当真相"),
        ("risk_review", "删除、敏感信息、扩大可见性和跨用户写入必须审核或拒绝。", "哪些写入不能自动放行"),
        ("dream_semantics", "语义冲突、合并和去重应该由Auto Dream裁决。", "谁来判断两条记忆是不是意思冲突"),
        ("policy_boundary", "确定性代码只处理schema、权限、安全和scope不变量。", "policy不要做哪些语义判断"),
        ("benchmark_boundary", "检索评测必须区分设计正确性和生产效果声明。", "小样本跑通能不能直接说生产可用"),
        ("topk_acl", "跨租户或跨agent记忆必须在TopK之前过滤。", "为什么不能先召回再过滤权限"),
        ("outbox_retry", "embedding outbox记录待索引任务，Qdrant短暂失败后可重试。", "向量库挂一下会不会影响真实写入"),
        ("version_conflict", "版本号变化时审核通过也要重新检查，不能覆盖新版本。", "review期间别人改了同一条怎么办"),
        ("idempotency", "proposal_id保证重复提交不会重复创建或重复reinforce。", "同一个proposal重试怎么不写两次"),
        ("rrf_reason", "RRF用排名融合FTS5和向量，不直接比较两种分数绝对值。", "为什么融合时不用原始分数硬加"),
        ("hard_negative", "embedding对否定语义有盲区，必须加入相反状态的负例检查。", "为什么相似度高还可能是错答案"),
        ("context_budget", "上下文筛选先按权限、分数、时效和多样性筛，再控制上下文预算。", "记忆进上下文前怎么裁剪"),
        ("audit_role", "AuditLog记录before/after和证据，用于追踪写入历史。", "审计日志到底保存什么"),
        ("auto_dream_trigger", "Auto Dream由后台job、会话结束hook或检查点推进触发。", "它既然auto什么时候跑"),
        ("qdrant_default", "runtime默认接入Qdrant时仍以SQLite为真实状态库。", "默认接Qdrant后事实源变了吗"),
        ("no_answer", "检索系统需要能承认没有相关记忆，而不是总塞一个近似结果。", "查不到时应该怎么办"),
    ]
    for index, (name, content, query) in enumerate(rows[:case_count], start=1):
        memory_id = f"{prefix}_{name}"
        _add_memory(memories, memory_ids, memory_id, "semantic_paraphrase_hard", content)
        cases.append(_case(f"{prefix}_{index:03d}", "semantic_paraphrase_hard", query, [memory_id]))


def _add_no_answer_cases(cases: list[dict[str, Any]], prefix: str, case_count: int) -> None:
    queries = [
        "用户的护照号码是什么",
        "火星基地发布窗口是哪天",
        "量子航运许可证由谁审批",
        "用户的银行卡CVV是多少",
        "北京办公室门禁密码是什么",
        "用户女儿的小学校名是什么",
        "上次牙科诊断结果是什么",
        "纽约仓库消防演练负责人是谁",
        "ORBIT-9999订单现在谁处理",
        "古巴比伦空中花园修复方案是什么",
        "用户是否喜欢榴莲披萨",
        "东京酒店会员号是多少",
        "Azure账单管理员手机号是什么",
        "项目Zephyr的根密钥在哪",
        "明年春节机票订单号是什么",
        "谁负责月球仓库库存盘点",
        "用户的医疗保险编号是多少",
        "黑五促销海报主视觉谁负责",
        "CRM沙盒的超级管理员密码是什么",
        "无人机巡检合同的签署日期",
    ]
    for index, query in enumerate(queries[:case_count], start=1):
        cases.append(_case(f"{prefix}_{index:03d}", "no_answer", query, []))


def _add_memory(
    memories: list[dict[str, Any]],
    memory_ids: set[str],
    memory_id: str,
    category: str,
    content: str,
) -> None:
    if memory_id in memory_ids:
        return
    memory_ids.add(memory_id)
    memories.append(
        {
            "memory_id": memory_id,
            "category": category,
            "content": content,
            "session_id": "recall-v1",
            "k": 5,
        }
    )


def _case(
    case_id: str,
    category: str,
    query: str,
    expected: list[str],
    *,
    forbidden: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": category,
        "query": query,
        "ground_truth_memory_ids": expected,
        "forbidden_memory_ids": forbidden or [],
        "session_id": "recall-v1",
        "k": 5,
    }


def _dataset(version: str, memories: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": version,
        "description": "Manual retrieval benchmark with hard negatives, temporal shifts, scope distractors, cross-lingual paraphrases, and no-answer cases.",
        "memory_count": len(memories),
        "case_count": len(cases),
        "category_counts": dict(Counter(case["category"] for case in cases)),
        "memories": memories,
        "cases": cases,
    }


def _write_dataset(path: Path, dataset: dict[str, Any]) -> None:
    path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _reverse_state_query(query: str) -> str:
    replacements = [
        ("开启", "关闭"),
        ("关闭", "开启"),
        ("active", "inactive"),
        ("enabled", "disabled"),
        ("on", "off"),
        ("approved", "blocked"),
        ("paid", "unpaid"),
        ("成功", "失败"),
        ("完成", "失败"),
        ("解决", "未解决"),
        ("允许", "禁止"),
    ]
    for left, right in replacements:
        if left in query:
            return query.replace(left, right)
    return query + " 的相反状态是什么"


def _swap_scope_query(query: str) -> str:
    replacements = [
        ("支付项目", "订单项目"),
        ("回归测试", "生产告警"),
        ("发票开具", "合同审核"),
        ("欧洲团队", "北美团队"),
        ("API兼容性", "权限和密钥"),
        ("开发者指南", "后台视觉规范"),
        ("索引优化", "对账流程"),
        ("灰度发布", "事故复盘"),
        ("搜索组件库", "图表视觉规范"),
        ("订阅计费", "收银台转化"),
        ("ACL过滤", "审计日志"),
        ("Kubernetes集群", "数据库慢查询"),
        ("移动端", "Web组件"),
    ]
    for left, right in replacements:
        if left in query:
            return query.replace(left, right)
    return query + "的相近但不同作用域负责人是谁"


if __name__ == "__main__":
    main()
