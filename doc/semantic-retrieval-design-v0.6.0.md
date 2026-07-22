# 语义检索设计与选型（v0.6 方案）

状态：Implemented（仓库内代码完成；生产模型校准与 shadow/canary 待目标环境执行）  
日期：2026-07-22

实施结果与运维手册见 [fts5-sqlite-vec-implementation-v0.6.0.md](fts5-sqlite-vec-implementation-v0.6.0.md)。本文的“现状核对”描述的是改造前 v0.5 基线，用来保留选型依据，不代表当前代码状态。

## 实施状态

- Phase 0 已完成：删除普通 `memory_terms` 基线，迁移到 FTS5/BM25，并补充语义与安全评测集。
- Phase 1 已完成：协议、schema v6、sqlite-vec 精确 cosine、ACL 投影、outbox 和批量 worker 已落地。
- Phase 2 已完成：独立双路召回、weighted RRF、deadline、cache、bulkhead、熔断、trace、generation 和删除/回放保护已落地。
- Phase 3 的仓库内工作已完成：5K/10K/100K 可复现基准和四模式评测 CLI 已落地；真实模型校准、shadow traffic 和 canary 属于部署动作，尚未伪报完成。
- Phase 4 是证据触发项：当前没有引入 Qdrant 或 FAISS 在线后端；本机 10K/100K 尾延迟已经给出真实分区压测与 Qdrant 评审依据。

## 结论

当前项目的在线语义检索不应直接使用 FAISS，推荐按以下路径建设：

1. 保留 SQLite 词法检索，新增独立的 dense semantic retriever。
2. 第一阶段使用稳定版 `sqlite-vec` 的 float32 向量函数，在普通 SQLite 表中保存向量，
   先执行 tenant/user/session/layer/status/ACL SQL 过滤，再做精确 cosine distance 排序。
3. 词法 top-N 与语义 top-N 必须独立召回后取并集，使用 weighted RRF 融合；不能只对词法候选做
   向量重排，否则没有词面重合的真正语义结果永远进不了候选集。
4. embedding 生成采用事务 outbox + 后台 worker。模型调用不进入记忆写事务；向量只有在
   `content_hash` 和 `last_event_sequence` 仍匹配时才能发布为 ready。
5. 当经过身份分区后的精确向量阶段无法满足实际 P95/SLA，或系统需要多节点、水平扩展和高并发时，
   保持同一 `VectorIndex` 接口切换到 Qdrant。不要在这一步切到需要自行补齐元数据、事务和运维面的
   FAISS 文件索引。

简化后的技术决策是：

```text
现在：SQLite + sqlite-vec 精确检索
以后：Qdrant 生产向量服务
不选：FAISS 作为在线主索引
```

## 改造前现状核对（历史基线）

### 改造前分支并未使用 FTS5

代码中的关键词索引是 schema v5 的 `memory_terms(memory_id, term)` 普通表，不是
`CREATE VIRTUAL TABLE ... USING fts5(...)`：

- `memory/retrieval/lexical.py` 生成拉丁词和 CJK 二元词。
- `SQLiteMemoryStore._replace_search_index()` 将去重词项写入 `memory_terms`。
- `SQLiteMemoryStore.query_records()` 按命中词项数量、salience、updated_at 取最多 256 个候选。
- `score_record()` 再用集合重合比例计算 keyword score。

因此当前实现没有 BM25 的词频、文档长度和 IDF 语义。如果 FTS5 在其他未合并分支，本方案只需让
`LexicalRetriever` 改接 FTS5；如果没有，应先把“已有 FTS5”的认知修正，不能基于不存在的能力设计。

### 当前检索边界

当前链路是：

```text
MemoryQuery
 -> normalize_query
 -> SQLiteMemoryStore.query_records(limit=256)
 -> hard_filter
 -> AccessChecker
 -> keyword/recency/salience/confidence/type/source scoring
 -> result budget
```

这个项目不是普通文档 RAG，向量检索必须保留以下语义：

- tenant 和 user 在候选截断前隔离。
- `exact/profile/all` 三种 session policy。
- Working/Core/Archival 分层与 archived 状态规则。
- private/shared/global、sensitive label、owner 和 `visible_to` 权限。
- Tombstone 删除水位、事件 replay 和派生状态可重建。
- `project_fast` 当前只有 150 ms 完整检索预算。

