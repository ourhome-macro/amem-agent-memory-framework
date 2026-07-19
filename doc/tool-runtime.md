# Tool Runtime

Tool Runtime 是 Agent Memory Runtime 的外部动作层。它解决的问题不是“怎么写一个函数”，而是 Agent 调用外部能力时如何注册、授权、执行、审计，并把结果转成事件进入记忆链路。

## 设计边界

工具结果不能直接写入 `MemoryStore`。标准链路是：

```text
ToolRequest
 -> ToolRegistry
 -> ToolPolicy
 -> ToolExecutor
 -> ToolResult
 -> AuditEnvelope(tool_call)
 -> Event(tool.result)
 -> EventStore / DerivationQueue
 -> MemoryStore
```

这条边界保持了既有原则：所有可回放状态都必须先成为事件，记忆只是从事件派生出来的状态。

## 当前内置工具

当前提供三个基础能力：

- `FunctionTool`：把 Python callable 注册为工具，适合 function calling。
- `FileReadTool` / `FileWriteTool`：在指定根目录内读写 UTF-8 文本文件。
- `WebSearchTool`：通过注入的 provider 执行搜索；测试和离线演示提供 `StaticWebSearchProvider`。

文件工具会把用户路径解析到配置根目录下；任何 `..` 逃逸都会被阻止并记录 `tool_call` 审计。

## 审计与脱敏

`ToolExecutor` 每次执行都会写入 `tool_call` 审计。审计保存：

- tool name
- actor / agent / session
- argument keys
- input hash
- output hash
- duration_ms
- error_type / error_hash
- 规范化后的 event_id

审计不会保存原始参数、文件正文、搜索 query、工具输出正文或异常消息正文。工具结果本身会返回给调用方，但进入 Event 时只保存摘要和输出 hash。

## Python 示例

```python
from agent_memory_runtime.tools import FunctionTool, ToolExecutor, ToolRegistry, ToolRequest

registry = ToolRegistry()
registry.register(FunctionTool(
    name="math.add",
    handler=lambda args: {"sum": int(args["a"]) + int(args["b"])},
))

executor = ToolExecutor(registry=registry)
execution = executor.execute(ToolRequest(
    tool_name="math.add",
    arguments={"a": 2, "b": 3},
    actor_id="user",
    agent_id="assistant",
    session_id="s1",
))

print(execution.result.output)
print(execution.event.to_dict())
```

如果要让工具结果进入长期记忆，应把 `execution.event` 交给 `runtime.ingest_async()` 或 `runtime.ingest()`，而不是直接写 `MemoryStore`。

## 后续扩展点

- WebSearchProvider 可接真实搜索服务 API。
- ToolPolicy 可扩展为 principal、scope、审批和限流策略。
- ToolExecutor 可扩展 timeout、重试、并发池和 streaming tool output。
- 工具 schema 可直接映射为 OpenAI-compatible function calling 的 `tools` 描述。
