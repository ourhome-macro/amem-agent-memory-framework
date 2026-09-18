# 五步结构收敛与验证

## 范围

在前一轮 LangGraph、Alembic、OpenTelemetry 迁移基础上继续重构，保留现有业务接口、数据库升级路径、审批、工具副作用账本与 Outbox。没有替换 Web 框架、ORM，也没有新增工作流框架。

## 1. 删除重复实现

- 删除未被源码引用的 `frontend/src/audio/WsClient.ts` 及 `socket.io-client` 直接依赖，更新 lockfile；音乐对话继续使用原生 SSE。
- 字幕二分定位统一到 `utils/subtitles.ts`，页面和播放器 Store 共用。
- 删除 Flask 启动阶段被覆盖的第一次 RecommendationService 构建。
- 清理抽取后失效的导入；保留旧队列排空工具和历史数据库迁移。

## 2. 统一依赖和资源所有权

- `service_factory.MusicServices` 是 API 与 Worker 共用的音乐服务组装入口。
- 工厂惰性创建服务，明确区分借用和持有的依赖；ExitStack 按画像刷新器、gRPC 通道、HTTP 客户端、账号会话顺序释放相关资源，异常路径同样释放。
- `agent_memory_runtime.llm.transport` 统一 OpenAI 兼容客户端创建、HTTPS/密钥校验和进程级连接复用；凭据、地址、超时不同的客户端隔离，fork 后不复用父进程池。
- 业务提示词、返回值解析、模型 Gateway 契约保持各自职责。

## 3. 拆分业务职责

- 对话：`dialogue_rules` 管理规则及表示转换，`dialogue_repository` 管理用户范围内的会话/卡片/撤销持久化，`dialogue_service` 保留流程协调。事务边界仍由业务调用方控制。
- 推荐：`recommendation_contracts` 管理数据契约及纯辅助函数，`recommendation_policy` 管理评分、过滤、探索与多样性，`user_profile_reader` 负责画像数据读取。
- RecommendationEngine 持有策略对象；Service 不再把算法回调传回引擎。黄金集保留原有 MMR/多样性隔离测试语义，改为显式策略注入，预期结果和门槛不变。
- 前端：`useDialogueTasks` 管理 SSE、恢复游标、任务超时和完成竞态，组件保留展示与交互。

## 4. 业务步骤持久化与恢复

Discovery 分为 `prepare → search → admit → embed` 四个有名称的工具步骤，使用现有 BusinessAgentRuntime 的 LangGraph 控制流，而不是再实现一套 scheduler/checkpointer。

- 完整步骤输出持久化在工具账本；下游读取完整结果，不依赖被模型上下文截断的工具消息。
- 控制流只传递前一步 call ID 和输入摘要，不把整个候选池重复放进工具参数。真实数据验证发现大结果触发预算保护，修复为引用传递，并补充 20 万字符结果的跨进程恢复测试。
- 修复通用工具输出压缩仅限制行数、不限制单行长度的问题：JSON 大字段的模型预览现在受 token 上限约束；完整原始结果仍保留在账本。
- 步骤具有稳定 call ID，已成功的步骤不重做。进程在已提交步骤后退出，可在 lease 到期后恢复。
- 搜索声明为只读；向量写入使用已有内容键去重；关键词准备、候选入池仍按非幂等副作用管理。
- 新 Discovery 作业允许队列重新投递以恢复图；这不等于允许重放副作用。未知结果映射为 `needs_reconciliation`，永久失败映射为 `failed`，不能用普通重试绕过。
- 原有单操作的工具名称和 call ID 保持兼容。部署前应排空旧版本未完成的 Discovery 作业；不能把旧整段工具执行结果自动解释成新四步结果。
- 对话和推荐入口继续通过通用 Agent 执行，但不是每个内部函数都成为图节点；没有可独立恢复语义的纯计算，不增加节点。

