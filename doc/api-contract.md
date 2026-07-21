# API 契约

## 通用业务 Agent Runtime

### `BusinessAgentRuntime.run(request)`

输入：携带 tenant/user/agent/session/request 身份的 `AgentRequest`。

输出：`AsyncIterator[AgentRunEvent]`。事件覆盖 context、模型原生 delta、tool request/start/result、
approval、reconciliation、evaluation 和 run 终态。每个执行代次有独立 `execution_id`；适配层按
`(run_id, execution_id, sequence)` 排序。

处理：创建或幂等读取 run，领取带 fencing token 的 lease，从 checkpoint 恢复消息和待执行工具，
按 `AgentPolicy` 推进模型/工具循环，并在每个不可重复边界之前持久化状态。

每次模型调用前执行强制 preflight：计算消息、工具 schema、预留输出、累计 Token 与可选美元成本。
达到 `context_compaction_ratio` 时，旧的完整消息组会转换为带来源 hash 的不可信确定性摘要，并先保存
Checkpoint；超过模型窗口、Run Token 或成本硬限制时，不会调用模型供应商。

`AgentRequest.output_contract` 可携带 `OutputContract`。Schema 在创建契约时按 Draft 2020-12 校验；
最终正文必须通过本地 JSON Schema 校验。失败时最多执行配置次数的受控修复，原始无效正文不会通过
`model.output.delta` 暴露；成功的 `run.completed` 同时包含 `output` 与 `structured_output`。

### `BusinessAgentRuntime.resume(run_id, tenant_id, user_id)`

仅允许完全匹配 run tenant/user 的调用方恢复 pending run。waiting approval 和 reconciliation run
必须先完成相应人工决定；completed/failed/cancelled run 不会重新执行。

### `BusinessAgentRuntime.cancel(run_id, tenant_id, user_id)`

原子地将非终态 run 标为 cancelled，并通知同进程的 cooperative cancellation token。已经进入系统
调用的同步副作用不能被 Python 强制终止，非幂等未知结果仍需人工对账。

### `BusinessAgentRuntime.decide_approval(...)`

批准或拒绝 pending approval。相同 reviewer 的相同决定可幂等重试；相反决定抛出
`AgentApprovalError`。拒绝会作为 tool result 返回模型，不直接把 run 标为失败。

### `BusinessAgentRuntime.reconcile_tool_call(...)`

仅处理 `reconciliation_required` tool call。operator 明确确认外部调用成功或失败后，run 回到
pending，可以通过 `resume()` 继续。

### `BusinessAgentRuntime.compensate_tool_call(...)`

仅补偿 succeeded tool call；工具必须实现 compensator。补偿是显式运维动作，不由模型自动触发。

### `AgentStateStore`

持久化协议包含 run、checkpoint、turn、tool call、approval 的创建、读取和乐观更新，以及 run
claim/renew/cancel。内建实现为 `InMemoryAgentStateStore` 和 `SQLiteAgentStateStore`。

SQLite agent payload 可通过 `StateCodec` 编码；默认 `JsonStateCodec` 是明文，生产环境应使用
KMS/信封加密 codec 或全盘加密。完整状态机和故障矩阵见
[`business-agent-runtime-v0.3.0.md`](business-agent-runtime-v0.3.0.md)。

## 受控多 Agent 编排

### `AgentOrchestrator.run(request)`

输入：包含有向无环 `AgentGraph`、tenant/user/session/request 身份的
`OrchestrationRequest`。所有节点必须预先注册，并通过 `OrchestrationPolicy` 的 Agent 白名单、节点、
扇出、深度、并发、Token 和超时校验。

输出：`AsyncIterator[OrchestrationEvent]`。根节点可以并行执行；下游节点只在全部依赖完成后启动。
父编排 ID 由 tenant + request_id 确定性生成，并发重复请求只会得到 busy、终态重放或同一执行流，
不会创建第二个逻辑编排。

### `AgentOrchestrator.resume(...)`

恢复等待人工审批、对账或因可重试超时回到 pending 的编排。节点子请求 ID 固定，已完成子 Run
直接重放，运行中的子 Run 返回 busy 并由调度器有界重试。

