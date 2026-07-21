# Agent Memory Runtime

## v0.4：受控多 Agent 编排与交互式 CLI

本版本在 `BusinessAgentRuntime` 之上增加可选的 `AgentOrchestrator`：使用静态注册表和有界 DAG 完成父子委派、依赖并行、审批暂停/恢复、取消传播、租约 fencing、全局预算结算与 SQLite 持久化。它不是允许 Agent 自由拉起 Agent 的开放式 swarm；图、白名单、深度、节点数、扇出和并发度都由宿主应用显式控制。

```python
from agent_memory_runtime import (
    AgentDefinition,
    AgentDefinitionRegistry,
    AgentGraph,
    AgentOrchestrator,
    DelegatedTask,
    OrchestrationRequest,
)

registry = AgentDefinitionRegistry()
registry.register(AgentDefinition("research", research_runtime))
registry.register(AgentDefinition("writer", writer_runtime))

orchestrator = AgentOrchestrator(registry=registry)
request = OrchestrationRequest(
    graph=AgentGraph(
        tasks=(
            DelegatedTask("facts", "research", "收集事实"),
            DelegatedTask("answer", "writer", "形成答复", depends_on=("facts",)),
        ),
        output_task_ids=("answer",),
    ),
    tenant_id="tenant-1",
    request_id="request-1",
)
```

CLI 新增了受 Pi 极简 harness 交互启发的 `chat` 命令，但保留本项目的持久状态、身份隔离和人工审批语义：

```powershell
amem chat --agent assistant --session demo
amem chat -p "总结今天的事项" --mode text
amem chat -p "总结今天的事项" --mode jsonl --no-remember
```

交互模式支持 `/status`、`/providers`、`/model`、`/provider`、`/session`、`/new`、`/history` 和 `/exit`。完整设计、事件协议、恢复语义、SQLite schema v4 与生产边界见 [`doc/controlled-orchestration-cli-v0.4.0.md`](doc/controlled-orchestration-cli-v0.4.0.md)。

## v0.3：通用业务 Agent 框架

项目现在同时提供两个边界清晰的运行时：

- `AgentMemoryRuntime`：事件溯源记忆、检索、访问控制和治理；
- `BusinessAgentRuntime`：异步 Agent 循环、原生流式模型、工具调用、Run/Checkpoint、审批、取消、恢复、策略预算和指标。

产品或传输适配层只负责把外部输入转换为 `AgentRequest` 并消费 `AgentRunEvent`；播放器、HTTP、WebSocket、语音和 UI 协议不进入通用框架。

```python
import asyncio

from agent_memory_runtime import (
    AgentRequest,
    BusinessAgentRuntime,
    LLMConfig,
    OpenAICompatibleModelGateway,
)


async def main() -> None:
    runtime = BusinessAgentRuntime(
        model_gateway=OpenAICompatibleModelGateway(
            LLMConfig.for_provider("openai")
        )
    )
    async for event in runtime.run(
        AgentRequest(
            tenant_id="tenant-1",
            user_id="user-1",
            agent_id="assistant",
            request_id="request-1",
            message="帮我完成这个业务任务",
        )
    ):
        print(event.to_dict())


asyncio.run(main())
```

完整状态机、工具可靠性语义、SQLite schema v3、加密 codec 边界和部署检查见 [`doc/business-agent-runtime-v0.3.0.md`](doc/business-agent-runtime-v0.3.0.md)。

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

## 快速上手

最小演示只需要本地 JSONL Store，不依赖真实 LLM：

```powershell
amem init
amem ingest examples/data/customer_support_events.jsonl
amem retrieve --agent support_agent --query "refund status"
amem project --agent support_agent --query "refund status"
amem replay
```

如果要演示低延迟写入路径，可以让事件先进入派生队列，再由 worker 后台生成记忆：

```powershell
amem init
amem ingest examples/data/customer_support_events.jsonl --async-derive
amem queue
amem worker
amem retrieve --agent support_agent --query "refund status"
```

如果要调用真实 OpenAI 兼容模型，先在 `.env` 中配置对应供应商密钥，再执行：

```powershell
amem respond --agent support_agent --query "退款进度怎么样" --stream --fast
```

## 命令行工具

