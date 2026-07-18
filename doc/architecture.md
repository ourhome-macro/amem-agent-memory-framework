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
 -> EventStore.append
 -> DerivationEngine.derive
 -> WriteGuard.validate
 -> LifecycleReducer.reduce
 -> MemoryStore.upsert
 -> RuntimeSnapshot
```

系统拒绝没有来源事件的候选记忆。私有记忆不能通过后续写入提升为 shared/global 记忆，
敏感标签也不能被静默移除。

## 读取链路

```text
MemoryQuery
 -> RetrievalPipeline
 -> hard filters
 -> AccessChecker
 -> scoring/rerank/budget
 -> ContextBuilder
 -> OpenAICompatibleChatClient（可选）
 -> Agent response
```

硬过滤会移除会话、类型、作用域、层级、标签或状态不匹配的记录。随后访问校验会阻止
未授权的 private 和 sensitive 记录。评分综合关键词重合度、时效性、显著性、置信度、
类型加权、强化次数和来源链接信号。

`OpenAICompatibleChatClient` 是可选的末端读取消费者，内置 DeepSeek、OpenAI、Gemini、Qwen、
Z.AI/GLM 和 Kimi 预设，并允许传入自定义兼容端点。它仅接收已投影的上下文，不持有 Store，也不具备记忆
写入能力。要把模型输出转化为长期记忆，应用必须创建新的 `Event` 并重新进入写入链路。

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

## 存储接口

存储接口被有意拆分为：

- `EventStore`：只保存原始事件。
- `MemoryStore`：保存正式派生出的 `MemoryRecord` 对象。
- `SnapshotStore`：保存运行时快照和回放检查点。

框架提供内存、JSONL 和 SQLite 实现。SQLite 可以作为三类存储的底层数据库，但运行时仍通过
相互独立的接口调用它们。
