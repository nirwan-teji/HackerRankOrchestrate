"""Output contract checks: schema, row count/order, step numbering, phase/operation/state vocabulary and ordering."""
from __future__ import annotations

import csv
import json

from .steps import OP_ORDER, PHASE, STATE

import re

PHASE_ORDER = ["FINDING", "REMEDIATION", "VERIFICATION"]


def case_key(cid: str):
    """Natural order: case_2 < case_10 < case_100 (ids carry a numeric suffix)."""
    m = re.search(r"(\d+)$", cid)
    return (cid[: m.start()], int(m.group(1))) if m else (cid, -1)


CITABLE = re.compile(r"^(evidence|activity|audit)_\w+$")


def validate_rows(rows: list[dict], expected_ids: list[str], known_ids: set[str] | None = None) -> list[str]:
    """known_ids: every participant-visible file_id / activity_id / audit_id, when available."""
    errs: list[str] = []
    if [r["case_id"] for r in rows] != sorted(expected_ids, key=case_key):
        errs.append("rows are not exactly the input cases sorted by case_id")
    for r in rows:
        cid = r["case_id"]
        if r["deletion_status"] not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            errs.append(f"{cid}: bad status {r['deletion_status']}")
        try:
            steps = json.loads(r["steps_json"])
        except ValueError:
            errs.append(f"{cid}: steps_json is not JSON"); continue
        if r["deletion_status"] == "NOT_APPLICABLE" and steps != []:
            errs.append(f"{cid}: NOT_APPLICABLE must have []")
        keys = []
        for i, s in enumerate(steps, 1):
            if set(s) != {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}:
                errs.append(f"{cid}: step {i} has wrong fields")
            if s.get("step_no") != i:
                errs.append(f"{cid}: step_no not consecutive at {i}")
            if s.get("operation") not in OP_ORDER or PHASE[s["operation"]] != s["phase"] or STATE[s["operation"]] != s["state"]:
                errs.append(f"{cid}: step {i} operation/phase/state mismatch")
                continue
            keys.append((PHASE_ORDER.index(s["phase"]), OP_ORDER.index(s["operation"]), s["system_id"], s["resource_id"]))
            if r["deletion_status"] == "COMPLETE" and (s["phase"] != "VERIFICATION" or not s["evidence_id"]):
                errs.append(f"{cid}: COMPLETE steps must be evidence-backed verification only")
            if r["deletion_status"] == "INCOMPLETE" and s["phase"] != "FINDING" and s["evidence_id"]:
                errs.append(f"{cid}: planned step {i} must not cite evidence")
            if r["deletion_status"] == "INCOMPLETE" and s["phase"] == "FINDING" and not s["evidence_id"]:
                errs.append(f"{cid}: finding {i} lacks evidence")
            if s["evidence_id"] and not CITABLE.match(s["evidence_id"]):
                errs.append(f"{cid}: step {i} cites {s['evidence_id']!r}, which is not an evidence/activity/audit id")
            if s["evidence_id"] and known_ids is not None and s["evidence_id"] not in known_ids:
                errs.append(f"{cid}: step {i} cites unknown id {s['evidence_id']!r}")
        if keys != sorted(keys):
            errs.append(f"{cid}: steps are not in the mandated order")
    return errs


def validate_file(path, expected_ids: list[str]) -> list[str]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return validate_rows(rows, expected_ids)
