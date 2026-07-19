from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.llm_trace import LLMCallTrace
from agent_memory_runtime.audit.stores.serialization import (
    envelope_from_dict_or_legacy,
    envelope_from_llm_trace,
    llm_trace_from_envelope,
)


class JsonlAuditStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append_envelope(self, envelope: AuditEnvelope) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope.to_dict(), ensure_ascii=True, sort_keys=True) + "\n")

    def list_envelopes(self) -> list[AuditEnvelope]:
        envelopes: list[AuditEnvelope] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    envelopes.append(envelope_from_dict_or_legacy(json.loads(line)))
        return envelopes

    def append_trace(self, trace: LLMCallTrace) -> None:
        self.append_envelope(envelope_from_llm_trace(trace))

    def list_traces(self) -> list[LLMCallTrace]:
        traces: list[LLMCallTrace] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload: dict[str, Any] = json.loads(line)
                if "audit_type" not in payload:
                    traces.append(LLMCallTrace.from_dict(payload))
                    continue
                envelope = AuditEnvelope.from_dict(payload)
                trace = llm_trace_from_envelope(envelope)
                if trace is not None:
                    traces.append(trace)
        return traces

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")
