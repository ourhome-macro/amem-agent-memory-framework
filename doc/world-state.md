# 世界状态

世界状态是 runtime 管理的持久记忆状态。

## 模块职责

- `MemoryRecord`：权威记忆项，包含 type、level、status、visibility、temperature、priority、ownership、confidence、version 和 source metadata。
- `MemoryAuditLog`：有序写入历史，保存 before/after record 和证据。
- `MemoryTombstone`：删除水位，供读取路径和审计重放恢复使用。
- `RuntimeSnapshot`：状态摘要，用于 trace 和快速响应 metadata。
- `EmbeddingJob`：异步向量投影 outbox item。
- `DreamJob`：带 lease 和 checkpoint 的 Auto Dream 维护任务。

## 记忆 Level

- `L0`：原始事件材料，只在显式要求时索引。
- `L1`：记忆原子，默认 hybrid retrieval 单元。
- `L2`：场景记忆，主要依赖元数据、文本和时间召回。
- `L3`：画像记忆，profile-aware 上下文中直接加载。

生命周期由 `status` 表达：`active`、`superseded`、`archived`、`deleted`。

检索温度由 `temperature` 表达：`hot`、`warm`、`cold`。它控制召回频率、索引成本和上下文优先级，不改变记忆的认知类别。

## 事实源

当前状态以 `MemoryRecord` 为准，历史变更以 `MemoryAuditLog` 为准，删除水位以 `MemoryTombstone` 为准。索引和缓存可以重建，不能成为权威状态。
