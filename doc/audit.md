# 审计设计

## 目标

审计系统的目标是记录结构化证据链，而不是保存 prompt、用户原文、召回记忆正文或模型回答。
每一次写入最小化、访问控制、上下文投影、模型调用和后续内容审核，都应能回答：

- 谁发起了动作。
- 审查对象是什么。
- 命中了什么规则或策略。
- 决策是放行、拦截、脱敏、复核还是隔离。
- 当时的 `rule_version`、`config_hash`、`last_event_sequence` 和 `state_hash` 是什么。
- 是否能用同一事件序列回放并复现。

## 统一外壳

所有审计记录统一序列化为 `AuditEnvelope`：

```text
audit_type
trace_id
occurred_at
actor_id
action
outcome
decision
subject
rule_version
config_hash
last_event_sequence
state_hash
payload
```

`subject` 使用 `AuditSubject` 表达被审查对象，例如：

```text
event:evt-1
memory:episodic:s1:evt-1
query:<query_hash_prefix>
llm_call:<response_id_or_trace_id>
```

`decision` 统一为：

```text
allow
block
redact
review
quarantine
observe
```

## 当前审计类型

### `pii`

`runtime.ingest` 在事件写入成功后记录 PII/凭据检测证据。记录字段路径、PII 类型、值 hash 和动作，
不保存原始值。

示例：

```json
{
  "audit_type": "pii",
  "decision": "redact",
  "subject": {"subject_type": "event", "subject_id": "evt-sensitive-1"},
  "payload": {
    "finding_count": 2,
    "findings": [
      {
        "field_path": "payload.card_number",
        "pii_type": "card_number",
        "value_hash": "..."
      }
    ]
  }
}
```

### `access`

`retrieve`、`project`、`project_fast` 和 `respond_stream` 的上下文构建阶段会记录访问控制审计。
记录已选记忆 ID、阻止数量、阻止原因、上下文来源和是否检索超时，不保存查询原文。

### `llm_call`

模型调用审计由 `LLMCallTrace` 生成，并包装为 `AuditEnvelope`。记录 provider、model、token 用量、
响应 ID、选中记忆 ID、阻止数量和回放定位字段。

除原有 `request_hash`、`response_hash` 外，metadata 还包含：

```text
system_prompt_hash
memory_context_hash
user_query_hash
selected_memory_ids
context_source
first_token_ms
```

### `moderation`

`ModerationTrace` 已定义为后续三层内容审核的审计骨架。当前版本不接真实敏感词、URL、分类器或
LLM 审核，只提供统一记录格式，避免后续审核结果成为黑箱。

## 禁止落库

普通审计日志禁止保存：

- prompt 原文
- 用户查询原文
- 召回记忆正文
- 模型回答正文
- API key、Authorization、token、password、secret
- 供应商异常消息原文
- 未脱敏 PII

`audit/redaction.py` 是最后一道保险，会在 `AuditEnvelope.to_dict()` 时再次清洗敏感 key 和伪造的
`<memory-context>` 围栏标签。它不能替代上游最小化，只用于防止审计 payload 被误用。

## Store

审计 Store 已从 memory store 中拆出：

```text
src/agent_memory_runtime/audit/stores/
  base.py
  in_memory.py
  jsonl.py
  sqlite.py
```

旧路径仍然兼容：

```python
from agent_memory_runtime.memory.stores import JsonlAuditStore
```

新路径为：

```python
from agent_memory_runtime.audit.stores import JsonlAuditStore
```

JSONL Store 会把每条 `AuditEnvelope` 写入 `.amem/audit.jsonl`。SQLite Store 使用 `audit_envelopes`
表，并保留旧 `llm_call_traces` 表读取兼容。

## CLI 查询

```powershell
amem audit
amem audit --type llm_call
amem audit --type access
amem audit --type pii
amem audit --outcome blocked
amem audit --subject event:evt-1
```

CLI 默认输出统一 `audit_records`，同时保留 `llm_call_traces` 字段以兼容旧调试脚本。

## 已知边界

当前审计使用 SHA-256 指纹。它适合关联和回放校验，但不是加密；低熵输入可能被字典攻击。高安全
部署应替换为带密钥的 HMAC，并将审计存储放入受控介质，配置保留期、删除流程和读取审计。

当前版本没有实现加密 prompt capture。若未来需要调试 prompt 原文，应单独实现短期、加密、受权限
控制的 `PromptCaptureStore`，读取原文也必须产生审计记录。
