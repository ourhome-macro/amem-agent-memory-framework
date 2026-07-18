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
 -> RetrievalPipeline
 -> hard filters
 -> AccessChecker
 -> scoring/rerank/budget
 -> ContextBuilder
 -> OpenAICompatibleChatClient（可选）
 -> AuditStore.append_trace
 -> Agent response
```

硬过滤会移除会话、类型、作用域、层级、标签或状态不匹配的记录。随后访问校验会阻止
未授权的 private 和 sensitive 记录。评分综合关键词重合度、时效性、显著性、置信度、
类型加权、强化次数和来源链接信号。

`OpenAICompatibleChatClient` 是可选的末端读取消费者，内置 DeepSeek、OpenAI、Gemini、Qwen、
Z.AI/GLM 和 Kimi 预设，并允许传入自定义兼容端点。它仅接收已投影的上下文，不持有 Store，也不具备记忆
写入能力。要把模型输出转化为长期记忆，应用必须创建新的 `Event` 并重新进入写入链路。

模型调用成功和失败都会生成 `LLMCallTrace`。审计记录保存调用来源、模型、选中记忆 ID、用量和
回放定位信息；请求、上下文、回答和异常消息只参与哈希计算，不能从审计存储中读取原文。

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
- `AuditStore`：保存不含提示词和回答原文的 `LLMCallTrace`。

框架提供内存、JSONL 和 SQLite 实现。SQLite 可以作为三类存储的底层数据库，但运行时仍通过
相互独立的接口调用它们。`SQLiteStoreBundle` 让 Event、Memory 与 Snapshot Store 共享一个
事务管理器，因此一次 `ingest` 或 `replay` 要么全部提交，要么全部回滚。JSONL 各文件独立，
不提供这一跨文件原子性保证。
