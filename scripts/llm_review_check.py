"""Try the LLM reviewer on the public samples:  python scripts/llm_review_check.py [--all]

The samples are the only cases with known answers, so this shows what the reviewer does on plans that are right (it should
agree) and on the three the engine gets wrong (case_18, case_20, case_23). Nothing here changes any output.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
os.environ["GHOST_LLM_REVIEW"] = "1"
os.environ["GHOST_REVIEW_SCOPE"] = "all" if "--all" in sys.argv else os.environ.get("GHOST_REVIEW_SCOPE", "risky")

from ghost import review as R  # noqa: E402
from ghost.run import run  # noqa: E402

KNOWN_WRONG = {"case_18", "case_20", "case_23"}  # plans that differ from the public answer key


def main() -> int:
    aud, results, _ = run(ROOT / "dataset", "sample_deletion_requests.csv")
    todo = {c: r for c, r in results.items() if R.in_scope(aud, r)}
    print(f"{len(todo)} of {len(results)} sample cases in scope ({os.environ['GHOST_REVIEW_SCOPE']})\n")
    out = R.review_all(aud, todo)
    if any(v.verdict == "error" for v in out.values()):
        print("model call failed:", next(v.reason for v in out.values() if v.verdict == "error"), "- run scripts/llm_selftest.py first")
        return 1
    flagged = {c for c, v in out.items() if v.verdict == "concerns"}
    for c, v in sorted(out.items()):
        tag = "  <-- plan known to differ from the key" if c in KNOWN_WRONG else ""
        print(f"{c}: {v.verdict}{tag}")
        for i in v.issues:
            print(f"     [{i['kind']}] {i['resource_id']}: {i['explanation']}")
    right = [c for c in out if c not in KNOWN_WRONG]
    print(f"\nflagged on plans that match the key: {len(flagged & set(right))} of {len(right)} (false alarms)")
    print(f"flagged on the {len(KNOWN_WRONG & set(out))} plans that differ from the key: {len(flagged & KNOWN_WRONG)}")
    print("note: the three known misses are an unobservable record and an ambiguous tie, which a reviewer cannot see either.")
    from orchestrate.llm import USAGE

    print("usage:", json.dumps(USAGE.report()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
