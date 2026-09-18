# DeepSeek API Key 用户配置（2026-09-18）

## 变更

- 登录用户在右上角账户菜单进入“API 设置”，可保存、替换或删除自己的 DeepSeek API Key。
- 个人密钥按用户独立读取，服务器 `.env` 文件不会被改写。2026-09-18 后续调整后，用户侧音乐助手不再回退到服务器变量；删除个人密钥会关闭音乐助手，首页推荐使用规则模式。
- 密钥按 `app_users.id` 隔离，AES-GCM 加密后存入持久化 SQLite `settings` 表。加密密钥由 `APP_SECRET_KEY` 派生，并将用户 ID 纳入认证数据。API 只返回是否配置，不返回密钥或尾号；前端不写入 localStorage。
- 对话、路由、画像提取、推荐画像和关键词演化的 DeepSeek 调用均在每次调用时读取所属用户的密钥。独立 AMEM 容器、后台任务容器与 API 容器使用同一个 `APP_SECRET_KEY` 和持久化数据目录。
- 重新生成了 AMEM gRPC Python 文件，并将代码生成工具固定为 `grpcio-tools==1.76.0`。这使 gRPC 代码与项目的 OpenTelemetry protobuf 6 依赖兼容，避免容器安装时出现互相冲突的版本约束。

## 上线条件

1. 生产环境必须设置长期稳定、随机生成且至少 32 字符的 `APP_SECRET_KEY`。所有读取用户密钥的容器必须使用相同值。不要使用 `.env.example` 中的示例值。
2. `recommend-radio/server-data` 与 `APP_SECRET_KEY` 都需要纳入备份。更换或丢失 `APP_SECRET_KEY` 后，已保存的个人密钥无法解密，用户必须重新填写。
3. 对公网部署继续使用 OIDC、HTTPS 和现有 CSRF 防护；密钥写入接口需要登录，响应禁止缓存。`DEEPSEEK_API_KEY` 可留空，此时未填写个人密钥的用户无法使用 DeepSeek 调用。

## 接口与验证

| 接口 | 功能 |
| --- | --- |
| `GET /api/settings/deepseek-key` | 返回 `configured`、`fallbackConfigured`、当前 `provider`，不返回密钥 |
| `PUT /api/settings/deepseek-key` | 请求体 `{ "deepseekApiKey": "..." }`，加密保存或替换 |
| `DELETE /api/settings/deepseek-key` | 删除当前用户的密钥 |

验证结果：DeepSeek 密钥及配置接口测试 3 项、Flask 启动测试 1 项、工作流与模型连接池测试 7 项均通过；前端 `npm run build` 与生产 Compose 配置解析通过。SSE 进度推送仍按现有业务阶段展示，未扩展到工具调用级别。
