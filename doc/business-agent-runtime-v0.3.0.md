# v0.3.0 通用业务 Agent Runtime 实施与生产边界

## 1. 结论

v0.3.0 在原有 `AgentMemoryRuntime` 之上新增 `BusinessAgentRuntime`，项目由“可被应用手动调用的记忆库”升级为“可恢复、可治理、可组合的通用业务 Agent 框架”。

本层只负责 Agent 的业务执行语义，不包含播放器、B 站、HTTP、WebSocket、语音、UI 或宿主进程协议。上述内容属于适配层；适配层只需把外部输入转换为 `AgentRequest`，消费 `AgentRunEvent`，并处理审批、取消和恢复命令。

```text
Host / Transport / Product Adapter
              |
              v
      BusinessAgentRuntime
       |      |       |
       |      |       +-- AgentModule / Policy / Metrics
       |      +---------- ModelGateway / ReliableToolRuntime
       +----------------- Run / Turn / Checkpoint / Approval Store
              |
              v
       AgentMemoryRuntime
```

## 2. 本次交付

- 异步 Agent 主循环：`async for event in runtime.run(request)`。
- 原生流式模型输出：模型网关可以逐块发送 `ModelGatewayStreamEvent`，运行时统一转换为 `model.output.delta`。
- Provider-neutral 模型协议：`ModelMessage`、`ModelResponse`、`ModelToolCall`、`ToolDefinition` 和 `ModelGateway`。
- OpenAI Chat Completions 兼容网关：支持普通补全、原生流式文本和分片 tool call 参数组装。
- 持久化状态：`AgentRun`、`AgentTurn`、`AgentCheckpoint`、`ToolCallRecord` 和 `ApprovalRecord`。
- SQLite schema v3：新增 run、turn、checkpoint、tool call 和 approval 表，沿用 checksum migration，未修改 v1/v2 历史迁移。
- Run lease 与 fencing token：防止两个 worker 同时推进同一 run；长任务自动续租。
- 工具输入约束：内建 JSON Schema 子集校验，拒绝缺字段、错误类型、越界数值和额外字段。
- 工具可靠性：稳定 `call_id`、持久化执行前状态、幂等重试、超时、审批、人工对账和补偿。
- 策略预算：steps、model calls、tool calls、输入/输出/总 token、模型/工具/run timeout。
- 身份隔离：run、tool call、approval、checkpoint 全部沿用 tenant/user/agent/session 边界。
- 业务模块：`AgentModuleRegistry` 组合模块指令与工具，重复模块或工具名直接报错，不静默覆盖。
- 可观测性：统一运行事件、进程内无依赖指标快照、observer 和 evaluator 扩展点。
- 状态编码边界：`StateCodec` 允许接入 KMS/信封加密；默认 `JsonStateCodec` 只适用于测试或已加密磁盘。

## 3. 公开入口

最小文本 Agent：

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
    request = AgentRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="assistant",
        session_id="session-1",
        request_id="request-1",
        message="整理今天要完成的事项",
    )
    async for event in runtime.run(request):
        print(event.to_dict())


asyncio.run(main())
```

生产单机持久化组合：

```python
from agent_memory_runtime import AgentMemoryRuntime, BusinessAgentRuntime
from agent_memory_runtime.agent import OpenAICompatibleModelGateway
from agent_memory_runtime.memory.stores import SQLiteStoreBundle

stores = SQLiteStoreBundle("runtime.sqlite", agent_state_codec=my_kms_codec)
memory = AgentMemoryRuntime(
    event_store=stores.event_store,
    memory_store=stores.memory_store,
    snapshot_store=stores.snapshot_store,
    audit_store=stores.audit_store,
    derivation_queue=stores.derivation_queue,
    transaction_manager=stores,
)
agent = BusinessAgentRuntime(
    model_gateway=OpenAICompatibleModelGateway(config),
    memory_runtime=memory,
    state_store=stores.agent_state_store,
)
```

`my_kms_codec` 必须实现：

```python
class StateCodec:
    def encode(self, value: dict[str, object]) -> str: ...
    def decode(self, payload: str) -> dict[str, object]: ...
