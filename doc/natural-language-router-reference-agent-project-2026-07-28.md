# Natural Language Router Reference

日期：2026-07-28

自然语言路由器的长期记忆输出目标是 `MemoryProposal`，不是 Event。

```text
utterance
 -> intent router
 -> save/revise/forget proposal
 -> MemoryWritePolicy
 -> MemoryService
```

路由器只做意图和字段抽取。语义整理由 Auto Dream 统一处理，安全和权限由 policy 统一处理。
