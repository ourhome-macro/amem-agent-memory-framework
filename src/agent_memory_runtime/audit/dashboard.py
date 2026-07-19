from __future__ import annotations

import html
import json
from collections import Counter

from agent_memory_runtime.audit.envelope import AuditEnvelope


def generate_audit_dashboard_html(envelopes: list[AuditEnvelope]) -> str:
    records = [envelope.to_dict() for envelope in envelopes]
    type_counts = Counter(str(record["audit_type"]) for record in records)
    outcome_counts = Counter(str(record["outcome"]) for record in records)
    decision_counts = Counter(str(record["decision"]) for record in records)
    rows = "\n".join(_row(record) for record in records)
    payload_json = html.escape(json.dumps(records, ensure_ascii=True, indent=2, sort_keys=True))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Agent Memory Runtime 审计面板</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #f7f8fa; color: #1f2937; }}
    h1 {{ margin: 0 0 16px; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .card {{ background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 12px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; background: white; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 8px; text-align: left; }}
    th {{ background: #eef2f7; }}
    pre {{
      white-space: pre-wrap; background: #111827; color: #e5e7eb;
      padding: 12px; border-radius: 8px;
    }}
  </style>
</head>
<body>
  <h1>Agent Memory Runtime 审计面板</h1>
  <div class="cards">
    {_card("审计类型", type_counts)}
    {_card("结果", outcome_counts)}
    {_card("决策", decision_counts)}
  </div>
  <table>
    <thead>
      <tr>
        <th>时间</th><th>类型</th><th>动作</th><th>结果</th><th>决策</th><th>对象</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>原始审计 JSON</h2>
  <pre>{payload_json}</pre>
</body>
</html>
"""


def _card(title: str, counts: Counter[str]) -> str:
    items = "".join(f"<li>{html.escape(key)}: {value}</li>" for key, value in counts.items())
    return f'<section class="card"><h2>{html.escape(title)}</h2><ul>{items}</ul></section>'


def _row(record: dict[str, object]) -> str:
    subject = dict(record.get("subject", {}))
    subject_label = f"{subject.get('subject_type')}:{subject.get('subject_id')}"
    return (
        "<tr>"
        f"<td>{html.escape(str(record.get('occurred_at', '')))}</td>"
        f"<td>{html.escape(str(record.get('audit_type', '')))}</td>"
        f"<td>{html.escape(str(record.get('action', '')))}</td>"
        f"<td>{html.escape(str(record.get('outcome', '')))}</td>"
        f"<td>{html.escape(str(record.get('decision', '')))}</td>"
        f"<td>{html.escape(subject_label)}</td>"
        "</tr>"
    )
