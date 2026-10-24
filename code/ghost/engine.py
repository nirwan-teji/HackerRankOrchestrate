"""Per-case audit: link resources to the subject, reconstruct state at audit_at, apply policy, emit steps."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .data import Dataset, canon
from .identity import IdentityIndex, Subject
from .model import Obs, category_of, rid_prefix, system_of
from .steps import Step, build_steps

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
    def usable(self, o: Obs, audit_at: datetime) -> bool:
        return P(o.t) <= audit_at and P(self.cap[o.file_id]) <= audit_at

    def direct_links(self, cid: str) -> dict[str, str]:
        sub, case = self.subj[cid], self.cases[cid]
        if not sub:
            return {}
        audit_at = P(case["audit_at"])
        out: dict[str, str] = {}
        for tok in sub.keys:
            for rid in self._key_index.get(tok, ()):
                if rid_prefix(rid) in ("user",):
                    continue
                if any(self.usable(o, audit_at) and tok in o.idents for o in self.obs_by_rid[rid]):
                    out[rid] = "direct"
        for a in self.ds.activity:  # data-catalog identity links
            if a["event_type"] == "LINK_RESOURCE_IDENTITY" and P(a["event_time"]) <= audit_at:
                if any(k in canon(a["details"]) for k in sub.emails) or re.search(rf"\b{sub.user_id}\b", a["details"]):
                    out[a["resource_id"]] = "catalog"
        return out

    def _link_all(self):
        for cid in self.cases:
            self.direct[cid] = self.direct_links(cid)
        claimed = defaultdict(set)  # rid -> cases that directly own it
        for cid, links in self.direct.items():
            for rid in links:
                claimed[rid].add(cid)
        # activity clusters: unclaimed worker events near reported_at are handed to cases lacking that resource kind
        by_anchor = defaultdict(list)
        for cid, c in self.cases.items():
            if self.subj[cid]:
                by_anchor[c["reported_at"]].append(cid)
        events = [a for a in self.ds.activity if a["event_type"] in WORKER_TYPES and a["source_system"] == "deletion_worker"]
        self.assigned = {cid: {} for cid in self.cases}
        for anchor, cids in by_anchor.items():
            t0 = P(anchor)
            near = [a for a in events if abs(P(a["event_time"]) - t0) <= WINDOW and a["resource_id"] not in claimed]
            groups = defaultdict(list)
            for a in near:
                groups[rid_prefix(a["resource_id"])].append(a)
            for prefix, evs in groups.items():
                evs.sort(key=lambda a: a["event_time"])
                lacking = sorted(c for c in cids if not any(rid_prefix(r) == prefix for r in self.direct[c]))
                if len(evs) == len(lacking):
                    for cid, a in zip(lacking, evs):
                        self.assigned[cid][a["resource_id"]] = a["activity_id"]

    # ------------------------------------------------------------------ state reconstruction
    def state_of(self, rid: str, audit_at: datetime, reported_at: datetime) -> ResourceState:
        cat, system = category_of(rid), system_of(rid)
        obs = [o for o in self.obs_by_rid.get(rid, []) if self.usable(o, audit_at)]
        if rid.startswith("support") and any(o.cat == "SUPPORT_ATTACHMENT" for o in obs):
            cat = "SUPPORT_ATTACHMENT"
        if obs:
            tmax = max(o.t for o in obs)
            top = [o for o in obs if o.t == tmax]
            top.sort(key=lambda o: (o.state not in BAD, o.file_id))
            return ResourceState(rid, cat, system, top[0].state, top[0].file_id)
        # no explicit lookup: infer absence from the latest populated snapshot that covers the system
        best = None
        for fid, sc in self.ds.scopes.items():
            ct = P(sc["captured_at"])
            if system in sc.get("systems", ()) and sc.get("rids") and reported_at < ct <= audit_at:
                if rid not in sc["rids"] and (best is None or ct > best[0]):
                    best = (ct, fid)
        if best and cat in ("USER_PROFILE", "SEARCH_INDEX_ENTRY", "USER_EXPORT"):
            return ResourceState(rid, cat, system, "ABSENT", best[1])
        return ResourceState(rid, cat, system, "UNKNOWN", "")

    # ------------------------------------------------------------------ audit
    def audit(self, cid: str) -> CaseResult:
        case, sub = self.cases[cid], self.subj[cid]
        res = CaseResult(cid, "INCOMPLETE", subject=sub)
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
            st = self.state_of(rid, audit_at, reported_at)
            st.via = via[rid]
            if not st.evidence and ev:
                st.evidence = ev
            res.resources.append(st)
        res.steps, res.status = build_steps(res.resources)
        return res
