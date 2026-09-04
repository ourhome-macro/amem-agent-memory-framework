# Recommend Radio

Recommend Radio 是 AMEM 的 B 站音乐推荐应用：Vue 前端、Flask API、AMEM gRPC 和记忆驱动的候选池推荐。

## 能力

- B 站视频/音频播放、曲库、播放历史、喜欢和歌单；
- 对话和首页共享同一个 RecommendationEngine；
- RequestSpec 支持语言、地区、流派、声线、情绪和排除条件；
- Discovery 负责 B 站补货，CandidatePool 只保留可服务候选；
- L0-L3 和记忆温度驱动长期画像、近期上下文、去重和疲劳控制；
- 推荐 trace 输出画像、请求约束、候选来源和性能耗时；
- 本地 bge-m3 embedding 支持 AMEM 语义检索。

## 启动

```powershell
docker compose up -d --build
```

访问 `http://localhost:3000`。

## 组件

```text
frontend :3000       Vue 静态前端和 API 代理
backend  :5000       对话、播放、Discovery、候选池、推荐 API
amem     :9090       记忆、画像、审计与检索 gRPC 服务
embedding :8001      宿主 bge-m3 OpenAI-compatible embedding 服务
```

## 推荐原则

```text
RequestSpec 硬约束
> L2 当前场景
> L3 稳定回避项
> L1 近期偏好
> L3 长期偏好
> 探索与多样性
```

明确请求会同步 bootstrap discovery 后再返回候选；首页默认池低于 32 条时后台预热。请求定向候选不会污染首页，泛推荐最多借入两首近期对话上下文候选。

## 性能

推荐 API 返回 `timing`，trace 保存画像、LLM API、L2、候选池、打分、MMR 和 Discovery 搜索/准入耗时。画像缓存命中后不会再次调用 LLM。
