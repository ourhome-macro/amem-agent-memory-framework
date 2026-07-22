# FTS5 + sqlite-vec 实施与上线手册（v0.6.0）

状态：代码完成，待目标 embedding 模型校准与生产 shadow/canary 验证  
日期：2026-07-22  
对应设计：[semantic-retrieval-design-v0.6.0.md](semantic-retrieval-design-v0.6.0.md)

## 1. 交付结论

本版本已经把默认 SQLite 检索链路从“普通 `memory_terms` 表 + Python 词项重叠”改造成真正的 FTS5 + sqlite-vec hybrid retrieval：

```text
MemoryQuery
  -> SQL tenant/user/session/layer/status/type/scope/tag/ACL 预过滤
  -> 并行的两个独立召回腿
       -> FTS5 + BM25 top 64
       -> query embedding + sqlite-vec cosine 精确扫描 top 64
  -> memory_id 去重 + weighted RRF
  -> 业务分（recency/salience/confidence/type/source）精排
  -> AccessChecker 二次校验
  -> 结果数与 token budget
```

关键决策没有变化：当前在线后端选择 SQLite 普通向量表 + sqlite-vec scalar distance，而不是 FAISS。原因不是 FAISS 的算法能力不足，而是本项目每次查询都有复杂身份、会话、层级和 ACL 约束；这些条件必须在 top-K 前过滤，并且记忆、删除水位、outbox 和向量发布需要可审计的一致性边界。规模化后优先切换实现同一 `VectorIndex` 协议的 Qdrant，而不是自行维护第二套 FAISS 文件状态。

默认 CLI 数据现在统一保存在 `.amem/runtime.sqlite`。JSONL store 仍保留为库级测试/轻量适配器，但不再是 CLI 的默认生产持久化路径。

## 2. 已完成范围

| 设计项 | 状态 | 实现 |
| --- | --- | --- |
| 真正的 FTS5/BM25 | 完成 | `memory_fts` 虚拟表、CJK 二元词、Latin token、原始 BM25 trace |
| 独立 lexical/semantic 召回 | 完成 | 两路分别取 top-N，再按 `memory_id` 合并；不是 FTS 候选向量重排 |
| sqlite-vec | 完成 | `sqlite-vec==0.1.9`，float32 BLOB，`vec_distance_cosine()` 精确扫描 |
| ACL top-K 前过滤 | 完成 | `memory_acl` 投影与结构化 SQL 条件在 `ORDER BY ... LIMIT` 前执行 |
| weighted RRF | 完成 | lexical/semantic 权重、`rrf_k`、融合明细和业务精排 |
| embedding outbox | 完成 | 批量 worker、retry、dead letter、lease、heartbeat、fencing |
| stale/tombstone/replay | 完成 | hash + source sequence 发布校验、级联删除、replay 重建、旧 generation stale 防回切 |
| generation 升级 | 完成 | backfill/active/retired、逐代 coverage/backlog、显式 activate、prune |
| 超时与降级 | 完成 | 独立线程池、100ms deadline、bulkhead、熔断、lexical-only 降级 |
| 评测与指标 | 完成 | 四种模式、Recall/Precision/MRR/nDCG、无结果、越权、P50/P95/P99 |
| 本机 5K/10K/100K 基准 | 完成 | 结果见第 10 节 |
| 生产 shadow/canary | 待部署 | 需要真实流量、真实 tenant 分布和最终模型；仓库内不能伪造已完成状态 |
| Qdrant/ANN | 条件未触发 | 接口边界已保留；是否实施由真实 P95/QPS 证据决定 |

## 3. Schema v6 与迁移

v6 新增或替换以下投影：

- `memory_fts`：FTS5 虚拟表，`memory_id UNINDEXED`，`terms` 使用 `unicode61`。
- `memory_acl(memory_id, principal_id)`：允许主体的规范化投影；`*` 表示公共可见。
- `embedding_generations`：模型、revision、维度、模板、前缀和 generation 状态。
- `embedding_jobs`：事务 outbox，包含内容 hash、源序列、重试、租约和 fencing token。
- `memory_embeddings`：每个 memory/generation 一条 float32 BLOB，状态为 `ready` 或 `stale`。
- `memories.retrieval_v6_indexed`：v5→v6 搜索投影回填水位。

旧 `memory_terms` 表在 v6 migration 中删除。迁移事务随后从 `memories.payload` 批量回填 FTS、tag 和 ACL 投影。向量不在 schema migration 中同步调用外部模型，而是通过 outbox 异步回填。

