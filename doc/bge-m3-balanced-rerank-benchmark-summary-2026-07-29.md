# BGE-M3 Balanced Benchmark with Deterministic Rerank v1

日期：2026-07-29

## 运行命令

```powershell
$env:AMEM_RECALL_DATASET='E:\project\agent-memory-runtime\benchmarks\data\recall_250_balanced_v1.json'
$env:AMEM_BENCHMARK_REPORT='E:\project\agent-memory-runtime\doc\bge-m3-balanced-rerank-benchmark-results.json'
py -3.12 benchmarks\bge_m3_recall_benchmark.py
```

## 总体结果

| 模式 | pass | recall@5 | precision@5 | MRR | nDCG@5 | forbidden | no-answer correct | p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FTS5-only | 58.0% | 0.612 | 0.166 | 0.482 | 0.499 | 16 | 14/16 | 78ms |
| Vector-only t=0.0 | 79.6% | 0.852 | 0.190 | 0.673 | 0.702 | 18 | 8/16 | 274ms |
| Hybrid-RRF t=0.0 | 77.2% | 0.840 | 0.187 | 0.653 | 0.684 | 21 | 8/16 | 188ms |
| Vector-only t=0.5 | 77.6% | 0.828 | 0.189 | 0.662 | 0.688 | 18 | 9/16 | 152ms |
| Hybrid-RRF t=0.5 | 77.6% | 0.836 | 0.190 | 0.649 | 0.680 | 20 | 9/16 | 115ms |
| Hybrid-RRF t=0.6 | 69.6% | 0.728 | 0.189 | 0.588 | 0.607 | 16 | 14/16 | 84ms |

最佳 Vector-only：`vector_only_t0.0/t0.3`

- pass：79.6%
- recall@5：0.852

最佳 Hybrid：`hybrid_rrf_t0.0/t0.3`

- pass：77.2%
- recall@5：0.840

## 对比上一版

| 模式 | pass 变化 | recall 变化 | forbidden 变化 | no-answer correct 变化 |
|---|---:|---:|---:|---:|
| FTS5-only | 54.0% -> 58.0% | 0.572 -> 0.612 | 15 -> 16 | 15 -> 14 |
| Vector-only t=0.0 | 76.8% -> 79.6% | 0.848 -> 0.852 | 23 -> 18 | 7 -> 8 |
| Hybrid-RRF t=0.0 | 74.0% -> 77.2% | 0.832 -> 0.840 | 27 -> 21 | 7 -> 8 |
| Hybrid-RRF t=0.5 | 75.6% -> 77.6% | 0.844 -> 0.836 | 28 -> 20 | 9 -> 9 |
| Hybrid-RRF t=0.6 | 67.6% -> 69.6% | 0.716 -> 0.728 | 21 -> 16 | 15 -> 14 |

## 分类变化

Best hybrid 从上一版 `hybrid_rrf_t0.5` 变为本版 `hybrid_rrf_t0.0`。

关键分类：

- `temporal_shift` 明显提升：
  - hybrid t=0.0：6/20 -> 14/20，recall 0.650 -> 0.800，forbidden 8 -> 2
  - hybrid t=0.5：3/20 -> 11/20，forbidden 10 -> 2
- `hard_negative_state` 基本没提升：
  - hybrid t=0.0：19/30 -> 19/30，forbidden 11 -> 11
  - 说明当前状态反义规则没有覆盖这批 hard negative 的主要失败面。
- `no_answer` 小幅提升：
  - hybrid t=0.0：7/15 -> 8/15
  - hybrid t=0.5：9/15 -> 9/15
- `semantic_paraphrase_hard` 有轻微回退：
  - hybrid t=0.0：10/20 -> 9/20
  - hybrid t=0.5：11/20 -> 10/20

## 结论

Deterministic rerank v1 是有效的，但不是全面提升：

- 它主要修了时间状态错配。
- 它降低了总 forbidden hits。
- 它让整体 pass 有 2-3 个百分点提升。
- 它还没有让 hybrid 超过 vector-only。

当前 hybrid 仍输给 vector-only，原因是 FTS5 在抽象改写、跨语言和 hard negative 上仍会把“词面相近但答案错误”的候选推高；deterministic rerank v1 只覆盖状态/时间/对象的显式规则，不能解决所有语义裁决。

下一步如果继续优化，优先级应该是：

1. 扩大 hard_negative_state 的结构化状态覆盖。
2. 把 `semantic_state` 的 entity/attribute/value 在 benchmark 写入时检查出来，确认失败样本是否真的有结构化字段。
3. 对 semantic paraphrase hard 引入轻量 LLM judge 或 cross-encoder reranker，而不是继续加关键词规则。

完整结果：`doc/bge-m3-balanced-rerank-benchmark-results.json`
