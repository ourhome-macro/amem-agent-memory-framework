# API 契约

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

当运行时注入共享 `TransactionManager`（例如 `SQLiteStoreBundle`）时，上述事件、记忆和快照
副作用在一个事务内提交；任一校验失败会回滚全部写入。

输出：`IngestResult`。

### `AgentMemoryRuntime.retrieve(query)`

输入：`MemoryQuery` 或符合查询结构的字典。

输出：`(list[MemoryRecord], RuntimeTrace)`。

除更新 `runtime.last_trace` 外，不产生状态变更。

### `AgentMemoryRuntime.project(query)`

输入：`MemoryQuery` 或符合查询结构的字典。

输出：`AgentContext`，包含已选记忆 ID、阻止数量、投影上下文、已投影记忆载荷和检索追踪。

### `AgentMemoryRuntime.project_fast(query)`

输入：`MemoryQuery` 或符合查询结构的字典。

处理：并行执行完整检索，并立即准备 Snapshot 热点记忆上下文。完整检索在
`RuntimeConfig.fast_response.retrieval_timeout_ms` 内完成则使用完整上下文；超时则使用
`RuntimeSnapshot.hot_memory_ids` 指向的热点记忆。热点记忆仍会经过 `RetrievalPipeline` 和访问校验。

输出：`AgentContext`。`AgentContext.metadata` 会标记 `context_source` 和 `retrieval_timed_out`。

除更新 `runtime.last_trace` 外，不产生状态变更。

### `AgentMemoryRuntime.replay(events=None)`

输入：可选事件列表，默认使用当前 `EventStore`。

副作用：

- 清空 `MemoryStore`
- 通过派生和生命周期链路重新应用事件
- 保存快照

输出：`RuntimeSnapshot`。

### `AgentMemoryRuntime.respond(query, instruction=None)`

输入：`MemoryQuery` 或符合查询结构的字典，以及可选的非机密应用指令。

处理：先执行 `project`，再通过 `OpenAICompatibleChatClient` 调用 OpenAI 兼容的 Chat Completions
接口。CLI 可用 `--provider` 选择 `deepseek`、`openai`、`gemini`、`qwen`、`zai`、`kimi` 或 `custom`；后者
必须同时提供 `--model`、`--base-url` 和 `--api-key-env`。密钥仅从对应环境变量或本地 `.env` 读取，
绝不进入 `RuntimeConfig`、快照或追踪输出。

输出：`AgentResponse`，包含模型回答、模型标识、可选用量与对应的 `AgentContext`。

副作用：无记忆状态变更。模型输出需要由调用方显式转换为 `Event` 后才能进入写入链路。

无论调用成功或失败，都会追加一条 `LLMCallTrace` 到 `AuditStore`。该记录只包含调用元数据、
选中记忆 ID、快照定位字段、用量以及请求/响应哈希；不包含查询、系统提示、上下文、回答、
API 密钥或异常消息。

### `AgentMemoryRuntime.respond_fast(query, instruction=None)`

输入和输出与 `respond` 相同，但上下文构建使用 `project_fast`。适合对首字延迟敏感、允许在完整检索
超时时退回热点 Snapshot 的交互式回答。

副作用：不写入记忆；成功或失败时写入 `LLMCallTrace`，metadata 记录 `context_source`。

### `AgentMemoryRuntime.respond_stream(query, instruction=None, fast_path=True)`

输入：`MemoryQuery` 或符合查询结构的字典，以及可选应用指令。`fast_path=True` 时使用 `project_fast`。

输出：`Iterator[AgentResponseStreamEvent]`：

- `started`：上下文已构建，可读取 `context`。
- `token`：模型返回的非空 delta，首个 token 事件包含 `first_token_ms`。
- `completed`：包含聚合后的 `AgentResponse`。

副作用：不写入记忆；完成或失败时写入 `LLMCallTrace`。审计 metadata 记录 `stream`、
`context_source` 和 `first_token_ms`，仍不保存提示词、上下文或回答正文。

## CLI 契约

`amem` CLI 提供 `init`、`ingest`、`derive`、`retrieve`、`project`、`respond`、`audit`、`replay`、
`eval` 以及三个演示命令。`respond` 支持 `--stream`、`--fast` 和 `--retrieval-timeout-ms`。`init`
会创建 `audit.jsonl`，`audit` 输出已持久化的无原文模型调用审计。
每次子命令执行都会先输出 AMEM ASCII 启动横幅；随后追踪输出必须包含：

- `selected_memory_ids`
- 评分明细
- 被阻止的记忆数量
- `rule_version`
- `config_hash`
- `last_event_sequence`
- `state_hash`
