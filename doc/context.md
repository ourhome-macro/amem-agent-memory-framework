# 上下文

context 模块把选中的记忆转换成模型可见输入。

## 模块职责

- `ContextBuilder`：在 `context_token_budget` 下选择记录、清理投影并构建 `AgentContext`。
- `select_under_budget`：保证注入记忆不超过配置预算。
- `project_record`：把 `MemoryRecord` 转换成结构化模型可见字段。
- `sanitize_context`：移除召回文本中的伪造 memory fence marker。
- `build_personalization_profile`：从选中记录里派生紧凑偏好和画像片段。
- `compact_checkpoint`：压缩较早 agent 消息，同时保留系统规则、固定事实、原始任务和近期轮次。
- `AdaptiveTokenEstimator`：估算文本、消息和工具 schema 的 token 使用量。

## 预算

`RuntimeConfig.context_token_budget` 默认给记忆注入 `1000` tokens。对话压缩使用独立的 `AgentPolicy` 设置。

## 安全边界

模型看到的是投影后的记忆上下文，不是原始存储记录。进入 prompt 前必须完成权限校验、预算裁剪和内容清理。
