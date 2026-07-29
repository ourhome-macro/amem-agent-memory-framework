# Recall@5 100条人工标注评测结果

日期：2026-07-29

## 数据集

数据文件：`benchmarks/data/recall_100_v1.json`

每条样本包含：

- `memory_id`
- `category`
- `content`
- `query`
- `ground_truth_memory_id`
- 可选 `forbidden_memory_ids`
- `k=5`

类别分布：

| 类型 | 数量 |
| --- | ---: |
| semantic_preference | 30 |
| relationship | 20 |
| episodic | 20 |
| profile | 20 |
| temporal | 10 |
| 总计 | 100 |

标注约束已固化在 `tests/test_benchmark_dataset.py`：

- 总数必须为 100。
- `memory_id` 必须唯一。
- `ground_truth_memory_id` 必须等于对应 `memory_id`。
- `query` 和 `content` 必须非空。
- `forbidden_memory_ids` 必须引用数据集中存在的 memory。

## 运行条件

- Benchmark：`benchmarks/bge_m3_recall_benchmark.py`
- 模型：`BAAI/bge-m3`
- 向量维度：1024
- 相似度：L2 normalize 后 cosine
- 运行模式：CPU sentence-transformers
- 写入方式：通过 `MemoryIntakeService.save_memory` 写入 `MemoryRecord`，再回填 embedding。
- 过滤条件：同 tenant/user/agent/session，TopK 前做 ACL/隔离过滤。
- 报告文件：`doc/bge-m3-benchmark-results.json`

## 总体结果

| 模式 | pass_rate | recall@5 | precision@5 | MRR | nDCG@5 | forbidden_hits | mean_latency_ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FTS5-only | 0.980 | 0.980 | 0.196 | 0.944 | 0.953 | 0 | 40.5 |
| Vector-only t=0.0 | 1.000 | 1.000 | 0.200 | 0.981 | 0.986 | 0 | 216.3 |
| Hybrid-RRF t=0.0 | 1.000 | 1.000 | 0.200 | 0.963 | 0.972 | 0 | 126.4 |
| Hybrid-RRF t=0.5 | 1.000 | 1.000 | 0.200 | 0.963 | 0.972 | 0 | 58.9 |
| Hybrid-RRF t=0.6 | 0.980 | 0.980 | 0.196 | 0.962 | 0.966 | 0 | 47.2 |
| Vector-only t=0.7 | 0.440 | 0.440 | 0.088 | 0.440 | 0.440 | 0 | 85.3 |

当前推荐观察点：

- 追求召回：`hybrid_rrf_t0.0` 和 `vector_only_t0.0` 均达到 recall@5 = 1.000。
- 延迟折中：`hybrid_rrf_t0.5` 仍保持 recall@5 = 1.000，mean latency 明显低于 t=0.0。
- 阈值过高风险：`vector_only_t0.7` recall@5 降到 0.440，不能作为默认阈值。
- FTS5-only 对偏好类语义改写仍弱，semantic_preference 只有 28/30 pass。

## 分类结果

### FTS5-only

| 类型 | pass | recall@5 |
| --- | ---: | ---: |
| episodic | 20/20 | 1.000 |
| profile | 20/20 | 1.000 |
| relationship | 20/20 | 1.000 |
| semantic_preference | 28/30 | 0.933 |
| temporal | 10/10 | 1.000 |

### Vector-only t=0.0

| 类型 | pass | recall@5 |
| --- | ---: | ---: |
| episodic | 20/20 | 1.000 |
| profile | 20/20 | 1.000 |
| relationship | 20/20 | 1.000 |
| semantic_preference | 30/30 | 1.000 |
| temporal | 10/10 | 1.000 |

### Hybrid-RRF t=0.0

| 类型 | pass | recall@5 |
| --- | ---: | ---: |
| episodic | 20/20 | 1.000 |
| profile | 20/20 | 1.000 |
| relationship | 20/20 | 1.000 |
| semantic_preference | 30/30 | 1.000 |
| temporal | 10/10 | 1.000 |

## Hard Negative 修正

本轮数据集中包含显式 forbidden 标注，用来测相似但不该进入 Top5 的记忆。

修正项：

- `不要/允许/可以/不能` 等中文允许态进入确定性 hard-negative 识别。
- 避免 `不允许` 同时因为子串命中 `允许`。
- 对“当前问题”命中“过去事实”的时间范围错配进行降权。
- 对 `Java/Lambda`、`Event Sourcing` 这类共享英文/代码标识符，在已有相反状态信号时放宽主题重叠判断。

最终 best hybrid 的 `forbidden_hits_total = 0`。

## 结论边界

这套 100 条人工标注集可以证明当前检索链路、ACL 前置过滤、BGE-M3 回填、RRF 融合和 hard-negative guard 在设计样本上可跑通。

它不能单独支撑生产规模效果声明。生产声明还需要更大的真实分布样本、跨用户/跨租户负样本、更多 hard negative、时间衰减样本、长上下文噪声样本，以及线上反馈闭环。
