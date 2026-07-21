# v0.4.0：受控多 Agent 编排与交互式 CLI

## 结论

v0.4.0 增加的是一个可选、受控、可恢复的多 Agent 编排层，而不是开放式 swarm。

`BusinessAgentRuntime` 继续负责单个业务 Agent 的模型循环、工具可靠性、审批、Checkpoint 和 Run 状态；`AgentOrchestrator` 只负责已注册 Agent 之间的显式 DAG 调度。外部应用仍负责传输协议、UI、音频、WebSocket 和业务对象适配。

本版本同时新增 `amem chat`。它借鉴 Pi 的极简终端交互方式，但没有复制编码工具、宽权限执行模型或 TypeScript 扩展体系。

## 为什么不是开放式 swarm

允许模型自行创建 Agent、修改拓扑或无限委派，会同时放大成本、权限、死循环、提示注入和故障恢复风险。通用业务框架更需要可审计边界，因此 v0.4 采用以下约束：

- Agent 必须预先注册到 `AgentDefinitionRegistry`。
- 调用方显式提交无环 `AgentGraph`。
- `OrchestrationPolicy` 限制 Agent 白名单、节点数、最大扇出、深度、并发数、总 Token、依赖载荷字符数、运行超时和租约。
- 子任务不能通过模型输出修改 DAG。
- 依赖结果以 JSON 数据注入，并附加“不得视为指令”的系统规则。
- 每个子请求使用确定性 `request_id`，重启或抢占后不会重复创建逻辑运行。

## 核心 API

```python
import asyncio

from agent_memory_runtime import (
    AgentDefinition,
    AgentDefinitionRegistry,
    AgentGraph,
    AgentOrchestrator,
    DelegatedTask,
    OrchestrationPolicy,
    OrchestrationRequest,
    SQLiteOrchestrationStore,
)


registry = AgentDefinitionRegistry()
registry.register(AgentDefinition("research", research_runtime))
registry.register(AgentDefinition("risk", risk_runtime))
registry.register(AgentDefinition("writer", writer_runtime))

orchestrator = AgentOrchestrator(
    registry=registry,
    state_store=SQLiteOrchestrationStore(".amem/runtime.sqlite"),
    policy=OrchestrationPolicy(
        allowed_agents=frozenset({"research", "risk", "writer"}),
        max_nodes=8,
        max_fan_out=4,
        max_parallelism=2,
        max_depth=2,
        max_total_tokens=80_000,
    ),
)

request = OrchestrationRequest(
    graph=AgentGraph(
        tasks=(
            DelegatedTask("facts", "research", "提取事实"),
            DelegatedTask("risks", "risk", "识别风险"),
            DelegatedTask(
                "answer",
                "writer",
                "生成最终答复",
                depends_on=("facts", "risks"),
            ),
        ),
        output_task_ids=("answer",),
    ),
    tenant_id="tenant-1",
    user_id="user-1",
    session_id="session-1",
    request_id="request-1",
)


async def main() -> None:
    async for event in orchestrator.run(request):
        print(event.to_dict())


asyncio.run(main())
```

未指定 `output_task_ids` 时，所有叶子节点是编排输出。聚合本身也是普通 DAG 节点，不在调度器中内置第二套模型调用。

## 状态与恢复

父运行状态：

```text
pending -> running -> completed
                   -> waiting -> pending -> running
                   -> failed
                   -> cancelled
```

节点状态：

```text
pending -> running -> completed
                   -> waiting -> pending -> running
                   -> failed
                   -> cancelled
```

恢复语义如下：

- 父运行使用带 fencing token 的租约，同一时刻只有一个 worker 可以推进 DAG。
- 节点使用乐观版本更新，取消与完成竞态不会静默覆盖。
- 过期父租约被新 worker 抢占后，残留的 `running` 节点回到 `pending`。
- 子请求 ID 固定为 `orchestration:{orchestration_id}:{task_id}`；子运行已完成时直接重放，仍忙时等待其租约或结果。
- 父运行 `request_id` 在租户内幂等。同一个 ID 绑定不同图会报冲突。
- SQLite 不是分布式数据库；多进程单机可使用 WAL 和租约，更大规模部署应实现 `OrchestrationStateStore` 对接生产数据库。

## 审批、对账和取消

子 Agent 返回 `approval.required` 或 `tool.reconciliation_required` 时，对应节点和父编排进入 `waiting`，不会丢失 Checkpoint。

调用方可以使用：

```python
await orchestrator.decide_approval(
    orchestration_id,
    task_id="write",
    approval_id=approval_id,
    tenant_id="tenant-1",
    user_id="user-1",
    reviewer_id="reviewer-1",
    approved=True,
)

async for event in orchestrator.resume(
    orchestration_id,
    tenant_id="tenant-1",
    user_id="user-1",
):
    ...
```

审批 ID 和对账 call ID 会校验是否属于指定节点的子运行，不能借共享 Store 越权决定另一个 Run。取消父编排会设置共享取消令牌、显式取消已知子运行，并把未终止节点收敛为 `cancelled`。

框架不会在兄弟节点失败时自动补偿已完成的外部副作用。自动补偿往往比失败本身更危险；应用应根据业务语义显式调用单 Agent 的补偿能力。

