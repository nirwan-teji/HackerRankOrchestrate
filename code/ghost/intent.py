"""Does the request actually authorise deletion?  (NOT_APPLICABLE otherwise.)

Two layers, both deterministic given a cache:
  1. rules  - multilingual deletion verbs vs explicit non-deletion / negation cues (fast, free, covers templated text)
  2. LLM    - optional (GHOST_LLM_INTENT=1): the LangChain model classifies each *distinct* request text once and may
              overrule the rules only when the rules were unsure. Results are cached on disk (orchestrate.llm cache).
A data-catalog `CLASSIFY_REQUEST ... as non-deletion` activity on a resource observed with the subject's identifier is a
third, independent signal that is applied by the engine.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DELETE_RX = re.compile(
    r"\b(delet\w*|eras\w*|remov\w*|clos\w*|wipe\w*|destroy\w*|terminat\w*|forget|cancel\w*|discard\w*)\b"
    r"|suppress|supprim|effac|élimin|elimin|borr[ae]|cerrar|löschen|lösch|entfern|cancell|apag|exclu|удал|削除|삭제|删除|刪除|"
    r"إزالة|حذف|sil\b|verwijder|usu[ńn]",
    re.I,
)
NEGATION_RX = re.compile(
    r"(do not|don't|dont|not|never|no need to|without)\s+(\w+\s+){0,2}(delet|eras|remov|clos)|"
    r"not a request to (erase|delete|remove)|"
    r"(leave|keep)\s+(the\s+|my\s+|this\s+)?(account|profile|records?|purchase history)\b[^.]*\b(active|as it is|in place|intact|open)|"
    r"(still|continue to)\s+(use|using|want)|continue using|i still use|marketing lists only|"
    r"information request",
    re.I,
)
OTHER_RX = re.compile(
    r"\b(download|copy of|tell me what|what personal information|portable format|invoices?|"
    r"correction|correct (it|my|the (spelling|name|surname|address|number))|misspelled|replace it|out of date|change the (email|address|phone)|update my)\b",
    re.I,
)


def rule_intent(text: str) -> tuple[str, bool]:
    """-> (label DELETE|OTHER, confident)"""
    neg = bool(NEGATION_RX.search(text))
    has_del = bool(DELETE_RX.search(text))
    other = bool(OTHER_RX.search(text))
    if neg:
        return "OTHER", True
    if has_del and not other:
        return "DELETE", True
    if has_del and other:
        return "DELETE", False  # e.g. "delete my account and the export" vs "download ... don't delete" -> let the LLM check
    if other:
        return "OTHER", True
    # no deletion verb and no other-action cue (e.g. "confirm the profile is gone"): an audit request about a
    # deletion that was already requested -> treat as deletion context, but flag as unsure for the LLM layer
    return "DELETE", False


_CACHE: dict[tuple, str] = {}


def llm_intent(text: str) -> str:
    from orchestrate.llm import ask  # LangChain provider-agnostic client with disk cache and usage accounting
    from pydantic import BaseModel

    class Intent(BaseModel):
        authorises_deletion: bool
        reason: str

    system = (Path(__file__).resolve().parents[1] / "prompts" / "intent_system.txt").read_text().strip()
    r = ask(system, f"<request>\n{text}\n</request>", schema=Intent)
    return "DELETE" if r.authorises_deletion else "OTHER"


def classify(text: str, use_llm: bool | None = None) -> str:
    use = os.getenv("GHOST_LLM_INTENT") == "1" if use_llm is None else use_llm
    use_all = os.getenv("GHOST_LLM_INTENT_ALL") == "1"
    key = (text, use, use_all)  # the memo is per configuration: a rules-only run must never reuse an LLM label, or vice versa
    if key in _CACHE:
        return _CACHE[key]
    label, sure = rule_intent(text)
    if use and (not sure or use_all):
        try:
            label = llm_intent(text)
        except Exception:  # offline / no key: keep the rule decision
            pass
    _CACHE[key] = label
    return label