`memory_embeddings` 使用标准 SQLite 约束 `length(embedding) = dimensions * 4`，所以不加载扩展的普通 SQLite 工具也能执行 `PRAGMA integrity_check`；真正的向量查询必须由运行时连接加载 sqlite-vec。每个连接只在初始化时短暂开启 extension loading，加载完成后立即关闭该能力。

迁移前必须使用 `SQLiteStoreBundle.backup()` 做在线备份，或在应用完全停止后复制数据库文件。不要只复制 WAL 模式下正在写入的主文件。

## 4. 词法召回

索引文本包含现有可搜索字段，并经过确定性规范化：

- Latin/数字 token 使用 NFKC + casefold；
- 中文、日文和韩文序列生成字符二元词；
- 重复 token 保留，FTS5 BM25 因而能使用 term frequency；
- query 只由受限 token 生成带引号的 OR MATCH 表达式，不拼接原始 FTS 语法；
- BM25、salience、updated_at 和 memory_id 构成稳定排序。

空查询不会构造 `MATCH`，而是只按结构化条件和业务字段返回候选。非空查询没有词法命中时 lexical leg 返回空，不会退化成“全库高 salience 结果”。

## 5. 语义召回与文本最小化

`EmbeddingSpec` 的以下字段共同生成不可变 generation ID：provider、model、revision、dimensions、distance、normalized、query/document prefix、模板版本和 semantic tag allowlist。任何会改变向量空间或输入模板的配置变化都必须产生新 generation，禁止原地覆盖。

document embedding 只接收：

```text
memory_type: ...
content: ...
tags: ...  # 仅显式 allowlist
```

memory ID、tenant/user/agent、时间、任意 metadata、source ID 和未允许的 tag 不进入 embedding 文本。带 `sensitive` label 或没有可检索 principal 的记录不写 FTS、tag、ACL 和 vector 派生索引。

query embedding 缓存使用 `SHA-256(generation + query)` 作为 LRU key，不保存明文 key。provider 返回值必须满足精确维度、全为有限数且不能为全零；非法向量不会进入缓存，并计入 provider 熔断。

当前实现使用普通表先完成任意 SQL/ACL JOIN，再调用 `vec_distance_cosine()` 排序。这是精确扫描，不是 ANN。`vec0`/ANN 只有在真实分区压测证明必要且过滤语义不退化时才应启用。

## 6. 写入一致性

记忆写入事务只完成本地状态：

```text
MemoryStore.upsert
  -> UPSERT memories（禁止 INSERT OR REPLACE 破坏级联子记录）
  -> 重建 FTS/tag/ACL 投影
  -> 将内容变化的 active/backfill 向量置 stale
  -> 将 retired generation 的该记录置 stale，阻止不安全回切
  -> upsert embedding job(content_hash, source_sequence)
  -> commit
```

worker 在事务外批量调用 embedding provider；发布时开启短事务并重新读取当前 MemoryRecord。只有 `generation`、`content_hash` 和 `last_event_sequence` 都仍匹配，且 worker 仍持有未过期 lease/fencing token 时，向量才可写为 `ready`。过期结果变为 `superseded`，不得复活旧内容。

任务领取只查询当前可执行的一个 pending job 或已过期 running job，不扫描整代 backlog。失败使用有界指数退避；达到 `max_attempts` 后进入 `dead_letter`。审计只保留异常类型和安全 hash，不保留 provider 原始错误正文、query、document 或向量。

删除 MemoryRecord 会通过外键级联删除 jobs/vectors/ACL/tags；FTS 行由 store 同事务显式删除。tombstone sequence 仍是 replay 的最终删除水位。

## 7. 读路径保护

lexical 和 semantic 使用独立执行资源。semantic 先提交到有界线程池，lexical 在调用线程立即执行，因此慢 embedding/vector scan 不会占用 lexical worker。

默认保护参数：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `lexical_candidate_limit` | 64 | FTS5 候选数 |
| `semantic_candidate_limit` | 64 | vector 候选数 |
| `rrf_k` | 60 | RRF 平滑常数 |
| `lexical_weight` / `semantic_weight` | 1 / 1 | 两路融合权重 |
| `semantic_timeout_ms` | 100 | 从提交开始计算的语义 deadline |
| `semantic_max_concurrency` | 2 | 单 runtime 语义 bulkhead |
| `semantic_failure_threshold` | 3 | 熔断前连续失败数 |
| `semantic_cooldown_seconds` | 30 | 熔断冷却时间 |
| `query_cache_size` | 256 | query vector LRU 条目数 |
| `embedding_coverage_cache_seconds` | 30 | 在线 coverage 指标缓存时间 |

