# LangGraph、Alembic、OpenTelemetry 迁移与端到端验证

## 执行架构

- `BusinessAgentRuntime` 使用 LangGraph `prepare → model → tool → finish` 节点及条件边驱动执行，原来的模型/工具双层 while 调度已移除。
- `AgentOrchestrator` 使用 LangGraph 调度依赖图、并行分支和汇合；保留 Agent 注册、权限、总预算及子任务审计契约。
- 审批/结果核对使用 LangGraph interrupt，恢复时结合原有审批和工具幂等账本。SQLite checkpointer 单独持久化控制流；业务 checkpoint 保留消息、预算和工具结果。
- 音乐对话、推荐和 Discovery 的公开执行入口统一通过 `music_operation` 调用同一个 `BusinessAgentRuntime`。操作路由是确定性的，不额外增加一个 LLM；真实模型调用仍位于业务操作内并有独立 span。
- Celery 负责跨进程投递，LangGraph 负责任务内部步骤；跨系统副作用不宣称 exactly-once。未知副作用仍要求核对。
- Celery 启动统一使用 `python -m celery`，避免控制台入口移除临时模块路径后导致 worker 的延迟导入失败。

## 数据迁移

- 业务库使用 Alembic `radio_001 → radio_002`：接管 v25 及更早数据库，新增 outbox trace context 和 agent run 关联。当前兼容 PRAGMA 版本为 26。
- AMEM SQLite 使用 Alembic `amem_001` 接管原 v1–v10 迁移，保留历史 SQL 校验和验证。后续 schema 修改新增 Alembic revision。
- 旧迁移逻辑作为冻结基线，正常 worker 只验证 head；一次性 migrate 服务负责升级，跨进程文件锁避免重复升级。
- 真实本机 v22 数据发现 30 条悬空会话外键。迁移依据 checkpoint/温记忆/信号中的一致用户归属，恢复 2 个 archived 会话父记录，保留历史并写 `migration_repairs`。归属不明确时拒绝自动修复。

## 全链路追踪

```text
浏览器 / HTTP 客户端
  → Flask API span
  → durable_jobs.trace_context
  → outbox producer span
  → Celery consumer span
  → LangGraph / music operation / model span
  → AMEM gRPC span / embedding HTTP span
  → SSE outbox → Go SSE span
```

- Python 使用 OpenTelemetry SDK 1.44.0；Flask、requests、gRPC 接入标准 instrumentation。
- Go SSE 接入 OTel Gin middleware 和 OTLP/HTTP exporter。
- 线程池搜索与后台画像刷新显式传播 ContextVar，避免并行 I/O 另起 trace。
- Collector 接收 OTLP 并写入有轮转限制的 JSON trace 文件；运行 span 不包含模型提示词、Cookie、API key 或请求正文，HTTP URL query 被移除。
- 业务 SQLite trace 保留用于评测；Agent run 与业务 trace 的父子关联已统一。数字 token 用量不再被误识别为敏感令牌。

## 本机数据端到端环境

用户授权使用本机账号数据。测试通过 SQLite online backup 复制本机数据库和账号加密 key：1 个应用用户、1 个 B 站账号、1,221 条曲目、11 条喜欢记录。

私有配置和备份仅放在忽略目录 `.amem/`。测试产生的会话和反馈写入独立 Linux Docker volume，不修改源业务数据库。数据库验证在容器内执行，避免 Windows 与 Linux 同时打开同一 WAL 文件。

```powershell
py -3.12 -m pip install -r recommend-radio/scripts/requirements-e2e.txt
py -3.12 recommend-radio/scripts/prepare_local_e2e.py
# 使用上一步输出的私有 env 文件路径
docker compose --env-file <private.env> -f recommend-radio/docker-compose.e2e.yml up -d --build --wait
py -3.12 recommend-radio/scripts/run_local_e2e.py --run-dir <run-directory>
# 浏览器验证使用 Playwright 和本机 Edge
py -3.12 recommend-radio/scripts/run_browser_e2e.py --run-dir <run-directory>
```

覆盖：真实 B 站登录验证、Vue 静态前端与代理、对话任务幂等提交、LangGraph/Celery 执行、真实推荐、音频流片段、SSE、反馈写入 AMEM、跨进程 trace 关联。

本次 trace 还定位并修复了会话创建的 30 秒停顿：序列化会话时，在未提交的 SQLite 写事务内调用画像分析，引起另一个连接等待写锁。现在对话响应的画像分析在业务事务提交后执行，并有专门的锁获取回归测试。

后台画像刷新在关闭 gRPC 通道之前排空运行中的 Future，避免正常任务收尾取消仍在执行的 RPC。候选文本 embedding 改为默认每批 8 条（`RECOMMEND_EMBEDDING_BATCH_SIZE`），避免大批量 CPU 推理超过单次请求超时；每批检查返回数量和索引顺序。

最终测试数量、业务结果、trace ID 和耗时另见本轮结果报告。生产 OIDC/TLS、监控告警通知与高并发容量不是本次真实账号端到端测试的验收范围。