这些约束正是 FAISS 与本项目不匹配的主要原因。

## 选型对比

| 方案 | 与 SQLite 同事务 | 复杂元数据预过滤 | 增量更新/删除 | 单机大规模 ANN | 运维成本 | 本项目结论 |
| --- | --- | --- | --- | --- | --- | --- |
| sqlite-vec 普通表 + scalar distance | 是 | 最强，可复用任意 SQL/ACL JOIN | 简单 | 否，精确扫描 | 低 | 当前首选 |
| sqlite-vec `vec0` | 是 | 支持元数据，但复杂 OR/NULL/ACL JOIN 较受限 | 简单 | 稳定版仍以精确扫描为主 | 低 | 压测后的可选优化 |
| FAISS | 否 | 弱，主要基于数值 ID 或事后过滤 | 不同索引能力不一致 | 强 | 中 | 只用于离线实验 |
| LanceDB | 否，成为第二套本地数据系统 | 强 | 支持 | 强 | 中 | 当前重复建设 |
| Qdrant | 否，需要 outbox/对账 | 强，payload index 与 tenant 能力成熟 | 支持 | 强 | 高 | 规模化后的目标后端 |
| pgvector | 仅在主存储迁到 PostgreSQL 后成立 | 强 | 支持 | 强 | 中 | 当前不引入 PostgreSQL |

### 为什么不是 FAISS

FAISS 是高质量的向量算法库，但不是带业务事务、ACL、WAL、备份、过滤与一致性协议的数据存储：

- SQLite 记忆提交和 FAISS index 文件更新无法原子提交，需要额外 outbox、checkpoint、重放、对账和
  损坏恢复协议。
- 本项目每次查询都包含 tenant/user/session/layer/status/visibility 约束。FAISS 官方说明动态过滤
  支持有限，主要依赖数值 vector ID；事后过滤会让无权或无关记录挤占 top-K。
- FAISS 的更新、删除能力取决于具体索引类型。例如 HNSW 不支持删除；部分 IVF 操作要求 direct map，
  一些删除会扫描索引。
- 既然已经承担第二套在线索引的一致性和运维成本，Qdrant 的 payload filter、混合检索、持久化和
  服务化能力比裸 FAISS 更适合生产请求路径。

FAISS 仍适合：离线 recall/latency 基线、批量重建验证、无复杂过滤的只读快照，以及算法实验。

### 为什么当前选择 sqlite-vec 精确检索

项目的可复现规模基线是 10,000 条记忆。假设使用 1,024 维 float32，原始向量约为 39.1 MiB，
在 tenant/user/session 过滤后实际参与 distance 计算的记录更少。此时 ANN 增加的索引训练、召回损失、
删除语义和运维复杂度没有被证明值得。

稳定版 sqlite-vec 可在 Windows/Linux/macOS 的 SQLite 中加载，提供 cosine/L2 distance、普通 BLOB
向量和 `vec0`。项目应固定精确版本，并将扩展封装在 adapter 后；sqlite-vec 仍是 pre-v1，不能无上限
自动升级。当前 v0.1.10 的 DiskANN/IVF 属于 alpha，文档和删除代价都未达到本项目生产采用标准。

首版优先使用普通表而不是直接把所有状态复制进 `vec0`，原因是普通表可以先通过现有
`memories`、`memory_tags` 和后续 `memory_acl` 任意组合过滤，再调用 `vec_distance_cosine()`；这比在
虚拟表中重复并同步一套 nullable user、session policy 和 `visible_to` 元数据更可靠。

## 目标架构

### 组件边界

新增三个协议，不把 embedding 或向量后端硬编码进 `MemoryStore`：

```python
class EmbeddingProvider(Protocol):
    @property
    def spec(self) -> EmbeddingSpec: ...

    def embed_query(self, text: str) -> list[float]: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class CandidateRetriever(Protocol):
    def retrieve(self, query: MemoryQuery, *, limit: int) -> list[CandidateHit]: ...


class VectorIndex(Protocol):
    def upsert(self, item: VectorRecord) -> None: ...
    def delete(self, memory_id: str, *, through_sequence: int) -> None: ...
    def search(
        self,
        vector: list[float],
        query: MemoryQuery,
        *,
        limit: int,
    ) -> list[VectorHit]: ...
```