```

编码器用于 run/checkpoint/tool/approval 的 `payload` 列。索引列仍保留 run ID、tenant、request ID、状态和版本，便于调度与治理；不要把密钥和密文放在同一数据库或同一配置文件中。

## 4. Run 状态机

```text
pending --claim+fencing--> running ---------------------> completed
                             |  |                            |
                             |  +--------------------------> failed
                             |
                             +--> waiting_approval --decision--> pending
                             |
                             +--> needs_reconciliation --operator--> pending
                             |
                             +--> timed_out(idempotent in-flight) --> pending
                             |
                             +--> cancelled
```

约束：

- `request_id` 在 tenant 内是幂等键；同一 ID 绑定不同请求会抛 `AgentRunConflictError`。
- `run_id` 不能跨 tenant/user 恢复、读取或取消。
- claim 会生成新的随机 `lease_token`；renew 和状态推进必须匹配该 token。
- heartbeat 不递增业务版本，避免与主循环的乐观更新互相制造冲突。
- checkpoint 在模型返回后、执行工具前落盘。进程崩溃后不会丢失已经确定的 tool call。
- completed/failed/cancelled 是终态；等待审批和等待对账不是失败，可以显式继续。
- 模型最终文本先写 checkpoint，再把 run 标记 completed；如果两次写入之间崩溃，恢复时直接完成，不重复调用模型。

## 5. 统一事件协议

每个事件都包含 `event_id`、`run_id`、`execution_id`、`sequence`、tenant/agent/session、时间和结构化 `data`。

`execution_id` 来自本次 claim 的 fencing token。稳定状态事件和原生模型 delta 共享同一执行代次；崩溃后新 worker 会获得新的 `execution_id`。适配层应按 `(run_id, execution_id, sequence)` 排序，并按 `event_id` 去重。

| 事件 | 含义 |
| --- | --- |
| `run.started` / `run.resumed` | 新运行或从 checkpoint 恢复 |
| `context.ready` | 已完成身份校验、检索和记忆投影 |
| `model.started` | 即将调用模型 |
| `model.output.delta` | 原生流式文本块；非流式网关退化为单块 |
| `model.completed` | 模型、用量、finish reason 和 tool call 数已确定 |
| `tool.requested` / `tool.started` | 模型请求工具、运行时即将执行 |
| `tool.completed` / `tool.blocked` / `tool.rejected` | 工具终态 |
| `approval.required` | 高风险工具等待人工决定 |
| `tool.reconciliation_required` | 副作用结果未知，禁止自动重放 |
| `evaluation.completed` | evaluator 结果 |
| `run.completed` / `run.failed` / `run.cancelled` | run 终态 |
| `run.timed_out` | 有可安全重试的幂等工具正在执行，run 回到 pending |
| `run.busy` / `run.lease_lost` | 已被其他 worker 持有或本 worker 丢失 fencing 权限 |

运行事件是面向已授权适配层的业务流，可能包含用户可见文本、tool arguments 和 tool output。不要把原始事件无筛选地发送到日志或第三方监控；默认 `RuntimeMetrics` 只记录计数与时长，不记录载荷。

## 6. 模型协议

`ModelGateway.complete()` 是基础协议；实现 `stream()` 后，运行时会优先使用原生流式路径。一个有效的模型响应必须至少包含以下之一：

- 非空 assistant content；
- 一个或多个具备唯一 `call_id`、工具名和 JSON object 参数的 tool call。

OpenAI-compatible 网关会：

- 把 provider tool call 转换为 `ModelToolCall`；
- 校验重复 call ID 和非法 JSON；
- 在流式模式下按 `index` 合并函数名与 arguments 分片；
- 保留 model、response ID、finish reason 和可获得的 token 用量；
- 绝不把 provider 异常正文直接写入 run 事件，只暴露异常类型。

旧 `ChatClient` 可以通过 `LegacyChatModelGateway` 使用，但它是 text-only 兼容桥，不能执行模型发起的工具调用。

## 7. 工具可靠性语义

工具通过 `ToolExecutionContext` 获得：

- `call_id`：外部 API 的幂等键；
- `run_id`；
- 完整身份请求；
- 当前 attempt；
- cooperative cancellation token。

推荐使用 `AgentFunctionTool`：

```python
from agent_memory_runtime import AgentFunctionTool