## 5. 收敛追踪写入

- FullTrace 的评测 span/event 在作用域结束或达到 128 条时批量写入；异常退出也刷新，Agent observer 按一次事件处理合并写入。
- `measure_step` 用一个计时作用域关联 OTel span 与评测记录，Discovery 向量步骤首先采用该接口。
- durable_jobs 与 agent_run 的关联从“每个事件 UPDATE”改成一次关联。
- Agent observer 恢复已有评测 trace，不再用 INSERT OR REPLACE 重置它的起始时间；sequence 同时考虑事件与 span。
- 仅评测投影允许缓冲；业务审计、工具账本、消息 Outbox 不缓冲、不删减。
- 硬进程退出可能损失最后一批尚未刷新的评测投影，权威业务/执行账本不受影响；OTel 仍是跨进程运行追踪。

## 验证

新增回归覆盖：进程强制退出后的步骤恢复、完整大结果传递、完成请求重放、未知副作用阻止后续步骤、作业核对状态、工厂关闭顺序及借用资源保护、模型连接复用及凭据隔离、批量追踪异常刷新与脱敏、会话撤销及跨用户读取隔离。

本次端到端使用本机数据的独立备份与 Linux named volume；不改写源账号数据库，不操作现有业务容器。依据 Docker Compose 编排技能采用独立测试项目、健康检查和保留证据的停止方式。修复重复运行时 RabbitMQ 复用旧匿名卷导致密码不匹配的问题：数据库、消息队列与 SSE 卷均按测试批次命名。

### 代码组织变化

| 文件 | 重构前行数 | 重构后行数 |
| --- | ---: | ---: |
| dialogue_service.py | 3325 | 1306 |
| recommendation_service.py | 2182 | 1252 |
| discovery_service.py | 636 | 431 |
| AgentDialogueView.vue | 1451 | 1356 |
| NowPlayingView.vue | 1829 | 1813 |

行数降低主要是职责迁移；不能把它当成全仓净删减。新增回归测试、步骤恢复及依赖所有权代码属于必要保障。

### 最终验证结果

- Linux Python 回归：**60 passed，29.33 秒**，包含隔离的真实 RabbitMQ/Celery prefork/gRPC 投递测试。
- 大结果/恢复/追踪相关本机回归：**21 passed**。
- 前端：`vue-tsc && vite build` 通过；Go：`go test ./...` 通过。
- 真实账号 API 端到端：通过；B 站登录有效，Discovery 的 prepare/search/admit/embed 四条执行账本均为 succeeded。
- 本轮显式 Discovery：44 条候选入池结果，43 条文本向量写入，0 条待处理文本向量；3 条推荐，读取 4096 字节真实音频，4 条 SSE 事件，反馈写入 AMEM 验证通过。
- OTel：5 个服务、140 个去重 span、0 个错误 span，四个步骤全部关联到同一条 trace；3 次成功模型调用。
- API trace：`e925f025aaca7bbc2011595e6eea1827`。
- 浏览器端到端：新建会话、发送消息、收到 SSE 回复通过，0 个 JavaScript 错误；trace：`4b99cce5daf85d697cfddb33905e6f21`。
- 私有结果、截图和原始 trace 保存在忽略目录 `.amem/framework-e2e-20260916-085607/`，不提交账号数据、Cookie、密钥或数据库。
- 暂存内容检查：87 个新增/修改文件未匹配本机敏感凭据；另有 1 个旧 WebSocket 文件删除，历史可从 Git 恢复。

当前同口径源文件总量为 71436 行，相比审查时 70773 行略增，包含新增恢复保护和测试。大文件缩短不是全仓净删减，不以行数作为唯一优化指标。

测试结束后仅停止本轮隔离测试项目及单测 broker，保留数据卷和证据；原运行中的业务容器不做升级或重启。此次端到端不代表生产 OIDC/TLS、高并发容量或真实故障切换已经验收。
