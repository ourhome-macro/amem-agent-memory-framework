# Governance

治理层现在只做确定性边界，不做语义合并。

## 保留组件

- `MemoryValidator`：字段完整性、类型、默认值。
- `AccessPolicy`：tenant/user/agent/session 权限边界。
- `RiskGuard`：PII、凭证、支付、医疗、扩大可见性、跨主体修改等高风险拦截。
- `RetentionPlanner/RetentionExecutor/RetentionWorker`：归档、删除、tombstone 和审计。
- `ReviewGuard`：人工审核队列，可用于 proposal 审核流。
- `PiiProtector`：敏感值 token 化和 vault 边界。

## 已移除组件

旧的事件派生队列、候选规则、生命周期 reducer 和 write guard 已从主源码删除。语义裁决由 Auto Dream 生成 `MemoryProposal`，安全和权限由 `MemoryWritePolicy` 的确定性代码负责。

## 审计语义

- proposal 写入记录 `MemoryAuditLog`，含 before/after、证据、来源 id、置信度和原因。
- 兼容事件只记录 `memory_event_audit`。
- 工具调用、读取、LLM 调用、PII 发现仍写脱敏 `AuditEnvelope`。

删除必须先写 tombstone，再移除当前 `MemoryRecord` 和检索投影；读取路径以 tombstone 作为权威删除水位。
