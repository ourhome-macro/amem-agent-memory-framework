# BGE-M3 检索基准评测报告

**日期**: 2026-07-23
**模型**: BAAI/bge-m3 (1024-dim, cosine, L2-normalized, CPU/sentence-transformers)
**测试集**: 45 条记忆 (26 条 JSONL 事件 + 19 条新增干扰/目标事件), 30 个评测用例

## 一、评测设计

### 1.1 测试数据

- **记忆库**: 45 条记忆, 涵盖 episodic / belief / strategy 三种类型, 分布在 2 个 tenant、2 个 agent、4 个 session
- **评测用例**: 30 个, 分 10 个类别:

| 类别 | 用例数 | 设计意图 |
|------|--------|----------|
| semantic_paraphrase | 5 | 中文同义改写, 零/低词面重叠 |
| cross_language | 4 | 中英跨语言语义匹配 |
| exact_identifier | 4 | 精确标识符检索 (含 3 个 expect_empty) |
| hard_negative | 2 | 否定区分 (续费开/关) |
| authorization | 3 | 权限隔离 (跨 tenant/agent/session) |
| no_result | 3 | 无结果校准 (quantum/mars/ancient) |
| preference_recall | 3 | 偏好记忆召回 |
| strategy_recall | 1 | 策略记忆召回 |
| episodic_recall | 2 | 情节记忆召回 (含归档层) |
| semantic_synonym | 3 | 同义概念匹配 |

### 1.2 检索模式

| 模式 | 描述 |
|------|------|
| FTS5-only | 仅 BM25 词面检索, 无向量 |
| Vector-only | 仅 bge-m3 余弦相似度检索, 无 FTS5 |
| Hybrid-RRF | FTS5 BM25 + bge-m3 向量双路, RRF(k=60) 融合 |

### 1.3 阈值矩阵

对 Vector-only 和 Hybrid-RRF, 测试 5 个语义相似度阈值: 0.0, 0.3, 0.5, 0.6, 0.7

### 1.4 评测指标

- **Pass rate**: 用例通过率 (recall=1.0 且 forbidden_hit=0)
- **Recall@K**: 期望记忆在 top-K 结果中的比例
- **Precision@K**: top-K 中相关结果占比
- **MRR**: 平均倒数排名
- **nDCG@K**: 归一化折损累计增益
- **forbidden_hits**: 不应出现的结果出现次数 (越低越好)
- **p50 latency**: 中位检索延迟 (ms)

## 二、评测结果

### 2.1 总览

| 模式 | Pass% | Recall | Precision | MRR | nDCG | p50(ms) |
|------|-------|--------|-----------|-----|------|---------|
| FTS5-only | 60.0% | 0.700 | 0.427 | 0.433 | 0.433 | 15 |
| Vector-only t=0.0 | 50.0% | 0.833 | 0.229 | 0.473 | 0.497 | 104 |
| Vector-only t=0.3 | 50.0% | 0.833 | 0.229 | 0.473 | 0.497 | 100 |
| **Vector-only t=0.5** | **86.7%** | **0.967** | 0.507 | **0.683** | **0.688** | 78 |
| Vector-only t=0.6 | 86.7% | 0.900 | 0.551 | 0.633 | 0.633 | 76 |
| Vector-only t=0.7 | 43.3% | 0.467 | 0.402 | 0.200 | 0.200 | 76 |
| Hybrid-RRF t=0.0 | 63.3% | 0.967 | 0.273 | 0.644 | 0.659 | 41 |
| Hybrid-RRF t=0.3 | 63.3% | 0.967 | 0.273 | 0.644 | 0.659 | 41 |
| **Hybrid-RRF t=0.5** | **86.7%** | **0.967** | 0.507 | **0.683** | **0.688** | 25 |
| Hybrid-RRF t=0.6 | 80.0% | 0.900 | 0.484 | 0.633 | 0.633 | 21 |
| Hybrid-RRF t=0.7 | 60.0% | 0.700 | 0.427 | 0.433 | 0.433 | 21 |

### 2.2 最优配置: Vector-only / Hybrid-RRF t=0.5

两者指标完全一致, 但 Hybrid-RRF 延迟低 3.1 倍 (25ms vs 78ms):

