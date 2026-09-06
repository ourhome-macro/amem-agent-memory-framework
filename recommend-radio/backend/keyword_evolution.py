from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

from keyword_governance import KeywordGovernance
from profile_projector import _default_llm_client


EVOLUTION_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_keyword_evolution",
        "description": "Propose bounded keyword rewrites and new exploration hypotheses.",
        "parameters": {
            "type": "object",
            "properties": {
                "proposals": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["rewrite", "explore"]},
                            "query": {"type": "string", "minLength": 3, "maxLength": 180},
                            "canonicalSpec": {"type": "object"},
                            "parentKeywordId": {"type": "string"},
                            "reason": {"type": "string", "maxLength": 300},
                        },
                        "required": ["action", "query", "canonicalSpec", "reason"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["proposals"],
            "additionalProperties": False,
        },
    },
}


class KeywordEvolutionService:
    """LLM proposes hypotheses; deterministic governance remains the only writer."""

    def __init__(self, governance: KeywordGovernance, *, llm_client: Any | None = None) -> None:
        self.governance = governance
        self.llm_client = llm_client

    def run(self, *, blocked_topics: list[str] | None = None) -> dict[str, Any]:
        run_id = f"keyword-evolution:{uuid4().hex}"
        if os.getenv("RECOMMEND_LLM_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
            self.governance.record_evolution_run(status="skipped", error="llm_disabled", run_id=run_id)
            return {"executed": False, "reason": "llm_disabled", "accepted": 0, "rejected": 0}
        snapshot = self.governance.evolution_snapshot(limit=24)
        client = self.llm_client or _default_llm_client()
        response = client.complete_tool(
            system_prompt=(
                "You improve music discovery queries from measured performance. "
                "Affinity is user preference and Yield is marginal search supply. "
                "Only rewrite high-affinity low-yield keywords or propose genuinely new adjacent exploration. "
                "Do not repeat failed/retired families, do not change scores, and do not violate blocked topics. "
                "A rewrite must preserve the parent's canonical intent and include parentKeywordId. "
                "Return one tool call and at most four proposals."
            ),
            user_prompt=json.dumps(
                {
                    "performance": snapshot,
                    "blockedTopics": list(blocked_topics or []),
                    "policy": {
                        "rewrite": "affinity high and yield low",
                        "explore": "new family adjacent to anchors",
                        "neverRepeatRetiredFamily": True,
                    },
                },
                ensure_ascii=False,
            ),
            tools=[EVOLUTION_TOOL],
        )
        if response.name != "propose_keyword_evolution":
            self.governance.record_evolution_run(status="rejected", error="unexpected_tool", run_id=run_id)
            return {"executed": True, "reason": "unexpected_tool", "accepted": 0, "rejected": 0}
        proposals = response.arguments.get("proposals")
        values = [item for item in proposals if isinstance(item, dict)] if isinstance(proposals, list) else []
        blocked = [str(item).strip().casefold() for item in blocked_topics or [] if str(item).strip()]
        if blocked:
            values = [
                item
                for item in values
                if not any(term in json.dumps(item, ensure_ascii=False).casefold() for term in blocked)
            ]
        result = self.governance.register_evolution_proposals(values, evolution_run_id=run_id)
        self.governance.record_evolution_run(
            status="completed",
            proposed_count=len(values),
            accepted_count=int(result.get("accepted") or 0),
            run_id=run_id,
        )
        return {"executed": True, "proposed": len(values), **result}
