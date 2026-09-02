# 工具

工具运行时暴露显式 Agent 能力，并通过类型化上下文记录工具执行。

## 模块职责

- `ToolRegistry`：注册可调用工具和 schema。
- `ToolExecutor`：校验工具参数、调用 handler、记录工具审计数据。
- `ToolExecutionContext`：把 tenant、user、agent、session、run、call 身份传入 handler。
- `MemoryIntakeService`：用 `save_memory`、`revise_memory`、`forget_memory` 生成 `MemoryProposal`。
- `MemorySearchTool`：通过 runtime projection path 暴露授权记忆搜索。

## 记忆工具语义

- `save_memory`：生成 `action=create`。
- `revise_memory`：对目标记忆执行 `merge` 或 `supersede`，并使用乐观版本校验。
- `forget_memory`：删除并写 tombstone，或把目标记忆标记为 `status=archived`。

工具只提出意图，不直接绕过策略改库。最终是否落库由 `MemoryWritePolicy` 和 `MemoryService` 决定。