发生 timeout、bulkhead reject、circuit open、provider error、sqlite-vec error 时，已完成的 lexical 结果继续返回。trace/audit 记录错误类型和计数，但不记录异常正文。在线 semantic 必须配置经过目标模型与数据校准的 `min_semantic_similarity`；未设置时拒绝启用，只有显式 offline 配置才允许无阈值实验。

## 8. 配置与首次启用

安装会固定 sqlite-vec 版本：

```powershell
py -3.12 -m pip install -e ".[dev]"
amem init
```

OpenAI-compatible embedding 配置示例：

```powershell
$env:AMEM_EMBEDDING_PROVIDER = "openai-compatible"
$env:AMEM_EMBEDDING_MODEL = "your-multilingual-embedding-model"
$env:AMEM_EMBEDDING_MODEL_REVISION = "pinned-revision"
$env:AMEM_EMBEDDING_DIMENSIONS = "1024"
$env:AMEM_EMBEDDING_BASE_URL = "https://embedding.example.com/v1"
$env:AMEM_EMBEDDING_API_KEY_ENV = "EMBEDDING_API_KEY"
$env:EMBEDDING_API_KEY = "<secret>"
$env:AMEM_EMBEDDING_MIN_SIMILARITY = "<calibrated-threshold>"
$env:AMEM_EMBEDDING_QUERY_PREFIX = ""
$env:AMEM_EMBEDDING_DOCUMENT_PREFIX = ""
$env:AMEM_EMBEDDING_SEMANTIC_TAGS = "preference,product"
```

可选项还有 `AMEM_EMBEDDING_TIMEOUT_SECONDS`、`AMEM_EMBEDDING_NORMALIZED`、`AMEM_EMBEDDING_SEND_DIMENSIONS`、`AMEM_EMBEDDING_QUERY_TEMPLATE_VERSION` 和 `AMEM_EMBEDDING_DOCUMENT_TEMPLATE_VERSION`。

首个 generation 也不会自动 active。严格顺序是：

```powershell
amem embedding backfill
amem embedding worker
amem embedding status
amem embedding activate
amem embedding status
```

`activate` 默认要求 coverage=1.0 且 pending/running job 为 0。`--minimum-coverage` 是显式发布策略参数；`--allow-pending-jobs` 只用于已评审的紧急操作，不应写进正常部署脚本。

若在 generation 尚未激活时直接启动在线 semantic，运行时会失败并提示先完成 backfill/worker/activate，不会用部分覆盖率悄悄参与排序。

## 9. 模型升级、回滚与清理

升级流程：

1. 固定新模型 revision、维度、prefix/template 和阈值候选。
2. 修改环境配置后执行 `amem embedding backfill`，新 generation 状态为 `backfill`。
3. 持续运行 `amem embedding worker --forever`；使用 `amem embedding status` 查看每个 generation 的 coverage、ready、job 分布和 backlog lag。
4. 在真实评测集运行 lexical-only、semantic-only、hybrid-rrf、hybrid-business。
5. shadow 流量验证安全、质量、P95/P99 和成本。
6. 停止/排空对应 worker 后执行 `amem embedding activate`。
7. canary 观察完成后，才执行 `amem embedding prune --generation <old-generation>`。

回滚不能盲目 activate 旧 generation。旧 generation 退役后发生过任何记忆变更，对应旧向量会变为 `stale`，coverage 门禁将阻止回切。正确做法是把环境配置恢复到旧 spec，重新执行 backfill、worker、评测和 activate。未确认观察窗口结束前不要 prune，因为 prune 会级联删除该 generation 的向量和任务且不可从索引本身恢复，只能重新生成。

若只需立刻停用 semantic，移除 `AMEM_EMBEDDING_MODEL`（或程序配置 `enable_semantic=False`）即可回到 FTS5-only；不要删除向量表作为故障处置手段。

## 10. 评测与基准证据

仓库新增语义/安全样本：

- `examples/data/semantic_eval_events.jsonl`
- `examples/evals/semantic_retrieval_cases.yml`

支持四种模式：

```powershell
amem eval examples/evals/semantic_retrieval_cases.yml --mode lexical-only
amem eval examples/evals/semantic_retrieval_cases.yml --mode semantic-only
amem eval examples/evals/semantic_retrieval_cases.yml --mode hybrid-rrf
amem eval examples/evals/semantic_retrieval_cases.yml --mode hybrid-business
```

