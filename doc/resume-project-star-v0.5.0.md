# Agent Memory Runtime 简历项目整理（STAR + 可复现数据）

## 项目定位

推荐项目名：**Agent Memory Runtime｜生产级通用业务 Agent 与事件溯源记忆框架**

一句话介绍：面向客服、个人助理、内容陪伴等上层应用，设计并实现与具体 UI/传输协议解耦的业务
Agent Runtime，覆盖模型—工具循环、受控多 Agent DAG、跨会话记忆、上下文压缩、结构化输出、
HITL、审计、故障恢复和 SQLite 持久化。

技术栈：Python 3.11+、asyncio、OpenAI-compatible API、JSON Schema Draft 2020-12、SQLite/WAL、
Typer/Rich、pytest、Ruff。

## 牛客公开优秀写法调研结论

牛客公开文章的共同判断不是“框架名越多越好”，而是要证明项目不是一次 LLM API 调用：

- [Agent 项目简历怎么写？一文讲清楚](https://www.nowcoder.com/discuss/832013460337672192)强调
  Tool 边界、Controller/Orchestrator、决策循环、监控反馈和量化结果。
- [简历中写了 RAG/Agent 项目，如何更能体现出自己的优势](https://www.nowcoder.com/discuss/898117229852585984)
  建议把技术亮点前置，展开 Memory、DAG、多 Agent、HITL、安全审计，并用 Recall@K、MRR、
  延迟等标准指标证明效果。
- [一个好的简历 Agent 项目，必须具备的几个关键因素](https://www.nowcoder.com/discuss/861207388076900352)
  把完整后端体系、真正的 Agent 系统能力、上下文工程、可观测性和评测体系视为 Demo 与产品级系统
  的分界线。
- [深势科技 Agent 全栈开发一面](https://www.nowcoder.com/feed/main/detail/f5ad9e14f2c94ef0b553e23c1bb944b9)
  的公开面经会继续追问多模块架构、结构化输出、多 Agent 动机、上下文共享、状态流转、三级记忆和
  准确率评估依据。
- [快手 AI Agent 开发一面](https://www.nowcoder.com/discuss/862642922603278336)把短期原文、历史
  摘要、结构化长期记忆、Token 成本和后端工具鉴权列为上下文与安全重点。

因此，本项目在简历上应主打“可恢复、可治理、可评测的 Agent Runtime”，不要写成“基于 DeepSeek
实现聊天机器人”。

## 简历可直接使用的短版（7 点）

1. **通用 Agent Loop**：从零实现异步模型—工具闭环，使用 Run/Turn/Checkpoint 状态机承载原生
   流式输出、多轮 Function Calling、重试、取消、恢复、幂等和未知副作用人工对账；Agent/协议专项
   28 项测试全部通过。
2. **事件溯源三层记忆**：以 EventLog 为唯一事实源，构建 Working/Core/Archival 派生记忆和
   exact/profile/all 会话策略；用不含 session 的稳定语义 ID 支持跨会话偏好修订，并通过白名单
   结构化画像安全注入 Prompt。
3. **检索与规模优化**：设计 SQLite schema v5，将身份、层级、状态、类型、scope 投影为索引列，
   增加 CJK 二元词、标签倒排和分页候选；10,000 条本地基准下候选量降低 97.44%，端到端检索
   P50 18.51 ms、P95 19.84 ms，相比已物化全量精排 P50 提速 10.98 倍。
4. **上下文与成本治理**：实现可插拔模型 TokenEstimator，在调用前统一核算 messages、tool schema、
   输出预留、累计 Token 和美元成本；按完整 tool-call 消息组压缩并持久化 Checkpoint，基准样本从
   5,513 降至 1,208 tokens，减少 78.09%，同时保留系统规则和原始任务。
5. **结构化输出可靠性**：实现 Draft 2020-12 `OutputContract`，支持 provider-native JSON Schema、
   本地强校验和有限修复；无效流式正文零透传，超限在模型调用前 fast-fail，避免下游把错误字符串
   当成业务对象。
6. **受控多 Agent 编排**：实现静态注册表 + 有界 DAG Orchestrator，支持依赖拓扑调度、独立节点
   并发、全局 Token/超时预算、父子租约 fencing、审批暂停恢复与取消传播，避免开放式 swarm 的
   无限委派和预算失控。
7. **安全、治理与可观测性**：实现 tenant/user/agent 多级访问控制、Prompt memory fence、PII
   最小化、HITL、无正文哈希审计、Retention Worker、Tombstone 防回放复活及 Snapshot 有界清理；
   安全/治理/可靠性专项 33 项测试全部通过。

## STAR 拆解

### 1. 通用业务 Agent Loop

- **S（Situation）**：一次性 `LLM query -> text` 无法承载多步工具、进程中断、重复请求和外部副作用。
- **T（Task）**：做成不依赖播放器、HTTP 或 UI 的可恢复业务 Agent 内核。
- **A（Action）**：设计 Run/Turn/Checkpoint 状态机；在模型和工具边界先持久化；加入 request 幂等、
  乐观版本、fenced lease、心跳、重试、取消、HITL 和非幂等未知结果对账。
- **R（Result）**：模型—工具循环、恢复、审批和协议行为由 28 个 Agent 专项测试覆盖；外部应用只需
  构造 `AgentRequest` 并消费 `AgentRunEvent`。

### 2. 跨会话三层记忆与个性化

- **S**：旧记忆 ID 含 session，用户换会话后偏好无法稳定更新；直接把偏好原文塞入系统 Prompt 又有
  注入风险。
- **T**：实现“长期画像跨会话、工作记忆不串会话、身份绝不越界”的明确语义。
- **A**：为 Core/Strategy 引入 v3 稳定语义 ID；设计 exact/profile/all 策略；归档仅在显式回忆意图
  下启用；仅把白名单 key 的结构化 value 注入 `<personalization-profile>`，自由文本继续放在不可信
  memory fence。
- **R**：4 个 smoke case 覆盖跨会话偏好、中文退款、真实归档和其他用户负样本，Pass Rate、
  Recall@K、MRR、nDCG@K 均值均为 1.0。

### 3. SQLite 索引化召回

- **S**：JSON payload 全量读取、反序列化和 Python 精排会随记忆量线性增长，且其他用户数据可能在
  候选截断前挤占合法结果。
- **T**：减少外部组件的同时，让单 SQLite Store 仍具备结构化过滤、中文召回和分页能力。
- **A**：追加 schema v5，建立 tenant/user/session/layer/status/type/scope/recency 组合索引、
  `memory_terms` CJK/Latin 倒排和 `memory_tags` 标签索引；数据库先取最多 256 个候选，再执行统一权限
  校验和精排。
- **R**：Windows 11 / Python 3.12.5 / 10,000 records / 100 次查询下，候选减少 97.44%；索引路径
  P50 18.51 ms、P95 19.84 ms；已物化全量精排 P50 203.25 ms，P50 提速 10.98 倍，目标记录 Top1。

### 4. 上下文压缩、Token 与成本预检

- **S**：长 Agent Loop 会重复携带历史与工具 schema，容易在供应商请求后才发现上下文或费用超限。
- **T**：在调用前给出确定的窗口/Token/成本边界，同时不能破坏 tool-call 协议。
- **A**：抽象模型感知 TokenEstimator；为输出预留空间；超过软阈值时按 assistant + tool result 完整组
  压缩，保留系统消息、原始任务、最近历史和 pending 协议；压缩 Checkpoint 先落库，硬限制在请求前
  fast-fail。
- **R**：42 条消息样本压缩为 9 条，Token 从 5,513 降至 1,208（-78.09%），移除 34 条旧消息且
  系统规则/原始任务校验保持不变。

### 5. 结构化输出契约

- **S**：提示模型“返回 JSON”并不能保证语法或字段正确；流式模式还可能先把无效结果发给业务方。
- **T**：让适配层只收到契约有效的业务对象，并控制修复成本。
- **A**：在 Run 创建前校验 Draft 2020-12 Schema；可选下发 provider-native `response_format`；本地再次
  校验并缓冲流式文本；只回传校验原因、不回灌无效原文，按持久化次数有限修复。
- **R**：受控测试中第一次错误类型 JSON 被完整拦截，第二次修复成功；外部只收到 1 个有效 delta，
  `run.completed.structured_output` 为已验证对象。

### 6. 受控多 Agent DAG

- **S**：开放式 Agent 自由委派容易形成循环、并发爆炸、身份错配和全局预算失控。
- **T**：适度提供复杂任务拆解能力，但保持宿主应用对拓扑和权限的最终控制。
- **A**：使用静态 Agent Registry 和有界 DAG；做环检测、节点/深度/扇出/并发限制；依赖满足后调度，
  汇总 child Run Token；父子状态持久化，支持审批、对账、恢复、取消和 lease fencing。
- **R**：能并行执行无依赖节点并串行推进依赖节点，同时拒绝环和越权 child Run；相关行为纳入上述
  28 项 Agent/协议专项回归。

### 7. 删除生命周期与安全审计

- **S**：仅删除派生记录会被 Event replay 复活；JSONL 跨文件崩溃可能留下已删投影；保存 Prompt 原文
  的日志又会扩大敏感面。
- **T**：让归档/删除可后台执行、可审计、可回放且不泄露正文。
- **A**：删除前持久化 sequence 水位 Tombstone；Replay 和读取双重拦截；SQLite 将 plan、删除、审计、
  Snapshot 放入共享事务；审计只存 hash/ID/决策，Snapshot 保留最近 N 份。
- **R**：旧事件无法复活删除内容，水位后的新显式事件仍可重建；安全、治理、可靠性 33 项专项测试
  全部通过。

## 验证数据与复现方式

当前本机结果（2026-07-21）：

| 验证项 | 结果 | 边界 |
|---|---:|---|
| 全量自动化测试 | 115 passed / 2.11 s | 单机功能回归，不等于生产流量压测 |
| Agent/协议专项 | 28 passed / 1.05 s | 编排、状态机、工具与模型协议 |
| 安全/治理/可靠性专项 | 33 passed / 0.34 s | 权限、审计、PII、Retention、故障语义 |
| 检索 smoke suite | 4/4；Recall@K/MRR/nDCG 均值 1.0 | 仅 4 个可审计 case，不冒充大规模 Golden Set |
| SQLite 10k 检索 | P50 18.51 ms；P95 19.84 ms | 本机 100 次；候选上限 256 |
| 全量精排对照 | P50 203.25 ms；索引路径 10.98x | 对照数据已反序列化，未把全量读取成本算进去 |
| Checkpoint 压缩 | 5,513 → 1,208 tokens（-78.09%） | 确定性合成的 42 消息样本 |
| 包构建 | wheel + sdist 成功；隔离目录安装冒烟通过 | 版本元数据 0.5.0，CLI Retention Worker 可加载 |

复现命令：

```powershell
py -3.12 -m ruff check benchmarks src tests
py -3.12 -m pytest -q
py -3.12 benchmarks\validate_runtime.py --records 10000 --iterations 100
```

## 简历中不要虚构的内容

- 项目没有使用 Milvus、Elasticsearch 或向量 Embedding，不要写“Milvus 召回率”；当前实现是 SQLite
  结构化索引 + Latin/CJK 词法召回，并为后续向量 Store 留协议边界。
- 4 个检索 case 是 P0 smoke suite，不是 200+ Golden Set；可以写真实的 4/4 和指标，后续扩容后再
  更新简历。
- 10.98 倍是本机可复现微基准，不是线上 SLA；必须同时写数据量、机器环境、对照口径。
- 未接真实业务流量，不能写“日均请求量、人工成本下降、任务完成率提升”等未经验证的数据。
- 多 Agent 是宿主定义的受控 DAG，不是模型自由生成 Agent 的开放式 swarm。

## 面试追问准备

1. 为什么 Core/Strategy ID 不含 session，但 Working 仍含 session？
2. profile 与 all 有什么安全差异，为什么 session policy 不能替代 AccessChecker？
3. 为什么用 Tombstone 水位，而不是 replay 后再删一次？
4. SQLite 候选索引如何避免全量 JSON 反序列化，为什么还要二次 hard filter？
5. CJK 二元词的优势、误召回和未来与向量召回融合的方案是什么？
6. Checkpoint 压缩如何保证 assistant tool call 与 tool result 不被拆散？
7. Token 预估为什么必须包含 tool schema 和预留输出，成本上限如何在调用前计算？
8. Provider 原生 JSON Schema 已开启时，为什么仍要本地校验？
9. 非幂等工具超时后为什么不能自动重试，`reconciliation_required` 如何收敛？
10. 为什么多 Agent 使用静态 DAG 和全局预算，而不是让模型自由委派？
