# v0.5.0 P0/P1：长期记忆与 Agent 上下文生产化

## 目标

本轮不增加播放器或传输层适配，专注通用业务 Agent 框架的正确性与生产边界：

- 打通同一 tenant/user/agent 下的跨会话 Core/Archival 召回，同时保持 Working 会话隔离。
- 将长期偏好和策略从 session 身份中解耦。
- 修复真实归档记录无法被回忆查询读取的问题。
- 为中文增加无外部服务依赖的字符二元词召回。
- 使用可插拔模型 Token 估算器取代空白分词估算，并严格执行上下文硬预算。
- 完成 Agent Checkpoint 压缩、调用前成本预检、结构化输出、持久化索引、
  Retention Worker、Tombstone 回放和 Snapshot 清理。

## 跨会话语义

`MemoryQuery.session_policy` 提供三种显式模式：

- `exact`：提供 session 时所有层只读取当前 session，保持旧 API 语义。
- `profile`：Working 只读取当前 session；Core 和被 planner 启用的 Archival 可以跨 session。
- `all`：允许所有层跨 session，适用于显式历史检索和运维工具。

业务 Agent 将使用 `profile`。`tenant_id`、`user_id`、`agent_id` 仍由硬过滤和访问控制共同校验，
`session_policy` 不能跨越身份边界。

`session_id=None` 保留“调用方未给出会话过滤”的兼容语义；生产交互入口应提供 session ID。user
边界在候选截断前执行，避免其他用户的大量记录挤占合法候选；AccessChecker 继续作为纵深复核。

Core belief/preference 和 strategy 使用新的 `v3` 稳定键：

```text
v3:{kind}:{tenant_id}:{user_identity}:{agent_id}:{semantic_key}
```

该键不再包含 session。`preference.updated` 和同任务 `task.outcome` 默认执行 revise，使新会话中的
明确更新覆盖旧投影，同时保留全部 `source_event_ids`。旧 v1/v2 投影应通过事件 replay 重建；事件
日志仍是唯一权威来源。

个性化 Prompt 不直接信任 preference 自由文本。只有 Core belief 中 `language`、`verbosity`、
`response_style`、`tone`、`timezone`、`accessibility` 等白名单 key，且其结构化 `value` 通过固定
值域校验后，才会进入 `<personalization-profile>`。原始偏好正文仍位于不可信 memory fence 内。

## 中文召回与 Token 预算

词法层对拉丁文本使用规范化词项，对中日韩文本生成字符二元词；例如“退款进度”会产生
“退款”“款进”“进度”等词项。该方案是确定性的基础召回，不冒充语义向量检索。

Token 估算通过 `TokenEstimator` 协议开放模型原生计数器。默认实现按 CJK 字符、ASCII 片段和
消息/工具协议开销保守估算，避免一整段中文被误算为一个 token。ContextBuilder 对超出预算的
单条记忆也不再破例放行。

## SQLite v5 候选索引

v5 migration 为 `memories` 增加 tenant、user、agent、session、layer、status、type、scope、
updated_at、salience 等投影列，并建立组合索引。`memory_terms` 保存规范化拉丁词和 CJK 二元词，
`memory_tags` 保存精确标签索引；两者由 MemoryStore upsert/replace 与记录保持同一事务。

检索先在 SQLite 中执行身份、会话策略、层级、状态、类型和 scope 过滤，再按词项命中、显著性和
更新时间分页读取有限候选；候选随后仍进入原有 `hard_filter`、`AccessChecker` 和精排，数据库索引
不会绕过权限控制。旧自定义 Store 若没有 `query_records`，运行时保留 `list_records` 兼容路径。
ContextBuilder 的预算选择保持精排顺序，不再按 salience 二次排序；显式回忆意图会给 Archival 记录
受控加权，避免“召回成功但相关结果排在无关高显著性画像之后”。

## Retention、Tombstone 与 Snapshot

删除操作先持久化 `MemoryTombstone`，记录删除覆盖到的事件 sequence，再删除 MemoryStore 投影。
Replay 遇到 sequence 不高于 tombstone 水位的同 ID 候选时跳过；水位之后的新用户事件仍可重新创建
该语义记忆。读取链路也检查水位，因此 JSONL 即使在 tombstone 落盘后、物理投影删除前崩溃，也
不会重新暴露已删除正文。SQLite 下 plan、tombstone、投影替换、retention 审计和快照共享事务。

