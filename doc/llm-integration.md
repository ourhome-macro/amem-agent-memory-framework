# OpenAI 兼容模型集成

## 协议与预设

运行时通过 OpenAI Python SDK 调用 Chat Completions 接口。`OpenAICompatibleChatClient` 只有一个
请求实现，厂商差异由 `LLMConfig` 的预设表达，因此不会为每个厂商复制状态、权限或审计逻辑。

| 提供商 | 默认模型 | `base_url` | 密钥环境变量 |
| --- | --- | --- | --- |
| `deepseek` | `deepseek-v4-flash` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `openai` | `gpt-5-mini` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `gemini` | `gemini-2.5-flash` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY` |
| `qwen` | `qwen3.6-plus` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| `zai` | `glm-5.2` | `https://api.z.ai/api/paas/v4/` | `ZAI_API_KEY` |
| `kimi` | `kimi-k2.6` | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` |

`kimi` 预设默认关闭 thinking，避免只读记忆回答被推理内容和低输出预算截断。`openai` 与 `kimi`
不传 `temperature`，由供应商采用模型默认行为。`custom` 不含默认值，必须明确提供模型、端点和
密钥环境变量。

通过 CLI 查看预设并选择提供商：

```powershell
amem providers
amem respond --agent support_agent --query "退款进度怎么样" --provider qwen
amem respond --agent support_agent --query "退款进度怎么样" --provider custom --model example-chat --base-url https://models.example.com/v1 --api-key-env EXAMPLE_API_KEY
```

Python 调用可使用 `LLMConfig.for_provider("kimi")`，或者使用
`LLMConfig.for_provider("custom", model=..., base_url=..., api_key_env=...)`。

## 密钥管理

本地开发可在仓库根目录创建 `.env`：

```dotenv
DEEPSEEK_API_KEY=你的_DeepSeek_API_密钥
OPENAI_API_KEY=你的_OpenAI_API_密钥
GEMINI_API_KEY=你的_Gemini_API_密钥
DASHSCOPE_API_KEY=你的_Qwen_API_密钥
ZAI_API_KEY=你的_ZAI_API_密钥
MOONSHOT_API_KEY=你的_Kimi_API_密钥
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
 -> OpenAICompatibleChatClient
 -> AuditStore
 -> AgentResponse
```

这是一条只读链路。`AgentResponse` 不是 `MemoryRecord`，也不会修改事件日志、记忆存储或快照。
需要长期记忆时，调用方必须将模型输出验证并转换为 `Event`，再进入 `runtime.ingest(event)`。

系统提示将投影记忆包裹为 `<memory_context>`，并声明其中的文本是不可信参考数据。这样可以防止
记忆正文中的提示注入覆盖访问控制或诱导模型绕过状态边界。

## 调用审计

`respond` 在模型调用成功和失败时都会写入 `LLMCallTrace`。记录包含提供商、模型、模型响应 ID、
输入输出 token、已选记忆 ID、被阻止数量，以及 `rule_version`、`config_hash`、
`last_event_sequence` 和 `state_hash`。请求和回答只保留 SHA-256 哈希；系统提示、用户查询、
投影上下文、回答正文、API 密钥和异常消息均不得写入审计记录。

CLI 使用 `.amem/audit.jsonl` 保存审计记录，可通过 `amem audit` 调试。生产环境应将
`AuditStore` 连接到受访问控制的持久化介质，并为审计数据配置独立保留和清理策略。

## 错误处理

- 未配置所选提供商的密钥环境变量时抛出 `LLMConfigurationError`。
- 网络、认证、配额或供应商请求失败时抛出 `LLMRequestError`。
- 返回为空或没有可用候选时抛出 `LLMResponseError`。

异常信息不包含 API 密钥。
