# 记忆工具和 Auto Dream 实现

## 这次落地的边界

这次把自然语言进入记忆的上游拆成两条通道：

```text
显式通道：save_memory / revise_memory / forget_memory
异步通道：AutoDreamAnalyzer 生成增量 MemoryProposal
```

两条通道都不直接绕过现有生命周期管线。

## 显式记忆工具

新增 `agent_memory_runtime.memory.intake` 包。

### save_memory

用于保存明确偏好、事实或任务经验。

支持事件类型：

- `preference.updated`
- `belief.stated`
- `task.outcome`

工具内部生成结构化 `Event`，再调用 `runtime.ingest(event)`，最终仍由 `DerivationEngine`、`WriteGuard`、`LifecycleReducer` 处理。

### revise_memory

用于修订或替代已有记忆。

它会把 `target_memory_id` 作为 `source_memory_ids` 写入事件 payload。`BeliefRule` 已补充透传 `source_memory_ids`，所以修订后的记忆能追踪旧记忆来源。

### forget_memory

用于删除或归档一条授权记忆。

支持：

- `memory_id` 精确删除/归档
- `query` 唯一命中后删除/归档

删除使用现有 tombstone 机制，避免 replay 时旧事件重新投影出已删除记忆。归档会把记忆转入 `archival` 并标记 `archived`。

`forget_memory` 的 Agent 工具包装设置为高风险并需要审批。

## Auto Dream

`AutoDreamAnalyzer` 是增量分析器，不是每次重新总结全量对话。

输入：

```text
events
records
DreamCheckpoint(last_processed_sequence, last_state_hash, dream_version)
```

输出：

```text
AutoDreamReport
  - source_sequence_range
  - base_state_hash
  - proposals
  - next checkpoint
```

当前实现是确定性的，主要做：

- 用正则识别显式记忆 marker；
- 检查结构化偏好/信念/任务结果事件是否漏派生；
- 检查重复 active memory；
- 检查 conflicted memory 并生成修订建议；
- 推进 `last_processed_sequence`。

Auto Dream 默认只生成 `DreamProposal`，不自动写最终记忆。后续可以在 proposal 之后接 validator/review/auto-apply。

## 为什么这样设计

同步路径只处理显式记忆操作，避免每轮对话都做不稳定自然语言抽取。

Auto Dream 放到后台增量做一致性整理，适合检查漏记、重复、冲突和后续推翻。

最终状态权威仍然在现有事件派生和生命周期管线中，保证可审计、可回放。

## 验证

新增测试：

- `tests/test_memory_intake.py`

覆盖：

- `save_memory` 派生 core preference；
- `revise_memory` 更新同一 profile memory 并保留来源；
- `forget_memory` tombstone 后检索不可见；
- Agent 工具包装可执行；
- Auto Dream 增量 proposal；
- typed event 漏派生检查；
- duplicate active memory proposal。
