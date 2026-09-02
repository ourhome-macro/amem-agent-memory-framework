# 记忆运行时简化完成记录 2026-09-02

## 已完成的刀

- 从公开 domain enum 中移除 `MemoryLayer` 和 `MemoryScope`。
- 从 `MemoryRecord`、`MemoryCandidate`、`MemoryProposal` 序列化中移除旧字段。
- 旧 payload 只保留加载迁移路径：历史值会转换为 `level`、`status`、`visibility`。
- 写入动作收敛为 `create`、`merge`、`supersede`、`ignore`、`delete`。
- review 使用 `decision_status=pending_review` 表达。
- 删除 retrieval router 和 routing tool 实现。
- query planning 只归一化 status 默认值，不再选择 lexical/vector/hybrid/state/temporal/no-answer 模式。
- 检索过滤改为使用 `status`、`level`、`visibility`。
- SQLite schema v9 删除旧投影列。
- embedding 调度改为：active L1 默认索引，L0 按需索引，L2/L3 不默认索引。
- profile-aware 查询直接加载 active L3 profile。

## 保留的生产边界

- SQLite 仍是事实源。
- `MemoryAuditLog` 仍保存 before/after，用于重放。
- tombstone 仍是删除水位。
- ACL prefilter 和 `AccessChecker` 二次校验继续保留。
- embedding outbox 仍是唯一向量发布路径。
- Qdrant 仍是可重建语义投影，不是事实源。

## 验证

- `py -3.12 -m ruff check src tests benchmarks`
- `py -3.12 -m pytest -q`

本轮提交会按要求排除 `tests/` 文件；测试修改只保留在本地工作树用于验证。
