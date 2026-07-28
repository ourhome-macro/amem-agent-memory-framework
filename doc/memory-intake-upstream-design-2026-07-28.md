# Memory Intake 上游设计

## 目标

当前 `DerivationEngine` 假设上游已经提供结构化 `Event`。下一步不应该让 LLM 直接写 `MemoryRecord`，而是增加一层 `MemoryIntake`：

```text
raw_text / chat turn / tool result
  -> MemoryIntake
  -> Event
  -> DerivationEngine
  -> ReviewGuard / WriteGuard
  -> LifecycleReducer
  -> MemoryStore
```

`MemoryIntake` 的职责是把自然语言归一化为结构化事件，不拥有最终记忆状态权威。

## 双通道

### 1. 显式记忆路由

处理用户明确说出的记忆操作：

- `记住...`
- `以后都...`
- `默认用...`
- `不要再...`
- `忘掉...`
- `刚才说错了...`
- `改成...`

这条通道应同步、高精度、确定性优先。

输出示例：

```json
{
  "kind": "preference.updated",
  "payload": {
    "key": "java_style",
    "preference": "用户希望 Java 示例代码避免使用 Lambda",
    "operation": "revise",
    "layer": "core",
    "scope": "private",
    "salience": 0.9,
    "confidence": 0.95,
    "explicit": true,
    "evidence_text": "以后 Java 代码别用 Lambda"
  }
}
```

### 2. 隐式候选提取

处理没有显式“记住”，但未来可能有用的上下文：

- 长期项目上下文
- 稳定偏好
- 反复出现的约束
- 近期任务状态
- 重要决策或任务结果

这条通道应异步、保守、候选化，不直接升级为核心记忆。

输出示例：

```json
{
  "kind": "belief.stated",
  "payload": {
    "key": "current_project",
    "belief": "用户正在开发 agent-memory-runtime 项目",
    "operation": "create",
    "layer": "working",
    "scope": "private",
    "salience": 0.65,
    "confidence": 0.72,
    "explicit": false,
    "evidence_event_ids": ["evt-101", "evt-207"]
  }
}
```

隐式候选进入 working 层或审核队列，后续通过多次证据强化再升级。

## 推荐组件

```text
MemoryIntakeService
  -> ExplicitMemoryRouter
  -> ImplicitMemoryExtractor
  -> MemorySlotNormalizer
  -> MemoryEventValidator
  -> MemoryEventFactory
```

### ExplicitMemoryRouter

规则识别高精度模式，输出候选操作：

- remember
- update
- forget
- forbid
- correct

### ImplicitMemoryExtractor

可先不做，或异步实现。后续可用 LLM JSON extractor，不建议第一版上 BERT。

原因：

- BERT 适合分类，不擅长 key、scope、否定、时间边界和证据抽取。
- LLM 更适合结构化抽取，但必须通过 schema、confidence、evidence 校验。

### MemorySlotNormalizer

负责把自然语言槽位标准化：

- `key`
- `subject_id`
- `scope`
- `layer`
- `operation`
- `salience`
- `confidence`
- `evidence`

### MemoryEventValidator

阻止危险写入：

- 无 evidence 不写长期记忆
- 低 confidence 不进 core
- 含 prompt-injection 指令不直接注入
- 涉及凭证/支付/医疗等走敏感或审核
- 不能判断主体时不写 core

## 路由表

| 输入模式 | 输出事件 | 默认层级 | 默认操作 |
|---|---|---|---|
| `记住...` | `belief.stated` 或 `preference.updated` | `core` | `create/revise` |
| `以后都...` | `preference.updated` | `core` | `revise` |
| `默认用...` | `preference.updated` | `core` | `revise` |
| `不要再...` | `preference.updated` | `core` | `revise` |
| `忘掉...` | 删除/归档请求事件 | - | `archive/delete` |
| `刚才说错了...` | `belief.stated` 或 `preference.updated` | `core/working` | `revise/supersede` |
| `这次解决方法是...` | `task.outcome` | `core` | `revise` |
| 普通闲聊 | `message.created` 或不写长期候选 | `working` | `create` |

## 核心边界

`MemoryIntake` 不直接写记忆，不直接合并冲突，不直接决定权限。它只负责产生结构化 `Event`。最终状态仍由现有规则管线处理。

面试表达：

> 我会把事件派生的上游做成 Memory Intake 层。显式记忆指令走确定性规则路由，隐式上下文走异步候选提取。两条通道最终都只输出结构化 Event，后面仍交给 DerivationEngine、WriteGuard 和 LifecycleReducer。这样既能从自然语言里提炼记忆，又不破坏可审计和可回放边界。
