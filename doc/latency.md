# Latency

长期记忆写入不再通过事件派生队列降低首字延迟。当前方案把用户显式写入和 Auto Dream 整理都收敛为 `MemoryProposal`，在 SQLite 事务内完成当前状态与审计落库。

## 写入延迟边界

```text
MemoryProposal
 -> MemoryWritePolicy
 -> MemoryService
 -> MemoryRecord + MemoryAuditLog
 -> embedding outbox
```

远程向量索引不在写入事务内执行。写入只保留 embedding outbox，后台 worker 负责重试和发布投影。

## 读取快路

`project_fast`、`respond_fast` 和 `respond_stream(fast_path=True)` 仍使用 snapshot 热点记忆快速构建上下文；完整检索在预算内完成则替换快路上下文，超时则使用 snapshot fallback。

```text
RuntimeSnapshot.hot_memory_ids
 -> MemoryStore.get
 -> RetrievalPipeline
 -> AccessChecker
 -> ContextBuilder
```

## 不再使用的方案

已删除事件派生队列、后台派生 worker 和 replay 派生恢复。`ingest_async(event)` 只保留兼容方法签名，返回 `job=None`。