### `AgentOrchestrator.decide_approval(...)` / `reconcile_tool_call(...)`

人工决定必须同时匹配父 tenant/user、task_id 和节点 child_run_id。即使多个 Agent 共享同一个
`AgentStateStore`，也不能使用一个节点的入口处理另一个 Run 的 approval/call。

### `AgentOrchestrator.cancel(...)`

先原子取消父编排，再传播 cooperative token、显式取消已知子 Run，并用乐观重试把未终止节点
收敛到 cancelled。

### `OrchestrationStateStore`

持久化父 Run、节点记录、请求幂等键、乐观版本和 fenced lease。内建
`InMemoryOrchestrationStore` 与 `SQLiteOrchestrationStore`；完整协议、预算和故障边界见
[`controlled-orchestration-cli-v0.4.0.md`](controlled-orchestration-cli-v0.4.0.md)。

## Python 运行时

### `AgentMemoryRuntime.ingest(event)`

输入：`Event` 或符合事件结构的字典。

副作用：

- 在追加前执行敏感载荷检测、标签补全和最小化；原始敏感文本不进入 `EventStore`
- 将最小化后的事件追加到 `EventStore`
- 派生记忆候选
- 校验来源和信息流
- 新增或更新正式的 `MemoryRecord` 对象
- 保存 `RuntimeSnapshot`
- 若检测到 PII/凭据，写入 `pii` 类型 `AuditEnvelope`

当运行时注入共享 `TransactionManager`（例如 `SQLiteStoreBundle`）时，上述事件、记忆和快照
副作用在一个事务内提交；任一校验失败会回滚全部写入。

输出：`IngestResult`。

### `AgentMemoryRuntime.retrieve(query)`

输入：`MemoryQuery` 或符合查询结构的字典。

输出：`(list[MemoryRecord], RuntimeTrace)`。

除更新 `runtime.last_trace` 和写入 `access` 审计外，不产生状态变更。

`MemoryQuery.session_policy` 支持：

- `exact`：指定 session 时，所有记忆层只读取该 session；这是兼容默认值。
- `profile`：Working 仍限定当前 session，Core 和被 planner 启用的 Archival 可跨 session。
- `all`：所有启用层均可跨 session，用于显式历史检索和运维工具。

会话策略不改变 tenant/user/agent 访问控制。`session_id=None` 表示调用方没有提供会话过滤条件；生产
交互请求应始终传入 session ID。SQLite Store 会先用结构化身份、层、状态、类型、scope、标签和词项
索引读取有限候选，再进入统一的 `hard_filter`、`AccessChecker` 和精排。

### `AgentMemoryRuntime.project(query)`

输入：`MemoryQuery` 或符合查询结构的字典。

输出：`AgentContext`，包含已选记忆 ID、阻止数量、投影上下文、已投影记忆载荷和检索追踪。

`AgentContext.personalization` 与 `personalization_context` 只来自 Core belief 中白名单 key 的结构化
`metadata.value`。自由文本偏好不会成为可信指令；语言、详细程度、语气、时区和无障碍值经过固定
格式/枚举校验后，才进入系统控制的 `<personalization-profile>` 块。

### `AgentMemoryRuntime.project_fast(query)`

输入：`MemoryQuery` 或符合查询结构的字典。

处理：并行执行完整检索，并立即准备 Snapshot 热点记忆上下文。完整检索在
`RuntimeConfig.fast_response.retrieval_timeout_ms` 内完成则使用完整上下文；超时则使用
`RuntimeSnapshot.hot_memory_ids` 指向的热点记忆。热点记忆仍会经过 `RetrievalPipeline` 和访问校验。

输出：`AgentContext`。`AgentContext.metadata` 会标记 `context_source` 和 `retrieval_timed_out`。

除更新 `runtime.last_trace` 和写入 `access` 审计外，不产生状态变更。

### `AgentMemoryRuntime.replay(events=None)`

输入：可选事件列表，默认使用当前 `EventStore`。

副作用：

- 清空 `MemoryStore`
- 通过派生和生命周期链路重新应用事件
- 保存快照

