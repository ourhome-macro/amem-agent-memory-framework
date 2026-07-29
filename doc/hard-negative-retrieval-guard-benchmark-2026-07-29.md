# Hard Negative 检索修正

## 问题

`自动续费已经关闭` 和 `自动续费仍然开启` 这类状态翻转句，embedding 余弦相似度通常很高。FTS5 也会因为共享主题词命中。单纯调高相似度阈值会同时伤害正常召回，不能解决否定语义盲区。

## 当前落地

- 新增 retrieval scoring 阶段的状态冲突 guard。
- FTS5/Qdrant 仍只负责召回候选。
- rerank/scoring 阶段识别同一主题下的相反状态，并写入 `ScoreBreakdown.hard_negative = -4.0`。
- 被 hard-negative 命中的候选通常会因为 `score.total <= 0` 被排出最终结果。

## 覆盖状态

当前确定性 guard 覆盖这些成对状态：

- enabled/disabled
- allowed/blocked
- succeeded/failed
- resolved/unresolved
- paid/unpaid

算法只在状态相反且主题 token 有足够重叠时触发，避免把无关句子因为都含有“关闭/open”等词误伤。

## Benchmark 修正

- BGE-M3 benchmark 不再依赖已经弱化的 `runtime.ingest(Event)` 派生记忆。
- benchmark 数据通过显式 `MemoryIntakeService.save_memory` 写入当前主链路。
- 新增 12 条 hard-negative case，覆盖自动续费、MFA、备份、发票、部署、工单等双向状态翻转。

## 边界

这不是完整 NLI。它解决的是高频、结构明确的 hard negative。更复杂的矛盾仍需要 cross-encoder/NLI reranker 或 Auto Dream review。
