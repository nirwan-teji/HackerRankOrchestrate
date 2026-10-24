"""Where does the LLM disagree with the intent rules?  python scripts/llm_intent_check.py

Classifies every DISTINCT request text in both request files with the configured model, compares with the rule-based
label and lists every disagreement. Decide GHOST_LLM_INTENT_ALL from this: if there are no disagreements the model adds
nothing but cost; if there are, read each one and decide who is right. Results are cached, so re-runs are free.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from ghost import intent  # noqa: E402
from ghost.data import Dataset  # noqa: E402


def main() -> int:
    ds = Dataset.__new__(Dataset)
    ds.root = ROOT / "dataset"
    texts: dict[str, list[str]] = {}
    for name in ("sample_deletion_requests.csv", "deletion_requests.csv"):
        for r in Dataset.requests(ds, name):
            texts.setdefault(r["request_text"], []).append(r["case_id"])
    print(f"{sum(len(v) for v in texts.values())} requests, {len(texts)} distinct texts\n")
    bad = unsure = 0
    for t, cids in sorted(texts.items(), key=lambda kv: kv[1][0]):
        rule, sure = intent.rule_intent(t)
        unsure += not sure
        try:
            llm = intent.llm_intent(t)
        except Exception as e:
            print(f"model call failed ({type(e).__name__}: {str(e)[:120]}); run scripts/llm_selftest.py first")
            return 1
        if llm != rule:
            bad += 1
            print(f"DISAGREE rule={rule} (sure={sure}) llm={llm}  cases={cids[:4]}\n   {t[:230]}\n")
    print(f"disagreements: {bad} of {len(texts)} distinct texts; rule-unsure texts: {unsure}")
    from orchestrate.llm import USAGE

    print("usage:", json.dumps(USAGE.report()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
