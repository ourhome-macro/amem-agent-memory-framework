# No-answer与Forbidden-aware Final Filter跟进

日期：2026-07-29

## 本次完成

本次没有新增 benchmark 用例，只在现有 `recall_250_v1` 和 `recall_holdout_50_v1` 上实现并验证：

- `FinalRetrievalFilterConfig`
- `apply_final_filter`
- query-record hard negative 最终过滤
- selected 内 pairwise state conflict 过滤
- no-answer / abstain calibration
- final filter 只处理前 32 个候选，避免全量候选 O(n^2) 比对
- 扩充确定性状态 marker：
  - `已经支付 / 支付完成 / 仍然未支付`
  - `成功完成 / 任务失败 / 未完成`
  - `已经批准 / 被阻止`
  - `已经解决 / 仍未解决`

实现位置：

- `src/agent_memory_runtime/config.py`
- `src/agent_memory_runtime/memory/retrieval/final_filter.py`
- `src/agent_memory_runtime/memory/retrieval/pipeline.py`
- `src/agent_memory_runtime/memory/retrieval/contradiction.py`

## 指标变化

### 250主集

之前最好结果：

- `vector_only_t0.5`
- pass_rate: 0.684
- recall@5: 0.892
- forbidden_hits: 46
- no_answer_accuracy: 0.600

现在最好结果：

- `vector_only_t0.5`
- pass_rate: 0.764
- recall@5: 0.848
- forbidden_hits: 27
- no_answer_accuracy: 0.600

主集结论：

- pass_rate 提升 8.0 个百分点。
- forbidden_hits 从 46 降到 27。
- recall 下降是预期代价，因为 final filter 会宁可拒掉弱证据或冲突项。

### 50条Holdout

之前最好结果：

- `vector_only_t0.5`
- pass_rate: 0.460
- recall@5: 0.860
- forbidden_hits: 19
- no_answer_accuracy: 0.833

现在最好结果：

- `vector_only_t0.0`
- pass_rate: 0.600
- recall@5: 0.840
- forbidden_hits: 17
- no_answer_accuracy: 0.833

Holdout 结论：

- pass_rate 提升 14.0 个百分点。
- no-answer 维持 5/6。
- forbidden_hits 小幅下降。
- 仍未达到 90%，不能做生产效果声明。

## 仍未解决

当前剩余失败集中在这些类型：

- 状态问句本身没有显式答案方向，例如“现在还会自动续费吗”。
- 两条候选互为反义，但 query 只是在问状态，确定性代码无法可靠判断应该选 on 还是 off。
- 时间状态 query 会同时召回过去、当前、未来记录。
- 近似作用域仍会进入 Top5，例如同项目不同负责人、同负责人不同职责。

这些问题不是继续调 embedding 阈值能解决的。下一步需要：

- MemoryRecord 增加可选结构化字段：`entity / attribute / state / temporal_scope / project_scope`。
- Auto Dream 把当前有效状态、历史状态、future plan 和 superseded record 整理出来。
- final filter 使用结构化字段做确定性裁决，而不是只读自然语言 content。

## 验证

已跑：

```powershell
ruff check src tests benchmarks
py -3.12 -m pytest -q
py -3.12 benchmarks/bge_m3_recall_benchmark.py
$env:AMEM_RECALL_DATASET='E:\project\agent-memory-runtime\benchmarks\data\recall_holdout_50_v1.json'
$env:AMEM_BENCHMARK_REPORT='E:\project\agent-memory-runtime\doc\bge-m3-holdout-results.json'
py -3.12 benchmarks/bge_m3_recall_benchmark.py
```

测试结果：

- `86 passed, 1 skipped`
