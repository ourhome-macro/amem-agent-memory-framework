# Memory Intake Tools 与 Auto Dream

日期：2026-07-28

## 当前链路

```text
save_memory / revise_memory / forget_memory
 -> MemoryProposal
 -> MemoryWritePolicy
 -> MemoryService.apply_proposal
 -> MemoryRecord + MemoryAuditLog
```

工具层不再生成长期记忆事件，也不再调用事件派生路径。工具结果保留 `event_id` 兼容别名，但真实 id 是 `proposal_id`。

## Auto Dream

Auto Dream 只输出 proposal，不直接改库。它负责语义整理：

- 补漏
- 去重
- 冲突识别
- 归并建议
- 低价值归档建议
- 不确定项标记 `needs_review`

确定性安全、权限和 schema 边界由 `MemoryWritePolicy` 处理。
