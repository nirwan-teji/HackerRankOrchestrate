"""Ghost Data deletion-audit agent.

    python code/main.py                              # dataset/deletion_requests.csv -> output.csv (repo root)
    python code/main.py --dataset path/to/dataset --output out.csv
    python code/main.py --input sample_deletion_requests.csv --eval dataset/sample_output.csv

Deterministic by default. GHOST_LLM_INTENT=1 additionally asks the configured LangChain model (see .env.example)
to double-check ambiguous request texts; results are cached so re-runs are free and identical.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost.run import run  # noqa: E402
from ghost.scoring import load, score  # noqa: E402
from ghost.validate import validate_rows  # noqa: E402


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(root / "dataset"))
    ap.add_argument("--input", default="deletion_requests.csv", help="request CSV inside the dataset dir")
    ap.add_argument("--output", default=str(root / "output.csv"))
    ap.add_argument("--eval", help="reference CSV (e.g. dataset/sample_output.csv) to score against")
    a = ap.parse_args()

    aud, results, rows = run(Path(a.dataset), a.input, Path(a.output))
    known = ({m["file_id"] for m in aud.ds.manifest} | {x["activity_id"] for x in aud.ds.activity}
             | {x["audit_id"] for x in aud.ds.audit_log})
    errs = validate_rows(rows, [r["case_id"] for r in rows], known)
    if errs:
        print("OUTPUT CONTRACT VIOLATIONS:", *errs[:20], sep="\n  ")
        return 1
    by = {}
    for r in rows:
        by[r["deletion_status"]] = by.get(r["deletion_status"], 0) + 1
    print(f"wrote {len(rows)} rows to {a.output}: {by}")
    rv = [r.review for r in results.values() if r.review is not None]
    if rv:
        tally = {}
        for v in rv:
            tally[v.verdict] = tally.get(v.verdict, 0) + 1
        print(f"LLM reviewer (advisory, answers unchanged): {tally}")
        for v in rv:
            if v.verdict == "concerns":
                print(f"  {v.case_id}: " + "; ".join(f"[{i['kind']}] {i['resource_id']}" for i in v.issues))
    if a.eval:
        pred = {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in rows}
        s = score(pred, load(a.eval))
        print(json.dumps({k: v for k, v in s.items() if k != "per_case"}, indent=2))
    try:
        from orchestrate.llm import USAGE

        print("LLM usage:", json.dumps(USAGE.report()))
        if a.input == "deletion_requests.csv":
            _save_usage(USAGE.report(), rv)
    except Exception:
        pass
    return 0


def _save_usage(report: dict, reviews: list) -> None:
    """Persist this run's model usage: evaluation/run_usage.json. `run_eval.py` is a separate process, so it cannot read
    this process's counters; without the file the usage report would always say 0 calls. The cumulative block adds up the
    real (uncached) calls of every resumed run, because a cache-assisted run reuses answers paid for in an earlier one."""
    import os
    import time

    path = Path(__file__).resolve().parent / "evaluation" / "run_usage.json"
    try:
        prev = json.loads(path.read_text()).get("cumulative", {})
    except Exception:
        prev = {}
    cum = {k: prev.get(k, 0) + report[k] for k in ("model_calls", "input_tokens", "output_tokens")}
    tally: dict[str, int] = {}
    for v in reviews:
        tally[v.verdict] = tally.get(v.verdict, 0) + 1
    path.write_text(json.dumps({
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": os.getenv("LLM_PROVIDER", "groq"), "model": os.getenv("LLM_MODEL", ""),
        "intent_llm": os.getenv("GHOST_LLM_INTENT") == "1", "review_llm": os.getenv("GHOST_LLM_REVIEW") == "1",
        "this_run": {k: report[k] for k in ("model_calls", "cache_hits", "input_tokens", "output_tokens")},
        "cumulative": cum, "review_verdicts": tally,
    }, indent=1) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
