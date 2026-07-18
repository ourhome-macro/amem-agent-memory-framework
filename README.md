# Agent Memory Runtime

Agent Memory Runtime 是一个面向有状态 Agent 的事件源记忆运行时框架。
普通 RAG 用于检索外部知识；本运行时维护 Agent 的长期交互状态，追溯每条记忆的来源，
治理其生命周期，并将经过安全筛选的记忆投影为 Agent 上下文。

核心记忆链路不依赖 LLM 或向量数据库。可选的 OpenAI 兼容模型层消费已经完成访问校验的上下文，
并生成 Agent 回答；它不会直接写入记忆。

## 核心链路

```text
Event
 -> SensitiveDataSanitizer
 -> EventStore
 -> Derivation
 -> Lifecycle
 -> MemoryStore
```

```text
Query
 -> Retrieval
 -> Access
 -> Compression
 -> Context
```

```text
EventStore
 -> Replay
 -> RuntimeSnapshot
 -> Consistency Check
```

## 安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## 命令行工具

```powershell
amem init
amem ingest examples/data/customer_support_events.jsonl
amem derive
amem retrieve --agent support_agent --query "refund status"
amem project --agent support_agent --query "refund status"
amem respond --agent support_agent --query "refund status"
amem providers
amem audit
amem replay
amem eval examples/evals/retrieval_cases.yml
amem demo customer-support
amem demo personal-assistant
amem demo mock-interviewer
```

CLI 追踪输出包含已选记忆 ID、评分明细、被阻止的记忆数量，以及
`rule_version`、`config_hash`、`last_event_sequence` 和 `state_hash`。
每次执行 `amem` 子命令都会先输出 AMEM 启动横幅和运行时定位说明。

## 数据安全与审计

事件带有 `sensitive` 标签，或检测到银行卡号、凭据等敏感字段时，运行时会在写入
`EventStore` 前自动标注并最小化其载荷。除路由所需的结构化标识外，文本和未知字段会被替换为
`[redacted]`；敏感记忆不得使用 `global` 作用域。读取链路仍会执行标签、可见范围和作用域校验。

`respond` 的每次成功或失败调用都会写入 `LLMCallTrace`。审计仅保留提供商、模型、记忆 ID、
用量、快照定位字段及请求/响应哈希，不保存提示词、查询、投影上下文、模型回答或异常消息。CLI
默认将这些记录保存在 `.amem/audit.jsonl`，可使用 `amem audit` 查看。

生产部署使用 `SQLiteStoreBundle` 时，单次写入会将事件、派生记忆和运行时快照放入同一 SQLite
事务；JSONL Store 适合本地演示和调试，不提供跨文件原子提交。

## OpenAI 兼容模型

项目使用 OpenAI Python SDK 的 Chat Completions 接口，提供 DeepSeek、OpenAI、Gemini、Qwen、
Z.AI/GLM 和 Kimi 的预设，并支持任意 OpenAI 兼容服务的 `custom` 配置。将相应密钥写入仓库根目录的
`.env`（该文件已经被 Git 忽略）：

```dotenv
DEEPSEEK_API_KEY=你的_DeepSeek_API_密钥
OPENAI_API_KEY=你的_OpenAI_API_密钥
GEMINI_API_KEY=你的_Gemini_API_密钥
DASHSCOPE_API_KEY=你的_Qwen_API_密钥
ZAI_API_KEY=你的_ZAI_API_密钥
MOONSHOT_API_KEY=你的_Kimi_API_密钥
```

准备演示数据后即可发起真实调用：

```powershell
amem init
amem ingest examples/data/customer_support_events.jsonl
amem respond --agent support_agent --query "退款进度怎么样"
amem respond --agent support_agent --query "退款进度怎么样" --provider kimi
amem respond --agent support_agent --query "退款进度怎么样" --provider custom --model example-chat --base-url https://models.example.com/v1 --api-key-env EXAMPLE_API_KEY
```

`respond` 只读取经过检索、授权和压缩后的上下文。若需要长期保留模型输出，应用必须先将输出
转换为 `Event`，然后显式调用 `runtime.ingest(event)`。

## Python 用法

```python
from agent_memory_runtime import AgentMemoryRuntime
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.query import MemoryQuery

runtime = AgentMemoryRuntime()
runtime.ingest(Event(
    event_id="evt-1",
    kind="message.created",
    actor_id="user",
    session_id="s1",
    labels=("private",),
    payload={
        "agent_id": "assistant",
        "subject_id": "user",
        "text": "User prefers concise status updates.",
    },
))

context = runtime.project(MemoryQuery(agent_id="assistant", text="status updates"))
print(context.projected_context)
```

## 仓库结构

```text
src/agent_memory_runtime/
  runtime.py
  config.py
  domain/
  memory/
    derivation/
    lifecycle/
    retrieval/
    compression/
    stores/
  access/
  context/
    audit/
  llm/
  evals/
  cli/
```

## 设计来源

本项目从相邻的悬疑 Agent 系统中提炼出可复用的生产级边界：LLM 可以生成表达或意图，
但持久状态变化必须由规则派生、关联来源、支持回放，并在进入上下文前完成访问校验。
