# 长期记忆主链路简化方案

日期：2026-07-28

## 当前结论

主链路已经收敛为：

```text
save/revise/forget 工具 + Auto Dream
  -> MemoryProposal
  -> MemoryWritePolicy
  -> MemoryService.apply_proposal
  -> MemoryRecord
  -> MemoryAuditLog
  -> tombstone / embedding outbox
```

`MemoryRecord` 是真实当前状态。`MemoryAuditLog` 是变更历史和证据来源。兼容 `Event` 只保留审计语义，不再驱动长期记忆派生。

## 已执行的大砍

- 删除旧候选派生包：`src/agent_memory_runtime/memory/derivation/`。
- 删除旧生命周期 reducer 包：`src/agent_memory_runtime/memory/lifecycle/`。
- 删除旧后台派生队列：`src/agent_memory_runtime/governance/queue/`。
- 删除旧写入守卫模块。
- `AgentMemoryRuntime` 不再接受旧派生、reducer、写入守卫、queue、review guard 注入参数。
- 删除旧派生执行、事件应用和候选审核批准入口。
- `ingest` 和 `ingest_async` 只写事件审计；`ingest_async` 返回 `job=None`。
- CLI 删除旧派生命令族和 legacy 开关。
- SQLite bundle 不再暴露派生队列。
- shadow replay 不再重建 MemoryRecord，只验证 audit-only event 状态。
- legacy 派生测试整批删除；保留 proposal、policy、audit、retrieval、embedding outbox 测试。

## 责任分层

Auto Dream 负责语义整理：

- create
- reinforce
- revise
- supersede
- keep_both
- archive
- needs_review

确定性代码负责边界：

- `MemoryValidator`：schema、类型、默认字段。
- `AccessPolicy`：tenant/user/agent/session 权限。
- `RiskGuard`：敏感信息、高风险操作、扩大可见性、跨主体修改。
- `MemoryWritePolicy`：组合 validator、access、risk，并输出 allow/reject/needs_review/conflict。

## Event 兼容语义

```text
Event
 -> sanitize
 -> EventStore.append
 -> AuditEnvelope(memory_event_audit)
 -> RuntimeSnapshot
```

Event 不再是 source of truth，不再参与语义合并，不再通过 replay 恢复当前记忆。

## 保留测试重点

- `save_memory` 直接创建/更新 `MemoryRecord` 并写 `MemoryAuditLog`。
- `revise_memory` 保留 before/after 和版本递增。
- `forget_memory` 写 tombstone/audit 并隐藏记忆。
- Auto Dream 重复 proposal 幂等。
- 冲突和高风险 proposal 进入 `needs_review` 或 `conflict`。
- 跨 tenant/user/agent proposal 被拒绝。
- `visible_to` 扩大共享范围触发审核。
- embedding outbox 失败不影响 SQLite 当前状态。
- 兼容事件 ingest 只产生审计，不产生长期记忆。
