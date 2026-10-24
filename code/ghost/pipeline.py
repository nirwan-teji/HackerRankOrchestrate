"""The per-case audit agent as a LangGraph state machine.

  classify_intent -> resolve_identity -> link_resources -> reconstruct_state -> plan_steps -> review_plan (optional, advisory)
        |                  |
        +-- NOT_APPLICABLE +-- unresolved identity (no authorised, attributable subject: nothing to audit)

Every node is a thin, individually testable function over the shared Auditor; the LLM (optional) only appears in
classify_intent. Everything downstream is deterministic code.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

import os

from . import review as review_mod
from .engine import Auditor, CaseResult


class AuditState(TypedDict, total=False):
    cid: str
    intent: str
    links: dict
    result: CaseResult


def build_graph(aud: Auditor):
    def classify_intent(s: AuditState) -> AuditState:
        return {"intent": aud.intent[s["cid"]]}

    def resolve_identity(s: AuditState) -> AuditState:
        cid = s["cid"]
        res = CaseResult(cid, "INCOMPLETE", subject=aud.subj[cid])
        if res.subject is None:
            res.notes.append("identity unresolved")
        return {"result": res}

    def link_resources(s: AuditState) -> AuditState:
        return {"links": aud.stage_links(s["cid"])}

    def reconstruct_state(s: AuditState) -> AuditState:
        res = s["result"]
        res.resources = aud.stage_states(s["cid"], s["links"])
        return {"result": res}

    def plan_steps(s: AuditState) -> AuditState:
        res = s["result"]
        res.steps, res.status, res.resources = aud.stage_plan(res.resources)
        return {"result": res}

    budget = {"used": 0}

    def review_plan(s: AuditState) -> AuditState:
        """Advisory LLM review of risky plans. Flags only: it can never alter the status or the steps."""
        res = s["result"]
        if review_mod.enabled() and review_mod.in_scope(aud, res):
            cap = int(os.getenv("GHOST_REVIEW_MAX", "0") or 0)
            if cap and budget["used"] >= cap:
                res.review = review_mod.Review(res.case_id, "skipped", reason="review cap reached")
            else:
                budget["used"] += 1
                res.review = review_mod.review_case(aud, res)
        return {"result": res}

    def not_applicable(s: AuditState) -> AuditState:
        res = CaseResult(s["cid"], "NOT_APPLICABLE", subject=aud.subj[s["cid"]])
        res.notes.append("request does not authorise deletion")
        return {"result": res}

    g = StateGraph(AuditState)
    for name, fn in [("classify_intent", classify_intent), ("not_applicable", not_applicable),
                     ("resolve_identity", resolve_identity), ("link_resources", link_resources),
                     ("reconstruct_state", reconstruct_state), ("plan_steps", plan_steps), ("review_plan", review_plan)]:
        g.add_node(name, fn)
    g.set_entry_point("classify_intent")
    g.add_conditional_edges("classify_intent", lambda s: "resolve_identity" if s["intent"] == "DELETE" else "not_applicable")
    g.add_conditional_edges("resolve_identity", lambda s: "link_resources" if s["result"].subject else END)
    g.add_edge("link_resources", "reconstruct_state")
    g.add_edge("reconstruct_state", "plan_steps")
    g.add_edge("plan_steps", "review_plan")
    g.add_edge("review_plan", END)
    g.add_edge("not_applicable", END)
    return g.compile()


def audit_case(graph: Any, cid: str) -> CaseResult:
    return graph.invoke({"cid": cid})["result"]
