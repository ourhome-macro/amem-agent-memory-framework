# Auto Dream 结构化当前状态与平衡评测集

日期：2026-07-29

## 本次落地

本次把“需要语义裁决的状态问句”前移到 Auto Dream 的语义整理层：

- 新增 `semantic_state.current_state.v1` 结构化抽取。
- `MemoryProposal` 增加 `metadata`，Auto Dream 可以把结构化状态随 proposal 输出。
- `MemoryService.apply_proposal` 在事务写入 `MemoryRecord` 时持久化 proposal metadata，并让审计 after_record 保留同一份结构化状态。
- Auto Dream 在整理 active records 时按 `tenant_id/user_id/agent_id/subject_id/entity/attribute` 聚合同一当前状态。
- 同一实体同一属性出现相反当前状态时，Auto Dream 生成 `needs_review`，不自动覆盖高置信旧记忆。
- 检索最终过滤和 hard-negative 评分会优先读取结构化状态；没有结构化状态时才回退到文本状态规则。

结构化字段写入 `MemoryRecord.metadata`：

- `semantic_state_schema`
- `semantic_state_source`
- `semantic_state_entity`
- `semantic_state_entity_tokens`
- `semantic_state_attribute`
- `semantic_state_value`
- `semantic_state_temporal_scope`

## 边界

这不是把 embedding 当最终裁判。Qdrant/向量召回仍只负责候选，当前状态冲突由 Auto Dream 生成 proposal 并交给 `MemoryWritePolicy`、审核和版本检查链路处理。

确定性抽取只覆盖低风险状态词，例如开启/关闭、允许/禁止、成功/失败、已解决/未解决、已支付/未支付。内部出现同一属性多值时不会产出结构化状态，避免把含糊句子误写成当前事实。

## 平衡评测集

新增 `benchmarks/data/recall_250_balanced_v1.json`，默认 benchmark 路径切到该文件。原 `recall_250_v1.json` 仍保留为更偏 hard-negative 的压力集，holdout 仍独立保留。

balanced 组成：

- simple：100 条，来自原人工标注 `recall_100_v1`
- medium：70 条，包含 natural rewrite、cross-lingual、semantic paraphrase
- hard：65 条，包含 hard negative state、temporal shift、near entity/scope
- no-answer：15 条

总计：

- memories：237
- cases：250

## 验证

已通过：

- `py -3.12 -m ruff check src tests benchmarks`
- `py -3.12 -m pytest -q`

结果：

- `89 passed, 1 skipped`

BGE-M3 完整 benchmark 使用 balanced 数据集启动后超过 4 分钟超时，未产出完整报告；残留 Python 进程已停止。当前提交只声明代码测试和数据集结构验证通过，不声明新的 BGE-M3 指标。
