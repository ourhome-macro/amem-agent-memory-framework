# API Contract

## MemoryProposal

长期记忆写入统一使用 `MemoryProposal`：

```text
proposal_id
source
action: create | reinforce | revise | supersede | archive | delete | keep_both | needs_review
target_memory_id
subject_id
key
content
memory_type
layer
scope
visible_to
confidence
salience
source_message_ids
source_memory_ids
evidence_text
reason
dream_run_id
dream_version
```

`save_memory`、`revise_memory`、`forget_memory` 和 Auto Dream 都只产生 proposal，再交给 `MemoryService.apply_proposal`。

## AgentMemoryRuntime.apply_memory_proposal(proposal)

处理：

- 调用 `MemoryWritePolicy` 做 schema、权限、安全、scope/layer/visible_to 不变量校验。
- 在事务内 create/update/archive/delete `MemoryRecord`。
- 写入 `MemoryAuditLog`，保留 before/after、证据、来源 message/memory id。
- 删除写 tombstone。
- 写 embedding outbox；向量索引失败不影响 SQLite 当前状态。

输出：`MemoryProposalResult`。高风险返回 `needs_review`，乐观锁冲突返回 `conflict` 且 `retryable=True`。

## AgentMemoryRuntime.ingest(event)

处理：

- 脱敏事件。
- 幂等写入 `EventStore`。
- 写 `memory_event_audit`。
- 刷新快照。

输出：`IngestResult(event, records=())`。该接口不再派生 `MemoryRecord`。

## AgentMemoryRuntime.ingest_async(event)

语义与 `ingest` 相同，返回 `AsyncIngestResult(event, job=None)`。保留该方法只是为了兼容旧调用方，不再创建后台派生任务。

## AgentMemoryRuntime.replay(events=None)

只重放兼容事件审计，不清空、不重建 `MemoryStore`。长期记忆状态由 `MemoryRecord + MemoryAuditLog + tombstone` 决定。

## CLI

```powershell
amem init
amem ingest events.jsonl
amem retrieve --agent assistant --query "..."
amem project --agent assistant --query "..."
amem respond --agent assistant --query "..."
amem retention plan
amem retention apply
amem audit
amem audit-dashboard --out .amem/audit.html
amem embedding status
amem embedding worker
```

旧派生命令族已删除，CLI 不再提供事件派生、队列消费或 legacy 派生开关。
