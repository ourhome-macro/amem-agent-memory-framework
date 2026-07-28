# 架构

## 定位

Agent Memory Runtime 的长期记忆真实状态是 `MemoryRecord`。写入历史和证据进入 `MemoryAuditLog` 与脱敏 `AuditEnvelope`。兼容 `Event` 仍可入库，但只作为审计输入和历史兼容层，不再派生、合并或重建记忆。

## 写入主链路

```text
save/revise/forget tools + Auto Dream
 -> MemoryProposal
 -> MemoryWritePolicy
    -> MemoryValidator
    -> AccessPolicy
    -> RiskGuard
 -> MemoryService.apply_proposal
 -> MemoryRecord
 -> MemoryAuditLog
 -> embedding outbox / tombstone
```

语义问题只交给 Auto Dream 形成 proposal：补漏、冲突、去重、归并、低价值建议。确定性代码只处理边界：schema、权限、scope/layer/visible_to 不变量、敏感信息、高风险审核、乐观锁和幂等。

## Event 兼容层

```text
Event
 -> SensitiveDataSanitizer
 -> EventStore.append
 -> AuditEnvelope(memory_event_audit)
 -> RuntimeSnapshot
```

`runtime.ingest(event)` 和 `runtime.ingest_async(event)` 不再写 `MemoryRecord`，也不创建派生队列任务。`runtime.replay()` 只重放事件审计状态，不清空或重建长期记忆。

## 读取链路

```text
MemoryQuery
 -> normalize_query
 -> SQLite FTS5 / sqlite-vec candidates
 -> RetrievalPipeline
 -> AccessChecker
 -> scoring / rerank / budget
 -> ContextBuilder
 -> AuditEnvelope(access)
```

Qdrant/sqlite-vec/FTS 都是投影和召回辅助，不是真实数据源。SQLite `MemoryRecord` 才是当前状态。

## 治理边界

`src/agent_memory_runtime/governance/` 只保留 retention、review、PII vault 等确定性治理能力。旧的事件派生队列、规则引擎、生命周期 reducer 已从主包物理删除。

## 存储边界

- `MemoryStore`：当前 `MemoryRecord` 状态。
- `AuditStore`：`MemoryAuditLog` 与脱敏 `AuditEnvelope`。
- `EventStore`：兼容事件审计，不参与长期记忆生成。
- `TombstoneStore`：删除水位，防止已删除内容被读取路径复活。
- `SnapshotStore`：运行时快照，用于快速上下文和一致性定位。
