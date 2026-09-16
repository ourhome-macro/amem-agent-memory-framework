# Agent loop 与基础设施框架评审

日期：2026-09-15。对象：当前工作区，包括未提交的 RabbitMQ 与 trace 改动。
方式：源码审查、两个非业务写入的复现检查、官方框架文档核对。本轮只新增评审文档，未安装框架或修改业务实现。

## 结论

核心 Agent 已有完整度较高的执行骨架，但生产可靠性仍有缺口；音乐应用又另外实现了一套进程内任务执行机制。优先统一后台任务与事件可靠投递，随后逐步收缩通用 Agent 编排自研范围。

当前推荐顺序：修复已确认缺陷 → jsonschema/Pydantic 边界校验 → Celery + RabbitMQ + 事务 outbox → OpenTelemetry/Alembic → LangGraph 单链路试点。

AMEM 的记忆权限、提案审核、版本一致性、领域审计，以及音乐推荐的硬约束、候选准入和排序应继续保留。它们是业务语义，任务框架不能代替。

## 1. Agent loop 现状

### 已有的有效设计

- `src/agent_memory_runtime/agent/runtime.py:467`：模型调用、待执行工具、checkpoint 和恢复组成了明确执行流程。
- `agent/policy.py:18`：步数、模型次数、工具次数、token、费用和时间预算有统一限制。
- `agent/runtime.py:353` 附近及 `agent/stores/sqlite.py:93`：运行领取、租约、续租和版本检查。
- `agent/tool_runtime.py:85`：已完成工具可复用结果；非幂等副作用在结果未知时进入人工核对状态，有审批和补偿边界。

这些能力值得保留其契约和测试。问题主要在可靠执行细节，以及它们是否真正被应用入口使用。

### 需要补强的部分

1. **校验器重复实现且不完整，已复现。** `agent/tool_runtime.py:311` 的手写 JSON Schema 校验没有处理 `pattern`、`maxItems` 等约束，也显式拒绝 `$ref`。本轮向它传入不匹配正则的字符串及超长数组，两者均被接受。与此同时，`agent/output.py:7` 已经使用 `Draft202012Validator`，依赖也已存在。应统一到标准校验器；引用解析限定为批准的本地 schema，format 校验显式配置。
2. **取消等待不等于停止同步工具。** `agent/tool_runtime.py:404` 将同步工具交给 `asyncio.to_thread`，外层使用 timeout。线程开始执行后，超时不会强制终止它；原调用可能仍在运行，重试却已经开始。需要工具原生超时、协作取消或有边界的进程隔离。非幂等工具必须保留结果核对，不能简单交给通用重试器。
3. **连续上下文压缩有信息遗失风险。** `agent/context_window.py:204` 会过滤旧生成的 summary/pinned/task-state，而新摘要主要从剩余消息重新构建。按代码路径，第二次压缩可能丢失只存在于上次摘要的约束。需做连续多轮压缩回归，把任务事实作为持久状态，并验证历史摘要的递归合并。框架 checkpoint 不能自动解决摘要质量。
4. **通用编排维护范围已经很大。** `agent/orchestration/runtime.py` 同时维护依赖调度、并发、恢复、取消、审批和租约，和成熟工作流框架重叠明显。继续扩张前应验证 LangGraph 适配是否能删除相应执行代码。

