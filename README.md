# AMEM Agent Memory Runtime

AMEM 是一个面向有状态 AI Agent 的长期记忆运行时。它负责显式记忆写入、语义整理、权限隔离、检索召回、上下文投影、审计留痕和后台向量索引。

项目的核心目标不是做普通 RAG 文档问答，而是维护 Agent 在多用户、多 Agent、多会话场景下的当前记忆状态，并把可追溯、可授权、可裁剪的记忆上下文交给模型使用。

## 现在能做什么

- 通过 `save_memory`、`revise_memory`、`forget_memory` 显式修改长期记忆。
- 将写入统一收敛为 `MemoryProposal`，再经过 `MemoryWritePolicy` 校验后写入 `MemoryRecord`。
- 支持 `core`、`working`、`archival` 三层记忆，用于区分稳定偏好、当前任务状态和归档历史。
- 使用 `tenant_id`、`user_id`、`agent_id`、`session_id`、`scope`、`visible_to` 做多租户和多 Agent 隔离。
- 使用 SQLite 保存真实状态、审计日志、tombstone、Auto Dream job、embedding outbox 和运行时状态。
- 默认使用 Qdrant 作为语义向量索引投影；SQLite 仍是事实源。
- 支持 SQLite FTS5 关键词检索、Qdrant 向量检索、RRF 融合、查询路由、确定性 rerank 和 no-answer 过滤。
- 支持基于 `MemoryAuditLog` 的审计重放，用 before/after 记录重建当前 `MemoryRecord` 状态。
- 支持 Auto Dream 后台任务，用于生成去重、强化、冲突审核、归档等维护 proposal。
- 支持 embedding outbox，Qdrant 临时失败不会影响 SQLite 中的真实记忆写入。
- 支持对话历史压缩和记忆上下文 token 预算，默认记忆注入预算为 `1000` tokens。
- 支持 OpenAI-compatible 模型网关、流式响应、工具调用、输出契约校验和本地 CLI。

## 主写入链路

```text
save/revise/forget tools or Auto Dream
  -> MemoryProposal
  -> MemoryWritePolicy
  -> MemoryService
  -> MemoryRecord
  -> MemoryAuditLog
  -> embedding outbox
  -> Qdrant vector projection
```

`MemoryRecord` 是当前状态事实源。`MemoryAuditLog` 是变更历史、证据来源和审计重放输入。Qdrant、FTS5 和 SQLite vector 都是可重建的检索投影。

## 主检索链路

```text
MemoryQuery
  -> QueryRouter
  -> FTS5 and/or Qdrant candidates
  -> RRF fusion
  -> deterministic rerank
  -> final filter
  -> AccessChecker
  -> ContextBuilder
```

查询路由会根据问题特征选择偏关键词、偏向量、混合、状态感知、时间感知或严格无答案模式。精确编号、负责人、字段类问题更偏 FTS5；自然语言改写、抽象语义和跨语言问题更偏 Qdrant；状态和时间问题会进入确定性 rerank。

## 核心模块

| 模块 | 作用 |
| --- | --- |
| `agent` | 执行 Agent 请求、模型调用、工具循环、checkpoint 和对话压缩。 |
| `tools` | 注册和执行显式工具，包括记忆保存、修订、删除和搜索。 |
| `memory.intake` | 把工具输入和 Auto Dream 输出转换为 `MemoryProposal`。 |
| `memory.write_policy` | 做字段校验、权限校验、乐观锁和风险拦截。 |
| `memory.service` | 事务内应用 proposal，写 `MemoryRecord`、tombstone 和 audit log。 |
| `memory.intake.dream` | 生成去重、强化、冲突审核、归档等语义维护 proposal。 |
| `memory.intake.worker` | 调度、租约、执行、重试和 checkpoint Auto Dream job。 |
| `memory.retrieval` | 查询路由、候选召回、RRF 融合、rerank、过滤和排序。 |
| `memory.embeddings` | 管理 embedding provider、generation、outbox worker、SQLite vector 和 Qdrant。 |
| `memory.stores` | 提供 SQLite、JSONL 和 in-memory 存储实现。 |
| `audit` | 记录审计 envelope、LLM trace、memory audit log 和审计重放输入。 |
| `access` | 做 principal-based 访问控制和敏感载荷清理。 |
| `context` | 构建模型可见的记忆上下文、结构化投影和个性化摘要。 |
| `llm` | 适配 OpenAI-compatible chat、streaming、tool call 和 usage 元数据。 |

