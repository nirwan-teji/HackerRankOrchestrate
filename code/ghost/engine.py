"""Per-case audit: link resources to the subject, reconstruct state at audit_at, apply policy, emit steps."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .data import Dataset, canon
from .identity import IdentityIndex, Subject
from . import intent as intent_mod
from .linking import assign_events
from .model import Obs, category_of, rid_prefix, system_of
from .steps import Step, build_steps, plan_for

TS = "%Y-%m-%dT%H:%M:%SZ"


def P(s: str) -> datetime:
    return datetime.strptime(s, TS)


BAD = {"PRESENT", "EXPOSED", "ENABLED", "RESTORABLE"}
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
                        out.setdefault(rid, "catalog")
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
        raw = assign_events(self.ds.activity, cases, self.direct, clean, anchor, set(self.obs_by_rid))
        self.assigned = {c: self._plausible(c, links) for c, links in raw.items()}

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
            cands = [o for o in obs if o.state == state and self.citable(o, audit_at)] or [o for o in obs if self.citable(o, audit_at)]
            ev = max(cands, key=lambda o: (o.t, o.file_id)).file_id if cands else ""
            return ResourceState(rid, cat, system, state, ev)
        # no explicit lookup: infer absence from the latest populated snapshot that covers the system
        best = None
        for fid, sc in self.ds.scopes.items():
            ct = P(sc["systems"].get(system, "1970-01-01T00:00:00Z")) if system in sc.get("systems", {}) else None
            if ct and sc.get("rids") and reported_at < ct <= audit_at and P(sc["captured_at"]) <= audit_at:
                if rid not in sc["rids"] and (best is None or ct > best[0]):
                    best = (ct, fid)
        if best and cat in ("USER_PROFILE", "SEARCH_INDEX_ENTRY", "USER_EXPORT"):
            return ResourceState(rid, cat, system, "ABSENT", best[1])
        return ResourceState(rid, cat, system, "UNKNOWN", "")

    # ------------------------------------------------------------------ audit
    def audit(self, cid: str) -> CaseResult:
        case, sub = self.cases[cid], self.subj[cid]
        res = CaseResult(cid, "INCOMPLETE", subject=sub)
        if self.intent[cid] != "DELETE":
            res.status = "NOT_APPLICABLE"
            res.notes.append("request does not authorise deletion")
            return res
        if sub is None:
            res.notes.append("identity unresolved")
            return res
        audit_at, reported_at = P(case["audit_at"]), P(case["reported_at"])
        links = {**self.assigned.get(cid, {}), **self.direct[cid]}
        via = {r: ("activity" if r in self.assigned.get(cid, {}) and r not in self.direct[cid] else "direct") for r in links}
        if not any(rid_prefix(r) == "account" for r in links):  # family-A convention: user_N -> account_00N
            m = re.fullmatch(r"user_(\d+)", sub.user_id)
            if m and int(m.group(1)) <= 10:
                rid = f"account_{int(m.group(1)):03d}"
                links[rid], via[rid] = "", "convention"
        for rid, ev in sorted(links.items()):
            st = self.state_of(rid, audit_at, reported_at, P(case["requested_at"]))
            st.via = via[rid]
            if not st.evidence and ev:
                st.evidence = ev
            res.resources.append(st)
        res.steps, res.status = build_steps(res.resources)
        return res
