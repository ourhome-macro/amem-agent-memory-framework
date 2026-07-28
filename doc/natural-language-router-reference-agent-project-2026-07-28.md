# 另一个 agent 项目的自然语言路由参考

## 结论

`E:\project\agent` 的自然语言路由不是 LLM 路由，也不是 BERT 分类器，而是确定性规则路由：

```text
raw_text
  -> ActionClassifier
  -> EntityResolver
  -> ActionRouter
  -> PlayerAction
  -> NarrativeDirector.precheck
  -> RuleEngine.precheck
  -> ActionService.handle
  -> WorldEvent
```

这个设计的重点是把自然语言入口收敛成强类型动作，不让自然语言直接修改世界状态。

## 三层路由结构

1. `ActionClassifier`

只判断动作类型，支持：

- `inspect`
- `talk`
- `ask_about`
- `present_clue`
- `accuse`

实现方式是动作触发词、否定指控保护、多动作检测等确定性规则。

2. `EntityResolver`

只做实体解析，把文本里的角色、线索、热点、场景、claim 映射到 case package 中的实体 ID。

解析依据包括：

- case 内置角色名、线索名、场景名、claim ID
- curated alias 表
- NFKC/casefold/去空白符后的包含匹配
- 分数用于排序和歧义判断

3. `ActionRouter`

把动作类型和实体 ID 组合成 `PlayerAction`。缺槽位时返回 `needs_clarification`，未知实体返回 `unknown`，不硬造动作。

## 安全边界

路由器只生成 `PlayerAction`，不生成最终事件。

路由后还有两道闸：

- `NarrativeDirector.precheck_player_action`：叙事安全预检。
- `RuleEngine.precheck_player_action`：规则权威预检。

真正事件写入在 `ActionService.handle` 和 `RuleEngine` 中完成。

## 对 amem 的借鉴

amem 如果要从普通对话自动识别偏好、信念、任务结果，不应该让 LLM 直接写 `MemoryRecord`。

更稳的结构是：

```text
raw_text
  -> MemoryActionClassifier / Extractor
  -> MemoryEventRoute
  -> Event(kind=preference.updated / belief.stated / task.outcome / note.created)
  -> DerivationEngine
  -> WriteGuard
  -> LifecycleReducer
  -> MemoryStore
```

可以先做确定性 MVP：

- 明确记忆指令：`记住`、`以后都`、`默认用`、`不要再`
- 明确偏好表达：`我喜欢`、`我不喜欢`、`我习惯`
- 明确任务结果：`这次解决方法是`、`以后遇到这个问题就`
- 否定和冲突保护：不要仅靠相似度合并
- 缺少 key 或 subject 时进入澄清，不硬写核心记忆

后续可以加 LLM/BERT，但只放在 extractor 层，输出结构化 Event，并记录 evidence、confidence、model_version/prompt_version。

## 面试说法

另一个项目给出的经验是：自然语言路由层应该是输入归一化层，不是状态权威层。它可以识别意图和实体，但最终状态改变必须经过规则引擎。放到 amem 里，就是自然语言 extractor 只负责生成结构化 Event，记忆派生、权限校验、冲突处理和落库仍由现有规则管线负责。
