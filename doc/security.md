# 安全设计

安全和权限问题由确定性代码负责，语义冲突和归并交给 Auto Dream 产生 proposal。

## 写入边界

`MemoryWritePolicy` 组合：

- `MemoryValidator`：schema、类型、默认值。
- `AccessPolicy`：tenant/user/agent/session 权限边界。
- `RiskGuard`：PII、凭证、支付、医疗、高风险删除、扩大可见性、跨主体修改。

高风险 proposal 返回 `needs_review` 或 `rejected`，不会直接覆盖当前 `MemoryRecord`。

## 事件边界

`sanitize_event` 仍在 `EventStore.append` 前执行。事件只进入兼容审计链路，不再派生长期记忆，因此旧事件不能通过 replay 复活已经删除或归档的内容。

## 读取边界

`AccessChecker` 对检索结果执行 scope、owner、visible_to 和 label 校验。带 tombstone 的记忆在读取路径被隐藏。

## 审计

- proposal 写入使用 `MemoryAuditLog` 保存 before/after、证据、来源 id、置信度和原因。
- 兼容事件使用 `AuditEnvelope(memory_event_audit)`。
- access、pii、llm_call、tool_call 继续写脱敏 `AuditEnvelope`，不保存 prompt、上下文、回答或异常原文。
