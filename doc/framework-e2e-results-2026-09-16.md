# 框架统一迁移：真实本机账号端到端结果

日期：2026-09-16。结果：通过。

## 测试对象与数据

- 原始数据：本机 `recommend-radio/server-data` 的 SQLite online backup。
- 数据规模：1 个应用用户、1 个真实 B 站账号、1,221 条曲目、11 条喜欢记录。
- B 站：实际登录态刷新验证通过，使用本机账号访问真实接口。
- 模型：本机配置的 DeepSeek `deepseek-chat`，最终追踪有 2 次成功调用、0 次模型调用错误。
- Embedding：本机 bge-m3 服务。
- 执行环境：真实 Vue 前端、Flask、RabbitMQ 4.3.5、Celery prefork worker、LangGraph、AMEM gRPC、Go SSE 和 OTel Collector。
- 业务写入仅发生在独立测试数据卷；没有改写原始业务数据库。配置、账号 key 和原始 trace 留在忽略目录 `.amem/`，不纳入提交。

## 验收结果

| 项目 | 结果 |
| --- | --- |
| Linux 回归与真实 MQ/gRPC 集成 | **53 passed，16.59 秒** |
| Go SSE exporter 路径回归 | `go test ./...` 通过 |
| Vue 生产构建 | `vue-tsc && vite build` 通过 |
| 浏览器交互 | 新建会话、发送消息、收到 SSE 回复、回复进入视口均通过；0 个 JavaScript 异常 |
| 真实 B 站账号 | 登录刷新验证通过 |
| 对话任务 | 经 Celery → LangGraph 执行完成，重复 Idempotency-Key 返回同一任务 |
| 显式 Discovery | 执行完成，真实搜索产生 58 条入队/准入候选；41 条文本向量生成成功，待处理 0 条 |
| 推荐接口 | 返回 3 条真实曲目 |
| 音频接口 | 获取流信息并读取 4,096 字节真实媒体片段 |
| SSE | 此次 API 场景捕获 7 条事件；浏览器另行验证了最终回复 |
| 反馈 | outbox 完成，AMEM 中对应事件恰好一条 |
| 全链路 trace | 同一 Trace ID 下关联 5 个服务、259 个 span，含 Discovery 与模型调用；错误 span 为 0 |
| 旧库升级 | 原 v22 数据升级至 Alembic `radio_002`；AMEM v10 接管至 `amem_001` |
| 旧库一致性 | 恢复 2 个有明确用户归属的 archived 会话父记录，保留历史；30 条悬空外键降至 0，修复留有审计 |

## 本次 trace 标识

- API/业务全链路：`2493e83a56badb011a77b61a2006f546`
- 浏览器交互：`4245d01fa2495c5538361de960ba66eb`
- 通用 Agent run：`dff84f2a-67f9-45c3-bd6d-ba487a596319`
- Discovery job：`discovery:05e1b66d20a14ac991bf846100049929`

| 服务 | 本次结果快照中的 span 数 |
| --- | ---: |
| recommend-radio-api | 123 |
| radio-worker | 88 |
| radio-outbox | 36 |
| amem-grpc | 5 |
| radio-sse | 7 |

外部 DeepSeek、B 站和 embedding 的调用通过本系统的客户端 span 观测；没有声称获得外部服务内部的 server span。

关联检查确认 259 个 span ID 均唯一；仅存在 1 个未导出的外部父 span，与测试客户端传入根 parent 的设计一致。

## 关键耗时

以下为本轮服务器 span 的实测值，不是 SLA 或负载压测结果。

| 操作 | 耗时 |
| --- | ---: |
| 新建会话 | 11.87 ms |
| 音乐对话操作 | 1,249.88 ms |
| DeepSeek 画像调用 | 5,627.37 ms |
| DeepSeek 工具调用 | 3,906.37 ms |
| 显式 Discovery | 29,925.70 ms |
| 同轮自动 Discovery | 36,895.32 ms |
| 获取 3 条推荐 | 907.67 ms |
| 提交反馈 | 54.73 ms |

## 测试发现并修复的问题

1. v22 历史会话父记录缺失阻断升级：按一致归属恢复父记录，保留历史并记录修复。
2. Celery 控制台入口导致延迟导入失败：统一改为 `python -m celery`。
3. 容器沿用宿主机 gRPC 回环绑定：容器内明确监听 `0.0.0.0:9090`。
4. Go OTLP exporter 发往 `/` 导致 404：改为 `/v1/traces`，新增 HTTP 接收端回归。
5. 画像分析在会话写事务中执行导致约 30 秒锁等待：分析移至提交后，新增写锁回归；修复后最终本轮为 11.87 ms。不同缓存条件下的模型耗时仍需单独考虑。
6. 旧脱敏逻辑误伤数字 token 用量：保留明确的数字用量字段，兼容历史已脱敏数据。
7. 音乐适配器提前停止读取 Agent 事件流：改为排空至运行结束，让 LangGraph 完成 checkpoint 收尾。
8. 并行搜索和后台画像刷新丢失 trace context：线程池提交显式复制上下文。
9. 任务结束过早关闭后台画像 RPC：关闭通道前排空运行中的刷新 Future，新增回归。
10. 大批候选请求超过本机 embedding 的 15 秒超时：改为默认每批 8 条，检查响应数量和索引，最终 41 条文本向量全部完成，错误 span 为 0。

## 证据与重跑

本机私有运行目录：`.amem/framework-e2e-20260916-071836/`。

- `result.json`：API/业务断言、trace ID、span 摘要与耗时。
- `browser-result.json`：浏览器结果。
- `browser-dialogue.png`：真实界面截图。
- `traces/traces.json`：Collector 输出的 OTLP JSON。
- `inventory.json`：数据快照规模。

凭据扫描未在 trace 中检出本次配置的 API key、令牌、密码或 B 站 Cookie 字段。私有 `private.env` 和账号 key 不应复制到文档或提交。

重跑入口、架构和迁移边界见 [框架迁移说明](framework-unification-2026-09-16.md)。本次验收未覆盖生产 OIDC/TLS、告警通知、高并发容量及长时间音频播放。

测试结束后停止 E2E 服务，保留独立数据卷与上述本机证据；临时单测 RabbitMQ 容器清理。重新启动可使用运行目录中的私有 env 文件执行 Compose `up -d --wait`。
