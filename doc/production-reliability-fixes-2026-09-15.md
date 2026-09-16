# 执行循环与后台任务可靠性修复

本次针对 `loop-framework-review-2026-09-15.md` 中的确认缺陷实施修复。
实施与验证跨越 2026-09-15 / 2026-09-16。

## 核心 Agent

- 工具参数统一使用 Draft 2020-12 JSON Schema 校验，覆盖正则、数组长度、组合约束和格式。只允许本地 schema 引用，禁止网络引用解析。格式检查器缺失时明确失败。
- 同步工具超时/取消后，若线程尚未结束，持久化 `RECONCILIATION_REQUIRED`，阻止自动重叠重试。工具可观察 `context.stop_requested` 配合停止；Python 无法强杀任意线程，因此不承诺超时即终止副作用。
- checkpoint 新增持久化摘要和原始 pinned messages，压缩时携带旧摘要，保留初始用户任务及工具消息组。约束不再因为软预算不足被静默丢弃；超出硬预算由原有预算检查拒绝模型调用。

## 应用后台执行

```text
业务事务 / 对话或 Discovery 入队
  → durable_jobs（业务事实与待发任务同事务提交）
  → outbox dispatcher（确认发布）
  → RabbitMQ quorum queues
  → Celery Linux workers
  → AMEM gRPC / Discovery / Dialogue / SSE
```

新增 Celery 5.6.3，Pydantic / pydantic-settings 负责 MQ 配置校验。Celery 负责消息消费、进程管理、心跳和执行时间限制；数据库记录任务输入、幂等键、状态、重试时间和结果。

- `radio.jobs.v2`：对话、Discovery、关键词演进、Discovery 结果刷新。
- `radio.events.v2`：行为事件和 SSE 事件，独立 worker 避免长任务阻塞事件处理。
- Web 进程不再用 ThreadPoolExecutor 承担对话和 Discovery 的后台投递。
- broker 消息仅携带 job ID；用户身份和输入从数据库读取，不序列化 Flask 请求、登录 cookie、service 对象或任意文件路径。
- 每用户待处理数量有上限，超限会使入队事务失败。同一会话/用户级后台通道通过数据库领取串行执行，使用递增 ID 保证同时间戳下仍有稳定次序。
- worker 领取有租约和令牌。过期持有者不能覆盖新持有者的结果。
- 行为/SSE 等可重试任务持久化指数退避，最多 8 次。重复 broker 消息不能绕过退避时间。
- 对话/Discovery/关键词演进可能包含部分业务副作用：执行中断后进入 `needs_reconciliation`，不自动整任务重放。待执行任务可重启后继续领取；已开始的非幂等任务必须先核对结果。这一边界避免重复对话、重复学习和重复写入。

## 行为事件的事务边界

- 点赞、取消点赞、评价和播放事件在业务 service 的事务内创建 outbox。
- 推荐反馈和对话信号在原数据事务内创建 outbox。
- 其他直接行为写入也经持久 outbox 发布。
- 点赞重复请求不产生新的状态变化事件；相同评价内容不重复发事件。
- 播放支持 `Idempotency-Key` 或 payload event ID；无 ID 时按播放状态生成稳定键。相同事件的 broker 重投不会重复写入 AMEM；不同输入复用同一键会报错。
- outbox 在发布确认前退出会继续重发；确认后、状态领取前退出可能重复投递，由数据库领取和 AMEM 事件 ID 去重。

原来的 Pika 线程缓存发布器已删除。v1 原始 JSON 消费器变为可选的入库桥接服务，只把旧消息可靠转入 v2 outbox；其回调不再同步执行 gRPC 或长时间 sleep。

## 启动与升级

```powershell
cd recommend-radio
.\start.ps1
# 已构建后启动
.\start.ps1 -NoBuild
```

入口采用命名参数 splatting，所有应用镜像由 Compose 构建；不再要求宿主机 npm 构建前端，不再手工维护 Compose 外的前端镜像标签。

脚本读取 Compose 的有效端口配置，预检 Docker 和本地 embedding 依赖。使用外部 embedding 地址时不启动宿主机 embedding。默认启动不移除其他 profile 的容器。

