# Auto Dream 后台化与 Qdrant Runtime 接入

## 当前落地

- Auto Dream 不再只是同步 `Analyzer`：新增 `SQLiteDreamStore` 持久化 `dream_jobs`、`dream_checkpoints` 和 `memory_proposal_reviews`。
- `AgentMemoryRuntime.on_session_end(...)` 会把会话结束转换为 Auto Dream job。
- `AgentMemoryRuntime.schedule_auto_dream(...)` 可显式调度租户/用户/Agent/会话范围内的整理任务。
- `AutoDreamWorker` 支持 `run_once()`、`run_forever()` 和 `start_background()` 后台线程。
- Worker 自己从 runtime 的 audit event store 和当前 `MemoryRecord` 读取输入，不再要求调用方手动传 events/records。
- Worker 会把 Analyzer 输出的 proposal 交给 `MemoryService.apply_proposal()`，低风险 create/reinforce/revise/archive 自动落库；needs_review/rejected/conflict 会进入 `memory_proposal_reviews`。

## Analyzer 职责边界

- 支持 `create`、`reinforce`、`revise`、`supersede`、`archive`、`keep_both`、`needs_review`。
- 按 `tenant_id + user_id + agent_id + subject_id + memory_type + key` 做同域候选分组。
- 重复判断从“标准化文本完全相等”增强为 token/CJK bigram 的确定性模糊相似度。
- 明确修订语句且能找到目标记忆时输出 `revise`，并带 `expected_version`。
- 同 key 高置信差异默认 `needs_review`，不由后台直接覆盖旧记忆。
- 低置信同 key 差异输出 `keep_both`，交由 policy/review 保守处理。

## Qdrant 接入

- `SQLiteStoreBundle` 支持注入通用 `VectorIndex`，不再固定 SQLiteVectorIndex。
- CLI/runtime 加载环境时：
  - 设置 `AMEM_QDRANT_URL` 时默认使用 Qdrant。
  - 或显式设置 `AMEM_VECTOR_BACKEND=qdrant`。
  - 未配置 Qdrant 时保留 sqlite-vec，避免本地和 CI 依赖外部服务。
- SQLite 仍是真实状态和 embedding outbox；Qdrant 只是异步向量 projection。
- Qdrant projection 失败不影响 SQLite memory 写入；embedding job 会保留待重试。

## 主线保持

```text
save/revise/forget + Auto Dream
  -> MemoryProposal
  -> MemoryWritePolicy
  -> MemoryService
  -> MemoryRecord + MemoryAuditLog
  -> Embedding outbox
  -> Qdrant/sqlite-vec projection
```

Event 仍是审计输入和兼容记录，不回到派生主链路。规则引擎仍只保留为确定性 `MemoryValidator`、`AccessPolicy`、`RiskGuard/MemoryWritePolicy`。
