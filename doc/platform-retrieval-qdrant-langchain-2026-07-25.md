# 平台化检索改造说明

状态：Implemented  
日期：2026-07-25

## 改造范围

本轮改造没有把核心运行时切到 Qdrant 或 LangChain，而是补齐平台化接入边界：

- 新增 `QdrantVectorIndex`，实现现有 `VectorIndex` 协议。
- `SemanticRetriever` 从 SQLite 专用类型放宽为通用 `VectorIndex`。
- `EmbeddingWorker` 发布向量时，如果后端支持完整 `MemoryRecord` payload，会把 tenant、user、session、ACL、tags 等过滤字段同步写入向量后端。
- 新增 `AgentMemoryLangChainRetriever`，把本项目检索结果适配成 LangChain `Document`，供外部平台接入。
- 新增 optional extras：`qdrant`、`langchain`、`platform`。

核心事实层仍然是 SQLite：events、memories、tombstones、snapshots、audit、queue、leases、agent run 和 orchestration run 不被 Qdrant 或 LangChain 接管。

## 关键词检索完整流程

关键词检索是当前默认可用链路，不依赖 embedding 服务：

```text
Event ingest
 -> sanitize
 -> append events
 -> derive MemoryRecord
 -> upsert memories
 -> 写入 memory_fts
 -> 写入 memory_tags
 -> 写入 memory_acl

Query
 -> MemoryQuery
 -> normalize / FTS query
 -> SQL 结构化预过滤
      tenant/user/session/layer/status/type/scope/tag/ACL
 -> FTS5 BM25 排序
 -> lexical top-N
 -> access checker 二次校验
 -> business rerank
 -> context budget
```

面试里要强调：关键词检索不是简单字符串 contains。它先用结构化字段和 ACL 把候选空间压住，再用 FTS5/BM25 排序；授权过滤发生在 top-K 之前，避免无权数据挤占候选。

## 向量检索完整流程

向量检索不是在写事务里直接调模型，而是 outbox 异步发布：

```text
Memory write transaction
 -> upsert memory
 -> schedule embedding job
 -> commit

Embedding worker
 -> claim job with lease
 -> read current memory
 -> verify content_hash/source_sequence
 -> call embedding provider, for example BGE-M3 service
 -> validate dimensions and finite values
 -> upsert VectorRecord
 -> complete job
```

在线查询时：

```text
Query
 -> lexical leg: FTS5/BM25 top-N
 -> semantic leg:
      embed query
      vector index search with same tenant/user/session/ACL filters
      min_semantic_similarity gate
 -> weighted RRF fusion
 -> access checker
 -> business rerank
 -> context budget
```

关键点：

- 词法和语义是独立召回，不是“FTS 候选再向量重排”。
- 语义检索必须带模型 generation，避免模型升级时新旧向量混用。
- `content_hash` 和 `source_sequence` 用来防止旧 job 覆盖新内容。
- Qdrant 只替代 vector index，不替代 SQLite 事实源。
- Qdrant payload 必须包含 tenant/user/session/ACL 等字段，保证 top-K 前预过滤。

## 评测完整流程

评测分两类输入：

- `examples/data/*.jsonl`：先导入事件，生成记忆和索引。
- `examples/evals/*.yml`：定义查询、期望 memory_id、禁止出现的 memory_id、k 和 relevance。

命令流程：

```powershell
amem init --path .amem-eval
amem ingest examples\data\memory_eval_events.jsonl --data-dir .amem-eval
amem eval examples\evals\retrieval_cases.yml --mode lexical-only --data-dir .amem-eval
```

语义评测还需要：

```powershell
$env:AMEM_EMBEDDING_MODEL = "BAAI/bge-m3"
$env:AMEM_EMBEDDING_MODEL_REVISION = "pinned-revision"
$env:AMEM_EMBEDDING_DIMENSIONS = "1024"
$env:AMEM_EMBEDDING_BASE_URL = "https://embedding.example.com/v1"
$env:AMEM_EMBEDDING_API_KEY_ENV = "EMBEDDING_API_KEY"
$env:EMBEDDING_API_KEY = "<secret>"
$env:AMEM_EMBEDDING_MIN_SIMILARITY = "<calibrated-threshold>"

amem embedding backfill --data-dir .amem-eval
amem embedding worker --data-dir .amem-eval
amem embedding activate --data-dir .amem-eval
amem eval examples\evals\semantic_retrieval_cases.yml --mode hybrid-business --data-dir .amem-eval
```

指标含义：

- `Recall@K`：期望 memory_id 有多少比例出现在前 K 个结果里。
- `Precision@K`：前 K 个结果里有多少比例是期望结果。
- `MRR`：第一个命中结果的倒数排名。
- `nDCG`：考虑 relevance grade 和排序位置的质量。
- `forbidden_hit_count`：越权或不应出现的记忆命中数，必须为 0。
- `no_result_accuracy`：无答案场景是否正确返回空结果。

## 面试讲法

可以按这条主线讲：

```text
这个项目不是普通 RAG，而是 event-sourced agent memory runtime。
事实源是 SQLite，保证事件回放、删除水位、审计、队列和 lease。
检索层拆成 lexical 和 semantic 两条腿：
  lexical 用 FTS5/BM25，适合精确词、编号、中文词面命中；
  semantic 用 embedding + VectorIndex，适合改写、跨语言、零词面重合。
两条腿独立 top-N，再用 weighted RRF 融合，最后做权限复核和业务精排。
平台化后 Qdrant 只替代 VectorIndex，SQLite 仍是 truth store。
上线 Qdrant 要 shadow read、coverage gate、Recall/latency/ACL 对比和 fallback。
```

不要说“我们把 SQLite 换成 Qdrant”。正确说法是：“我们把语义向量索引抽象出来，SQLite 是默认实现，Qdrant 是规模化实现，事实存储不切。”