一次性 `migrate` 服务先升级数据库到 version 25，再启动 backend、Celery workers 与 outbox。服务模式遇到旧 schema 会明确拒绝启动；已升级的 worker 不重复运行 schema 变更。旧版本未持久化完整输入的 abandoned Discovery 会标记为失败，避免永久停留在 queued/running。

升级前备份 `server-data`。不要用删除数据卷的方法解决 schema 或队列参数不匹配。

v1 队列有积压时，先启动桥接器，并确认其积压清空：

```powershell
docker compose --profile legacy-drain up -d --build behavior-worker
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged
```

该命令处理原始 AMQP v1 消息，不能转换 RocketMQ 存储文件。没有检查线上历史消息数量，部署时需要实际核对。

## 运维与核对

```powershell
docker compose exec backend python job_admin.py status
docker compose exec backend python job_admin.py resolve <job-id> --decision failed --reason "已核对部分写入，终止此任务"
docker compose logs --tail=100 outbox task-worker event-worker
```

只有 `needs_reconciliation` 可以被 resolve，决定、理由、操作者和时间会落审计表。completed/failed 的决定以实际副作用核对结果为准，CLI 不提供盲目重放。

用户可查询 `GET /api/agent/dialogue/tasks/<task-id>`，只返回自己任务的状态和结果。提交对话可带 `Idempotency-Key`；重试会复用任务，同键不同输入报错。

worker 健康检查使用 Celery 控制通道，outbox 与旧消息桥接器使用活动时间戳。检查不只探测 TCP 端口。SSE 发送失败会保留待发任务并重试，允许重复进度事件。

RabbitMQ 4.3 默认拒绝 transient non-exclusive queues，真实联调确认 Celery 控制队列默认声明会触发重启。已设置 `control_queue_exclusive=True` 和 `event_queue_exclusive=True`；业务任务仍是持久 quorum queue，没有重新启用 broker 的弃用功能。

## 验证与后续边界

新增故障回归覆盖 schema 漏校验、连续压缩、同步工具超时、事务回滚、确认发布后故障、并发领取、过期租约、跨用户隔离、重试退避和原始队列桥接。

真实协议测试使用独立 RabbitMQ 与临时数据库，连接真实 Celery 和 AMEM gRPC。`backend/Dockerfile.test` 用于 Linux 环境回归，根 `.dockerignore` 排除密钥和业务数据。

### 验证记录

- Linux / Python 3.12 独立容器回归：42 passed，10.26 秒。集成用例启动独立 Celery prefork worker，经过真实 RabbitMQ 和真实 AMEM gRPC，确认重复投递只产生一条 AMEM 事件。
- 最后主机名校验修正：4 项针对性配置检查通过，覆盖合法 DNS 名、空白主机名和非法端口。
- 默认启动参数与 `-NoBuild -NoFrontend` 参数组合实际通过 `start.ps1 -ValidateOnly`，不是仅做语法解析。
- 本地 Compose 与基础/监控/生产三文件组合配置解析通过。
- 新增执行/消息模块通过 Ruff；core 修改通过未定义变量/未使用符号检查。仓库其他文件已有的 lint 问题没有批量改写。
- 没有启动业务全栈，也没有调用真实推荐模型、B 站账号或写入用户的业务数据库；本次端到端范围是后台消息与 AMEM 入库链路，以及隔离数据库下的 HTTP readiness。

重跑 Linux 回归需要独立测试 RabbitMQ。在隔离网络上设置 `RABBITMQ_INTEGRATION=1` 才启用真实协议用例；普通单测默认跳过它。测试镜像使用 `backend/Dockerfile.test` 构建。

本次保留现有 Agent 执行接口以及历史 SQL 迁移；没有把内核替换为 LangGraph，也没有重写已有迁移历史为 Alembic。它们在评审中属于后续架构替换，需要单独验证审批、预算和旧库契约。OpenTelemetry 跨语言导出也未在本次引入；现有业务 trace 与审计继续保留。
