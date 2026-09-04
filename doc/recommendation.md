# 推荐系统架构

Recommend Radio 将 AMEM 作为记忆与画像运行时，B 站作为候选内容来源。

```text
RequestSpec + MusicProfile
  -> DiscoveryPlanner
  -> DiscoveryService
  -> discovery_candidates
  -> content_cache
  -> RecommendationEngine
  -> shown / played / liked / skipped
  -> L0/L1/L2/L3 memory lifecycle
```

## 职责

| 组件 | 责任 |
| --- | --- |
| RequestInterpreter | 将当轮自然语言转为地区、语言、流派、声线、情绪和排除条件 |
| MusicProfile | 从 AMEM 投影用户长期偏好、回避项、近期偏好和探索策略 |
| DiscoveryService | 搜索 B 站、准入候选并维护库存，不直接服务推荐结果 |
| CandidatePool | 保存默认候选和 request-scoped 候选 |
| RecommendationEngine | 硬过滤、L0 去重、画像排序、MMR、UP/歌手多样性 |
| Feedback/Lifecycle | 将行为事件沉淀为 L1、L2 和 L3 |

## 作用域

- 明确请求候选属于 request scope，只供相同 RequestSpec 使用。
- 首页只读 default scope。
- 对话泛推荐以 default scope 为主，最多借入两首有效 L2 请求候选。
- 已展示、已听和跳过内容不会作为库存不足的回填项重复推荐。

## 性能

首轮主要成本是画像 LLM API 和 B 站 Discovery。画像命中缓存后，推荐只需读取候选池和执行本地排序。API `timing` 和 discovery job `timing` 提供可观测性。