建议实现：

```text
LexicalRetriever  -> SQLite FTS5 或现有 memory_terms
SemanticRetriever -> EmbeddingProvider + VectorIndex
HybridRetriever   -> 并行执行两路、去重、RRF
RetrievalPipeline -> hard_filter、AccessChecker、业务精排、预算
```

`MemoryStore` 需要补充 `get_many(memory_ids)`，避免融合后逐 ID 查询造成 N+1。

### 读取链路

```text
MemoryQuery
 -> QueryPlanner / structured filter plan
 -> 并行
      -> LexicalRetriever top 64
      -> embed_query -> SemanticRetriever top 64
 -> union by memory_id
 -> hard_filter
 -> AccessChecker
 -> weighted RRF
 -> recency/salience/confidence/type/source business rerank
 -> min relevance gate
 -> result/context token budget
```

关键要求：

1. 两路检索必须使用相同的结构化身份过滤计划。
2. tenant/user/session/layer/status/type/scope/tag 必须在各自 top-N 截断前过滤。
3. `AccessChecker` 继续作为纵深校验，不能因为数据库已过滤就移除。
4. semantic retriever 超时不能拖垮 lexical retriever。`project_fast` 应改为按检索腿设置 deadline，
   使用在总预算内已经完成的腿，而不是因为 query embedding 超时丢弃已完成的词法结果并整体退回快照。
5. 语义检索总会返回“最近的某条”，所以必须按 embedding model 独立校准最低相关性门槛；不能使用
   未经评测的通用 cosine 阈值。

### 不能做成“FTS 候选 + 向量重排”

下列查询与记忆语义相关，但可能没有词项重合：

```text
query:  包裹还没到怎么办
memory: 快递目前仍在运输途中，可申请物流催办
```

如果先用 FTS/`memory_terms` 截到 256 条，再只对这 256 条做向量排序，相关记忆可能在第一阶段已经
被永久裁掉。正确方式是 lexical 与 semantic 各自产生候选，再融合。

## 排名与分数

### 第一版使用 weighted RRF

BM25、词项命中数和 cosine similarity 的分布不同，不能直接相加原始分数。第一版使用 rank-based
fusion：

```text
rrf(memory) = lexical_weight  / (rrf_k + lexical_rank)
            + semantic_weight / (rrf_k + semantic_rank)
```

初始值可使用：

```text
lexical_weight = 1.0
semantic_weight = 1.0
rrf_k = 60
lexical_limit = 64
semantic_limit = 64
```

这些只是起始参数，最终值必须用项目自己的 query-memory relevance 数据调优。不要把当前
`ScoreBreakdown.keyword` 偷换成 semantic score；应显式增加：

```text
lexical
semantic
fusion
recency
salience
confidence
type_boost
source_link
```

业务信号只在融合召回后精排。它们不能强到让高 salience 的无关画像稳定压过高相关结果。可先把
融合相关性固定为最终分数的主要部分，再通过离线 ablation 确定其余权重。

### 可选二阶段 reranker

在至少 100 条人工标注 query 和足够 hard negatives 之前，不引入 cross-encoder reranker。后续只有在
hybrid top-20 的 precision 仍不足、且延迟预算允许时，才把 reranker 作为独立第三阶段；不要用 LLM
自由打分替代可重复的检索评测。

## Embedding 设计

### 模型不是数据库的一部分

`EmbeddingSpec` 至少包含：

```text
provider
model_id
model_revision
dimension
distance_metric
normalized
query_template_version
document_template_version
generation
```

必须固定 model revision 和模板版本。相同模型配不同 query/document prefix 也视为不同 generation。
不同模型或 generation 的向量绝不能放在同一个相似度空间中搜索。

### 文本规范化

建议只对以下稳定语义字段生成 document embedding：

```text
memory_type: {memory_type}
content: {content}
tags: {经过白名单筛选的语义标签}
```

不要放入：memory_id、tenant_id、user_id、agent_id、时间戳、event ID、原始 metadata、审计字段。
这些字段主要制造伪相似度，也可能扩大敏感信息暴露面。embedding 输入必须来自已经完成 PII
最小化的正式 `MemoryRecord`，不能绕过现有 sanitizer 读取原事件。

