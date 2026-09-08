# SSE 任务事件链路

更新日期：2026-09-08

## 边界

Go Gateway 只负责长连接、事件持久化和广播，不承载 Agent、Memory、Discovery 或推荐逻辑。

```text
Vue EventSource
    ↑ session SSE
Go SSE Gateway :8080
    ↑ internal event POST
Python Backend :5000
    → Agent / Memory / Recommendation / Discovery
```

## 接口

- `GET /api/agent/events?sessionId=...`：浏览器 SSE 长连接。
- `POST /internal/events`：Python 发布内部事件，要求 Bearer Token。
- `GET /health/live`：进程存活。
- `GET /health/ready`：SQLite 事件库可用。
- `POST /api/agent/dialogue/tasks`：Python 创建异步对话任务，立即返回 `taskId`。

事件类型为 `task`、`progress`、`session`、`discovery`、`done`、`error`。事件先写入
`sse-events.sqlite3`，再广播到按 `user_id + session_id` 隔离的内存 Channel。

## 可靠性

- 每条事件具有 SQLite 自增 `event_id`。
- 浏览器断线重连使用 `Last-Event-ID`，最多补发 500 条。
- 新连接默认从当前游标开始，不回放已结束的陈旧任务。
- 15 秒发送一次 heartbeat。
- 每个订阅者缓冲 64 条；慢消费者溢出时断开，由客户端重连并从 SQLite 补发。
- 默认保留 7 天任务事件，每小时清理一次，可通过 `SSE_EVENT_RETENTION_DAYS` 调整。
- 本地模式使用 `legacy-owner`；OIDC 模式通过 Python `/api/session/me` 校验 Cookie。
- 生产环境必须显式配置 `SSE_INTERNAL_TOKEN`。

## 本地联调结论

- 任务提交响应：75ms，HTTP 202。
- 完整任务事件：`queued → route → session → done`。
- 推荐任务事件：命中 `memory`、`recommendation`、`discovery`、推荐卡片和 `done`。
- Gateway 重启后，指定 `Last-Event-ID` 可以补发遗漏事件。
- 通过 `localhost:3000` 静态代理接收事件时未发生响应缓冲。

启动：

```powershell
.\scripts\start-local.ps1 -Rebuild
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:18080/health/ready
```
