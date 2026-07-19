# 首字响应优化

## 目标

首字响应优化的目标是降低用户从发出消息到看到第一个模型 token 的等待时间，同时不破坏事件源、
访问控制、审计和回放一致性。

当前版本优先实现读取快路径和流式响应；真正的后台记忆治理 worker 尚未引入。原因是异步写入一旦
落地，就必须同时定义 pending event、worker 幂等、失败重试、派生进度和回放一致性，否则会把
EventStore 与 MemoryStore 的关系变成不可解释的隐式状态。

## 快路径

默认完整路径仍然是：

```text
MemoryQuery
 -> RetrievalPipeline
 -> AccessChecker
 -> ContextBuilder
 -> OpenAICompatibleChatClient
```

首字快路径由 `project_fast` 和 `respond_fast` 提供：

```text
MemoryQuery
 -> Snapshot hot_memory_ids
 -> MemoryStore.get(memory_id)
 -> RetrievalPipeline(records=hot records)
 -> ContextBuilder
 -> LLM

并行：
MemoryStore.list_records
 -> 完整 RetrievalPipeline
 -> 150ms 内完成则使用完整上下文
 -> 超时则使用 Snapshot 上下文
```

`RuntimeSnapshot` 只保存 `hot_memory_ids`，不复制记忆正文。快路径按 ID 读取 `MemoryRecord` 后，
仍然经过 `RetrievalPipeline`，因此 scope、label、owner、`visible_to`、候选预算和围栏清洗不会被绕过。

## 当前消息

当前用户消息不会等待记忆派生完成。`respond`、`respond_fast` 和 `respond_stream` 都会把
`MemoryQuery.text` 作为独立的 user prompt 发送给模型；记忆围栏只放历史召回上下文。

这保证了本轮输入一定进入模型，同时避免把当前消息伪装成历史记忆。

## 归档记忆

默认查询只检索 `core` 和 `working` 层。查询出现回忆意图时，planner 才把 `archival` 层加入候选：

```text
previous
last time
remember
之前
以前
上次
还记得
曾经
```

这样普通问题不会为了长期归档记忆扫描付出额外成本；用户明确询问历史时仍能召回归档层。

## 流式响应

`respond_stream` 返回 `AgentResponseStreamEvent`：

- `started`：上下文已经构建，可读取 `context` 和 `selected_memory_ids`。
- `token`：第一个非空 delta 到达时记录 `first_token_ms`。
- `completed`：聚合完整 `AgentResponse`，写入 LLM 审计。

`first_token_ms` 从 `respond_stream` 开始迭代时计算，包含上下文构建、快路径降级和模型首 token
耗时。审计只记录数值和上下文来源，不记录提示词、上下文正文或模型回答。

## CLI

```powershell
amem respond --agent support_agent --query "退款进度怎么样" --stream
amem respond --agent support_agent --query "退款进度怎么样" --fast
amem respond --agent support_agent --query "退款进度怎么样" --stream --fast --retrieval-timeout-ms 150
```

CLI trace 会输出：

- `context_source`：`retrieval`、`fast_retrieval` 或 `snapshot`
- `retrieval_timed_out`
- `first_token_ms`
- `selected_memory_ids`
- `blocked_memory_count`
- `rule_version`
- `config_hash`
- `last_event_sequence`
- `state_hash`

## 后续异步写入

后台记忆治理建议作为下一阶段实现，接口应保持：

```text
append_event_only(event)
 -> EventStore
 -> pending_derivation_queue

background worker
 -> Derivation
 -> Lifecycle
 -> MemoryStore
 -> RuntimeSnapshot
```

必须先补齐：

- pending event 的持久化进度
- worker 幂等和重试
- 事件已追加但记忆未派生时的 snapshot 标记
- replay 对 pending 状态的校验
- CLI 调试 pending 队列和失败原因

在这些能力完成前，不应把写入异步化伪装成已经具备生产一致性。
