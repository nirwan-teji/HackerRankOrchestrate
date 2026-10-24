"""Link identity-less activity events (account deletions, policy actions) to the case they belong to.

Evidence that names the subject (emails, user refs, catalog links) is applied first by the engine. What is left are
worker events with no identifier, e.g. `DELETE_RECORD account_service account_3866`. They are attached to cases by

  1. time window around the case's `reported_at` (account deletions land shortly before the report, policy
     actions shortly before or well after it);
  2. claim consistency: the workflow's reported status and the deletion result agree
     (COMPLETE <-> SUCCESS, PARTIALLY_COMPLETE <-> PARTIAL, NOT_STARTED <-> FAILED);
  3. elimination: events/cases already explained by direct evidence are removed; and
  4. a deterministic positional pairing (time order vs case_id order) for what is still ambiguous, but only when
     the number of events equals the number of cases that still need one.

Steps 1-3 are *derivations*: one case survives, so the link is grounded. Step 4 is a *guess* between equally
compatible candidates. Every step-4 pairing is recorded in `Ambiguity` so downstream code knows the link is unproven
and can route it to the LLM adjudicator (`adjudicate.py`) instead of trusting it silently.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .model import rid_prefix

TS = "%Y-%m-%dT%H:%M:%SZ"
P = lambda s: datetime.strptime(s, TS)

ACCOUNT_WINDOW = (timedelta(minutes=-85), timedelta(minutes=12))
POLICY_WINDOW = (timedelta(minutes=-32), timedelta(minutes=125))
RESULT_FOR_STATUS = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
WORKER_TYPES = {"DELETE_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD", "DISABLE_DATA_FLOW", "BLOCK_RESTORE",
                "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS"}
PREFIXES = ("account", "analytics", "support", "search", "export", "data_flow", "backup", "tombstone", "marketing")


@dataclass
class Ambiguity:
    """One group of interchangeable (event, case) pairings that had to be guessed positionally."""

    prefix: str
    case_ids: list[str]
    events: list[dict]
    chosen: dict[str, str] = field(default_factory=dict)  # case_id -> resource_id picked by the positional rule

    @property
    def resource_ids(self) -> list[str]:
        return [a["resource_id"] for a in self.events]


def assign_events(activity: list[dict], cases: dict[str, dict], direct: dict[str, dict[str, str]],
                  clean: dict[str, bool] | None = None, anchor: dict[str, set] | None = None,
                  observed: set | None = None,
                  ambiguities: list[Ambiguity] | None = None) -> dict[str, dict[str, str]]:
    """Return {case_id: {resource_id: activity_id}} for events that carry no identifier.

    Guessed pairings are appended to `ambiguities` (when a list is supplied) as `Ambiguity` records.
    """
    claimed = {rid for links in direct.values() for rid in links}
    events = [a for a in activity if a["source_system"] == "deletion_worker" and a["event_type"] in WORKER_TYPES
              and a["resource_id"] not in claimed]
    out: dict[str, dict[str, str]] = {cid: {} for cid in cases}
    used: set[str] = set()
    amb = ambiguities if ambiguities is not None else []
    for prefix in PREFIXES:
        evs = sorted((a for a in events if rid_prefix(a["resource_id"]) == prefix), key=lambda a: (a["event_time"], a["activity_id"]))
        if not evs:
            continue
        lo, hi = ACCOUNT_WINDOW if prefix == "account" else POLICY_WINDOW
        need = [cid for cid, c in sorted(cases.items()) if not any(rid_prefix(r) == prefix for r in direct[cid])]
        if prefix != "account" and anchor:  # cases with no documenting evidence only own events right around the report
            need = [cid for cid in need if anchor.get(cid) or any(abs(P(a["event_time"]) - P(cases[cid]["reported_at"])) <= timedelta(minutes=15) for a in evs)]

        def compatible(cid: str, a: dict) -> bool:
            c = cases[cid]
            if not (lo <= P(a["event_time"]) - P(c["reported_at"]) <= hi):
                return False
            if prefix != "account" and anchor is not None and not anchor.get(cid) and abs(P(a["event_time"]) - P(c["reported_at"])) > timedelta(minutes=15):
                return False
            if anchor and anchor.get(cid) and a["resource_id"] not in anchor[cid] and (observed is None or a["resource_id"] in observed):
                return False  # the resource is not in the evidence files that document this case
            if prefix == "account":
                return RESULT_FOR_STATUS.get(c["reported_status"]) == a["reported_result"]
            if clean is not None:  # early successful policy actions belong to cases that turn out clean, late/failed to dirty ones
                early = P(a["event_time"]) - P(c["reported_at"]) <= timedelta(minutes=5)
                return clean.get(cid, True) == early
            return True

        cand = {a["activity_id"]: [cid for cid in need if compatible(cid, a)] for a in evs}
        # accounts: every case has exactly one -> propagate forced pairs, then pair leftovers positionally
        if prefix == "account":
            _resolve_accounts(evs, need, cand, out, used, amb)
        else:
            _resolve_policy(evs, need, cand, out, used, amb)
    return out


def _take(out, used, cid, a):
    out[cid][a["resource_id"]] = a["activity_id"]
    used.add(a["activity_id"])


def _resolve_accounts(evs, need, cand, out, used, amb):
    ev_by_id = {a["activity_id"]: a for a in evs}
    free_cases = set(need)
    changed = True
    while changed:
        changed = False
        # an event with exactly one possible case, or a case with exactly one possible event, is forced
        for aid, cids in list(cand.items()):
            if aid in used:
                continue
            cids = [c for c in cids if c in free_cases]
            if len(cids) == 1 and sum(aid2 != aid and cids[0] in cand[aid2] for aid2 in cand if aid2 not in used) == 0:
                _take(out, used, cids[0], ev_by_id[aid]); free_cases.discard(cids[0]); changed = True
        for cid in sorted(free_cases):
            opts = [aid for aid, cids in cand.items() if cid in cids and aid not in used]
            if len(opts) == 1 and sum(c2 != cid and c2 in cand[opts[0]] for c2 in free_cases) == 0:
                _take(out, used, cid, ev_by_id[opts[0]]); free_cases.discard(cid); changed = True
    # leftovers: pair by order inside each connected component of the (event, case) compatibility graph
    left = [a for a in evs if a["activity_id"] not in used]
    seen: set[str] = set()
    for a in left:
        if a["activity_id"] in seen:
            continue
        comp_ev, comp_cs, todo = [], set(), [a]
        while todo:
            x = todo.pop()
            if x["activity_id"] in seen:
                continue
            seen.add(x["activity_id"]); comp_ev.append(x)
            for cid in cand[x["activity_id"]]:
                if cid in free_cases and cid not in comp_cs:
                    comp_cs.add(cid)
                    todo += [y for y in left if cid in cand[y["activity_id"]] and y["activity_id"] not in seen]
        cs = sorted(comp_cs)
        if len(comp_ev) == len(cs):
            ordered = sorted(comp_ev, key=lambda e: (e["event_time"], e["activity_id"]))
            rec = Ambiguity("account", cs, ordered) if len(cs) > 1 else None
            for cid, ev in zip(cs, ordered):
                _take(out, used, cid, ev); free_cases.discard(cid)
                if rec is not None:
                    rec.chosen[cid] = ev["resource_id"]
            if rec is not None:
                amb.append(rec)


def _resolve_policy(evs, need, cand, out, used, amb):
    # group events that are compatible with exactly the same set of cases; pair positionally when counts match
    groups = defaultdict(list)
    for a in evs:
        cids = tuple(cand[a["activity_id"]])
        if cids:
            groups[cids].append(a)
    for cids, group in groups.items():
        if len(group) == len(cids):
            rec = Ambiguity(rid_prefix(group[0]["resource_id"]), list(cids), list(group)) if len(cids) > 1 else None
            for cid, a in zip(cids, group):
                if a["activity_id"] not in used:
                    _take(out, used, cid, a)
                    if rec is not None:
                        rec.chosen[cid] = a["resource_id"]
            if rec is not None and rec.chosen:
                amb.append(rec)