- **Pass rate**: 86.7% (26/30 通过)
- **Recall@K**: 0.967 (29/30 用例 recall=1.0)
- **MRR**: 0.683
- **nDCG@K**: 0.688
- **forbidden_hits**: 0 (无权限泄漏)

### 2.3 分类别表现 (t=0.5 最优配置)

| 类别 | 用例数 | Pass | Recall | 说明 |
|------|--------|------|--------|------|
| semantic_paraphrase | 5 | 5/5 | 1.000 | bge-m3 中文同义改写全命中 |
| cross_language | 4 | 4/4 | 1.000 | bge-m3 多语言跨语言匹配全命中 |
| preference_recall | 3 | 3/3 | 1.000 | 偏好记忆全部召回 |
| semantic_synonym | 3 | 3/3 | 1.000 | 同义概念匹配全命中 |
| episodic_recall | 2 | 2/2 | 1.000 | 情节记忆召回全命中 |
| strategy_recall | 1 | 1/1 | 1.000 | 策略记忆召回命中 |
| no_result | 3 | 3/3 | 1.000 | t=0.5 正确过滤无关查询 |
| exact_identifier | 4 | 3/4 | 1.000 | 1 个 expect_empty 失败 (向量返回相似结果) |
| authorization | 3 | 2/3 | 1.000 | 1 个跨 session 失败 (同 tenant 内向量返回相似结果) |
| hard_negative | 2 | 0/2 | 0.500 | 否定区分失败 (向量无法区分"已关闭"vs"仍开启") |

### 2.4 FTS5-only 分类别表现

| 类别 | Pass | Recall | 说明 |
|------|------|--------|------|
| exact_identifier | 3/4 | 1.000 | 精确标识符检索强项 |
| preference_recall | 3/3 | 1.000 | 偏好记忆通过 memory_id 词面匹配 |
| episodic_recall | 2/2 | 1.000 | 情节记忆命中 |
| no_result | 3/3 | 1.000 | 正确返回空结果 |
| authorization | 2/3 | 1.000 | SQL WHERE 下推 ACL 隔离生效 |
| semantic_paraphrase | 3/5 | 0.600 | CJK bi-gram 部分匹配, 但改写查询词面重叠低 |
| semantic_synonym | 1/3 | 0.333 | 同义概念无词面重叠, FTS5 无法匹配 |
| cross_language | 1/4 | 0.250 | 跨语言无词面重叠, FTS5 基本失效 |
| strategy_recall | 0/1 | 0.000 | 英文 strategy 记忆与中文查询无词面重叠 |
| hard_negative | 0/2 | 0.500 | FTS5 无法区分否定语义 |

## 三、失败案例分析

### 3.1 hard_negative (0/2 通过)

- `neg_1_renewal_off`: 查询 "自动续费已经关闭", 期望同时命中 "自动续费已经关闭" 和 "自动续费已经关闭" (重复事件), 实际仅命中 1 条
- `neg_2_renewal_on`: 查询 "自动续费仍然开启", 期望命中 2 条, 实际仅命中 1 条

**根因**: 两条事件 "自动续费已经关闭" 和 "自动续费仍然开启" 在向量空间高度相似 (仅末尾 2 字不同), bge-m3 无法区分否定语义。FTS5 也只能命中其中词面完全匹配的一条。这是已知限制, 需要专门训练的 NLI 模型或规则引擎处理。

### 3.2 authorization (2/3 通过)

- `auth_3_cross_session` 失败: 查询 "SESSION-6633", 期望返回空 (该记忆在 other-session 中, session_policy=PROFILE 应过滤), 但向量返回了同 tenant 的其他记忆

**根因**: 向量检索的 SQL WHERE 子句对 PROFILE 策略生成 `(session_id = ? OR layer != ?)`, 当记忆 layer=working 且 session_id 不匹配时会被过滤。但同 tenant 的其他 working 记忆如果 session_id 匹配查询 session, 仍会被返回。这是向量检索的固有行为 — 余弦相似度总会返回结果。提高阈值至 0.6 可解决此问题 (pass=86.7%, recall=0.900)。

### 3.3 exact_identifier (3/4 通过)