### 初始模型候选

中文和英文混合场景可把 BGE-M3 作为质量基线：官方模型卡给出的 dense dimension 为 1,024，支持
多语言和 8,192 token 输入。但它约 568M 参数，不应未经延迟压测直接塞进 150 ms 的进程内快路径。

生产模型应在两类候选中实测后决定：

- 质量基线：BGE-M3 或同级多语言模型。
- 延迟基线：更小的多语言模型、ONNX/量化本地服务，或与运行时同地域的 embedding service。

项目核心包只依赖 `EmbeddingProvider`，本地重模型放入 optional extra 或独立服务，避免把
PyTorch/Transformers 强加给所有运行时用户。

## SQLite schema 草案

schema v6 只追加 migration，不修改 v1-v5 checksum。

迁移前必须先修正一个现有写入细节：`SQLiteMemoryStore.upsert()` 当前使用
`INSERT OR REPLACE INTO memories`。SQLite 的 `REPLACE` 本质上可能先删除旧行再插入新行；一旦 v6
向量/job/ACL 表通过外键引用 `memories`，普通 revise 也会触发 `ON DELETE CASCADE`，意外清掉子表，
并破坏预期的 outbox 状态机。这里必须改成：

```sql
INSERT INTO memories(/* columns */)
VALUES (/* values */)
ON CONFLICT(memory_id) DO UPDATE SET
    payload = excluded.payload,
    /* 逐列更新其余投影字段 */
    search_indexed = excluded.search_indexed;
```

该变更需要单独覆盖 child-row 保留、revision、rollback 和 concurrent reader 测试，不能等加完外键后
再处理。

```sql
CREATE TABLE embedding_jobs (
    job_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_sequence INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(memory_id, generation, content_hash),
    FOREIGN KEY(memory_id) REFERENCES memories(memory_id) ON DELETE CASCADE
);

CREATE INDEX idx_embedding_jobs_ready
ON embedding_jobs(status, available_at, lease_expires_at);

CREATE TABLE memory_embeddings (
    vector_id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    source_sequence INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    status TEXT NOT NULL,
    embedded_at TEXT NOT NULL,
    UNIQUE(memory_id, generation),
    FOREIGN KEY(memory_id) REFERENCES memories(memory_id) ON DELETE CASCADE,
    CHECK(typeof(embedding) = 'blob'),
    CHECK(vec_length(embedding) = dimensions)
);

CREATE INDEX idx_memory_embeddings_generation_status
ON memory_embeddings(generation, status, memory_id);
```

精确 semantic query 的形态：

```sql
WITH eligible AS (
    SELECT m.memory_id
    FROM memories AS m
    WHERE m.tenant_id = :tenant_id
      AND /* user/session/layer/status/type/scope/tag/ACL filters */
),
semantic AS (
    SELECT
        e.memory_id,
        vec_distance_cosine(e.embedding, :query_embedding) AS distance
    FROM memory_embeddings AS e
    JOIN eligible USING (memory_id)
    WHERE e.generation = :generation
      AND e.status = 'ready'
    ORDER BY distance ASC, e.memory_id ASC
    LIMIT :semantic_limit
)
SELECT memory_id, distance FROM semantic;
```

`embedding` 用 `sqlite_vec.serialize_float32()` 绑定为 BLOB，不通过巨大的 JSON 数组写 SQL。

### ACL 预过滤

当前 `visible_to` 在 JSON payload 内，`AccessChecker` 在候选截断后才检查。为了让语义 top-K 不被其他
Agent 的 private 记录挤占，v6 应同时将可见主体规范化：

```sql
CREATE TABLE memory_acl (
    memory_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    PRIMARY KEY(memory_id, principal_id),
    FOREIGN KEY(memory_id) REFERENCES memories(memory_id) ON DELETE CASCADE
);

CREATE INDEX idx_memory_acl_principal
ON memory_acl(principal_id, memory_id);
```

owner、`visible_to` 和允许的 shared/global 语义投影到 ACL 查询；sensitive label 仍由 AccessChecker
复核。无论使用 sqlite-vec、FAISS 还是 Qdrant，这一步都是权限正确性要求，不是性能优化项。

