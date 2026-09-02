# AMEM Agent 记忆运行时

AMEM 是一个面向有状态 AI Agent 的长期记忆运行时。它负责显式记忆写入、语义维护、权限隔离、检索召回、上下文投影、审计重放和后台向量索引。

这个项目不是普通 RAG 文档问答层。它要解决的是：在多用户、多 Agent、多会话场景下，维护当前可信记忆状态，并把可追溯、可授权、可裁剪的记忆上下文交给模型使用。

## 当前能力

- 通过 `save_memory`、`revise_memory`、`forget_memory` 显式修改长期记忆。
- 所有写入先收敛成 `MemoryProposal`，再由 `MemoryWritePolicy` 校验，最后落成 `MemoryRecord`。
- 使用 `level=L0/L1/L2/L3` 表达原始事件、记忆原子、场景记忆和用户画像。
- 使用 `status=active/superseded/archived/deleted` 表达生命周期。
- 使用 `visibility=private/shared/public` 和 `visible_to` 表达可见性与授权对象。
- SQLite 保存事实源、审计日志、tombstone、Auto Dream job、embedding outbox 和运行时状态。
- Qdrant 作为可重建的生产语义索引。
- 支持 SQLite FTS5 关键词检索、Qdrant 语义检索、RRF 融合、硬过滤、确定性保护和最终权限校验。
- 支持基于 `MemoryAuditLog` 的 before/after 审计重放。
- 支持 Auto Dream 后台维护，用于合并、替代、审核、归档状态转换和缺失记忆派生。
- 支持 embedding outbox，向量索引失败不会破坏 SQLite 中的真实写入。
- 支持对话历史压缩、记忆上下文 token 预算、OpenAI-compatible 模型网关、流式响应、工具调用和本地 CLI。

## 记忆模型

AMEM 把抽象层级、生命周期和可见性拆开，不再用一个 `layer` 字段混合表达。

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `level` | `L0`、`L1`、`L2`、`L3` | 原始事件、记忆原子、场景、画像 |
| `status` | `active`、`superseded`、`archived`、`deleted` | 生命周期状态 |
| `visibility` | `private`、`shared`、`public` | 默认可见性 |
| `visible_to` | principal id 列表 | 显式授权对象 |
| `priority` | 数值 | 轻量排序和上下文预算信号 |

历史 payload 中的旧 `layer`、`scope` 会在加载时转换。新的公共契约应只使用 `level`、`status`、`visibility`。

## 主写入链路

```text
save/revise/forget 工具或 Auto Dream
  -> MemoryProposal
  -> MemoryWritePolicy
  -> MemoryService
  -> MemoryRecord
  -> MemoryAuditLog
  -> embedding outbox
  -> Qdrant vector projection
```

`MemoryRecord` 是当前事实源。`MemoryAuditLog` 保存变更历史和审计重放输入。Qdrant、FTS5、SQLite vector 都是可以重建的检索投影。

写入动作只保留少量真正改变状态的动作：

- `create`：创建新记忆。
- `merge`：合并证据或更新现有记忆。
- `supersede`：把目标记忆标记为已被替代。
- `ignore`：明确不产生持久状态变更。
- `delete`：删除并写 tombstone。

审核不是写入动作，而是 `decision_status=pending_review`。归档不是层级，而是 `status=archived`。

## 主检索链路

```text
MemoryQuery
  -> query normalization
  -> structured filters
  -> FTS5 and Qdrant candidates when configured
  -> RRF fusion
  -> hard filters and deterministic guards
  -> AccessChecker
  -> ContextBuilder
```

Query understanding 只抽取过滤条件，例如 `status`、`level`、`visibility`、标签、类型、身份、会话策略、实体和时间范围。它不再决定是否走 lexical 或 vector。配置启用的关键词和语义召回会共同运行，再通过 RRF 融合。

## 向量化策略

不是所有记忆都应该 embedding。