- `exact_4_session` 失败: 与 auth_3 相同的 expect_empty 用例, 向量返回了相似结果

**根因**: 同 auth_3, 向量无法区分"应该返回空"的场景。

## 四、阈值敏感性分析

| 阈值 | Vector Pass% | Hybrid Pass% | Vector Recall | Hybrid Recall |
|------|-------------|-------------|---------------|---------------|
| 0.0 (无阈值) | 50.0% | 63.3% | 0.833 | 0.967 |
| 0.3 | 50.0% | 63.3% | 0.833 | 0.967 |
| 0.5 | **86.7%** | **86.7%** | **0.967** | **0.967** |
| 0.6 | 86.7% | 80.0% | 0.900 | 0.900 |
| 0.7 | 43.3% | 60.0% | 0.467 | 0.700 |

**关键发现**:
- t=0.0~0.3: 低阈值导致 false positive 过多, no_result 和 authorization 用例大量失败
- t=0.5: 最优阈值, false positive 被过滤, recall 保持在 0.967
- t=0.6: 开始丢失部分有效结果 (recall 降至 0.900)
- t=0.7: 阈值过高, 大量有效结果被过滤 (recall 降至 0.467)

**bge-m3 最优阈值 = 0.5**, 与之前设计文档中的建议一致。

## 五、FTS5 vs Vector vs Hybrid 对比

| 维度 | FTS5-only | Vector-only (t=0.5) | Hybrid-RRF (t=0.5) |
|------|-----------|---------------------|---------------------|
| 精确标识符 | 强 | 弱 (无法精确匹配标识符) | 中 (RRF 稀释了 FTS5 匹配) |
| 语义改写 | 弱 (0.600 recall) | 强 (1.000 recall) | 强 (1.000 recall) |
| 跨语言 | 极弱 (0.250 recall) | 强 (1.000 recall) | 强 (1.000 recall) |
| 无结果校准 | 强 (3/3) | 强 (3/3) | 强 (3/3) |
| 权限隔离 | 强 (SQL 下推) | 强 (SQL JOIN pre-filter) | 强 (SQL JOIN pre-filter) |
| 否定区分 | 弱 (0/2) | 弱 (0/2) | 弱 (0/2) |
| 延迟 (p50) | 15ms | 78ms | 25ms |

**核心结论**: Hybrid-RRF t=0.5 在语义检索能力上与 Vector-only t=0.5 完全一致, 但延迟低 3.1 倍。FTS5 leg 在 t=0.5 时未额外贡献召回率 (vector 已覆盖所有语义匹配场景), 但 FTS5 在精确标识符场景仍有不可替代的价值。

## 六、与简历指标对比

| 简历声明 | 实测结果 | 判定 |
|---------|---------|------|
| "100% recall" | FTS5-only 4 case recall=1.0; Vector/Hybrid t=0.5 全 30 case recall=0.967 | 部分 true (FTS5 仅 4 case; 完整 30 case 为 0.967) |
| "90% pass" | 不存在此指标; 实测最优 pass=86.7% | fabricated (90% 不存在) |
| "token 725-886" | 不存在; 实测未测量 token 数 | fabricated |
| "bge-m3 Recall=1.0, Neg_acc=3/4" | Recall@t=0.5=0.967; Neg_acc(否定区分)=0/2 | fabricated (Neg_acc 实际为 0/2) |

## 七、结论

1. **bge-m3 t=0.5 是最优配置**: pass=86.7%, recall=0.967, MRR=0.683, nDCG=0.688
2. **Hybrid-RRF t=0.5 是最优部署方案**: 与 vector-only 指标一致但延迟低 3.1 倍 (25ms vs 78ms)
3. **bge-m3 多语言能力突出**: cross_language 4/4 全命中, semantic_paraphrase 5/5 全命中
4. **bge-m3 否定语义区分能力弱**: hard_negative 0/2, "已关闭" vs "仍开启" 无法区分
5. **FTS5 在精确标识符场景不可替代**: Vector 无法精确匹配 "ZX-49271" 等标识符
6. **ACL 10 层纵深有效**: forbidden_hits=0, 无权限泄漏
7. **t=0.5 阈值与设计文档建议一致**: 低于 0.5 false positive 过多, 高于 0.6 recall 下降
