# Agent 运行时

agent runtime 负责协调模型调用、工具执行、checkpoint 和记忆上下文注入。

## 模块职责

- `BusinessAgentRuntime`：执行 `AgentRequest`，准备模型输入，运行工具循环并记录 run state。
- `AgentMemoryRuntime`：提供记忆检索、投影、proposal 应用、审计记录和 Auto Dream 调度。
- `AgentPolicy`：定义 token budget、工具策略、retry 边界、审批规则和压缩阈值。
- `AgentCheckpoint`：保存模型可见消息、工具调用状态、usage 和压缩 metadata。
- `ToolExecutionContext`：把 tenant、user、agent、session、run、call 身份传入工具 handler。
- `ModelGateway`：隔离 provider-specific chat、streaming、usage 和 tool call 协议。

## 记忆边界

agent runtime 通过 `AgentMemoryRuntime.project()` 读取记忆，通过显式工具创建 `MemoryProposal` 写入记忆。模型文本不能直接修改记忆状态。

## 运行约束

工具输出、记忆上下文和历史摘要都按不可信输入处理。系统规则、工具权限、上下文预算和审计记录由 runtime 维护，不交给模型自由决定。
