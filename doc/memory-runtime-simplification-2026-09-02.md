# 记忆运行时简化方案 2026-09-02

## 为什么要砍

旧模型把多个语义混在公共字段里：

- `layer` 同时表达抽象层级、生命周期、召回范围和稳定偏好选择。
- `scope` 和 `visible_to` 重叠，ACL 语义不清楚。
- lifecycle action 里混入 review、archive、move 等状态或治理意图。
- retrieval router 在没有评测证明的情况下提前决定 lexical/vector/hybrid，导致检索链路多一层不可解释决策。
- 排序 factor 过多，接近手写 learning-to-rank，线上排查困难。
- JSONL、SQLite vector、Qdrant 看起来像平等后端，事务和恢复语义容易发散。
- 全量 embedding 浪费资源，也让 L3 profile 被向量召回误导。

简化目标是保留生产安全边界，砍掉重复语义和隐藏决策。

## 目标形态

- SQLite：事实源。
- Qdrant：可重建语义索引。
- JSONL：导出、备份、调试。
- SQLite vector：测试和本地开发 fallback。
- 记忆形态：`level`、`status`、`visibility`、`priority`。
- 写入动作：`create`、`merge`、`supersede`、`ignore`、`delete`。
- 检索链路：query normalization、结构化过滤、hybrid retrieval、RRF、硬过滤、上下文预算。

## 兼容映射

历史 payload 和旧调用在边界转换：

- `layer=core` -> `level=L3`
- `layer=working` -> `level=L1`
- `layer=archival` -> `level=L1`，默认 `status=archived`
- `scope=private` -> `visibility=private`
- `scope=shared` -> `visibility=shared`
- `scope=global` -> `visibility=public`
- `reinforce` -> `merge`
- `revise` -> `merge`
- `archive` -> `status=archived`
- `keep_both` -> `create`
- `needs_review` -> `decision_status=pending_review`
- `move_layer` -> 不再存在，对应 level/status 更新或 review

兼容只存在于输入边界和历史数据加载，不作为新公共契约。

## 第一轮执行

- 引入 `MemoryLevel`、`MemoryVisibility` 和收敛后的 canonical operation。
- 把 memory record、proposal、projection、query 逐步迁到 `level/status/visibility/priority`。
- hybrid retriever 默认同时使用配置启用的关键词和语义召回。
- 保留 ACL、tombstone、audit replay、embedding outbox 和 Qdrant projection。

## 第二轮完成

第二轮移除了旧公共 domain surface。新 record、proposal、query、tool schema 和 retrieval 主链路不再输出或消费旧字段。历史 SQLite payload 仍可在加载时转换到新模型。