```powershell
amem init
amem ingest examples/data/customer_support_events.jsonl
amem ingest examples/data/customer_support_events.jsonl --async-derive
amem queue
amem queue run-once
amem retention plan
amem retention apply
amem derive
amem retrieve --agent support_agent --query "refund status"
amem project --agent support_agent --query "refund status"
amem respond --agent support_agent --query "refund status"
amem respond --agent support_agent --query "refund status" --stream --fast
amem providers
amem audit
amem audit --type access
amem audit-dashboard --out .amem/audit.html
amem worker
amem replay
amem eval examples/evals/retrieval_cases.yml
amem demo customer-support
amem demo personal-assistant
amem demo mock-interviewer
```

CLI 追踪输出包含已选记忆 ID、评分明细、被阻止的记忆数量，以及
`rule_version`、`config_hash`、`last_event_sequence` 和 `state_hash`。流式和快路径响应还会输出
`context_source`、`retrieval_timed_out` 和 `first_token_ms`。
每次执行 `amem` 子命令都会先输出 AMEM 启动横幅和运行时定位说明。

## 审计面板与监控

CLI 会把审计记录写入 `.amem/audit.jsonl`。执行一些写入、检索、工具调用或模型调用后，可以生成静态 HTML 审计面板：

```powershell
amem audit-dashboard --out .amem/audit.html
```

在 Windows 上直接打开：

```powershell
Invoke-Item .amem/audit.html
```

或者使用默认浏览器打开绝对路径：

```powershell
Start-Process (Resolve-Path .amem/audit.html)
```

面板会展示审计类型、执行结果、决策分布和脱敏后的审计 JSON。它适合本地调试和面试演示；当前不是常驻 Web 服务。如果要刷新监控内容，重新执行 `amem audit-dashboard --out .amem/audit.html` 即可。

## 数据安全与审计

事件带有 `sensitive` 标签，或检测到银行卡号、凭据等敏感字段时，运行时会在写入
`EventStore` 前自动标注并最小化其载荷。除路由所需的结构化标识外，文本和未知字段会被替换为
`[redacted]`；敏感记忆不得使用 `global` 作用域。读取链路仍会执行标签、可见范围和作用域校验。

运行时将 PII 检测、访问控制和 LLM 调用写入统一 `AuditEnvelope`。审计仅保留类型、决策、记忆
ID、阻止原因、用量、快照定位字段及请求/上下文/回答哈希，不保存提示词、查询、投影上下文、模型
回答或异常消息。CLI 默认将这些记录保存在 `.amem/audit.jsonl`，可使用 `amem audit`、
`amem audit --type pii`、`amem audit --type access` 或 `amem audit --type llm_call` 查看。

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
amem respond --agent support_agent --query "退款进度怎么样" --stream --fast
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
  agent/
    runtime.py
    model_gateway.py
    tool_runtime.py
    policy.py
    modules.py
    stores/
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
  governance/
  tools/
  llm/
  evals/
  cli/
```

## 记忆治理

治理模块已经提供异步派生队列、保留策略、人工审核和 PII Vault。同步写入仍是默认路径；需要优化用户请求首字延迟时，使用 `ingest --async-derive` 或 Python 的 `runtime.ingest_async(event)` 先落 Event 并创建队列任务，再由 `runtime.run_derivation_once()` 或 `amem queue run-once` 后台派生记忆。

Retention 可按事件序列年龄归档低价值 working memory，或删除过期 sensitive memory。Human Review 会把高风险 `MemoryCandidate` 先放进审核队列，批准后才进入 `MemoryStore`。PII Vault 负责把可逆敏感值换成 `${PII_...}` 令牌；runtime 的 `sanitize_event` 仍会作为兜底，阻止原文敏感载荷进入事件、记忆和审计。

## 工具调用

`tools/` 模块提供基础 Tool Runtime：`ToolRegistry` 注册工具，`ToolPolicy` 执行授权，`ToolExecutor` 负责调用和 `tool_call` 审计，`ToolResult` 会被规范化为 `tool.result` 事件。内置工具包括 function calling、根目录沙箱内的文件读写，以及 provider 驱动的 `web.search`。

工具结果不会直接写 `MemoryStore`。如果要进入长期记忆，应把 `ToolExecutor.execute(...).event` 交给 `runtime.ingest_async()` 或 `runtime.ingest()`。

## 设计来源

本项目从相邻的悬疑 Agent 系统中提炼出可复用的生产级边界：LLM 可以生成表达或意图，
但持久状态变化必须由规则派生、关联来源、支持回放，并在进入上下文前完成访问校验。