reserve = AgentFunctionTool(
    name="inventory.reserve",
    description="Reserve inventory.",
    input_schema={
        "type": "object",
        "properties": {
            "sku": {"type": "string", "minLength": 1},
            "quantity": {"type": "integer", "minimum": 1},
        },
        "required": ["sku", "quantity"],
        "additionalProperties": False,
    },
    handler=reserve_handler,
    side_effects=True,
    idempotent=True,
)
```

生产语义不是虚假的“框架保证 exactly-once”，而是：

1. tool call 先持久化为 pending；
2. 执行前更新为 executing；
3. 幂等工具始终使用同一 `call_id` 重试；
4. succeeded output 持久化，崩溃恢复直接复用，不再次执行；
5. 非幂等副作用只允许一次尝试；超时、取消或异常均进入 `reconciliation_required`；
6. operator 通过 `reconcile_tool_call()` 确认外部真实结果后才能继续；
7. 已成功工具若提供 compensator，可以通过 `compensate_tool_call()` 显式补偿。

同步函数放在线程执行。Python 无法安全杀死已经运行的线程，因此超时不等于副作用已停止；正因如此，非幂等副作用必须进入对账态。

内建 JSON Schema 校验器支持生产常用子集：object、array、string、integer、number、boolean、null、required、properties、additionalProperties、enum、items、长度和数值边界。复杂 `$ref` schema 应在自定义 tool/gateway 边界使用完整验证器；内建验证器会明确拒绝 `$ref`，不会假装已校验。

## 8. 审批、拒绝和对账

- 默认风险阈值为 `high`；side-effect tool 未声明风险时默认为 high，因此默认需要审批。
- `decide_approval()` 要求 tenant 和 reviewer ID；相同决定可幂等重试，相反决定会被拒绝。
- 拒绝不是 run failure。运行时把 `rejected` 作为 tool message 交回模型，让模型向用户解释或选择其他方案。
- executing 的非幂等副作用在恢复时不会再次执行，而是进入人工对账。
- `reconcile_tool_call()` 只接受处于 reconciliation 状态的记录。
- compensation 是显式运维动作，不在普通模型循环中自动触发。

## 9. 策略与模块

`AgentPolicyResolver` 可以按 tenant、user、agent 或 request metadata 返回不同策略。默认策略对所有资源设置有限上限，避免无界循环和账单失控。

模型只会看到策略允许的工具。即使模型伪造一个未暴露工具名，运行时也会持久化 blocked 结果并把失败交回模型，不会执行。

`AgentModule` 只贡献两类内容：

- 受信任的 system instructions；
- tools。

它不处理 HTTP、播放器命令、WebSocket session 或产品 UI。模块名、全局工具名和模块工具名冲突均为启动/运行错误，禁止“后注册覆盖前注册”。

## 10. SQLite schema v3

新增表：

- `agent_runs`：tenant request 幂等索引、status、version 和完整 payload；
- `agent_turns`：每次模型调用的 started/completed/failed 记录；
- `agent_checkpoints`：消息历史、待处理 tool calls 和最终输出；
- `agent_tool_calls`：执行状态、attempt、幂等属性、输出和错误哈希；
- `agent_approvals`：reviewer、decision、reason 和决定时间。

所有表使用外键关联 run，关键查询有 tenant/status 或 run/status 索引。写操作继续使用 `BEGIN IMMEDIATE`、busy timeout、WAL 和 checksum migration。

默认 codec 是明文 JSON。正式环境必须至少满足以下一项：

- 数据盘、快照和备份均启用平台级加密，并严格限制数据库文件权限；
- 传入自定义 `StateCodec`，使用 KMS 管理的信封加密。

更换 codec 后必须使用同一 codec 读取已有行。密钥轮换应由 codec 自己携带 key version，并通过离线迁移重编码；不要原地猜测密钥。

## 11. 记忆边界

`BusinessAgentRuntime` 组合而不是替换 `AgentMemoryRuntime`：

- 发起模型调用前，使用 tenant/user/agent/session 构造 `MemoryQuery`；
- 只把经过访问控制和围栏处理的 memory projection 放入 system message；
- tool result 以确定性的 `agent-tool:{call_id}` event ID 写入异步派生队列；
- tool event 只包含状态、错误类型和 output hash，不把原始 tool output 写入记忆事件；
- 模型最终文本不会自动升级为长期记忆，业务应用必须显式产生可治理的 domain event。

## 12. 故障恢复矩阵

| 故障点 | 恢复行为 |
| --- | --- |
| 模型调用前崩溃 | 同一 turn 重新调用模型 |
| 模型返回后、run 计数更新前崩溃 | 从 completed turn 和 checkpoint 重建计数 |
| 最终文本 checkpoint 后、run completed 前崩溃 | 直接完成，不重复调用模型 |
| tool pending 后崩溃 | 从 checkpoint 继续审批或执行 |
| 幂等 tool executing 时崩溃 | 使用同一 call ID 重试 |
| 非幂等 tool executing 时崩溃 | 停在 reconciliation，不自动重试 |
| tool succeeded 后、checkpoint 前崩溃 | 复用持久化 output，再补 tool message |
| approval required 后崩溃 | 重放同一 approval record |
| worker lease 过期 | 新 worker 以新 fencing token claim；旧 worker 更新被拒绝 |
| 外部取消 | run 进入 cancelled，cooperative token 通知模型/工具 |

## 13. 当前生产边界

v0.3.0 是单节点或共享 SQLite 的中低并发生产候选，不是完整分布式控制面。

- `AgentStateStore` 是协议，可实现 PostgreSQL、DynamoDB 或其他事务存储；本仓库当前提供内存和 SQLite。
- SQLite 适合单机多线程/多进程协调，不适合跨地域高写入吞吐。
- 宿主 transport 的断线重连、背压和消息投递确认由适配层负责。
- 原始运行事件面向可信调用方，不是脱敏审计日志。
- 自定义 tool 的外部幂等能力、事务性和 compensator 正确性仍由 tool 实现者负责。
- 默认 JSON codec 不提供应用层加密。

## 14. 发布验证

固定使用 Python 3.12（项目声明 Python >= 3.11）：

```powershell
py -3.12 -m pytest -q -p no:cacheprovider
py -3.12 -m ruff check .
git diff --check
py -3.12 -m build
```

v0.3.0 回归覆盖文本与原生流式运行、普通/分片 tool call、schema 阻断、幂等重试、审批通过/拒绝、SQLite 重开恢复、身份隔离、lease fencing、取消、策略预算、非幂等对账、幂等超时恢复、补偿和 at-rest codec。

2026-07-21 本地发布验证结果：Python 3.12 下 `86 passed`；Ruff 全通过；`git diff --check`
无错误；隔离构建成功生成 `agent_memory_runtime-0.3.0-py3-none-any.whl` 和 sdist。wheel 解包后
`BusinessAgentRuntime`、`SQLiteAgentStateStore` 导入成功，包元数据版本为 `0.3.0`，最新 SQLite
schema 版本为 `3`，并确认 wheel 包含 Agent runtime、model gateway 和 SQLite state store。
