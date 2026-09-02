# 模块职责

| 模块 | 职责 |
| --- | --- |
| `agent` | 执行 Agent 请求、模型调用、工具循环、checkpoint 和对话压缩 |
| `tools` | 注册并执行显式工具，包括记忆保存、修订、删除和搜索 |
| `memory.intake` | 从工具输入和 Auto Dream 输出构建 `MemoryProposal` |
| `memory.write_policy` | 执行 schema、访问、版本和风险校验 |
| `memory.service` | 将通过校验的 proposal 应用到 `MemoryRecord`、tombstone 和 audit log |
| `memory.intake.dream` | 为重复、冲突、派生和状态维护生成 proposal |
| `memory.intake.worker` | 调度、租约、执行、重试和 checkpoint Auto Dream job |
| `memory.retrieval` | 召回 FTS5/Qdrant 候选、RRF 融合、rerank、过滤和预算选择 |
| `memory.embeddings` | 管理 embedding provider、generation、outbox job、worker、SQLite vector 和 Qdrant |
| `memory.stores` | 提供 SQLite、JSONL、in-memory 存储实现 |
| `audit` | 记录 audit envelope、LLM trace、memory write log 和审计重放输入 |
| `access` | 执行 principal-based 访问控制和敏感载荷清理 |
| `context` | 构建模型可见记忆上下文、结构化投影和个性化片段 |
| `llm` | 归一化 chat provider 请求、响应、streaming event、usage 和错误 |
| `config` | 定义 runtime、retrieval、rerank、worker、LLM 和 token budget 配置 |

## 数据契约

- `MemoryQuery`：带身份和检索约束的读取请求。
- `MemoryProposal`：带 action、target、identity、evidence 和 version 的写入请求。
- `MemoryRecord`：持久记忆状态。
- `MemoryAuditLog`：持久写入历史和重放输入。
- `MemoryTombstone`：持久删除水位。