| level | 默认索引行为 |
| --- | --- |
| `L0` 原始事件 | 仅当 `metadata.embedding_index=true` 时索引 |
| `L1` 记忆原子 | 默认 embedding |
| `L2` 场景记忆 | 依赖元数据、文本和时间召回 |
| `L3` 用户画像 | profile-aware 查询时直接加载 |

## 存储边界

- SQLite 是生产事实源。
- Qdrant 是可重建的语义索引。
- JSONL 用于导出、备份和调试。
- SQLite vector 是测试和本地开发 fallback。

这个边界让事务、恢复和故障语义保持清楚。

## 核心模块

| 模块 | 职责 |
| --- | --- |
| `agent` | Agent 请求、模型调用、工具循环、checkpoint、对话压缩 |
| `tools` | 显式记忆写入和搜索工具 |
| `memory.intake` | 将工具输入和 Auto Dream 输出转换为 `MemoryProposal` |
| `memory.write_policy` | 字段、身份、版本、权限和写入风险校验 |
| `memory.service` | 应用 proposal，写入记忆、tombstone 和审计 |
| `memory.intake.dream` | 生成语义维护 proposal |
| `memory.intake.worker` | Auto Dream 租约、执行、重试、checkpoint 和 review |
| `memory.retrieval` | 关键词/语义召回、RRF、过滤、确定性保护和预算选择 |
| `memory.embeddings` | embedding provider、outbox worker、SQLite vector fallback、Qdrant |
| `memory.stores` | SQLite、JSONL、in-memory 存储实现 |
| `audit` | 审计 envelope、LLM trace、memory audit log 和重放输入 |
| `access` | principal-based 访问控制和敏感载荷清理 |
| `context` | 模型可见记忆上下文和个性化投影 |
| `llm` | OpenAI-compatible chat、streaming、tool call、usage 元数据 |

## 安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,qdrant]"
```

只跑本地单元测试不需要真实 Qdrant。语义检索需要启动 Qdrant 并配置 embedding provider。

## 常用配置

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

本地 fallback：

```dotenv
AMEM_VECTOR_BACKEND=sqlite
```

## CLI 快速使用

```powershell
amem init
amem ingest examples/data/customer_support_events.jsonl
amem retrieve --agent support_agent --query "refund status"
amem project --agent support_agent --query "refund status"
amem respond --agent support_agent --query "refund status"
```

审计和 embedding：

```powershell
amem audit
amem audit --type access
amem audit-dashboard --out .amem/audit.html

amem embedding status
amem embedding backfill
amem embedding worker
amem embedding activate
```

检索评测：

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
        "level": "L3",
        "visibility": "private",
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

显式写入会生成 `MemoryAuditLog`。每条日志包含 `before_record` 和 `after_record`：

- `after_record` 存在：重放时 upsert 该记忆。
- `after_record` 为空：重放时删除该记忆，并重建 tombstone。

```python
report = runtime.replay_memory_audit()
print(report.final_memory_ids)
```

## Auto Dream

Auto Dream 是后台语义维护链路。它不会绕过写策略，而是输出 `MemoryProposal`：

- 重复记忆：`merge`，重复项通过 `status=archived` 退出 active 集合。
- 同 key 冲突：`ignore` 加 `decision_status=pending_review`。
- 明确修正：`merge` 或 `supersede`。
- 缺失派生记忆：`create`。

SQLite `DreamStore` 保存 job、lease、checkpoint 和 review。多 worker 通过租约领取任务；失败任务按 retry 策略回到队列。

## 上下文控制

`ContextBuilder` 只注入与当前查询相关、当前身份可访问、已清理、且落在 token 预算内的记忆。对话历史压缩由 agent checkpoint 负责。

## 测试

```powershell
py -3.12 -m ruff check src tests benchmarks
py -3.12 -m pytest -q
```

## 文档

核心文档在 `doc/`：

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
- `doc/memory-runtime-simplification-2026-09-02.md`
- `doc/memory-runtime-simplification-completion-2026-09-02.md`
