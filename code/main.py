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
    errs = validate_rows(rows, [r["case_id"] for r in rows])
    if errs:
        print("OUTPUT CONTRACT VIOLATIONS:", *errs[:20], sep="\n  ")
        return 1
    by = {}
    for r in rows:
        by[r["deletion_status"]] = by.get(r["deletion_status"], 0) + 1
    print(f"wrote {len(rows)} rows to {a.output}: {by}")
    if a.eval:
        pred = {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in rows}
        s = score(pred, load(a.eval))
        print(json.dumps({k: v for k, v in s.items() if k != "per_case"}, indent=2))
    try:
        from orchestrate.llm import USAGE

        print("LLM usage:", json.dumps(USAGE.report()))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
