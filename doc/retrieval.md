# 检索

检索模块负责在身份、会话、level、status、visibility、类型、标签和 token 预算约束下，为当前请求选出可用记忆。

## 模块职责

- `StoreLexicalRetriever`：使用 SQLite FTS5 和结构化过滤召回关键词候选。
- `SemanticRetriever`：使用 embedding provider 和向量索引召回语义候选。
- `QdrantVectorIndex`：默认生产语义投影，在 vector top-k 前执行 payload 过滤。
- `HybridCandidateRetriever`：运行配置启用的 lexical 和 semantic 召回腿，并用 RRF 融合。
- `RetrievalPipeline`：执行硬过滤、轻量 priority/freshness 调整、确定性保护、最终过滤、权限校验和预算选择。

## 输入

- `MemoryQuery`：查询文本和身份约束。
- `RuntimeConfig.hybrid_retrieval`：候选数量、超时、权重和语义 provider 控制。
- `RuntimeConfig.deterministic_rerank`：状态、时间、实体和 no-answer 保护控制。

## 查询理解

Query understanding 只做过滤条件归一化，例如：

- `status`
- `level`
- `visibility`
- `tags`
- `memory_types`
- identity 和 session policy
- entity 和 time range

它不再决定是否使用 lexical 或 vector。召回方式由 runtime 配置决定，候选通过 RRF 融合。这样可以避免一个隐藏 router 同时承担理解、召回策略、短路检索和排序解释。

## Qdrant Payload

embedding outbox 发布向量到 Qdrant 时，payload 包含 `tenant_id`、`user_id`、`session_id`、`level`、`memory_status`、`visibility`、`tags`、`acl_principals` 等过滤字段。

Qdrant payload ACL 只是检索投影。最终授权仍由 `AccessChecker` 基于当前 SQLite `MemoryRecord` 二次校验。

## 排序边界

主排序使用 RRF。确定性规则负责硬过滤和保护，例如 ACL、status、tombstone、时间冲突、实体冲突和 no-answer。轻量调整只保留 priority/freshness。没有评测证明收益的复杂 factor 不进入主链路。