参考：[Python Future 取消语义](https://docs.python.org/3/library/concurrent.futures.html)、[jsonschema 校验接口](https://python-jsonschema.readthedocs.io/en/stable/validate/)。

## 2. 应用后台任务是最高优先级

`recommend-radio/backend/dialogue_task_service.py:69` 用线程池接收任务；`discovery_service.py:49` 写入 queued 状态后，仍通过 `_EXECUTOR.submit` 执行。当前这两个执行入口没有提供重启后的自动领取/恢复机制。`FullTrace.resume` 恢复的是追踪记录，不是任务调度。

风险：

- 进程退出会丢失内存中的任务，数据库可能留下 queued/running 状态。
- 固定线程数限制执行并发，但没有提供业务入队背压。
- `_EVOLUTION_LOCK` 仅在当前进程内生效；多 Gunicorn worker 不共享这个锁。
- 音乐应用主链路没有实际调用 `BusinessAgentRuntime`/`AgentOrchestrator`；新增 observer 的接入只在测试中出现。因此核心运行时已有的恢复语义不能算作应用的恢复保障。

### 推荐 Celery + 现有 RabbitMQ

先迁移 Discovery，再迁移对话任务、embedding 等应用后台执行。消息只传任务 ID 与必要的身份/版本字段；worker 从持久存储恢复输入，重新创建服务和身份上下文，不序列化 Flask 请求上下文或整个 service 对象。

业务数据库保存任务状态及结果。Celery 管投递、执行并发、重试和调度；不要把 Celery result backend 当业务事实源。

采用 late ack 前先保证幂等，明确 worker 丢失后的重投设置；按长短任务分队列，限制 prefetch 和入队量。重复投递必须由稳定业务 ID 和落库约束处理。进程锁应改为数据库原子领取或共享锁。

Celery 原生 Windows worker 不在其支持范围内，本项目已有 Docker，建议运行 Linux worker 容器，PowerShell 只作为启动入口。[RabbitMQ 集成](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/rabbitmq.html)、[任务确认与幂等](https://docs.celeryq.dev/en/stable/userguide/tasks.html)、[Windows 支持说明](https://docs.celeryq.dev/en/stable/faq.html)。

若长期只保留少量独立后台函数，Dramatiq 是更轻量的候选；这里倾向 Celery，是因为应用已有多种后台任务、状态与后续周期调度需求。两者选一个即可。[Dramatiq guide](https://dramatiq.io/guide.html)。

## 3. RabbitMQ 改动仍未达到生产闭环

### 数据库与消息不是原子提交

`app.py:856` 先调用 playback service 完成数据库写入，再调用行为发布。在两步之间退出，业务状态已经提交但消息不存在。publisher confirm 只能确认到达 broker 的消息，无法覆盖这个窗口。

应把业务变更与 outbox 事件放进同一数据库事务，dispatcher 读取 outbox 并发布，确认后再标记。dispatcher 若在确认后、标记前退出会产生重复消息，因此消费者仍需幂等。Celery 同样不能自动提供数据库到 RabbitMQ 的跨系统原子性。

当前 UUID 在一次 publish 的重试中稳定，但 HTTP 请求重试可能再次产生新 ID。需要请求/业务事件层面的稳定去重键，而不只依赖 AMQP message_id。

### 消费线程承担业务处理和心跳

`behavior_worker.py:60` 在 Pika 回调内同步调用 gRPC，失败后 `time.sleep` 最长 30 秒；连接 heartbeat 配置也是 30 秒。阻塞 I/O 线程存在心跳中断风险，prefetch=16 也不等于 16 路实际消费并发。

`rabbitmq_bus.py` 的生产连接按线程缓存，但空闲线程没有常驻事件泵；长时间空闲的连接可能失效，首次发布再重连。worker 的 TCP healthcheck 也不能证明它仍在订阅、消费或成功写入 AMEM。

应交给成熟 worker 管理连接及业务执行，或把原始 AMQP 消费与业务执行线程严格分开；重试应使用延迟调度。健康检查应验证 worker 活性，观测消费停滞和队列积压。[Pika 心跳与阻塞说明](https://pika.readthedocs.io/en/stable/examples/heartbeat_and_blocked_timeouts.html)。

现有原始 JSON 消息不能直接被 Celery task worker 当成 Celery 协议消费。迁移要使用独立版本队列，先上线消费者，再切生产者，最后排空旧队列。

## 4. LangGraph 的引入边界

长期建议：AMEM 提供领域记忆能力，LangGraph 承担通用 Agent 状态流转和 checkpoint。先选择一条多步骤对话链路试点，在现有接口外包一层 adapter，验证后再替换自研 orchestration。

LangGraph 支持 checkpoint、恢复和中断，也提供适配普通函数的 Functional API。恢复执行仍要求副作用可幂等重试，不能宣称天然 exactly-once。[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)。

必须保留的契约：

- 租户和用户隔离，工具授权、审批、撤销与副作用核对。
- 稳定 run/task/tool-call ID 和输入版本。
- token/费用预算及取消行为。
- 记忆提案写入策略与审计。

同一运行的调度/checkpoint 只由一套执行引擎负责。Celery 可以负责触发或唤醒运行，LangGraph 负责运行内部步骤；不允许 Celery 整任务重试、自研工具重试、图节点重试同时无限叠加。

若核心需求升级为跨服务、长时间等待审批、必须自动持续恢复的业务工作流，再评估 Temporal 作为外层持久执行引擎。它会增加独立服务和部署成本，目前不建议与 Celery、LangGraph 一起全面引入。[Temporal 文档](https://docs.temporal.io/)。

## 5. 其他值得复用的基础能力

| 位置 | 建议 | 预期收益与边界 |
| --- | --- | --- |
| 工具 schema 校验 | 立即复用现有 jsonschema | 修复实际漏校验，不增加新依赖 |
| HTTP 输入、模型结构化输出、环境变量 | Pydantic + pydantic-settings | 集中字段校验；关键配置非法时明确失败，不默默回退 |
| SQL schema 演进 | Alembic + 必要的 SQLAlchemy 连接层 | 当前已有 24 个 user_version 阶段；先建立旧库基线，迁移做独立部署步骤；无需重写全部 SQL 为 ORM |
| 运行追踪 | OpenTelemetry | 统一 HTTP、gRPC、任务队列的上下文传播；保留业务审计和推荐评估表 |
| 模型调用 | 暂时保留当前 gateway 接口 | 没有证据表明现在需要再加多供应商代理；先统一 timeout、错误分类与总重试预算 |
| Web、登录 | 保留 Flask、Authlib | 迁到 FastAPI 不会解决任务恢复；已有成熟认证库应继续复用 |
| 检索与推荐 | 保留 AMEM + 现有索引适配器 | 权限、记忆写入、硬约束、候选准入是领域核心；不为统一框架再包一层通用链 |
| 启动与部署 | Docker Compose 为唯一服务定义 | PowerShell 做前置检查和参数转发，避免维护两套构建/镜像规则 |

参考：[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)、[Alembic SQLite batch migration](https://alembic.sqlalchemy.org/en/latest/batch.html)、[OpenTelemetry Python 传播](https://opentelemetry.io/docs/languages/python/propagation/)。

## 6. 上一轮启动交付中的确认问题

`recommend-radio/start.ps1:9` 把 `-Rebuild` 放入字符串数组并 splat 给 PowerShell 脚本。对同签名高级函数的复现报错：`A positional parameter cannot be found that accepts argument '-Rebuild'.` 应使用命名参数/hashtable splatting。

`scripts/start-local.ps1:122` 手动构建固定的 `recommend-radio-frontend:latest`，而 Compose 中 frontend 没有显式 image，依赖 Compose 项目名生成镜像名；项目名变化时可能不一致。脚本还依赖宿主机 npm 环境、已有依赖和 embedding Python 环境。应让 Compose 管理构建，再补环境预检与实际启动验证。

上一轮 23 个测试通过只能说明当时所跑用例通过，不能代表重启恢复、故障注入或一键启动已经验证。本轮没有运行真实 Docker 消息闭环，也没有验证部署现场是否存在旧 RocketMQ backlog。

## 7. 实施优先级与验收

1. 修复入口参数、schema 漏校验、连续压缩；明确同步工具超时语义。
2. 对行为消息补事务 outbox、稳定事件键和消费者幂等。故障注入覆盖事务提交后、publish 确认后和 ack 前退出。
3. 用 Celery 替换 Discovery/对话的进程内任务投递。覆盖任务入队后重启、worker 被杀、重复投递和跨用户并发。
4. 补数据库迁移基线、跨进程 trace、队列积压与死信观测。
5. LangGraph 单链路试点。验证工具调用后崩溃恢复、审批恢复、取消、预算以及记忆权限；通过后删除被替代的执行逻辑。

框架引入的验收标准是减少自研执行代码、统一恢复语义并通过故障测试，而不是依赖列表里多了几个包。
