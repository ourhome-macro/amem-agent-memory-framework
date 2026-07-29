# BGE-M3 Balanced Recall Benchmark 结果

日期：2026-07-29

## 运行命令

```powershell
$env:AMEM_RECALL_DATASET='E:\project\agent-memory-runtime\benchmarks\data\recall_250_balanced_v1.json'
$env:AMEM_BENCHMARK_REPORT='E:\project\agent-memory-runtime\doc\bge-m3-balanced-benchmark-results.json'
py -3.12 benchmarks\bge_m3_recall_benchmark.py
```

## 数据集

- dataset：`recall_250_balanced_v1`
- cases：250
- memories：236 写入 runtime，数据文件内为 237 条；其中 1 条在写入时按同 profile key 合并成同一 MemoryRecord。
- difficulty：simple 100 / medium 70 / hard 65 / no-answer 15

## 总体结果

| 模式 | pass | recall@5 | precision@5 | MRR | nDCG@5 | forbidden | no-answer accuracy | mean latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FTS5-only | 54.0% | 0.572 | 0.162 | 0.450 | 0.465 | 15 | 93.8% | 87ms |
| Vector-only t=0.0 | 76.8% | 0.848 | 0.185 | 0.667 | 0.697 | 23 | 43.8% | 312ms |
| Hybrid-RRF t=0.0 | 74.0% | 0.832 | 0.182 | 0.656 | 0.684 | 27 | 43.8% | 229ms |
| Vector-only t=0.5 | 75.6% | 0.828 | 0.189 | 0.659 | 0.686 | 23 | 56.2% | 263ms |
| Hybrid-RRF t=0.5 | 75.6% | 0.844 | 0.192 | 0.652 | 0.684 | 28 | 56.2% | 224ms |
| Hybrid-RRF t=0.6 | 67.6% | 0.716 | 0.190 | 0.582 | 0.600 | 21 | 93.8% | 152ms |

最佳 pass 是 `vector_only_t0.0/t0.3`，为 76.8%。最佳 hybrid 是 `hybrid_rrf_t0.5`，pass 75.6%，recall@5 0.844。

## Best Hybrid 分类表现

`hybrid_rrf_t0.5`：

- semantic_preference：30/30，recall 1.000
- relationship：20/20，recall 1.000
- episodic：20/20，recall 1.000
- profile：20/20，recall 1.000
- temporal：10/10，recall 1.000
- near_entity_scope：8/15，recall 0.933，forbidden 7
- natural_rewrite：23/30，recall 0.800
- cross_lingual：16/20，recall 0.800
- hard_negative_state：19/30，recall 0.633，forbidden 11
- temporal_shift：3/20，recall 0.600，forbidden 10
- semantic_paraphrase_hard：11/20，recall 0.550
- no_answer：9/15 正确拒答

## 结论

这个结果比之前 100 条简单集可信得多：简单 profile/relationship/episodic 仍稳定，但一旦进入状态反义、时间状态变化、抽象改写和拒答，系统还没有生产级指标。

当前最该继续修的是：

- hard-negative 状态反义：仍有 11 个 forbidden hit，说明结构化状态还没有覆盖 benchmark 写入后的所有反义场景。
- temporal shift：当前/过去/未来经常互相干扰，尤其未来或过去 query 容易被过滤到空结果或打到 current。
- no-answer calibration：hybrid t=0.5 只有 9/15 正确拒答，t=0.6 可提升到 15/16 no-result correct，但总体 recall 明显下降。
- natural rewrite / semantic paraphrase：不是关键词问题，更多是候选融合和 rerank 缺少语义裁决层。

完整 JSON 报告：`doc/bge-m3-balanced-benchmark-results.json`
