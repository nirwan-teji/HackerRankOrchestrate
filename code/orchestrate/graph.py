"""LangGraph pipeline skeleton: guard -> retrieve -> extract -> decide -> validate.

Replace the node bodies per task; keep the shape (each node small and testable).
"""
from __future__ import annotations
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .guardrails import looks_like_injection


class State(TypedDict, total=False):
    row: dict
    flags: list[str]
    context: list
    facts: Any
    output: dict


def guard(s: State) -> State:
    text = " ".join(str(v) for v in s["row"].values())
    return {"flags": ["injection"] if looks_like_injection(text) else []}


def retrieve(s: State) -> State:
    return {"context": []}  # TODO: orchestrate.retrieval.Index.search(...)


def extract(s: State) -> State:
    return {"facts": {}}  # TODO: orchestrate.llm.ask(..., schema=Facts)


def decide(s: State) -> State:
    return {"output": {}}  # TODO: orchestrate.rules.RuleEngine.decide(facts)


def validate(s: State) -> State:
    return {}  # TODO: enum check; repair/escalate on failure


def build():
    g = StateGraph(State)
    for name, fn in [("guard", guard), ("retrieve", retrieve), ("extract", extract), ("decide", decide), ("validate", validate)]:
        g.add_node(name, fn)
    g.set_entry_point("guard")
    for a, b in [("guard", "retrieve"), ("retrieve", "extract"), ("extract", "decide"), ("decide", "validate")]:
        g.add_edge(a, b)
    g.add_edge("validate", END)
    return g.compile()
