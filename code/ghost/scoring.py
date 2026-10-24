"""Compare predictions with a reference output: status, step-, field- and sequence-level accuracy."""
from __future__ import annotations

import csv
import json

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load(path) -> dict[str, tuple[str, list[dict]]]:
    return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(open(path))}


def score(pred: dict, gold: dict) -> dict:
    n = len(gold)
    status = sum(pred.get(c, ("", []))[0] == g[0] for c, g in gold.items())
    exact = seq = 0
    step_tp = step_gold = step_pred = 0
    field_hits = {f: 0 for f in FIELDS}
    field_total = 0
    per_case = {}
    for c, (gs, gsteps) in gold.items():
        ps, psteps = pred.get(c, ("", []))
        key = lambda s: (s["operation"], s["system_id"], s["resource_id"])
        gk, pk = [key(s) for s in gsteps], [key(s) for s in psteps]
        tp = len(set(gk) & set(pk))
        step_tp += tp; step_gold += len(gk); step_pred += len(pk)
        seq += pk == gk
        exact += psteps == gsteps
        for g in gsteps:
            field_total += 1
            p = next((x for x in psteps if key(x) == key(g)), None)
            if p:
                for f in FIELDS:
                    field_hits[f] += p[f] == g[f]
        per_case[c] = {"status_ok": ps == gs, "seq_ok": pk == gk, "exact": psteps == gsteps,
                       "missing": sorted(set(gk) - set(pk)), "extra": sorted(set(pk) - set(gk))}
    prec = step_tp / step_pred if step_pred else 1.0
    rec = step_tp / step_gold if step_gold else 1.0
    return {
        "cases": n, "status_accuracy": status / n, "exact_case_match": exact / n, "sequence_accuracy": seq / n,
        "step_precision": prec, "step_recall": rec, "step_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "field_accuracy": {f: (field_hits[f] / field_total if field_total else 1.0) for f in FIELDS},
        "per_case": per_case,
    }
