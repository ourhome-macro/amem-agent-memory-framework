# 审计

审计模块记录记忆变更、访问决策、模型调用 metadata 和历史事件观测。

## 模块职责

- `MemoryAuditLog`：保存 proposal 写入前后的记忆状态。
- `AuditEnvelope`：保存 access、model、event observation 等归一化审计记录。
- `AuditStore`：持久化 audit envelope、LLM trace 和 memory audit log。
- `replay_memory_audit_logs`：从有序 `MemoryAuditLog` 重建记忆状态。
- `RuntimeTrace`：暴露一次 runtime 请求中的检索、投影和模型调用 metadata。

## 记忆重放语义

- `after_record` 存在：重放时 upsert 该记录作为当前记忆状态。
- `after_record` 不存在：重放时删除该 memory id。
- delete 日志会从 `before_record` 和 audit metadata 重建 tombstone。
- 重放完成后通过 memory store 重建检索投影。

## 保留理由

`MemoryAuditLog` 是生产必须保留的边界。它让写入可追踪、状态可恢复、删除水位可重建，也让后续排查 LLM/Auto Dream 写入问题时有证据链。