输出包含 Recall@K、Precision@K、MRR、nDCG@K、无结果准确率、forbidden-hit count、P50/P95/P99、semantic 完成数和 coverage。语义样本覆盖中文零词面重合、跨语言、精确 ID、否定 hard negative、跨 tenant、跨 agent、跨 session 和校准后的无结果查询。stale、tombstone、batch、timeout、bulkhead、generation switch/rollback 由确定性单元测试覆盖。

以下数据由同一台 Windows 11、Python 3.12.5 机器在 2026-07-22 运行：

```powershell
py -3.12 benchmarks\validate_runtime.py --records 5000 --iterations 30 --dimensions 1024
py -3.12 benchmarks\validate_runtime.py --records 10000 --iterations 30 --dimensions 1024
py -3.12 benchmarks\validate_runtime.py --records 100000 --iterations 30 --dimensions 32
```

| 可见记录/维度 | FTS5 P50/P95/P99 ms | semantic P50/P95/P99 ms | hybrid P50/P95/P99 ms | hybrid 状态 |
| ---: | ---: | ---: | ---: | --- |
| 5K / 1024 | 2.79 / 3.39 / 3.56 | 26.23 / 27.67 / 27.78 | 26.88 / 38.25 / 41.91 | 30/30 semantic 完成，0 timeout |
| 10K / 1024 | 2.65 / 3.10 / 3.14 | 51.52 / 53.57 / 54.42 | 54.31 / 108.16 / 108.32 | 28/30 完成，2 timeout |
| 100K / 32 | 3.17 / 3.30 / 3.30 | 415.33 / 422.83 / 425.49 | 2.85 / 106.19 / 107.25 | 0 完成、2 timeout、28 bulkhead reject |

100K 行的 hybrid P50 很低不是语义性能好，而是 bulkhead 快速拒绝后返回了 lexical-only。该组 FTS5 的 materialized full scan P50 为 5028.42ms，FTS5 候选链路 P50 为 3.17ms，合成选择性查询的加速比约 1584 倍。

这些结果只能证明实现边界，不能直接当生产 SLA：合成数据全部位于同一个可见 ACL 分区；100K 使用 32 维，仅用于观察记录数增长；FTS 查询只命中 1 条，不能代表高频宽查询。真正决定 exact vector scan 成本的是每次结构化/ACL 过滤后的可见向量数、维度、并发和硬件，而不是全库总条数。

本机证据表明 5K×1024 有充分余量，10K×1024 已出现 100ms deadline 尾部放大，100K 精确扫描明显不可用于该预算。生产必须用真实分区分布复跑，不能写死“超过 N 条就迁移”的通用阈值。

## 11. Qdrant 触发条件

满足任一条件时启动 Qdrant 方案评审：

- 真实 ACL 过滤分布和目标并发下，优化后的 exact vector leg P95 仍超过分配预算；
- 单 tenant/可见分区持续增长，CPU exact scan 成为瓶颈；
- 多实例需要共享同一实时向量索引；
- 需要在线 ANN、replica、水平扩展、独立资源隔离或高 QPS；
- 向量维护影响 SQLite 主事务写延迟或备份窗口。

迁移时 SQLite 继续是 Event/MemoryRecord/tombstone 的权威来源；Qdrant 只保存可重建向量与过滤 payload。沿用 generation、outbox、source sequence、幂等 point ID、对账 worker 和删除水位，不得把 Qdrant 变成第二个事实源。

## 12. 发布门禁

代码验证：

```powershell
py -3.12 -m ruff check src tests benchmarks
py -3.12 -m pytest
```

生产发布还必须满足：

- 目标模型、revision、维度和模板全部固定；
- `AMEM_EMBEDDING_MIN_SIMILARITY` 由目标数据校准，不使用示例值；
- generation coverage 达标、pending/running 为 0、dead letter 已处置；
- exact/ID suite 不回归，semantic suite 有稳定增益；
- forbidden-hit count 和 stale/tombstone 命中恒为 0；
- 真实 tenant/ACL 分布与目标并发下 P50/P95/P99 达标；
- shadow/canary 期间成本、provider error、timeout、bulkhead 和 circuit 指标可接受；
- 已验证备份恢复、semantic-off 降级和旧 generation 重建回滚。

仓库内代码、迁移、测试和可复现基准已经完成；模型校准、真实流量 shadow 与 canary 是环境相关发布动作，未执行前不得把整体状态标记为“生产已上线”。
