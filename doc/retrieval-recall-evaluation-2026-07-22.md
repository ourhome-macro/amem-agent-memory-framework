# 检索召回率测试报告（2026-07-22）

状态：FTS5 基线已实测；真实 semantic/hybrid 质量待配置目标 embedding 模型  
代码基线：`91fb2e9`（实现 FTS5 与 sqlite-vec 混合语义检索）

## 1. 测试结论

本次测试不能给出真实 semantic/hybrid 召回率，因为环境中没有配置以下任何 embedding 运行参数：

- `AMEM_EMBEDDING_MODEL`
- `AMEM_EMBEDDING_DIMENSIONS`
- `AMEM_EMBEDDING_MIN_SIMILARITY`
- embedding endpoint/API key

因此本报告严格区分：

1. 使用真实 SQLite FTS5/BM25 跑出的 lexical 召回质量；
2. 使用确定性 provider 单元测试验证的 sqlite-vec、ACL、超时和 generation 链路正确性；
3. 尚未测量、不能伪报的目标模型 semantic/hybrid 质量。

核心结果：

| 评测集 | 模式 | 正样本 Recall@K | MRR | nDCG@K | 无结果准确率 | forbidden hits | 通过 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 基础业务集 | FTS5-only | 1.0000 | 1.0000 | 1.0000 | 无负样本 | 0 | 4/4 |
| 语义挑战集 | FTS5-only | 0.5000 | 0.5000 | 0.5000 | 0.7500 | 0 | 5/8 |
| 语义挑战集 | semantic-only | 未测 | 未测 | 未测 | 未测 | 未测 | 缺目标模型 |
| 语义挑战集 | hybrid-RRF | 未测 | 未测 | 未测 | 未测 | 未测 | 缺目标模型 |
| 语义挑战集 | hybrid-business | 未测 | 未测 | 未测 | 未测 | 未测 | 缺目标模型 |

基础集延迟为 P50 9.10ms、P95 9.10ms；语义挑战集为 P50 7.55ms、P95 10.03ms。样本只有 4/8 个，这些延迟只用于 smoke test，不能作为性能 SLA。

## 2. 测试方法

测试使用临时 SQLite 数据库，确保走到真实的 schema v6、FTS5、结构化 SQL 和 ACL 投影，而不是 InMemory store。

数据源：

- `examples/data/customer_support_events.jsonl`
- `examples/data/memory_eval_events.jsonl`
- `examples/data/semantic_eval_events.jsonl`

评测集：

- `examples/evals/retrieval_cases.yml`：4 个基础业务 case；
- `examples/evals/semantic_retrieval_cases.yml`：8 个语义、安全和无结果 case。

配置：

```python
RuntimeConfig(
    hybrid_retrieval=HybridRetrievalConfig(enable_semantic=False)
)
```

每个 query 都经过 tenant、user、session policy、layer、status、scope、type、tag 和 `memory_acl` SQL 预过滤，再由 FTS5/BM25 产生候选，随后经过 hard filter 与 AccessChecker 复核。

## 3. 基础业务集

四个 case 全部命中：

- `refund_strategy`：英文退款策略；
- `cross_session_response_style`：跨会话 Core 偏好；
- `chinese_refund_progress`：中文退款进度；
- `archived_cross_session_recall`：历史意图触发 Archival。

结果：Recall@K、MRR、nDCG@K 均为 1.0，forbidden-hit count 为 0。这说明 FTS5 改造没有破坏既有 exact/CJK/Core/Archival 基线。

## 4. 语义挑战集失败分析

### 4.1 中文零词面释义未召回

case：`zero_lexical_overlap_chinese_paraphrase`

```text
query: 产品何时可以发布
memory: 上线窗口顺延至本周五
```

FTS5 候选数为 0，Recall@3=0。这是预期的 lexical 能力边界，必须由真实 multilingual embedding 补足。

### 4.2 中英跨语言未召回

case：`chinese_english_cross_language`

```text
query: Where is my car service appointment?
memory: 车辆保养预约在北辰维修中心
```

