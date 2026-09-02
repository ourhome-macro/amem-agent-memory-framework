# 架构

AMEM 是面向有状态 AI Agent 的长期记忆运行时。SQLite 保存持久事实状态，FTS5 和 Qdrant 提供可重建检索投影。Agent 代码通过 runtime API 访问记忆，不直接读取或修改底层存储。

## 主写入链路

```text
save/revise/forget 工具或 Auto Dream
  -> MemoryProposal
  -> MemoryWritePolicy
  -> MemoryService
  -> MemoryRecord
  -> MemoryAuditLog
  -> embedding outbox
  -> Qdrant vector projection
```

`MemoryRecord` 的主语义是 `level`、`status`、`visibility` 和 `priority`。历史 payload 如果含有旧字段，会在加载时转换；新记录和新查询接口不再输出旧字段。

## 主读取链路

```text
MemoryQuery
  -> query normalization
  -> structured filters
  -> FTS5 and Qdrant candidates when configured
  -> RRF fusion
  -> hard filters, deterministic guards, final filter
  -> AccessChecker
  -> ContextBuilder
```

检索链路不再使用独立 router 来决定 lexical/vector/hybrid。Query understanding 只负责抽取过滤条件；召回腿是否运行由配置决定，候选结果通过 RRF 融合。

## 核心边界

- `memory.intake`：把工具输入和 Auto Dream 输出转换为 proposal。
- `memory.write_policy`：做字段、身份、版本、权限和风险校验。
- `memory.service`：应用通过校验的 proposal，并写审计记录。
- `memory.retrieval`：在授权边界内召回、融合、过滤和预算选择。
- `context`：把选中的记忆渲染成模型可见上下文。
- `audit`：记录写入历史和审计重放输入。
- `agent`：协调模型调用、工具循环、checkpoint 和记忆投影。

## 设计原则

- SQLite 是事实源；Qdrant、FTS5、SQLite vector 都是投影。
- L3 profile 数量少且影响大，profile-aware 查询直接加载，不依赖向量搜索。
- 规则引擎只做确定性约束，不承担隐藏检索路由或复杂排序。
- 旧 payload 兼容只存在于入口和迁移层，不能扩散到新公共契约。
