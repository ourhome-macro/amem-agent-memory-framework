# 存储抽象与 Qdrant 热切换决策

状态：Proposed  
日期：2026-07-23

## 结论

项目不应该在产品层继续和 SQLite 强绑定。SQLite 应保留为默认嵌入式后端，但运行时需要暴露清晰的存储能力层，让部署可以按能力选择：

- SQLite：单节点、低运维成本的事实存储。
- SQLite FTS5/sqlite-vec：默认嵌入式词法与语义索引。
- Qdrant：当规模、多实例共享检索或向量尾延迟需要时，作为生产向量索引。

这个抽象不能做成一个泛化的 `Storage` 接口。当前运行时包含多个一致性域，Qdrant 只适合其中的向量索引域，不适合替代事件源、审计、队列、lease、回放和快照。

## 当前形态

SQLite 目前承载了多类职责：

- 事件源：append-only 事件与 replay 顺序。
- 记忆投影：当前 `MemoryRecord` 与结构化查询字段。
- Tombstone：删除水位。
- Snapshot：加速 replay，并提供有界恢复状态。
- Derivation queue：持久 outbox 与 worker lease。
- Agent run 状态：乐观版本、checkpoint、tool call、approval、fenced lease。
- Orchestration 状态：DAG run、delegation、版本与 lease。
- Audit envelope：合规和 trace 记录。
- 检索索引：FTS5/BM25 与 sqlite-vec embedding。

代码里已经有若干有价值的协议，`AgentMemoryRuntime` 也支持注入 store。真正的耦合在于：`SQLiteStoreBundle`、SQLite migration、embedding job、FTS5 和 sqlite-vec 被打包成了一个统一的操作后端。

## 为什么不能做成整体后端切换

Qdrant 是向量数据库，不是事件源，也不是运行时状态机后端。把所有状态放到 `sqlite | qdrant` 这样的开关后面，会制造错误契约：

- Qdrant 无法提供事件 append、记忆派生、snapshot 更新和队列状态之间的本地事务边界。
- Agent 与 orchestration 的 lease 依赖 rich payload 上的 compare-and-set 语义，这不是向量索引职责。
- Audit 与 tombstone 需要持久、可排序、可审计的事实记录。
- 向量索引天然是异步同步出来的投影，把它当事实源会破坏 replay 和删除语义。

正确拆法是：事实存储和检索索引分离。

## 推荐能力接口

保留现有领域协议，但把后端能力提升成更明确的接口：

```python
class RuntimeStateBackend(Protocol):
    transaction_manager: TransactionManager
    events: EventStore
    memories: MemoryStore
    snapshots: SnapshotStore
    tombstones: TombstoneStore
    audit_store: AuditStore
    audit: AuditStore
    agent_state: AgentStateStore
    orchestration_state: OrchestrationStateStore


class LexicalIndex(Protocol):
    def search(self, query: MemoryQuery, *, limit: int) -> list[CandidateHit]: ...
    def upsert(self, record: MemoryRecord) -> None: ...
    def delete_memory(self, memory_id: str, *, through_sequence: int | None = None) -> None: ...


class VectorIndex(Protocol):
    def upsert(self, record: VectorRecord) -> None: ...
    def search(
        self,
        vector: list[float],
        query: MemoryQuery,
        *,
        spec: EmbeddingSpec,
        limit: int,
    ) -> list[VectorHit]: ...
    def delete_memory(self, memory_id: str, *, through_sequence: int | None = None) -> None: ...
    def coverage(self, *, generation: str) -> float: ...
```

现有 `VectorIndex` 协议已经接近正确形态。Qdrant 应该实现这个协议，而不是替代 `MemoryStore`。

## 配置模型

配置上不要使用一个含义过载的 storage flag，而应拆成事实存储、词法索引和语义索引：

```yaml
runtime_state:
  backend: sqlite
  sqlite:
    path: .amem/runtime.sqlite

retrieval:
  lexical:
    backend: sqlite-fts5
  semantic:
    backend: sqlite-vec
    # 或者:
    # backend: qdrant
    qdrant:
      url: http://localhost:6333
      collection: agent_memory_runtime
```

默认值应继续是零外部依赖的 SQLite。Qdrant 应作为 optional extra dependency 和显式部署选择。

## Qdrant 一致性契约

如果引入 Qdrant，SQLite 仍然是事实源。写入流程应是：

```text
Memory write transaction
 -> upsert memory row
 -> schedule embedding/index job
 -> commit

Embedding/index worker
 -> read memory from truth store
 -> verify content_hash and source_sequence
 -> embed document
 -> upsert vector payload into Qdrant
 -> mark job complete only after successful publish
```

Qdrant payload 必须包含足够字段，以便在 top-K 截断前完成结构化预过滤：