## 事件协议

父编排输出统一 `OrchestrationEvent`，关键类型包括：

- `orchestration.started`
- `delegation.started`
- `delegation.child_event`
- `delegation.waiting`
- `delegation.completed`
- `delegation.failed`
- `orchestration.waiting`
- `orchestration.completed`
- `orchestration.failed`
- `orchestration.cancelled`
- `orchestration.busy`
- `orchestration.lease_lost`
- `orchestration.timed_out`

事件带有 `orchestration_id`、`execution_id`、单调 `sequence`、租户/会话身份和稳定事件 ID。`delegation.child_event` 内嵌原始 `AgentRunEvent`，外部适配层可以统一转发流式文本、工具进度和人工审批。

编排状态是持久的，事件流采用“可重放状态 + 幂等事件 ID”语义，不是独立消息队列。需要跨服务至少一次投递时，应由适配层增加事务 Outbox。

## 预算语义

`max_total_tokens` 在节点结算边界检查。超限编排不会以成功结束，也不会继续调度新节点。并行批次中的在途节点可能造成有限超额，因此生产配置应同时给每个 `BusinessAgentRuntime` 设置单 Run Token 上限，并用 `max_parallelism` 控制最大在途预算。

## SQLite schema v4

v4 只追加迁移，不修改 v1-v3 的名称、SQL 或 checksum：

- `agent_orchestrations`：父运行、租约、状态、版本、请求幂等键和编码后的 payload。
- `agent_delegations`：节点 Agent、状态、版本、子 Run、输出和 Token 结算。
- `idx_agent_orchestrations_tenant_status`
- `idx_agent_delegations_run_status`

`SQLiteStoreBundle.orchestration_store` 与记忆、审计、派生队列、单 Agent Store 共用同一事务管理器和可选 `StateCodec`。

升级前应备份数据库。初始化 `SQLiteStoreBundle` 或 `SQLiteOrchestrationStore` 会自动执行 v4 迁移；历史迁移 checksum 不一致会拒绝启动。

## `amem chat`

### 交互模式

```powershell
amem chat --agent assistant --tenant tenant-1 --user user-1 --session daily
```

支持命令：

| 命令 | 作用 |
| --- | --- |
| `/status` | 查看 provider、model、Agent、租户、用户和 session |
| `/providers` | 查看内置 provider preset |
| `/model [id]` | 查看或切换当前模型 |
| `/provider [id]` | 查看或切换 provider preset |
| `/session [id]` | 查看或切换持久 session |
| `/new [id]` | 创建新 session |
| `/history [n]` | 查看当前 session 最近的脱敏事件 |
| `/exit` | 退出 |

输入以 `//` 开头时会向 Agent 发送一个字面量 `/`。工具需要审批时，CLI 会显示工具名和 call ID，并要求用户确认；未知副作用结果必须人工选择成功、失败或继续暂停。

默认会把已完成的一问一答作为两个 `message.created` 事件原子写入 SQLite。使用 `--no-remember` 可关闭这一适配层行为。

### 单次纯文本

```powershell
amem chat -p "总结当前风险" --mode text --session daily
```

### JSONL 事件流

```powershell
amem chat -p "总结当前风险" --mode jsonl --no-remember
```

`chat` 不输出全局 ASCII 横幅，因此 JSONL 模式默认就是纯事件流；其他命令需要机器输出时仍可把根选项 `--no-banner` 放在子命令之前。JSONL 模式逐行输出完整 `AgentRunEvent`，适合 shell、进程管道和外部应用适配器。退出码为：完成 `0`、等待人工处理 `2`、失败或取消 `1`。

## 与 Pi 的关系

DeepSeek 官方集成页明确说明 Pi 是第三方 Agent，而不是 DeepSeek 自研 harness：<https://api-docs.deepseek.com/quick_start/agent_integrations/pi_mono/>。

Pi 官方资料强调极简核心、交互/打印/JSON/RPC/SDK 多种模式、斜杠命令、运行时切模型、树形会话和扩展机制：<https://pi.dev/>、<https://pi.dev/docs/latest>。

v0.4 只借鉴与通用业务 Agent CLI 直接相关的部分：

- 一个默认可用的交互入口；
- 人类模式与机器事件模式分离；
- 可见的 provider/model/session 状态；
- 小而稳定的斜杠命令集合；
- 原生流式输出。

没有照搬的部分：编码文件工具、shell 执行、运行时自修改、无内置权限边界、会话分享和 TypeScript 扩展。这些不是通用业务 Agent 核心的职责。

## 验证范围

v0.4 新增测试覆盖：

- DAG 环检测、白名单和扇出策略；
- 真并行根节点与依赖输出注入；
- 请求幂等重放；
- 子审批暂停、越权审批阻断和恢复；
- 节点失败阻断下游；
- 总 Token 超限失败；
- 父取消传播到活动子 Run；
- 内存/SQLite 租约 fencing 和身份隔离；
- SQLite 重启恢复与 schema v4 完整性；
- CLI JSONL 纯输出、交互命令、审批和原子记忆写入。

发布前执行：

```powershell
py -3.12 -m pytest -q
py -3.12 -m ruff check .
git diff --check
```
