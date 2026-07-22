# 架构

## 定位

Agent Memory Runtime 是面向生产环境的 Agent 状态运行时，不是 Prompt 辅助工具。
运行时保存原始事件，从事件派生类型化的记忆候选，再将候选归并为正式记忆记录，
最后才允许检索链路把记忆投影到上下文中。

根本边界如下：

```text
事件日志是权威来源
记忆记录是派生状态
上下文是临时投影
```

## 写入链路

```text
Event
 -> SensitiveDataSanitizer
 -> EventStore.append
 -> DerivationEngine.derive
 -> WriteGuard.validate
 -> LifecycleReducer.reduce
 -> MemoryStore.upsert
 -> RuntimeSnapshot
```

系统拒绝没有来源事件的候选记忆。私有记忆不能通过后续写入提升为 shared/global 记忆，
敏感标签也不能被静默移除；带有敏感标签的记忆不能使用 `global` 作用域。

`SensitiveDataSanitizer` 在事件序列化前执行。显式 `sensitive` 标签、银行卡号和凭据类字段都会
触发最小化：敏感字段、自由文本和未知文本字段替换为 `[redacted]`，仅保留派生和路由需要的
结构化标识。这样 `EventStore` 中保存的是可回放的最小化事件，而不是原始敏感载荷。

## 读取链路

```text
MemoryQuery
 -> normalize_query
 -> SQL 结构化边界与 ACL 预过滤
 -> 并行候选召回
      -> SQLite FTS5/BM25 top-N
      -> query embedding + sqlite-vec cosine top-N
 -> weighted RRF + get_many
 -> RetrievalPipeline
 -> hard filters
 -> AccessChecker
 -> business scoring/rerank/budget
 -> ContextBuilder
 -> AuditStore.append_envelope
 -> OpenAICompatibleChatClient（可选）
 -> AuditStore.append_trace
 -> Agent response
```

tenant、user、session、类型、作用域、层级、标签、状态和规范化 ACL 会在候选 `LIMIT` 前进入 SQL，
随后 hard filter 与 AccessChecker 再做防御性复核。FTS5 和 semantic 是两路独立召回，不是先由词法
截断后再做向量重排；两路按 weighted RRF 融合，再叠加时效性、显著性、置信度、类型、强化次数和
来源链接等业务信号。semantic 超时、bulkhead、熔断或 provider 故障不会阻塞已经完成的 FTS5 结果。

`OpenAICompatibleChatClient` 是可选的末端读取消费者，内置 DeepSeek、OpenAI、Gemini、Qwen、
Z.AI/GLM 和 Kimi 预设，并允许传入自定义兼容端点。它仅接收已投影的上下文，不持有 Store，也不具备记忆
写入能力。要把模型输出转化为长期记忆，应用必须创建新的 `Event` 并重新进入写入链路。

访问控制会生成 `access` 审计，记录已选记忆、阻止原因和上下文来源。模型调用成功和失败都会生成
`LLMCallTrace`，并包装为 `llm_call` 类型 `AuditEnvelope`。审计记录保存调用来源、模型、选中记忆
ID、用量和回放定位信息；请求、上下文、回答和异常消息只参与哈希计算，不能从审计存储中读取原文。

## 首字快路径

低延迟响应不改变默认 `project` 和 `respond` 语义。调用方显式使用 `project_fast`、`respond_fast`
或 `respond_stream(fast_path=True)` 时，运行时先准备 Snapshot 热点记忆上下文，同时在独立线程中执行
完整检索。完整检索在配置的毫秒预算内完成则使用完整上下文；超时则用 Snapshot 上下文继续调用模型。

```text
RuntimeSnapshot.hot_memory_ids
 -> MemoryStore.get
 -> RetrievalPipeline
 -> AccessChecker
 -> ContextBuilder
 -> respond_stream
```

Snapshot 只保存热点记忆 ID，不保存记忆正文；按 ID 读取出的记录仍然经过检索和访问校验，因此
private、sensitive、visible_to、上下文预算和记忆围栏不会被快路径绕过。

默认查询只检索 core/working 层。查询出现“之前、上次、还记得”等回忆意图时，planner 才把
archival 层纳入候选，避免普通问题为归档记忆付出额外首字延迟。

跨会话不是一个隐式布尔开关。`exact` 保持指定 session 的旧语义；`profile` 只允许 Core 和显式
启用的 Archival 跨 session，Working 始终会话隔离；`all` 仅用于显式历史/运维读取。无论选择哪种
模式，tenant/user 边界都会在候选截断前过滤，Agent scope/label/visible_to 仍由 AccessChecker
复核。SQLite schema v6 将常用过滤字段投影为列，使用 `memory_fts`、`memory_tags` 和 `memory_acl`
做 FTS5、标签和授权索引，并以 `memory_embeddings` 保存 sqlite-vec float32 投影，避免每次请求
反序列化全量记忆。没有可检索 principal 的记录不进入这些派生搜索索引。

## Embedding 派生链路

embedding 不是记忆写事务中的远程调用。记忆提交只负责将旧向量置 stale 并写入同事务 outbox；
后台 worker 在事务外批量推理，再用短事务完成内容 hash、源事件序列、租约和 fencing 校验后发布。