## 安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,qdrant]"
```

只跑本地单元测试时不需要真实 Qdrant 服务。需要语义向量检索时，启动 Qdrant 并配置 embedding provider。

## 环境配置

`.env.example` 包含可用配置项。常用配置如下：

```dotenv
AMEM_VECTOR_BACKEND=qdrant
AMEM_QDRANT_URL=http://localhost:6333
AMEM_QDRANT_COLLECTION=agent_memory

AMEM_EMBEDDING_PROVIDER=openai-compatible
AMEM_EMBEDDING_MODEL=your-embedding-model
AMEM_EMBEDDING_DIMENSIONS=1024
AMEM_EMBEDDING_BASE_URL=https://api.openai.com/v1
AMEM_EMBEDDING_API_KEY_ENV=EMBEDDING_API_KEY
EMBEDDING_API_KEY=your-key
```

没有 Qdrant 时可以显式回退：

```dotenv
AMEM_VECTOR_BACKEND=sqlite
```

## CLI 快速使用

初始化本地运行目录：

```powershell
amem init
```

写入示例事件并检索：

```powershell
amem ingest examples/data/customer_support_events.jsonl
amem retrieve --agent support_agent --query "refund status"
amem project --agent support_agent --query "refund status"
```

生成模型回答：

```powershell
amem respond --agent support_agent --query "refund status"
amem respond --agent support_agent --query "refund status" --stream --fast
```

查看审计：

```powershell
amem audit
amem audit --type access
amem audit-dashboard --out .amem/audit.html
```

管理 embedding outbox：

```powershell
amem embedding status
amem embedding backfill
amem embedding worker
amem embedding activate
```

运行检索评测：

```powershell
amem eval examples/evals/retrieval_cases.yml
amem eval examples/evals/semantic_retrieval_cases.yml --mode hybrid-rrf
```

## Python 用法

```python
from agent_memory_runtime import AgentMemoryRuntime
from agent_memory_runtime.memory.intake import MemoryIntakeService, MemoryToolIdentity
from agent_memory_runtime.domain.query import MemoryQuery

runtime = AgentMemoryRuntime()
intake = MemoryIntakeService(runtime)

identity = MemoryToolIdentity(
    actor_id="user-1",
    tenant_id="tenant-1",
    user_id="user-1",
    agent_id="assistant",
    session_id="session-1",
)

result = intake.save_memory(
    {
        "kind": "preference.updated",
        "key": "reply_style",
        "content": "Use concise status updates.",
        "layer": "core",
    },
    identity=identity,
    idempotency_key="reply-style",
)

records, trace = runtime.retrieve(
    MemoryQuery(
        agent_id="assistant",
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        session_policy="profile",
        text="How should replies be written?",
        limit=5,
    )
)

context = runtime.project(
    MemoryQuery(
        agent_id="assistant",
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        session_policy="profile",
        text="How should replies be written?",
    )
)

print(result.memory_ids)
print([record.memory_id for record in records])
print(context.projected_context)
```

## 审计重放

显式写入会生成 `MemoryAuditLog`。每条日志保存 `before_record` 和 `after_record`：

- `after_record` 存在：重放时 upsert 该记忆。
- `after_record` 为空：重放时删除该记忆，并重建 tombstone。

Python 调用：

```python
report = runtime.replay_memory_audit()
print(report.final_memory_ids)
```

这条链路用于从审计日志恢复当前 memory store。legacy `runtime.replay(Event)` 仍保留为兼容入口，但新记忆主线以 `MemoryRecord + MemoryAuditLog` 为准。

## Auto Dream

Auto Dream 是后台语义维护链路。它不会直接绕过写策略改库，而是输出 `MemoryProposal`：

- 重复记忆：生成 reinforce/archive proposal。
- 同 key 冲突：生成 needs_review proposal。
- 明确修订：生成 revise proposal。
- 缺失派生：生成 create proposal。

SQLite `DreamStore` 保存 job、lease、checkpoint 和 review 状态。多 worker 通过租约领取任务；失败任务按 retry 策略回到队列。

## 上下文控制

`ContextBuilder` 只把当前查询相关、当前身份可访问、且落在 token 预算内的记忆注入模型上下文。默认记忆注入预算是 `1000` tokens。

对话历史压缩由 Agent checkpoint 负责，保留系统规则、原始任务、重要约束和近期对话，再把较早消息压缩成结构化摘要。

## 测试

```powershell
py -3.12 -m ruff check src tests benchmarks
py -3.12 -m pytest -q
```

## 文档

模块职责文档在 `doc/`：

- `doc/modules.md`
- `doc/architecture.md`
- `doc/api-contract.md`
- `doc/retrieval.md`
- `doc/storage.md`
- `doc/audit.md`
- `doc/governance.md`
- `doc/context.md`
- `doc/security.md`
- `doc/tools.md`
- `doc/operations.md`
- `doc/world-state.md`
