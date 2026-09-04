# 治理

治理层是记忆写入的确定性边界。LLM 和 Auto Dream 可以提出建议，但不能绕过规则直接修改长期状态。

## 模块职责

- `MemoryValidator`：校验必填字段、枚举、置信度、显著性和 action 形态。
- `AccessPolicy`：拒绝跨租户、跨用户、跨 Agent、跨 subject 写入，并执行乐观版本校验。
- `RiskGuard`：把删除、敏感内容、敏感标签、可见性扩大和低信号 profile 提升路由到 review。
- `PiiProtector`：把 email、银行卡、敏感路径等 PII 替换成 `${PII_xxx}` 占位符。
- `HmacPiiVault`：只保存按 tenant/type scoped 的 HMAC-SHA256 摘要，用于不可逆等值匹配。
- `MemoryWritePolicy`：按顺序执行 validator、access policy 和 risk guard。
- `MemoryService`：只应用允许通过的 proposal，并写 audit log。

## 边界

语义聚合、冲突发现和派生建议属于 Auto Dream。权限、schema、风险、状态转换和乐观锁属于确定性策略代码。

## Profile 提升

Auto Dream 可以把重复强化且高置信的 `L1` 记忆原子提议提升为 `L3` profile。提升使用 `action=merge`，目标 level 在 proposal 中表达。低信号提升会被策略层打回 review。

归档是 `status=archived`，不是单独层级。审核是 `decision_status=pending_review`，不是写入 action。

## Retention 冷热流转

retention 支持 `mark_warm` 和 `mark_archived` 两类非删除动作。`mark_warm` 把到期的 active hot 记忆降为 warm；`mark_archived` 把满足归档条件的 active 非 profile 记忆标为 `status=archived` 并强制 `temperature=cold`。
