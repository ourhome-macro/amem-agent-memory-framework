# 存储

存储模块把持久事实状态和检索投影明确拆开。

## 模块职责

- `SQLiteStoreBundle`：组装 SQLite memory store、embedding queue、vector index、audit store、dream store、agent state 和 orchestration state。
- `SQLiteMemoryStore`：保存 `MemoryRecord` payload，并维护 FTS5、tag、ACL 和 embedding job 投影。
- `SQLiteAuditStore`：保存 audit envelope、LLM trace 和 memory audit log。
- `SQLiteTombstoneStore`：保存删除水位。
- `SQLiteEmbeddingJobStore`：保存异步 embedding outbox job。
- `SQLiteDreamStore`：保存 Auto Dream job、lease、checkpoint 和 review。
- `QdrantVectorIndex`：保存可重建向量点和检索 payload。
- `InMemory*Store`：测试和嵌入式运行时使用。
- `Jsonl*Store`：导出、备份和调试使用；不是生产事实源。

## 存储边界

SQLite 中的 memory record、audit log、tombstone、job、checkpoint 是持久运行时状态。FTS5、SQLite vector 和 Qdrant point 是可重建检索投影。

生产主线：

- SQLite：事实源。
- Qdrant：语义索引。
- JSONL：导出、备份、调试。
- SQLite vector：测试和本地开发 fallback。

不要把 JSONL、SQLite vector、Qdrant 都设计成平等正式后端。正式事实源越多，事务、恢复和故障语义越难保证。

## Embedding 调度

embedding job 按 level 调度：

- active warm `L1` atom 默认 embedding。
- active warm `L0` raw event 只有在 `metadata.embedding_index=true` 时 embedding。
- `L2` scenario 不默认 embedding，依赖元数据、文本和时间召回。
- `L3` profile 不 embedding，profile-aware 查询直接加载。

向量发布只能通过 embedding outbox。Qdrant 故障不影响 SQLite 写入。

## 冷热分层

- `hot`：仍在热队列中，优先服务低延迟上下文，普通查询可见，但默认不发布向量。
- `warm`：主索引层，承载 FTS、keywords、metadata 和 embedding。
- `cold`：归档和历史层，保留在 SQLite 和 FTS 中，默认不进入普通检索。
