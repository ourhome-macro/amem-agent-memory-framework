# 安全

安全由身份感知检索、确定性写策略、上下文清理和审计记录共同保证。

## 模块职责

- `Principal`：表示 tenant、user、agent 身份。
- `AccessChecker`：在上下文投影前授权每条记忆。
- `structured_memory_where`：在 SQLite 查询里应用 tenant、user、session、status、level、visibility、type、tag 和 ACL 过滤。
- `QdrantVectorIndex`：在 vector top-k 前执行等价 payload 过滤。
- `sanitize_event`：清理历史事件审计中的敏感 payload 字段。
- `PiiProtector`：在记忆、索引或模型上下文扩散前替换 PII。
- `HmacPiiVault`：保留不可逆摘要，避免保存可恢复 PII 明文。
- `sanitize_context`：移除伪造的 memory-context fence marker。
- `RiskGuard`：将敏感或高风险写入路由到 review。

## 访问控制

检索侧先做 ACL prefilter，减少候选泄漏面。候选回到 runtime 后，`AccessChecker` 会基于当前 SQLite 事实源再做一次校验。Qdrant payload 只是一份投影，不能替代事实源授权。

## Prompt Injection 边界

记忆和工具输出都被视为不可信数据。被召回内容会放入固定 `<memory-context>` 块，伪造 fence 会被清理，个性化只允许白名单字段进入模型上下文。
