# Agent 设计

v0.3 起，`BusinessAgentRuntime` 负责通用业务 Agent 的 Run/Turn/Checkpoint 状态机、模型工具循环、
审批、取消、恢复和策略预算；`AgentMemoryRuntime` 仍是它组合使用的记忆子系统。产品 transport、
播放器协议、语音和 UI 都留在适配层。

推荐的完整调用链：

```text
外部输入 -> Adapter -> AgentRequest
                     -> BusinessAgentRuntime
                        -> ModelGateway / ToolRuntime
                        -> AgentRunEvent -> Adapter
                        -> AgentMemoryRuntime（只读投影 + 受治理事件）
```

运行时不向 LLM 授予记忆写入权限。Agent 输出应先转换为事件，随后由派生规则和生命周期归并器
决定这些事件是否产生记忆。

推荐的集成模式：

```text
Agent 输出
 -> 结构化事件
 -> runtime.ingest(event)
 -> 在下一轮执行检索/投影
```

Prompt 可以要求 LLM 总结或提出一条记忆，但生产写入仍必须经过类型化事件、已注册的派生规则、
写入守卫和归并器。

上下文投影有意是有损的。它是下一轮 Agent 调用可安全读取的视图，而不是状态对象。

当使用 `runtime.respond(query)` 时，运行时先完成检索和上下文投影，再将结果发送给模型。
模型只拥有该次投影的读取权限。系统提示会把记忆内容视为不可信参考数据，避免记忆正文中的
指令覆盖运行时约束；模型输出也不会被自动持久化。
