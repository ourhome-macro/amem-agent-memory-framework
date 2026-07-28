# 长期记忆主链路简化方案

日期：2026-07-28

## 目标

把长期记忆主链路从 `Event -> DerivationEngine -> Rule Engine -> Reducer -> Snapshot/Replay`
收敛为：

```text
save/revise/forget 工具 + Auto Dream
  -> MemoryProposal
  -> MemoryWritePolicy
  -> MemoryService
  -> MemoryRecord + MemoryAuditLog
  -> Embedding/Qdrant 异步索引
```

第一阶段不物理删除 legacy Event、DerivationEngine、Snapshot、Replay 代码，但它们不再是工具写入的默认主线。

## 核心事实源

- `MemoryRecord` 是真实当前状态。
- `MemoryAuditLog` 是写入变更历史，记录 proposal、before/after、证据、原因和操作者。
- `EventStore` 降级为 legacy 兼容入口，继续服务旧的 `runtime.ingest(Event)` 测试和调用方。
- Qdrant/embedding 只是异步检索投影，不作为真实数据源。

## MemoryProposal

所有新写入都统一为 `MemoryProposal`：

```text
proposal_id
source
action: create | reinforce | revise | supersede | archive | delete | keep_both | needs_review
target_memory_id
subject_id
key
content
memory_type
layer
scope
visible_to
confidence
salience
source_message_ids
source_memory_ids
evidence_text
reason
dream_run_id
dream_version
actor_id
agent_id
tenant_id
user_id
session_id
labels
tags
expected_version
```

工具层不再先构造 Event：

- `save_memory` 生成 `source=save_memory` 的 `create` proposal。
- `revise_memory` 生成 `source=revise_memory` 的 `revise` proposal，并带 `target_memory_id` 和 `expected_version`。
- `forget_memory` 生成 `archive/delete` proposal；工具自身仍标记为高风险、需要审批。

## Auto Dream 职责

Auto Dream 只做语义整理并输出 proposal，不直接改库。

它负责：

- 补漏：发现 typed event 没有形成记忆时输出 `create`。
- 冲突：把已有冲突记忆标为 `needs_review`。
- 去重：同内容重复时输出对保留记忆的 `reinforce`，不直接删除。
- 明确遗忘、修正：没有唯一 target 时默认输出 `needs_review`。

语义判断不再放进确定性规则引擎。

## 确定性边界

旧的“规则引擎”职责缩小为三个组件：

- `MemoryValidator`：字段完整性、动作合法性、scope/layer 枚举、置信度和显著性范围。
- `AccessPolicy`：`tenant_id/user_id/agent_id/subject_id` 不变量、`expected_version` 乐观锁。
- `RiskGuard`：删除、敏感内容、敏感标签、可见性扩大等高风险写入的拦截或审核路由。

这些组件不做语义冲突判断，也不做内容归并。

## MemoryService 事务

`MemoryService.apply_proposal` 在事务内完成：

```text
读取旧 MemoryRecord
-> MemoryWritePolicy 校验
-> create/update/archive/delete
-> 写 MemoryRecord 或 tombstone
-> 写 MemoryAuditLog
-> 触发 embedding outbox
```

已落地的保证：

- `proposal_id` 幂等：同 proposal 重试不会重复创建或重复强化。
- `expected_version` 乐观锁：版本不一致返回 `conflict` 且 `retryable=True`。
- `delete` 写 tombstone 后删除 MemoryRecord 投影。
- SQLite 写入成功后，embedding outbox 保留待 worker 重试；worker/Qdrant 失败不影响 MemoryRecord。
- legacy `runtime.ingest(Event)` 仍保留原有派生兼容路径。

## 后续清理

第二阶段可以继续弱化 legacy 叙事：

- 文档和 README 去掉 Event Sourcing 作为主卖点。
- 把 DerivationEngine 测试标记为 legacy compatibility。
- Auto Dream 增加 LLM/结构化语义裁决器后，只输出 proposal，不直接写库。
- 审核队列可以接入 `needs_review` proposal，审批后再调用 `MemoryService.apply_proposal`。

## 2026-07-28 大砍结果

本轮已经把默认 Runtime 行为改为 proposal-first：

- `AgentMemoryRuntime()` 默认不再初始化 `DerivationEngine`、`LifecycleReducer`、`WriteGuard`。
- `runtime.ingest(Event)` 默认只记录事件审计，不再派生 `MemoryRecord`。
- `runtime.ingest_async(Event)` 默认只记录事件审计，不再入 `DerivationJob` 队列。
- Event 仍可进入 `EventStore`，但语义是 legacy/audit 兼容，不再是记忆事实源。
- 默认事件审计写 `audit_type=memory_event_audit`，记录 event id、kind、identity、payload hash。
- 旧 Event -> Derivation -> Reducer 路径必须显式使用 `legacy_event_derivation=True`。
- CLI 默认 `ingest` 只做审计写入；旧派生必须显式传 `--legacy-derive`。
- CLI banner 已从 Event Sourcing 改为 Proposal-first / Audited writes。

仍保留的 legacy 内容：

- `Event` 模型、`EventStore` 和 SQLite `events` 表保留，用于审计和兼容历史数据。
- `DerivationEngine`、builtin rules、`LifecycleReducer`、`WriteGuard` 保留，但只在显式 legacy 开关下启用。
- replay/shadow replay 属于 legacy compatibility，内部显式打开 legacy 派生。

测试约束：

- 新增默认 `Event` 审计-only 测试，防止 Event 重新成为主写入链路。
- 旧 Event 派生测试全部显式 opt-in `legacy_event_derivation=True` 或 CLI `--legacy-derive`。
- 全量验证：`py -3.12 -m ruff check src tests` 和 `py -3.12 -m pytest -q`。