## 写入、一致性与删除

### Outbox 写入链路

embedding API/本地模型推理不能放在 SQLite `BEGIN IMMEDIATE` 内。正确链路是：

```text
MemoryStore.upsert(record)
 -> 在同一 SQLite 事务中
      -> 更新 memories
      -> 将旧 generation 向量标记 stale
      -> upsert embedding_job(content_hash, source_sequence)
 -> commit

EmbeddingWorker.claim(job)
 -> 事务外批量 embed_documents
 -> 开启短事务
      -> 重新读取当前 MemoryRecord
      -> 校验 content_hash 和 last_event_sequence
      -> 若匹配：upsert vector，status=ready
      -> 若不匹配：job=superseded，不发布旧向量
 -> commit
```

重试必须是有界指数退避、lease + fencing，复用现有 derivation worker 的生产语义。审计只记录
model/generation、数量、耗时、错误类型和 hash，不保存正文或向量。

### 更新与删除

- revise：事务内先让旧向量不可检索，再排新 embedding job；新向量未完成期间由词法腿服务。
- tombstone/delete：SQLite 事务内删除/失效 embedding；读取仍以 tombstone sequence 为最终水位。
- replay：`memories` 重建后按 generation 批量重建向量；不能假设旧向量文件是权威状态。
- clear/replace_all：必须同步清空或重建 embedding state，避免孤儿向量。
- backup：向量在同一 SQLite 文件时沿用在线 backup；embedding model artifact 和 generation 配置另行
  记录并校验。

向量是正文的可推断派生物，不是匿名数据。它必须继承正文的 retention、备份、租户隔离、加密和删除
要求。

## 模型升级

禁止原地覆盖整个向量空间。采用 generation 切换：

```text
generation A serving
 -> 创建 generation B
 -> 后台 backfill B
 -> 新写入双写 job A/B（或记录变更水位）
 -> coverage B 达标
 -> shadow query，对比 A/B/hybrid 指标
 -> 原子切换 active_generation=B
 -> 观察期
 -> 删除 A
```

切换条件至少包括：

- ready coverage 达到发布门槛，且无持续增长的 backlog。
- 新 generation 的 Recall@K/MRR/nDCG 不低于门槛。
- exact keyword/ID 查询无回归。
- forbidden memory 命中数恒为 0。
- P50/P95/P99 和资源占用满足目标。

## 降级与超时

语义检索是增强能力，不应成为记忆系统单点故障：

- query embedding 超时：保留已完成的 lexical 结果并标记 `semantic_timed_out=true`。
- embedding worker 积压：旧 ready 向量继续服务；发生 revise 的记录不得使用 stale 向量。
- sqlite-vec 加载失败：启动 health check 明确报告 semantic unavailable；生产 strict 模式可选择启动
  失败，开发模式才允许 lexical-only。
- 维度/model generation 不匹配：拒绝查询，不做截断、补零或跨模型比较。
- 外部 provider 熔断：错误类型进入指标，不记录供应商异常原文。

需要新增 trace 字段：

```text
retrieval_legs
lexical_candidate_count
semantic_candidate_count
semantic_generation
embedding_ms
vector_search_ms
fusion_ms
semantic_timed_out
embedding_coverage
```

## 评测与发布门禁

现有 `examples/evals/retrieval_cases.yml` 只有 4 个 case，只能做 smoke test，不能用于选择 embedding
模型或融合权重。至少补充以下集合：

1. 零词面重合的中文释义检索。
2. 中英跨语言 query-memory 配对。
3. 数字、订单号、产品名等必须由词法腿命中的 exact queries。
4. 否定、反义、同主题不同事实的 hard negatives。
5. preference revise 后旧向量绝不能命中。
6. other tenant/user/agent、private、sensitive、session policy 负样本。
7. archived recall intent 与普通查询隔离。
8. embedding timeout、worker backlog、replay、tombstone、generation switch。

每次评测同时运行：

```text
lexical only
semantic only
hybrid RRF
hybrid + business rerank
```

指标包括：Recall@K、Precision@K、MRR、nDCG@K、无结果准确率、forbidden-hit count、P50/P95/P99、
embedding coverage/lag、每 query 成本。发布要求不是“hybrid 平均分更高”这么简单，而是：

