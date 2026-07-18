from __future__ import annotations

from agent_memory_runtime.memory.derivation.builtin import builtin_rules
from agent_memory_runtime.memory.derivation.rule import DerivationRule


class DerivationRegistry:
    def __init__(self, rules: list[DerivationRule] | None = None) -> None:
        self._rules: dict[str, DerivationRule] = {}
        for rule in rules or builtin_rules():
            self.register(rule)

    def register(self, rule: DerivationRule) -> None:
        self._rules[rule.rule_id] = rule

    def list_rules(self) -> list[DerivationRule]:
        return list(self._rules.values())

    def rule_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._rules))

