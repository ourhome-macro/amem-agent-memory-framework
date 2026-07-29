# Recall@5 250条困难集与50条Holdout评测

日期：2026-07-29

## 先检查的四个问题

旧 `recall_100_v1.json` 的主要问题成立：

- Query 与目标 content 偏接近，很多样本保留了实体、动作和关键词。
- hard negative 明显不足，100 条里只有 3 条 case 带 `forbidden_memory_ids`。
- relationship、temporal 类内部句式模板化，适合功能回归，不足以证明真实自然语言鲁棒性。
- 缺少大规模同 key 反义、时间变化、跨语言、近义作用域干扰和无答案 query。

因此旧 100 条达到 100% recall@5 不代表生产效果，只能说明主链路和基础检索功能跑通。

## 新数据集

主集：`benchmarks/data/recall_250_v1.json`

- 233 条 memory
- 250 条 case
- 93 条 case 带 forbidden 约束
- 118 个 forbidden 引用
- 20 条 no-answer case
- 明显 query/content 包含式近复制：1 条以内

类别分布：

| 类别 | 数量 |
| --- | ---: |
| semantic_preference | 30 |
| relationship | 20 |
| episodic | 20 |
| profile | 20 |
| temporal | 10 |
| hard_negative_state | 40 |
| temporal_shift | 25 |
| near_entity_scope | 25 |
| cross_lingual | 20 |
| semantic_paraphrase_hard | 20 |
| no_answer | 20 |

Holdout：`benchmarks/data/recall_holdout_50_v1.json`

- 45 条 memory
- 50 条 case
- 28 条 case 带 forbidden 约束
- 36 个 forbidden 引用
- 6 条 no-answer case
- 不参与本轮规则和阈值调参

## 运行方式

主集：

```powershell
py -3.12 benchmarks/bge_m3_recall_benchmark.py
```

Holdout：

```powershell
$env:AMEM_RECALL_DATASET='E:\project\agent-memory-runtime\benchmarks\data\recall_holdout_50_v1.json'
$env:AMEM_BENCHMARK_REPORT='E:\project\agent-memory-runtime\doc\bge-m3-holdout-results.json'
py -3.12 benchmarks/bge_m3_recall_benchmark.py
```

报告：

- `doc/bge-m3-benchmark-results.json`
- `doc/bge-m3-holdout-results.json`

## 主集结果

| 模式 | pass_rate | recall@5 | precision@5 | MRR | nDCG@5 | forbidden_hits | no_answer_accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FTS5-only | 0.568 | 0.884 | 0.169 | 0.650 | 0.689 | 78 | 0.100 |
| Vector-only t=0.0 | 0.644 | 0.908 | 0.166 | 0.708 | 0.739 | 51 | 0.000 |
| Vector-only t=0.5 | 0.684 | 0.892 | 0.210 | 0.701 | 0.729 | 46 | 0.600 |
| Hybrid-RRF t=0.0 | 0.588 | 0.904 | 0.165 | 0.691 | 0.725 | 66 | 0.000 |
| Hybrid-RRF t=0.6 | 0.588 | 0.920 | 0.176 | 0.715 | 0.747 | 81 | 0.100 |

主集上 pass_rate 最高的是 `vector_only_t0.5`，为 68.4%。这不是坏事，说明新数据集确实把问题打出来了。

## Holdout结果

| 模式 | pass_rate | recall@5 | precision@5 | MRR | nDCG@5 | forbidden_hits | no_answer_accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FTS5-only | 0.380 | 0.880 | 0.212 | 0.618 | 0.654 | 28 | 0.500 |
| Vector-only t=0.0 | 0.440 | 0.980 | 0.172 | 0.711 | 0.749 | 23 | 0.000 |
| Vector-only t=0.5 | 0.460 | 0.860 | 0.248 | 0.647 | 0.671 | 19 | 0.833 |
| Hybrid-RRF t=0.0 | 0.380 | 0.980 | 0.172 | 0.695 | 0.737 | 30 | 0.000 |
| Hybrid-RRF t=0.6 | 0.400 | 0.900 | 0.216 | 0.653 | 0.686 | 28 | 0.500 |

Holdout 没有达到 90%。这说明当前系统不能拿这版 benchmark 声称生产检索效果已经可靠。

## 关键解释

mean recall 仍然偏高，但 pass_rate 很低，原因是新评测把 forbidden 计入失败条件：

- 正确答案进了 Top5，但同主题反义状态也进了 Top5，判失败。
- 当前状态 query 把过去/未来状态一起召回，判失败。
- 近似实体或近似作用域进入 Top5，判失败。
- no-answer query 仍返回近似记忆，判失败。

也就是说，当前系统的问题不是单纯“找不到”，而是“找到了但不能可靠排除不该进入上下文的记忆”。

## 当前结论

旧 100 条可以保留为 smoke/regression 集。

250 主集和 50 holdout 更接近真实拷打集，当前结果说明：

- BGE-M3 对跨语言和语义召回有帮助。
- FTS5 对结构化、关系、时间、profile 的直接关键词样本很强。
- hard negative 不能靠 embedding 阈值解决。
- RRF 只做候选融合，不做语义裁决。
- 当前缺少最终拒答/abstain 层。
- 时间状态、作用域、反义状态需要更强的 reranker 或 Auto Dream 结构化归并结果辅助。

下一步如果要把 holdout 拉到 90% 以上，不能再调这批数据的阈值刷分；应该补：

- 状态/时间/作用域 aware reranker
- no-answer calibration
- forbidden-aware final filter
- 更细的 MemoryRecord key/value/state 结构化字段
- 用另一批新的 holdout 再验证
