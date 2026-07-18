# 记忆状态模型

## 核心类型

- `Event`：不可变的来源输入，包含 `event_id`、`sequence`、`kind`、`actor_id`、
  `session_id`、`payload`、标签、标记和时间戳。
- `MemoryCandidate`：由规则派生的候选记忆。只有通过写入守卫和生命周期归并后，
  它才成为权威状态。
- `MemoryRecord`：可正式检索的记忆，包含类型、作用域、层级、所有者、可见范围、
  标签、来源链接、显著性、置信度、状态和生命周期元数据。
- `RuntimeSnapshot`：包含规则哈希、配置哈希和状态哈希的回放检查点。
- `AgentResponse`：一次模型调用的临时读取结果，包含回答、模型标识、用量和对应的
  `AgentContext`；它不是持久状态，也不会自动生成记忆。

## 记忆类型

- `episodic`：具体的交互或观察。
- `belief`：偏好、明确陈述的信念，或推断出的稳定用户/Agent 信念。
- `relationship`：主体间的关系信号。
- `strategy`：任务结果或学习得到的执行启发式。

## 作用域与层级

作用域：

- `private`：由单个 Agent 所有，或仅对显式指定的主体可见。
- `shared`：对已配置主体可见。
- `global`：可广泛访问的记忆。

层级：

- `core`：稳定的长期记忆。
- `working`：当前会话中的活动记忆。
- `archival`：保留用于审计和回放，通常不进入上下文。

## 来源链接

每条活动记忆都必须携带 `source_event_ids`。派生出的 strategy 记忆还可以携带
`source_memory_ids`，以保留推理链路。
