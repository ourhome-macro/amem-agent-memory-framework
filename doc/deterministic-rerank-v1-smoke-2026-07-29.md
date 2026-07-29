# Deterministic Rerank v1 小样本验证

日期：2026-07-29

## 本次范围

本次没有跑 250 条 BGE-M3 benchmark，只先实现并验证 deterministic rerank v1。

链路调整为：

```text
FTS5 + Qdrant candidates
-> RRF / score rerank
-> deterministic rerank
-> final filter / no-answer
-> TopK
```

## 实现点

- 新增 `DeterministicRerankConfig`。
- 新增 `memory/retrieval/deterministic_rerank.py`。
- `semantic_state.py` 增加 query intent 抽取：
  - `entity_tokens`
  - `attribute`
  - `expected_value`
  - `temporal_scope`
- rerank v1 先做确定性检查：
  - query 与 record 状态相反时踢掉。
  - query 明确问 current/past/future 时，踢掉显式时间不匹配的记录。
  - 对象 token 明显不匹配且证据弱时踢掉。
  - archived/conflicted 不复活。
  - 无可靠候选时允许最终拒答。

## 40 条 smoke

运行命令：

```powershell
py -3.12 benchmarks\deterministic_rerank_smoke.py
```

结果：

- cases：40
- pass：40
- pass_rate：1.000

分类：

- state：10/10
- temporal：10/10
- entity：10/10
- no_answer：10/10

完整结果：`doc/deterministic-rerank-smoke-results.json`

## 测试

已通过：

```powershell
py -3.12 -m ruff check src tests benchmarks
py -3.12 -m pytest -q
```

结果：

- `91 passed, 1 skipped`

## 边界

这个结果只证明确定性 rerank 在 40 条手工小样本上能解决明显错候选，不代表 250 balanced benchmark 或生产指标已经提升。下一步再跑 250 时，应单独看：

- hard_negative_state forbidden hit 是否下降
- temporal_shift 是否提升
- no-answer 是否提升
- hybrid 是否超过 vector-only baseline