输出：`RuntimeSnapshot`。

Replay 会查询 `TombstoneStore`。事件 sequence 不高于删除水位的同 ID 候选不会复活；删除水位之后的
新显式事件可以重新创建该语义记忆。读取路径也执行相同水位检查，防止 JSONL 在 Tombstone 已落盘、
物理投影尚未删除时崩溃而重新暴露数据。

### `RetentionWorker`

`run_once()` 在一个可用的共享事务中完成计划、归档/删除、Tombstone、审计和 Snapshot 刷新。
`run_forever(stop_event=..., max_cycles=..., on_cycle=...)` 使用可中断等待，返回常量内存的累计报告，
不会把无限周期历史保存在进程内。Snapshot Store 在每次保存后只保留配置的最近 N 份。

### `TokenEstimator`

`AdaptiveTokenEstimator` 提供保守 Unicode/CJK 估算，并可按模型注册原生计数器；
`CallableTokenEstimator` 可直接适配供应商 tokenizer。ContextBuilder 和 BusinessAgentRuntime 共享该
协议，分别约束记忆投影预算和完整模型调用预算。

### `AgentMemoryRuntime.ingest_async(event)`

输入：`Event` 或符合事件结构的字典。
处理：

- 先执行与 `ingest` 相同的敏感最小化。
- 立即把最小化事件写入 `EventStore`。
- 创建 `DerivationJob` 并写入 `derivation_queue`。
- 保存当前 `RuntimeSnapshot`，但不派生 `MemoryRecord`。

输出：`AsyncIngestResult`，包含已落库事件和队列任务。

### `AgentMemoryRuntime.run_derivation_once()`

输入：无。
处理：从 `derivation_queue` 领取一个 pending job，按事件 ID 读取源事件并执行派生、审核、写入和快照保存。成功时任务标记为 `succeeded`；失败时增加 attempts，未超过重试次数则回到 `pending`，超过后进入 `dead_letter`。失败审计只记录错误类型和错误 hash。
输出：处理后的 `DerivationJob`，若没有 pending job 则返回 `None`。

### `AgentMemoryRuntime.approve_review_item(review_id, reviewer_id, reason=None)`

输入：审核项 ID、审核人 ID 和可选原因。
处理：读取 pending 审核项，把其中的 `MemoryCandidate` 按正常 `WriteGuard` 和 `LifecycleReducer` 写入 `MemoryStore`，随后把审核项标记为 approved 并写入 `human_review` 审计。
输出：批准写入的 `MemoryRecord`；审核项不存在、已处理或源事件不存在时返回 `None`。

### `AgentMemoryRuntime.respond(query, instruction=None)`

输入：`MemoryQuery` 或符合查询结构的字典，以及可选的非机密应用指令。

处理：先执行 `project`，再通过 `OpenAICompatibleChatClient` 调用 OpenAI 兼容的 Chat Completions
接口。CLI 可用 `--provider` 选择 `deepseek`、`openai`、`gemini`、`qwen`、`zai`、`kimi` 或 `custom`；后者
必须同时提供 `--model`、`--base-url` 和 `--api-key-env`。密钥仅从对应环境变量或本地 `.env` 读取，
绝不进入 `RuntimeConfig`、快照或追踪输出。

输出：`AgentResponse`，包含模型回答、模型标识、可选用量与对应的 `AgentContext`。

副作用：无记忆状态变更。模型输出需要由调用方显式转换为 `Event` 后才能进入写入链路。

无论调用成功或失败，都会追加一条 `llm_call` 类型 `AuditEnvelope` 到 `AuditStore`。该记录包装
`LLMCallTrace`，只包含调用元数据、选中记忆 ID、快照定位字段、用量以及请求/响应哈希；metadata
包含 `system_prompt_hash`、`memory_context_hash` 和 `user_query_hash`。它不包含查询、系统提示、
上下文、回答、API 密钥或异常消息。

### `AgentMemoryRuntime.respond_fast(query, instruction=None)`

输入和输出与 `respond` 相同，但上下文构建使用 `project_fast`。适合对首字延迟敏感、允许在完整检索
超时时退回热点 Snapshot 的交互式回答。

