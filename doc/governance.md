# 记忆治理

Memory Governance 负责把“能存、能查”的记忆系统推进到“可异步生成、可治理、可审核、可隔离敏感值”的运行时形态。当前版本实现四块能力：异步派生队列、Retention Policy、Human Review 和 PII Vault。

## 异步派生队列

同步写入仍然保留：

```text
Event -> EventStore -> Derivation -> Lifecycle -> MemoryStore -> Snapshot
```

新增异步写入：

```text
Event -> EventStore -> DerivationQueue
Worker -> Derivation -> Lifecycle -> MemoryStore -> Snapshot
```

`runtime.ingest_async(event)` 只做三件事：

- 对事件做敏感最小化。
- 把最小化后的事件写入 `EventStore`。
- 为该事件创建 `DerivationJob`。

这样用户请求不用等待派生、归并、冲突检测和快照更新的完整链路。后台或 CLI 再调用 `runtime.run_derivation_once()` 消费一个 pending job。任务成功后写入 `governance_job` 审计；失败时只记录错误类型和错误 hash，不保存异常原文。

队列接口当前提供内存和 JSONL 实现：

- `InMemoryDerivationQueueStore`：单进程测试和嵌入式运行。
- `JsonlDerivationQueueStore`：CLI 本地调试，可重启恢复 pending job。
- `SQLiteDerivationQueueStore`：与 `SQLiteStoreBundle` 共用数据库，适合单机/内网 MVP。

## 保留策略

Retention Policy 当前按事件序列年龄治理，不依赖真实时间：

- 低显著性的旧 working memory 可归档为 `archival`。
- 过期 sensitive memory 可从 `MemoryStore` 删除。
- 每次执行生成 `retention` 审计，记录归档和删除的 memory id，不记录正文。

接口：

```python
policy = RetentionPolicy(
    archive_working_after_sequences=30,
    archive_below_salience=0.2,
    delete_sensitive_after_sequences=10,
)
plan = RetentionPlanner(policy).plan(records, current_sequence=snapshot.last_event_sequence)
report = RetentionExecutor(memory_store=store, audit_store=audit).apply(plan, snapshot=snapshot)
```

## 人工审核

`ReviewGuard` 在候选记忆写入前执行风险评分。高风险候选不会直接进入 `MemoryStore`，而是进入 `InMemoryReviewQueue` 等待人工确认：

```text
MemoryCandidate -> RiskAssessment -> ReviewQueue -> approve/reject
```

默认风险因素包括：

- `sensitive` label。
- `global` scope。
- shared sensitive memory。
- `health`、`medical`、`credential`、`payment`、`legal` 等高风险 tag。

审核队列接收的是已经经过事件最小化后的候选记忆。也就是说，如果源事件被标记为 sensitive，候选内容和非路由字段会先变成 `[redacted]`，审核系统不会绕过写入前脱敏。

审核入口：

```python
review_queue = InMemoryReviewQueue()
runtime = AgentMemoryRuntime(
    review_guard=ReviewGuard(review_queue=review_queue, risk_threshold=0.7)
)
runtime.ingest(event)
item = review_queue.pending_items()[0]
runtime.approve_review_item(item.review_id, reviewer_id="operator")
```

## PII Vault

`PiiProtector` 提供显式的敏感值令牌化工具：

```text
payload: 我的邮箱是 a@example.com
vault: PII_000001 -> a@example.com
payload: 我的邮箱是 ${PII_000001}
```

`SimpleEncryptedPiiVault` 是本地测试和演示用的可逆加密 Vault，使用进程内存保存密文，并按 `owner_id` 做解析授权。生产环境应替换为 KMS/HSM 或独立密钥服务，不应直接使用该演示实现承载真实敏感数据。

PII Vault 与现有 `sanitize_event` 是两条边界：

- `PiiProtector` 用于应用接入层主动令牌化需要可逆找回的敏感值。
- `sanitize_event` 是 runtime 的强制最小化兜底，防止原文进入 `EventStore`、`MemoryStore` 和审计。

## CLI

```powershell
amem ingest examples/data/customer_support_events.jsonl --async-derive
amem queue
amem queue run-once
amem worker
amem retention plan --archive-after-seq 30 --archive-below-salience 0.2
amem retention apply --delete-sensitive-after-seq 10
```

`amem ingest` 默认仍然同步派生。只有显式加 `--async-derive` 时，事件才会先进队列、后派生。
`amem worker` 默认会处理到队列为空；使用 `--forever` 时会常驻轮询，适合作为开发期后台 worker。

## 审计类型

治理模块新增两类审计：

- `governance_job`：异步派生任务成功、失败、重试、死信。
- `retention`：归档和删除计划的执行结果。

人工审核使用 `human_review` 审计，记录 queued/approved/rejected 决策、风险分和原因，不保存候选正文。
