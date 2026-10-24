"""Per-case audit: link resources to the subject, reconstruct state at audit_at, apply policy, emit steps."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .data import Dataset, canon
from .identity import IdentityIndex, Subject
from . import adjudicate as adjudicate_mod
from . import intent as intent_mod
from .linking import Ambiguity, assign_events
from .model import Obs, category_of, rid_prefix, system_of
from .steps import Step, build_steps, plan_for

TS = "%Y-%m-%dT%H:%M:%SZ"


def P(s: str) -> datetime:
    return datetime.strptime(s, TS)


BAD = {"PRESENT", "EXPOSED", "ENABLED", "RESTORABLE"}
CITABLE = re.compile(r"^(evidence|activity|audit)_\w+$")  # only these id shapes may appear in evidence_id
WORKER_TYPES = {"DELETE_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD", "DISABLE_DATA_FLOW", "BLOCK_RESTORE",
                "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS"}
WINDOW = timedelta(minutes=130)


@dataclass
class ResourceState:
    rid: str
    cat: str
    system: str
    state: str  # PRESENT/EXPOSED/MASKED/ABSENT/ENABLED/DISABLED/RESTORABLE/BLOCKED/UNKNOWN
    evidence: str = ""  # file_id or activity_id proving the state ("" if none)
    via: str = ""  # how the resource was linked: direct | activity | catalog | convention


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: list[Step] = field(default_factory=list)
    subject: Subject | None = None
    resources: list[ResourceState] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    review: object = None  # ghost.review.Review (advisory only; never changes status or steps)


class Auditor:
    def __init__(self, ds: Dataset, all_cases: list[dict]):
        self.ds = ds
        self.ids = IdentityIndex(ds)
        self.cap = {m["file_id"]: m["captured_at"] for m in ds.manifest}
        self.cases = {c["case_id"]: c for c in all_cases}
        self.subj = {cid: self.ids.resolve(c["email"]) for cid, c in self.cases.items()}
        self.obs_by_rid: dict[str, list[Obs]] = defaultdict(list)
        for o in ds.obs:
            self.obs_by_rid[o.rid].append(o)
        self.act_by_rid: dict[str, list[dict]] = defaultdict(list)
        for a in ds.activity:
            self.act_by_rid[a["resource_id"]].append(a)
        self._key_index: dict[str, set] = defaultdict(set)  # identifier token -> rids observed with it
        for o in ds.obs:
            for tok in o.idents:
                self._key_index[tok].add(o.rid)
        self.direct: dict[str, dict[str, str]] = {}
        self.assigned: dict[str, dict[str, str]] = {}
        self._link_all()

    # ------------------------------------------------------------------ linking
    def usable(self, o: Obs, audit_at: datetime, requested_at: datetime | None = None) -> bool:
        """State gating uses the observation's own time (a bundle exported later may hold earlier snapshots)."""
        t = P(o.t)
        return t <= audit_at and (requested_at is None or t >= requested_at)

    def citable(self, o: Obs, audit_at: datetime) -> bool:
        """A file may only be cited if it was captured (manifest time) by audit_at."""
        return P(self.cap[o.file_id]) <= audit_at

    def direct_links(self, cid: str) -> dict[str, str]:
        sub, case = self.subj[cid], self.cases[cid]
        if not sub:
            return {}
        audit_at, requested_at = P(case["audit_at"]), P(case["requested_at"])
        out: dict[str, str] = {}
        for tok in sub.keys:
            for rid in self._key_index.get(tok, ()):
                if any(self.usable(o, audit_at, requested_at) and tok in o.idents for o in self.obs_by_rid[rid]):
                    out[rid] = "direct"
        for a in self.ds.activity:  # data-catalog identity links bind identity-less observations to the subject
            if a["event_type"] == "LINK_RESOURCE_IDENTITY" and P(a["event_time"]) <= audit_at:
                if any(k in canon(a["details"]) for k in sub.emails) or re.search(rf"\b{sub.user_id}\b", a["details"]):
                    rid = a["resource_id"]
                    if any(self.usable(o, audit_at, requested_at) for o in self.obs_by_rid.get(rid, [])):
                        out.setdefault(rid, a["activity_id"])  # the catalog link itself is a citable activity_id
        return out

    def _link_all(self):
        for cid in self.cases:
            self.direct[cid] = self.direct_links(cid)
        claimed = defaultdict(set)  # rid -> cases that directly own it
        for cid, links in self.direct.items():
            for rid in links:
                claimed[rid].add(cid)
        self.intent = {c: intent_mod.classify(x["request_text"]) for c, x in self.cases.items()}
        cases = {c: x for c, x in self.cases.items() if self.subj[c] and self.intent[c] == "DELETE"}
        clean = {c: not any(plan_for(self._state(c, r))[0] for r in self.direct[c]) for c in cases}
        anchor = {c: self._anchor_rids(c) for c in cases}
        self.ambiguities: list[Ambiguity] = []
        raw = assign_events(self.ds.activity, cases, self.direct, clean, anchor, set(self.obs_by_rid), self.ambiguities)
        self.assigned = {c: self._plausible(c, links) for c, links in raw.items()}
        self.llm_trail: list[dict] = []
        if adjudicate_mod.enabled() and self.ambiguities:
            try:
                swaps, self.llm_trail = adjudicate_mod.adjudicate(self, self.ambiguities)
            except Exception as e:  # never let the advisory layer break a deterministic run
                swaps, self.llm_trail = {}, [{"outcome": f"adjudication failed: {type(e).__name__}"}]
            for cid, moves in swaps.items():
                for old, new in moves.items():
                    act = self.assigned[cid].pop(old, "")
                    self.assigned[cid][new] = act
                    for a in self.ambiguities:
                        if a.chosen.get(cid) == old:
                            a.chosen[cid] = new
        # resources whose owner was guessed, not derived: {case_id: {resource_id: [interchangeable case_ids]}}
        self.guessed: dict[str, dict[str, list[str]]] = defaultdict(dict)
        for a in self.ambiguities:
            for cid, rid in a.chosen.items():
                if rid in self.assigned.get(cid, {}):
                    self.guessed[cid][rid] = a.case_ids

    def _anchor_rids(self, cid: str) -> set:
        """Resources listed in the latest evidence files that mention this subject directly."""
        c = self.cases[cid]
        at, rq = P(c["audit_at"]), P(c["requested_at"])
        files: dict[str, str] = {}
        for rid in self.direct[cid]:
            for o in self.obs_by_rid.get(rid, []):
                if self.usable(o, at, rq) and (set(o.idents) & self.subj[cid].keys):
                    files[o.file_id] = max(files.get(o.file_id, ""), o.t)
        if not files:
            return set()
        newest = max(files.values())
        top = {f for f, t in files.items() if P(newest) - P(t) <= timedelta(days=2)}
        return {o.rid for f in top for o in self.ds.obs if o.file_id == f}

    def _state(self, cid: str, rid: str) -> "ResourceState":
        c = self.cases[cid]
        return self.state_of(rid, P(c["audit_at"]), P(c["reported_at"]), P(c["requested_at"]))

    def _plausible(self, cid: str, links: dict[str, str]) -> dict[str, str]:
        """Drop activity-assigned resources that evidence ties to somebody else, or that have no evidence at all."""
        sub, c = self.subj[cid], self.cases[cid]
        keep = {}
        for rid, act in links.items():
            obs = [o for o in self.obs_by_rid.get(rid, []) if self.usable(o, P(c["audit_at"]), P(c["requested_at"]))]
            who = {t for o in obs for t in o.idents if "@" in t or t.startswith("+") or t.startswith("user_")}
            if who and not (who & sub.keys):
                continue  # evidence names a different person
            if not obs and rid_prefix(rid) != "account":
                continue  # nothing to cite or judge
            keep[rid] = act
        return keep

    # ------------------------------------------------------------------ state reconstruction
    def state_of(self, rid: str, audit_at: datetime, reported_at: datetime, requested_at: datetime) -> ResourceState:
        cat, system = category_of(rid), system_of(rid)
        obs = [o for o in self.obs_by_rid.get(rid, []) if self.usable(o, audit_at, requested_at)]
        explicit = [o.cat for o in obs if o.cat != category_of(rid)]
        if explicit:  # evidence typed the record more precisely than its id prefix (attachment, contact index, ...)
            cat = explicit[-1]
        if obs:
            tmax = max(o.t for o in obs)
            top = sorted((o for o in obs if o.t == tmax), key=lambda o: (o.state not in BAD, o.file_id))
            state = top[0].state
            # Citation is resolved independently of the observation that fixed the state: a file captured after
            # audit_at may establish the state (its lines predate the cutoff) but must never be cited.
            cands = [o for o in obs if o.state == state and self.citable(o, audit_at)] or [o for o in obs if self.citable(o, audit_at)]
            ev = max(cands, key=lambda o: (o.t, o.file_id)).file_id if cands else ""
            if not ev and state == "ABSENT":
                ev = self._absence_proof(rid, system, audit_at, reported_at) or ""
            if not ev:  # last resort: the worker event itself is a participant-visible activity_id
                acts = [a for a in self.act_by_rid.get(rid, []) if P(a["event_time"]) <= audit_at]
                ev = max(acts, key=lambda a: (a["event_time"], a["activity_id"]))["activity_id"] if acts else ""
            return ResourceState(rid, cat, system, state, ev)
        # no explicit lookup: infer absence from the latest populated snapshot that covers the system
        best = self._absence_proof(rid, system, audit_at, reported_at)
        if best and cat in ("USER_PROFILE", "SEARCH_INDEX_ENTRY", "USER_EXPORT"):
            return ResourceState(rid, cat, system, "ABSENT", best)
        return ResourceState(rid, cat, system, "UNKNOWN", "")

    def _absence_proof(self, rid: str, system: str, audit_at: datetime, reported_at: datetime) -> str | None:
        """A citable snapshot that covers `system`, postdates the report and does not list `rid`."""
        best = None
        for fid, sc in self.ds.scopes.items():
            ct = P(sc["systems"].get(system, "1970-01-01T00:00:00Z")) if system in sc.get("systems", {}) else None
            if ct and sc.get("rids") and reported_at < ct <= audit_at and P(sc["captured_at"]) <= audit_at:
                if rid not in sc["rids"] and (best is None or ct > best[0]):
                    best = (ct, fid)
        return best[1] if best else None

    # ------------------------------------------------------------------ audit stages (wired as a LangGraph in pipeline.py)
    def stage_links(self, cid: str) -> dict[str, tuple[str, str]]:
        """rid -> (how it was linked, activity/evidence hint). Direct identifier matches win over activity links."""
        sub = self.subj[cid]
        guessed = self.guessed.get(cid, {})
        links = {r: ("guess" if r in guessed else "activity", a) for r, a in self.assigned.get(cid, {}).items()}
        links.update({r: ("direct", "" if ev == "direct" else ev) for r, ev in self.direct[cid].items()})
        # a linked search document names the account it was indexed from (source_resource_id) -> that account is the subject's too
        c = self.cases[cid]
        for rid in list(links):
            for o in self.obs_by_rid.get(rid, []):
                src = o.attrs.get("source_resource_id")
                if src and self.usable(o, P(c["audit_at"]), P(c["requested_at"])) and src not in links:
                    links[src] = ("alias", o.file_id)
        if not any(rid_prefix(r) == "account" for r in links):  # user_N -> account_00N for the earliest accounts
            m = re.fullmatch(r"user_(\d+)", sub.user_id)
            if m and int(m.group(1)) <= 10:
                links[f"account_{int(m.group(1)):03d}"] = ("convention", "")
        return links

    def stage_states(self, cid: str, links: dict[str, tuple[str, str]]) -> list[ResourceState]:
        case = self.cases[cid]
        audit_at, reported_at, requested_at = P(case["audit_at"]), P(case["reported_at"]), P(case["requested_at"])
        out = []
        for rid, (via, hint) in sorted(links.items()):
            st = self.state_of(rid, audit_at, reported_at, requested_at)
            st.via = via
            if not st.evidence and CITABLE.match(hint or ""):
                st.evidence = hint
            out.append(st)
        return out

    @staticmethod
    def stage_plan(resources: list[ResourceState]) -> tuple[list[Step], str, list[ResourceState]]:
        _, status = build_steps(resources)
        if status == "COMPLETE":  # a deletion can only be declared complete from evidence: drop convention-only accounts nobody observed
            resources = [r for r in resources if not (r.state == "UNKNOWN" and not r.evidence)]
        steps, status = build_steps(resources)
        return steps, status, resources

    def audit(self, cid: str) -> CaseResult:
        """Straight-line equivalent of the LangGraph pipeline (kept for tests and scripts)."""
        res = CaseResult(cid, "INCOMPLETE", subject=self.subj[cid])
        if self.intent[cid] != "DELETE":
            res.status = "NOT_APPLICABLE"
            res.notes.append("request does not authorise deletion")
            return res
        if res.subject is None:
            res.notes.append("identity unresolved")
            return res
        res.resources = self.stage_states(cid, self.stage_links(cid))
        res.steps, res.status, res.resources = self.stage_plan(res.resources)
        return res
