# 音乐搭子对话页改版（2026-09-18）

## 参考与范围

参考 Deer Flow 官方仓库的 [ChatBox 布局](https://github.com/bytedance/deer-flow/blob/main/frontend/src/components/workspace/chats/chat-box.tsx) 和 [会话页](https://github.com/bytedance/deer-flow/blob/main/frontend/src/app/workspace/chats/page.tsx)：会话导航与对话区分栏、简洁的对话顶部信息、空会话引导、底部固定输入框。视觉层采用本项目的颜色变量和组件，没有引入 Deer Flow 的 React 依赖。

## 保留与调整

- 保留会话创建、切换、搜索、刷新、撤回、上下文引用、SSE 进度、推荐卡片、回忆卡片、歌曲播放、跳过、讨论、卡片反馈和候选异步回填。
- 对话区显示当前账户、该账户的 API Key 状态与设置入口；移动端会话列表以抽屉方式展示。
- 个人 DeepSeek Key、会话和卡片仍按服务端登录用户 ID 隔离。页面不保存或展示 API Key 明文；账号切换后从服务端读取对应状态。
- 示例提问只填充输入框，由用户确认后发送。
- 新建会话的服务端欢迎语在空会话阶段由引导面板承载；用户发消息后恢复正常消息记录。
- 修复新建或切换会话尚未完成时仍可发送的问题，避免消息进入旧会话。

## 联调与验证

- 本地忽略的 `recommend-radio/.env` 已生成稳定随机 `APP_SECRET_KEY`，供个人密钥加密使用；该值不写入版本控制。
- 前端 `npm run build` 通过；后端用户隔离测试覆盖两个账户的个人密钥与会话边界。
- `run_browser_e2e.py` 支持 `--base-url`，并按新页面按钮名称检查真实浏览器交互。
- 浏览器回归现在等待新会话 ID 生效和正式助手回复，不把“正在思考”占位消息算作回复。

2026-09-18 联调结果：AMEM、后端、RabbitMQ、SSE 网关、两个任务 worker 与 outbox 健康；前端 HTTP 可访问。后端定向测试 12 项通过，真实 Edge 浏览器完成新建会话、发送消息和接收 SSE 回复，无 JavaScript 错误。桌面 1440px 与手机 390px 布局无横向溢出；历史推荐会话中的 3 张推荐卡和 8 首歌曲的播放、跳过、讨论按钮可见。

当前本地 `.env` 使用 `AUTH_MODE=disabled`，因此本机演示账号是单一的 `legacy-owner`。两个账号的 API Key 和会话边界已用独立用户请求测试；公网多账号联调需按生产配置接入 OIDC。若生产要求每个用户必须自带密钥，应将服务器 `DEEPSEEK_API_KEY` 留空。
