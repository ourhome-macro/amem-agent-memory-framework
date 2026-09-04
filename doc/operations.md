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
- retention worker 应按环境设置 `--cool-hot-after-seq`，控制 hot 记忆在多少 sequence 后降到 warm。
- 删除通过 tombstone 表达水位，避免审计重放时恢复已删除记忆。

## Recommend Radio 性能

- 推荐 API 的 `timing` 输出 L2、画像、LLM API、候选池、打分和 MMR span。
- Discovery job 记录每条 B站搜索和候选准入耗时。
- 画像缓存命中不再调用 LLM；默认候选库存低于 32 条时后台预热。
- 首轮主要关注画像 LLM 与 Discovery 延迟，排序路径通常不是瓶颈。
