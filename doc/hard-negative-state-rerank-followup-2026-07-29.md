# hard_negative_state rerank 跟进结果

## 本次改动

- 新增 `benchmarks/hard_negative_failure_analyzer.py`，从 benchmark 报告中抽出失败或 forbidden 命中的 `hard_negative_state` case，并输出 query intent、目标状态、forbidden 状态和最终命中。
- `semantic_state.py` 补强状态抽取：
  - 修复 `inactive` 被 `active` 子串污染的问题。
  - 修复 `未完成` / `未通过` 被 `完成` / `通过` 子串污染的问题。
  - query intent 中让明确问句信号优先，避免 `处理完了吗` 被 `完了` 误判成普通 `success`。
  - 清理 `相反状态是什么` 这类评测包装词，避免污染实体 token。
- deterministic rerank 增加同 entity + attribute 的结构化反义状态 drop。
- deterministic rerank 增加有限属性兼容：`allowed` 问句可以用 `success/resolved` 状态回答，`success` 问句可以用 `resolved` 状态回答。
- `bge_m3_recall_benchmark.py` 增加 `AMEM_RECALL_CATEGORY`，支持只跑指定类别，例如 `hard_negative_state`。

## 40 条 smoke

`py -3.12 benchmarks\deterministic_rerank_smoke.py`

结果：

- 40 / 40 passed
- state: 10 / 10
- temporal: 10 / 10
- entity: 10 / 10
- no_answer: 10 / 10

## hard_negative_state 子集

命令：

```powershell
$env:AMEM_RECALL_CATEGORY='hard_negative_state'
$env:AMEM_BENCHMARK_REPORT='E:\project\agent-memory-runtime\doc\bge-m3-hard-negative-rerank-results.json'
py -3.12 benchmarks\bge_m3_recall_benchmark.py
```

结果：

| 模式 | pass | Recall@5 | forbidden hits |
| --- | ---: | ---: | ---: |
| FTS5-only | 21 / 30 | 0.700 | 2 |
| Vector-only t0.0 | 25 / 30 | 0.833 | 2 |
| Hybrid-RRF t0.0 | 25 / 30 | 0.833 | 2 |

对比旧 full report 中的 hybrid `hard_negative_state`：

- 旧：19 / 30，forbidden 11
- 新：25 / 30，forbidden 2

这说明本轮修复主要有效降低了反义状态污染，而不是靠调阈值牺牲召回来换安全性。

## 250 全量

命令：

```powershell
$env:AMEM_BENCHMARK_REPORT='E:\project\agent-memory-runtime\doc\bge-m3-balanced-rerank-benchmark-results.json'
py -3.12 benchmarks\bge_m3_recall_benchmark.py
```

主要结果：

| 模式 | pass | Recall@5 |
| --- | ---: | ---: |
| FTS5-only | 62.4% | 0.656 |
| Vector-only t0.0 | 81.6% | 0.872 |
| Hybrid-RRF t0.0 | 79.2% | 0.860 |
| Hybrid-RRF t0.5 | 80.0% | 0.860 |

全量中 `hard_negative_state`：

| 模式 | pass | Recall@5 | forbidden hits |
| --- | ---: | ---: | ---: |
| Vector-only t0.0 | 25 / 30 | 0.833 | 2 |
| Hybrid-RRF t0.0 | 25 / 30 | 0.833 | 2 |

## 剩余问题

剩余 `hard_negative_state` 失败不是单一阈值问题：

- `bal_hn_001_yes`：`现在还会自动续费吗` 在当前数据集中标注到 `已经关闭`，但同一 active 数据集里还存在 `仍然开启`。如果没有 Auto Dream 先把当前状态合并成唯一记录，检索层无法可靠判断哪个才是当前事实。
- `bal_hn_007_no`：query 为 `Can productioff deployment proceed?`，这是数据生成时把 `production` 错替换成 `productioff` 的噪声样本，不应写生产规则硬编码。
- 部分 `相反状态是什么` case 已能踢 forbidden，但目标没有进入 Top5，说明是召回覆盖问题，不是 rerank 能凭空补回的问题。

结论：本轮 deterministic rerank 适合处理简单、高置信、结构化的反义状态；真正的当前状态冲突仍应由 Auto Dream 上游整理成结构化唯一当前状态，或者进入 review。