- exact query suite 不回归。
- semantic/paraphrase suite 有统计上稳定的提升。
- forbidden-hit count 必须为 0。
- stale/tombstoned vector 命中必须为 0。
- 目标并发下完整链路满足项目的 latency budget。

基准应覆盖真实 tenant 分布，而不只是均匀合成数据；向量扫描成本取决于每次权限过滤后的候选数，
不能只看全库总条数。

## 何时切换 Qdrant

不要用一个拍脑袋的固定条数阈值做迁移。出现任一事实时启动 Qdrant 方案评审：

- 在真实过滤分布和目标并发下，优化后的 SQLite vector leg P95 仍超过分配给它的预算。
- 单个 tenant/可见分区持续增长，精确扫描 CPU 成为主要瓶颈。
- 多个应用实例需要共享同一实时向量索引。
- 需要在线 ANN、水平扩展、replica、独立资源隔离或高 QPS。
- 向量索引维护开始影响 SQLite 主事务的写延迟或备份窗口。

迁移时保持 SQLite 为事件和 MemoryRecord 权威来源，Qdrant 只保存可重建的向量投影和过滤 payload；
使用 outbox、幂等 point ID、source sequence、对账 worker 和 tombstone 水位解决跨系统一致性。
Qdrant collection 按 embedding generation/model 建，不为每个 user 建 collection；tenant/user/session/ACL
字段建立 payload index，并继续在应用层执行 `AccessChecker`。

## 实施顺序

### Phase 0：修正词法基线

- 确认要保留 `memory_terms` 还是正式迁到 FTS5。
- 将词法召回封装为 `LexicalRetriever`，补齐真实 BM25/命中 trace。
- 扩充语义与安全评测集。

### Phase 1：协议与精确向量基线

- 新增 `EmbeddingProvider`、`VectorIndex`、`CandidateRetriever`、`CandidateHit`。
- 增加 sqlite-vec optional dependency，固定稳定版本并在每个连接上受控加载后立即关闭 extension loading。
- 增加 schema v6、embedding outbox worker、`get_many` 和 ACL 投影。
- 实现 SQLite 精确 cosine retriever。

### Phase 2：Hybrid 与生产保护

- 两路并行召回、weighted RRF、模型相关 relevance gate。
- 分腿 deadline、query embedding cache、trace/metrics/audit。
- tombstone/replay/revise/backup/generation migration 测试。

### Phase 3：压测和上线

- 10K、100K 及真实 tenant 分布基准。
- lexical/semantic/hybrid 离线对比和权重调优。
- shadow traffic，不影响现有结果。
- 小流量启用 hybrid，观察命中、延迟、成本和安全指标后扩大。

### Phase 4：按证据升级

- 优先压测 `vec0` tenant partition 或量化是否足够。
- 仍不达标且具备服务化需求时，实现 `QdrantVectorIndex`。
- FAISS 仅保留在 benchmark/offline tooling，不进入默认在线架构。

## 上游依据

- [sqlite-vec KNN 与 cosine distance](https://alexgarcia.xyz/sqlite-vec/features/knn.html)
- [sqlite-vec metadata/partition filtering](https://alexgarcia.xyz/blog/2024/sqlite-vec-metadata-release/index.html)
- [sqlite-vec Python 加载方式](https://alexgarcia.xyz/sqlite-vec/python.html)
- [sqlite-vec release 与 ANN alpha 状态](https://github.com/asg017/sqlite-vec/releases)
- [sqlite-vec pre-v1 兼容性说明](https://github.com/asg017/sqlite-vec)
- [FAISS index 选择与更新/删除边界](https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index)
- [FAISS 动态过滤边界](https://github.com/facebookresearch/faiss/wiki/FAQ)
- [Qdrant hybrid/RRF](https://qdrant.tech/documentation/search/hybrid-queries/)
- [Qdrant payload filtering](https://qdrant.tech/documentation/search/filtering/)
- [Qdrant multitenancy](https://qdrant.tech/documentation/tutorials/multiple-partitions/)
- [LanceDB filtering/hybrid API](https://lancedb.github.io/lancedb/python/python/)
- [BGE-M3 官方模型卡](https://huggingface.co/BAAI/bge-m3)