```text
MemoryStore.upsert
 -> FTS/tag/ACL projection
 -> embedding_jobs(pending)
 -> commit

EmbeddingWorker
 -> claim + lease
 -> embed_documents（事务外）
 -> re-read MemoryRecord
 -> content_hash/source_sequence/fencing check
 -> memory_embeddings(ready) + job(succeeded)
```

embedding model、revision、维度、prefix 和模板共同确定 generation。新 generation 必须先 backfill，
coverage 达标且 outbox 排空后才能显式 active。退役 generation 对应的记忆一旦更新，其旧向量立即
变为 stale；回滚前必须重新 backfill，不能直接启用陈旧空间。

个性化注入是独立的可信投影，不会把召回自由文本提升为系统指令。只有 Core belief 的白名单 key
及结构化 value 通过枚举/格式校验后，才会生成 `<personalization-profile>`；原始记忆正文继续位于
不可信 memory fence 内。

## 回放链路

```text
EventStore.list_events
 -> MemoryStore.clear
 -> 逐事件应用派生和生命周期链路
 -> RuntimeSnapshot
 -> 一致性比对
```

回放快照包含 `rule_version`、`config_hash`、`last_event_sequence` 和 `state_hash`。
规则或配置变化会导致状态哈希变化，因此可被检测。

Retention 删除先写 `MemoryTombstone`，再移除派生投影。Tombstone 保存删除覆盖到的 event sequence；
Replay 和读取链路都拒绝不高于该水位的候选，所以删除不会被旧事件回放或 JSONL 部分写入复活。
SQLite 下计划、Tombstone、投影、审计和 Snapshot 刷新共享事务。Snapshot 保存后按配置裁剪，避免
检查点无限增长。

## 存储接口

存储接口被有意拆分为：

- `EventStore`：只保存原始事件。
- `MemoryStore`：保存正式派生出的 `MemoryRecord` 对象。
- `SnapshotStore`：保存运行时快照和回放检查点。
- `TombstoneStore`：保存不可由旧事件越过的删除水位。
- `AuditStore`：保存不含提示词、查询、记忆正文和回答原文的 `AuditEnvelope`。

框架提供内存、JSONL 和 SQLite 实现。SQLite 可以作为三类存储的底层数据库，但运行时仍通过
相互独立的接口调用它们。`SQLiteStoreBundle` 让 Event、Memory 与 Snapshot Store 共享一个
事务管理器，因此一次 `ingest` 或 `replay` 要么全部提交，要么全部回滚。JSONL 各文件独立，
不提供这一跨文件原子性保证。

审计 Store 已迁入 `audit/stores/`，旧的 `memory/stores` 导入路径保留兼容。SQLite 审计写入
`audit_envelopes` 表，并兼容读取旧的 `llm_call_traces` 表。

## 记忆治理链路

治理模块位于 `src/agent_memory_runtime/governance/`，由四个子域组成：

- `queue/`：`DerivationJob`、队列 Store 和队列消费入口。
- `retention/`：归档、删除计划和执行器。
- `review/`：候选记忆风险评分、审核队列和批准入口。
- `pii/`：敏感值令牌化和可替换 Vault 接口。

异步写入链路为：

```text
Event
 -> SensitiveDataSanitizer
 -> EventStore.append
 -> DerivationQueue.enqueue

DerivationJob
 -> EventStore.get/list
 -> DerivationEngine.derive
 -> optional ReviewGuard
 -> WriteGuard.validate
 -> LifecycleReducer.reduce
 -> MemoryStore.upsert
 -> RuntimeSnapshot
 -> AuditEnvelope(governance_job)
```

`ingest_async` 不会写入 `MemoryRecord`，因此当前用户请求不被完整治理链路阻塞。当前消息仍应由上层应用直接放入本轮 LLM prompt；长期记忆由队列 worker 后台生成。同步 `ingest` 保持原语义，适合测试、批处理和需要强一致写入的场景。

## 工具调用链路

外部动作统一经过 Tool Runtime：

```text
ToolRequest
 -> ToolRegistry
 -> ToolPolicy
 -> ToolExecutor
 -> ToolResult
 -> AuditEnvelope(tool_call)
 -> Event(tool.result)
```

`tool.result` 事件可以继续进入同步或异步记忆写入链路。工具层不允许直接写 `MemoryStore`，避免外部副作用绕过事件源、生命周期治理和审计回放。

当前内置工具覆盖三类基础能力：function calling、根目录沙箱内的文件读写、provider 驱动的 web search。真实搜索服务、浏览器自动化、第三方 API 和 MCP 工具都应作为 Tool Runtime 的扩展工具接入。

## Agent 上下文窗口

业务 Agent 的模型调用预算不是事后统计。每一轮调用前，`TokenEstimator` 会计算完整消息、工具
schema 与协议开销，并预留最大输出；可配置的单价进一步形成最坏成本上界。超过软阈值时按完整
assistant/tool 消息组压缩旧历史，保留系统规则、原始任务、最近消息和未完成工具协议；压缩结果先
写入 Checkpoint。超过模型窗口或 Run Token/成本硬限制时，请求在供应商调用前终止。

结构化输出由 `OutputContract` 约束。Provider-native JSON Schema 只是优化，本地 Draft 2020-12
校验始终执行。无效结果不会作为流式 delta 对外发送；有限修复失败后 Run 明确进入 failed，而不是
把不满足契约的字符串伪装为业务对象。