副作用：不写入记忆；成功或失败时写入 `llm_call` 审计，metadata 记录 `context_source`。

### `AgentMemoryRuntime.respond_stream(query, instruction=None, fast_path=True)`

输入：`MemoryQuery` 或符合查询结构的字典，以及可选应用指令。`fast_path=True` 时使用 `project_fast`。

输出：`Iterator[AgentResponseStreamEvent]`：

- `started`：上下文已构建，可读取 `context`。
- `token`：模型返回的非空 delta，首个 token 事件包含 `first_token_ms`。
- `completed`：包含聚合后的 `AgentResponse`。

副作用：不写入记忆；完成或失败时写入 `llm_call` 审计。审计 metadata 记录 `stream`、
`context_source` 和 `first_token_ms`，仍不保存提示词、上下文或回答正文。

## 审计 Store

`AuditStore` 支持：

- `append_envelope(envelope)`
- `list_envelopes()`
- `append_trace(trace)`：兼容旧的 `LLMCallTrace` 写入
- `list_traces()`：兼容旧的 LLM 调用审计查询

新实现位于 `agent_memory_runtime.audit.stores`，旧的 `agent_memory_runtime.memory.stores` 导入路径
仍然可用。

## CLI 契约

`amem` CLI 提供 `init`、`ingest`、`derive`、`retrieve`、`project`、`respond`、`chat`、`queue`、
`retention`、`audit`、`replay`、`eval` 以及三个演示命令。`retrieve`、`project`、`respond` 支持
`--tenant`、`--user`、`--session` 和 `--session-policy`；`respond` 另支持 `--stream`、`--fast` 和
`--retrieval-timeout-ms`。`init` 会创建 `audit.jsonl` 和 `derivation_queue.jsonl`，`audit` 输出已持久化的无原文审计记录。

治理命令：

```powershell
amem ingest examples/data/customer_support_events.jsonl --async-derive
amem queue
amem queue run-once
amem worker
amem retention plan --archive-after-seq 30 --archive-below-salience 0.2
amem retention apply --delete-sensitive-after-seq 10
amem retention worker --forever --interval-seconds 300
amem audit-dashboard --out .amem/audit.html
```

`amem eval` 输出每个 case 与整体的 Recall@K、Precision@K、MRR、nDCG；任一 expected 未在 K 截断内
命中或任一 forbidden ID 出现在返回结果中时，命令以退出码 1 结束，可直接作为 CI 质量门禁。

## Tool Runtime API

### `ToolRegistry.register(tool)`

注册一个工具对象。工具对象需要提供 `name`、`description`、`side_effects` 和 `run(arguments)`。

### `ToolExecutor.execute(request)`

输入：`ToolRequest`，包含工具名、参数、调用者、Agent、会话和可选标签。

处理：

- 从 `ToolRegistry` 找到工具。
- 用 `ToolPolicy` 校验 allowed/blocked tool 和 side effect 策略。
- 执行工具并返回 `ToolResult`。
- 写入 `tool_call` 审计。
- 将结果规范化为 `Event(kind="tool.result")`。

输出：`ToolExecution`，包含原始请求、工具结果和可进入记忆链路的事件。

### 内置工具

- `FunctionTool`：把 Python callable 包装为 function calling 工具。
- `FileReadTool`：读取配置根目录内的 UTF-8 文本文件。
- `FileWriteTool`：写入配置根目录内的 UTF-8 文本文件。
- `WebSearchTool`：通过注入 provider 执行搜索。

工具审计只保存参数 hash、输出 hash、字段名、错误类型和规范化事件 ID，不保存原始参数或输出正文。

`audit` 支持：

```powershell
amem audit --type llm_call
amem audit --type access
amem audit --type pii
amem audit --outcome blocked
amem audit --subject event:evt-1
```
每次子命令执行都会先输出 AMEM ASCII 启动横幅；随后追踪输出必须包含：

- `selected_memory_ids`
- 评分明细
- 被阻止的记忆数量
- `rule_version`
- `config_hash`
- `last_event_sequence`
- `state_hash`
