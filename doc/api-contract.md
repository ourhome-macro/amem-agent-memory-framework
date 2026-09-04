# API 契约

本文档列出 runtime 模块之间的公开数据契约。

## MemoryQuery

`MemoryQuery` 描述一次记忆检索请求。

- 身份字段：`tenant_id`、`user_id`、`agent_id`、`session_id`。
- 查询字段：`text`、`limit`、`tags`、`memory_types`、`levels`、`statuses`、`visibilities`、`temperatures`。
- 会话策略：`session_policy` 控制 exact-session、profile 或 broad recall。

`MemoryQuery` 不再暴露旧的层级或混合可见性字段。旧字典输入如果仍带历史字段，只能在 runtime 边界转换。

## MemoryProposal

`MemoryProposal` 是 `MemoryService` 接受的写入意图。

- 身份字段：`actor_id`、`tenant_id`、`user_id`、`agent_id`、`session_id`。
- 写入目标：`action`、`target_memory_id`、`subject_id`、`key`、`content`。
- 写入动作：`create`、`merge`、`supersede`、`ignore`、`delete`。
- 审核路由：用 `decision_status=pending_review` 表达，不作为写入动作。
- 记忆形态：`memory_type`、`level`、`status`、`visibility`、`temperature`、`priority`、`visible_to`、`labels`、`tags`。
- 证据字段：`source_message_ids`、`source_memory_ids`、`evidence_text`、`reason`。
- 安全和幂等：`proposal_id`、`expected_version`、`source`。
- Auto Dream 元数据：`dream_run_id`、`dream_version`。

## Runtime 方法

- `retrieve(query)`：返回检索到的 `MemoryRecord` 和 trace。
- `project(query)`：构建模型可见的记忆上下文。
- `apply_memory_proposal(proposal)`：校验并应用记忆写入。
- `replay_memory_audit()`：从 `MemoryAuditLog` 重建记忆状态。
- `schedule_auto_dream(...)`：入队语义维护任务。
- `run_auto_dream_once(...)`：处理一个 Auto Dream job。

## 兼容边界

历史输入可以在边界转换，但新契约不要继续暴露旧字段或旧动作。旧 payload 的读取兼容服务于迁移，不代表新数据模型继续支持那些概念。
