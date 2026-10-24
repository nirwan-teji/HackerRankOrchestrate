"""CLI: python -m ghost.run [--dataset DIR] [--input CSV] [--output CSV] [--eval GOLD.csv]"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .data import Dataset
from .engine import Auditor
from .pipeline import audit_case, build_graph
from .scoring import load, score
from .steps import to_json
from .validate import case_key


def run(dataset: Path, input_csv: str, output: Path | None = None, verbose=False):
    ds = Dataset.load(dataset)
    sample, evalset = ds.requests("sample_deletion_requests.csv"), ds.requests("deletion_requests.csv")
    cases = ds.requests(input_csv)
    aud = Auditor(ds, sample + evalset)
    graph = build_graph(aud)
    rows, results = [], {}
    for c in sorted(cases, key=lambda c: case_key(c["case_id"])):
        r = audit_case(graph, c["case_id"])
        results[c["case_id"]] = r
        rows.append({"case_id": c["case_id"], "deletion_status": r.status, "steps_json": json.dumps(to_json(r.steps), separators=(",", ":"))})
    if output:
        with open(output, "w", newline="") as f:
            w = csv.DictWriter(f, ["case_id", "deletion_status", "steps_json"], lineterminator="\n")  # LF, like the official template
            w.writeheader(); w.writerows(rows)
    return aud, results, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(Path(__file__).resolve().parents[2] / "dataset"))
    ap.add_argument("--input", default="deletion_requests.csv")
    ap.add_argument("--output")
    ap.add_argument("--eval", help="gold CSV to score against")
    a = ap.parse_args()
    aud, results, rows = run(Path(a.dataset), a.input, Path(a.output) if a.output else None)
    if a.eval:
        pred = {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in rows}
        s = score(pred, load(a.eval))
        print(json.dumps({k: v for k, v in s.items() if k != "per_case"}, indent=2))
        for c, d in s["per_case"].items():
            if not d["exact"]:
                print(c, "status" if not d["status_ok"] else "", "MISSING", [(o[:12], r) for o, _, r in d["missing"]], "EXTRA", [(o[:12], r) for o, _, r in d["extra"]])


if __name__ == "__main__":
    main()
