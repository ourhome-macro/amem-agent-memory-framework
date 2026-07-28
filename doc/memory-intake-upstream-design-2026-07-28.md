# Memory Intake Upstream Design

日期：2026-07-28

上游不再输出长期记忆 Event。显式记忆指令和隐式 Auto Dream 整理都输出 `MemoryProposal`。

```text
natural language / tool call / dream run
 -> MemoryProposal
 -> deterministic policy
 -> MemoryService
 -> MemoryRecord
 -> MemoryAuditLog
```

LLM 可以参与语义判断，但不能绕过 proposal、policy、audit 和 tombstone。跨租户、跨用户、扩大可见性、删除、敏感信息等操作必须由确定性代码拒绝或送审。