`RetentionWorker` 提供 `run_once` 和带可中断等待的 `run_forever`。每个周期规划归档/删除、应用计划、
刷新快照；常驻循环只累计计数和最后一个周期，不会无限保存历史报告。SnapshotStore 新增 `prune`，
运行时每次保存后只保留配置的最近 N 个快照，默认 32。

## Agent 调用前预算与 Checkpoint 压缩

每次模型调用前，运行时使用完整 messages、tool schemas 和协议开销计算预计输入，并为输出预留固定
空间。预估会同时检查模型上下文窗口、Run 输入/输出/总 Token 上限；策略配置输入输出单价后，还会
计算本次调用的最大美元成本并在调用前执行 `max_run_cost_usd`。

当预计输入超过可用窗口的软比例（默认 80%）时，运行时按完整消息组压缩旧历史：系统规则、原始
用户任务、最近消息组和未完成 tool-call 协议必须保留；旧的已完成消息转换为带 source hash 的确定性
不可信摘要。压缩后的 Checkpoint 先持久化，再发起模型调用，并发送 `context.compacted` 事件。

## 结构化输出契约

`AgentRequest.output_contract` 接受 JSON Schema、最大修复次数和 provider-native 开关。Schema 在
Run 创建前按 Draft 2020-12 校验；最终文本也必须通过同一版本校验。结构化运行会缓冲流式文本，
避免把无效 JSON 提前发送给外部适配层。首次
失败会把不含原文的校验原因写回 Checkpoint 并受控重试，超过次数后 Run 明确失败。

支持原生 JSON Schema 的 OpenAI 兼容端点可设置 `provider_native=True`；无论供应商是否原生约束，
运行时本地校验始终执行。`run.completed` 同时返回原始 `output` 和已验证的 `structured_output`。

## 检索评测

检索评测不再只判断 expected ID 是否出现，同时输出 Recall@K、Precision@K、MRR 和 nDCG@K，
并支持 `forbidden_memory_ids` 验证跨用户泄漏。`examples/data/memory_eval_events.jsonl` 与
`examples/evals/retrieval_cases.yml` 覆盖跨会话偏好、中文退款召回、归档回忆和其他用户负样本。
CLI 汇总均值，expected 未在 K 截断内命中或出现 forbidden ID 时返回退出码 1，可直接用于 CI 门禁。

## 兼容性

`MemoryQuery` 新字段追加在末尾，默认值为 `exact`，旧调用和位置参数语义不变。后续 SQLite
迁移只追加 v5，不会修改 v1-v4 migration 的 SQL 或 checksum。

## 运维入口

```powershell
amem retrieve --agent assistant --query "按我的偏好回答" --tenant tenant-1 --user user-1 --session current --session-policy profile
amem retention worker --forever --interval-seconds 300
amem ingest examples/data/memory_eval_events.jsonl
amem eval examples/evals/retrieval_cases.yml
```

`retrieve`、`project`、`respond` 均暴露 tenant/user/session/session-policy，避免 CLI 调试路径绕过真实身份
语义。Retention Worker 支持单周期调试和常驻模式；评测命令的退出码是稳定自动化契约。

## 2026-07-21 验证结果

- Ruff：`benchmarks`、`src`、`tests` 全部通过。
- pytest：115 项全量测试通过；其中 Agent/协议专项 28 项、安全/治理/可靠性专项 33 项。
- 检索 smoke suite：4/4 通过，Recall@K、MRR、nDCG@K 均值均为 1.0。
- SQLite 10,000 条、100 次本机基准：候选减少 97.44%，P50 18.51 ms、P95 19.84 ms；相对已物化
  全量精排 P50 203.25 ms，提速 10.98 倍。
- Checkpoint 合成样本：5,513 tokens 压缩至 1,208，减少 78.09%，系统规则与原始任务保持不变。
- `agent_memory_runtime-0.5.0` wheel/sdist 构建成功，wheel 隔离目录安装、版本和 CLI 冒烟通过。

规模和压缩数据可通过 `benchmarks/validate_runtime.py` 重跑；这是本机工程基准，不表述为线上 SLA。
