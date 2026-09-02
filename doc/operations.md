# 运维

运维能力由 runtime 配置、后台 worker、retry 机制和状态 API 共同提供。

## 模块职责

- `EmbeddingWorker`：处理 embedding outbox job 并发布向量。
- `AutoDreamWorker`：领取 dream job、运行 analyzer、应用 proposal、记录 review、推进 checkpoint。
- `WorkerConfig`：定义 batch size、lease duration、retry delay 和 retry limit。
- `semantic_status`：报告 embedding generation、coverage、ready vector、job count 和 backlog lag。
- `activate_embedding_generation`：在覆盖率和 pending job 检查通过后切换 active embedding generation。
- `delete_retired_embedding_generation`：删除退役向量 generation。

## 故障边界

记忆写入先提交到 SQLite，再通过 outbox 异步发布向量。向量发布失败只会留下可重试 job，不改变已提交的记忆事实状态。

## 推荐生产策略

- 对 SQLite 做常规备份和恢复演练。
- 对 Qdrant 做可重建索引处理，不把它当事实源。
- embedding worker 和 Auto Dream worker 都应有明确 lease、retry 和 checkpoint。
- 删除通过 tombstone 表达水位，避免审计重放时恢复已删除记忆。
