# Agent Memory Runtime

Agent Memory Runtime 是一个面向有状态 Agent 的事件源记忆运行时框架。
普通 RAG 用于检索外部知识；本运行时维护 Agent 的长期交互状态，追溯每条记忆的来源，
治理其生命周期，并将经过安全筛选的记忆投影为 Agent 上下文。

核心记忆链路不依赖 LLM 或向量数据库。可选的 DeepSeek 集成通过 OpenAI 兼容 API 消费
已经完成访问校验的上下文，并生成 Agent 回答；它不会直接写入记忆。

## 核心链路

```text
Event
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
amem replay
amem eval examples/evals/retrieval_cases.yml
amem demo customer-support
amem demo personal-assistant
amem demo mock-interviewer
```

CLI 追踪输出包含已选记忆 ID、评分明细、被阻止的记忆数量，以及
`rule_version`、`config_hash`、`last_event_sequence` 和 `state_hash`。

## DeepSeek API

项目使用 OpenAI Python SDK 的兼容接口连接 DeepSeek，默认模型为 `deepseek-v4-flash`。
将密钥写入仓库根目录的 `.env`（该文件已经被 Git 忽略）：

```dotenv
DEEPSEEK_API_KEY=你的_DeepSeek_API_密钥
```

准备演示数据后即可发起真实调用：

```powershell
amem init
amem ingest examples/data/customer_support_events.jsonl
amem respond --agent support_agent --query "退款进度怎么样"
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