- tenant_id
- user_id
- agent_id
- session_id
- layer
- status
- memory_type
- scope
- tags
- ACL principals 或规范化后的 visibility payload
- generation
- content_hash
- source_sequence

检索链路仍必须在候选返回后执行现有 access checker。数据库过滤是优化和第一道防线，不是最终授权边界。

## 热切换方案

系统可以支持从 `sqlite-vec` 热切换到 Qdrant，但只能切换语义检索的向量索引腿。运行时事实状态不能从 SQLite 热切换到 Qdrant，因为 Qdrant 不是事件源、lease store、queue store、audit store 或 replay store。

热切换应使用 active backend 指针和显式 promotion gate：

```yaml
retrieval:
  semantic:
    active_backend: sqlite-vec
    shadow_backend: qdrant
    generation: embedding-...
```

推荐发布流程：

1. 准备 Qdrant collection：使用当前 active embedding generation、dimensions、distance metric、payload indexes 和 ACL/filter 字段。
2. 从 SQLite 事实状态 backfill 到 Qdrant。point identity 使用 `memory_id` + `generation`，payload 带上 `content_hash` 和 `source_sequence`。
3. 正常写入继续先进 SQLite。embedding/index worker 在 shadow 阶段同时发布到 `sqlite-vec` 和 Qdrant。
4. 执行 shadow read：线上回答仍使用 `sqlite-vec`，但运行时在同一 deadline 下额外查询 Qdrant，记录 recall overlap、latency、filter correctness、stale/missing vector count 和 access-check reject。
5. 只有完整观察窗口内所有门禁通过，才允许 promotion。
6. 翻转 `active_backend` 到 Qdrant，不改变事实存储。
7. 在 Qdrant 通过 canary 和全量流量窗口前，保持 `sqlite-vec` warm fallback。

最低 promotion gate：

- Qdrant 对目标 generation 的覆盖率达到配置阈值，例如 active 且可见记忆的 99.9%。
- 不存在已知 stale vector：Qdrant payload 中的 `content_hash` 和 `source_sequence` 与 SQLite 在抽样和近期更新记录上匹配。
- ACL 与结构化过滤在对抗性测试中等价。
- semantic leg P95/P99 在生产并发下满足检索预算。
- shadow recall 在评测集上不显著差于 sqlite-vec。
- 删除和 tombstone 传播延迟低于配置的安全窗口。
- `sqlite-vec` fallback 已自动化并通过演练。

运行时应在每次查询时从一个小型配置对象或 feature flag cache 解析 active semantic backend。指针可以周期性刷新，但必须带版本；每条 request trace 都要记录本次由哪个 backend、generation 和 collection 提供向量候选。

## 规模触发条件

不要因为 Qdrant 可用就切。只有当 SQLite exact vector search 不再是合适的操作形态时才切。可执行触发条件包括：

- 经过 tenant/user/session/ACL 过滤后的 semantic leg P95 或 P99 持续超过请求预算。
- 单个 SQLite 文件成为 embedding worker 与在线检索之间的读写协调瓶颈。
- 多个应用实例需要共享在线向量检索服务。
- 过滤后的向量规模足够大，exact scan 成本成为主要延迟来源。
- 部署需要 Qdrant 的运维能力：payload index、collection snapshot、replication、服务级隔离。

在这些条件出现前，SQLite FTS5/sqlite-vec 是更简单、更安全的默认方案。

## 回滚

回滚必须是 backend 指针翻转，不是数据手术：

```text
active_backend=qdrant
 -> detect gate breach or incident
 -> active_backend=sqlite-vec
 -> keep Qdrant writer running or pause it explicitly
 -> reconcile Qdrant from SQLite before the next promotion attempt
```

回滚条件应包括 Qdrant error rate 升高、timeout rate 升高、异常 access-check reject、stale vector rate、tombstone lag 或 recall regression。SQLite 始终保持权威，因此回滚不需要把数据从 Qdrant 复制回 SQLite。

## 迁移路径

1. 重命名 SQLite-specific bundle/config 对象，让它们明确只是一个后端实现，而不是运行时契约。
2. 引入 `RuntimeStateBackend` 和 CLI/runtime factory。
3. 从 `SQLiteMemoryStore` 中拆出 lexical index 与 semantic index 构建逻辑。
4. 保留 SQLite FTS5/sqlite-vec 作为默认实现。
5. 仅在现有 `VectorIndex` 形态后面增加 Qdrant。
6. 增加 SQLite vector 与 Qdrant vector 共享的 conformance tests：ACL 过滤、generation activation、stale vector rejection、删除、coverage、timeout fallback、replay recovery。

## 决策

增加存储能力抽象，但不要把 SQLite 和 Qdrant 做成全状态的平级替代。SQLite 继续作为嵌入式事实后端。Qdrant 作为可选语义检索后端，只有在生产规模证明需要第二套在线向量服务时才启用。
