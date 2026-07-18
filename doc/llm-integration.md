# DeepSeek LLM 集成

## 协议与默认配置

运行时通过 OpenAI Python SDK 调用 DeepSeek 的 OpenAI 兼容 Chat Completions 接口。默认配置为：

- `base_url`：`https://api.deepseek.com`
- `model`：`deepseek-v4-flash`
- `api_key_env`：`DEEPSEEK_API_KEY`
- `temperature`：`0.2`
- `max_tokens`：`512`

`deepseek-chat` 和 `deepseek-reasoner` 已进入弃用窗口，因此默认使用当前的 V4 模型名。
如需调整模型、超时或输出长度，应创建 `RuntimeConfig(llm=LLMConfig(...))`；配置哈希会记录这些
非机密配置变化，API 密钥不会进入配置对象。

## 密钥管理

本地开发可在仓库根目录创建 `.env`：

```dotenv
DEEPSEEK_API_KEY=你的_DeepSeek_API_密钥
```

`.env` 已被 `.gitignore` 忽略。生产环境应由部署平台或密钥管理系统注入同名环境变量；运行时
优先使用已有的进程环境变量，只有缺失时才加载 `.env`。

## 调用边界

`runtime.respond(query)` 的执行顺序如下：

```text
MemoryQuery
 -> Retrieval
 -> AccessChecker
 -> ContextBuilder
 -> DeepSeekChatClient
 -> AgentResponse
```

这是一条只读链路。`AgentResponse` 不是 `MemoryRecord`，也不会修改事件日志、记忆存储或快照。
需要长期记忆时，调用方必须将模型输出验证并转换为 `Event`，再进入 `runtime.ingest(event)`。

系统提示将投影记忆包裹为 `<memory_context>`，并声明其中的文本是不可信参考数据。这样可以防止
记忆正文中的提示注入覆盖访问控制或诱导模型绕过状态边界。

## 错误处理

- 未配置 `DEEPSEEK_API_KEY` 时抛出 `LLMConfigurationError`。
- 网络、认证、配额或供应商请求失败时抛出 `LLMRequestError`。
- 返回为空或没有可用候选时抛出 `LLMResponseError`。

异常信息不包含 API 密钥。
