# v0.2.0 生产加固实施记录

## 第一批：事件与异步派生可靠性

- `event_id` 是强幂等键。同一 ID 的完整规范化事件（不含存储分配的 `sequence`）必须一致；不一致时抛出 `EventConflictError`，禁止静默覆盖。
- 内存、JSONL、SQLite event store 的 `append` 都必须返回已存在的相同事件，使调用方重试不会追加第二条事实。
- worker job 使用有时限的 lease；每次 claim 都生成独立 `lease_token`。完成、失败和续租必须同时匹配 owner、token，且 lease 必须仍在有效期内。
- `attempts` 在 claim 时递增，因此进程在业务处理期间崩溃也会消耗尝试次数，不会形成无限崩溃循环。
- retry 使用指数退避并受上限约束；达到 `max_attempts` 后进入 `dead_letter`。DLQ 只能显式 redrive，重复 enqueue 不会复活失败任务。
- JSONL 只保证单进程线程安全；生产多 worker 场景使用 SQLite store。

实现状态：内存和 JSONL event store 已加入冲突检测、幂等返回与进程内锁；JSONL 追加在返回前执行 flush/fsync。

job 载荷现已包含 `available_at`、lease owner/expiry、claim/error 时间和 redrive 计数。旧载荷读取时会自动补默认值。

内存队列已实现原子 claim、过期 lease 回收、owner 校验的 complete/fail/renew，以及仅允许 DLQ 项的显式 redrive。

JSONL 队列复用相同状态机，并以临时文件 + 原子替换持久化每次状态变化，避免崩溃留下半行 JSON。

SQLite 队列在 `BEGIN IMMEDIATE` 写事务内回收过期 lease 并 claim，多个 worker 不会领取同一任务；所有 ack/retry/renew 都再次读取并校验 owner。

Runtime 在事务内先检查事件幂等键；相同重试直接返回既有事件和派生记录，不重复快照或审计。异步派生采用 at-least-once：memory/snapshot 先原子提交，再 ack queue。若进程在两者之间崩溃，lease 到期后重放；reducer 以 source event 去重。不能把独立 SQLite manager 的 queue ack 嵌入状态写事务，否则会自锁。

SQLite event append 在写锁内再次校验冲突，覆盖“两个进程同时首次写入相同 event_id”的竞态。

首批回归覆盖：同步 ingest 重试、event_id 冲突、lease 过期转移、旧 owner 拒绝 ack、崩溃耗尽进入 DLQ 与显式 redrive。

后续批次将在本文档继续记录 SQLite migration/backup/shadow replay、身份模型、检索插件、治理持久化和发布验证。

## 第二批：SQLite 数据安全

- schema 由带 checksum 的顺序 migration 管理，当前版本为 2；历史 migration 被修改会拒绝启动。
- 写操作使用 `BEGIN IMMEDIATE` 和 busy timeout；嵌套操作一旦失败会把外层事务标记为 rollback-only。
- 新增只读事务入口，普通查询不再获取 reserved write lock。
- 在线备份使用 SQLite Backup API 写入同目录临时文件，完整性检查通过后再原子替换目标，并返回页数和 schema 版本。

event/memory/snapshot/audit/queue 的查询路径均已切换为 deferred read transaction；WAL 模式下读者不再与写者争抢 reserved lock。

`SQLiteStoreBundle.shadow_replay()` 会把线上 event log 读入完全隔离的内存 stores，重建后比较 rule/config/sequence/state hash；不会调用原有 `replay()`，因此绝不清空线上 memory。

## 第三批：身份边界

Event、MemoryCandidate、MemoryRecord、MemoryQuery 与 Principal 现在分别携带 `tenant_id`、`user_id`、`agent_id`。默认 tenant 为 `default` 以读取旧数据，旧 `owner_id` 只作为 agent owner 的兼容回退。检索先硬隔离 tenant，再由访问策略分别校验 user 与 agent。

内置派生规则会把事件身份完整传入候选和记录。默认租户继续使用历史 memory ID，避免破坏现有单租户数据；非默认租户使用 `v2:<kind>:<tenant>:...` 命名空间，并对动态片段进行 URL 编码，防止跨租户主键碰撞。写入前还会再次校验候选身份与 source event 一致，自定义派生规则不能伪造 tenant/user/agent。

公开 dataclass 的身份字段追加在原有参数之后，保留 v0.1 的位置参数顺序。`MemoryRecord.from_dict()` 只在旧载荷完全缺少 `agent_id` 字段时回退到 `owner_id`；显式 `agent_id: null` 可以稳定往返，避免 SQLite 读取和内存重放产生不同 state hash。

## 第四批：阻断修复与回归验证

- 包元数据版本已从 `0.1.0` 升至 `0.2.0`，与本次兼容性边界和实施记录一致。
- dict 事件使用固定 `event_id` 重试但未携带 `occurred_at` 时，会在同一写事务内复用既有事件时间；不会因为服务端再次生成时间而误报冲突。
- complete/fail/renew 遇到已过期 lease 时会先把任务恢复为 pending 或 dead letter，再拒绝旧 owner 的操作；不再存在“尚未被其他 worker claim，所以旧 owner 仍可 ack”的窗口。
- claim 使用随机 fencing token；即使同一 `worker_id` 在旧执行尚未退出时重新领取任务，旧 token 也不能确认新 lease。runtime 会按 lease 的三分之一间隔自动 heartbeat，长时间自定义派生不再依赖人为放大租期。
- SQLite queue 的 enqueue 已改为单个 `BEGIN IMMEDIATE` 事务内查重并插入；不同 store 实例并发写入同一 `event_id` 时返回同一个 job，不抛唯一键竞态错误。
- SQLite store 已删除旧 transaction manager 死代码，只保留带 migration、rollback-only、read transaction 和 backup 能力的新实现。
- shadow replay 的记录身份序列化现已稳定，SQLite 备份后隔离重放可以通过 state hash 比对。
- 新增回归覆盖：无时间戳 dict 重试、双租户同业务键隔离、身份新旧载荷往返、过期 complete/fail/renew、同 worker 旧 token fencing、runtime 长任务 heartbeat、JSONL 过期恢复持久化、SQLite 跨实例原子 enqueue、SQLite 过期 ack、自定义规则跨租户写入回滚。

验证环境固定为 Python 3.12（项目声明 Python >= 3.11）。发布前必须执行：

```powershell
py -3.12 -m pytest -q -p no:cacheprovider
py -3.12 -m ruff check .
git diff --check
```

2026-07-21 本地发布验证结果：Python 3.12 下 `67 passed`，Ruff 全通过，`git diff --check` 无错误；隔离构建成功生成 `agent_memory_runtime-0.2.0-py3-none-any.whl`，wheel 包含 `memory/stores/sqlite_manager.py`，解包后包导入与 schema version 读取成功。

当前边界：JSONL store 只用于单进程低并发场景，多进程或多 worker 必须使用 SQLite。heartbeat 能保护长时间计算，但如果状态写事务长时间独占与 queue 相同的 SQLite 数据库，续租也会等待该写锁；因此派生规则仍不应在 SQLite 写事务内执行无界外部 I/O。
