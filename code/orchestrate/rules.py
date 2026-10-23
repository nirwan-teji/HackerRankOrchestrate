"""Deterministic, auditable decision layer.

Pattern from the top finishers: the LLM returns structured *facts*; an ordered
rule table turns facts into final labels. Every output carries the rule id that
produced it, so the AI-judge interview can trace any answer.
"""
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Rule:
    id: str
    when: Callable[[dict], bool]
    then: dict[str, Any]
    why: str


class RuleEngine:
    def __init__(self, rules: list[Rule], default: dict[str, Any]):
        self.rules, self.default = rules, default

    def decide(self, facts: dict) -> dict:
        for r in self.rules:  # first match wins -> precedence = list order
            if r.when(facts):
                return {**r.then, "rule_id": r.id, "reason": r.why}
        return {**self.default, "rule_id": "default", "reason": "no rule matched"}
