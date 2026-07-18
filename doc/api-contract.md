# API 契约

## Python 运行时

### `AgentMemoryRuntime.ingest(event)`

输入：`Event` 或符合事件结构的字典。

副作用：

- 将事件追加到 `EventStore`
- 派生记忆候选
- 校验来源和信息流
- 新增或更新正式的 `MemoryRecord` 对象
- 保存 `RuntimeSnapshot`

输出：`IngestResult`。

### `AgentMemoryRuntime.retrieve(query)`

输入：`MemoryQuery` 或符合查询结构的字典。

输出：`(list[MemoryRecord], RuntimeTrace)`。

除更新 `runtime.last_trace` 外，不产生状态变更。

### `AgentMemoryRuntime.project(query)`

输入：`MemoryQuery` 或符合查询结构的字典。

输出：`AgentContext`，包含已选记忆 ID、阻止数量、投影上下文、已投影记忆载荷和检索追踪。

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

## CLI 契约

`amem` CLI 提供 `init`、`ingest`、`derive`、`retrieve`、`project`、`respond`、`replay`、`eval`
以及三个演示命令。追踪输出必须包含：

- `selected_memory_ids`
- 评分明细
- 被阻止的记忆数量
- `rule_version`
- `config_hash`
- `last_event_sequence`
- `state_hash`