FTS5 候选数为 0，Recall@3=0。同样必须由跨语言 embedding 解决，不能通过扩大 FTS 候选数解决。

### 4.3 exact-session 无越权，但出现无关误召回

case：`exact_session_boundary`

查询 `SESSION-6633` 时，other-session 的 forbidden memory 没有进入候选，说明 session/ACL 边界正确，forbidden-hit count 仍为 0。

但 query 被拆成 `session OR 6633`。当前 session 内其他 memory ID 含有公共 token `session`，因此返回了 5 条无关记录，导致无结果用例失败。这是 relevance/FTS query precision 问题，不是越权泄漏。

建议后续修复：对形如 `PREFIX-1234` 的 exact identifier 使用 AND/phrase 语义，或把结构性 memory/session ID 从普通正文 FTS field 中拆出独立 exact field；不能简单把所有自然语言查询统一改成 AND，否则会损害中文和长问题召回。

### 4.4 否定 hard negative 仍需加强

`negation_hard_negative` 的正确记录排在 top-1，所以 Recall@1/MRR@1 通过；但完整返回列表同时包含“自动续费仍然开启”的矛盾记录。当前 case 只按 K=1 判断，没有把相反事实标成 forbidden。

建议把反事实记录加入 forbidden/graded relevance，并用目标 embedding 阈值或二阶段 reranker 验证矛盾抑制，避免上下文预算较大时同时注入相反事实。

## 5. 安全结果

以下边界全部通过，forbidden-hit count 为 0：

- other tenant；
- other private agent；
- other exact session；
- calibrated no-result 中不存在的主题没有词法命中。

需要注意：`exact_session_boundary` 虽没有返回被禁止的旧 session 记录，但返回了当前 session 的无关记录，所以安全正确不等于检索质量正确。

## 6. 语义链路正确性

执行：

```powershell
py -3.12 -m pytest tests\test_semantic_retrieval.py -q
```

结果：`14 passed in 0.99s`。

覆盖内容包括：

- sqlite-vec 零词面召回的确定性集成测试；
- ACL 在 vector top-K 前过滤；
- semantic timeout/bulkhead 后保留 FTS5；
- provider 熔断和非法向量拒绝；
- batch embedding、outbox lease/fencing；
- revise/stale/tombstone/replay；
- generation coverage、激活、退役和安全回滚；
- sensitive 记录不进入派生搜索索引；
- v5→v6 migration。

这些测试证明链路行为正确，但确定性测试向量不代表任何真实 embedding 模型的召回质量。

## 7. 完成真实 semantic/hybrid 评测的条件

先配置并固定目标模型：

```powershell
$env:AMEM_EMBEDDING_MODEL = "<model>"
$env:AMEM_EMBEDDING_MODEL_REVISION = "<pinned-revision>"
$env:AMEM_EMBEDDING_DIMENSIONS = "<dimensions>"
$env:AMEM_EMBEDDING_BASE_URL = "<endpoint>"
$env:AMEM_EMBEDDING_API_KEY_ENV = "EMBEDDING_API_KEY"
$env:EMBEDDING_API_KEY = "<secret>"
$env:AMEM_EMBEDDING_MIN_SIMILARITY = "<calibrated-threshold>"
```

完成 generation 流程：

```powershell
amem embedding backfill
amem embedding worker
amem embedding status
amem embedding activate
```

然后运行四组对照：

```powershell
amem eval examples/evals/semantic_retrieval_cases.yml --mode lexical-only
amem eval examples/evals/semantic_retrieval_cases.yml --mode semantic-only
amem eval examples/evals/semantic_retrieval_cases.yml --mode hybrid-rrf
amem eval examples/evals/semantic_retrieval_cases.yml --mode hybrid-business
```

发布门禁至少应为：

- 基础业务集 Recall/MRR/nDCG 不低于当前 1.0 基线；
- 零词面中文和跨语言 case 均命中；
- forbidden-hit count 恒为 0；
- 无结果准确率达到目标；
- 否定 hard negative 不把矛盾事实放入最终上下文；
- 在真实 query embedding 延迟下 P95/P99 满足预算。
