"""Where does the LLM disagree with the intent rules?  python scripts/llm_intent_check.py

Classifies a deterministic sample of the DISTINCT request texts with the configured model (default 60 of the 398; use
--limit N, or --limit 0 for all of them) and lists every disagreement with the rule-based label. The sample always contains
every rule-unsure text and every text the rules call non-deletion, then is filled with an evenly spaced slice of the rest, so
it is the same sample on every run and costs ~60K tokens instead of ~400K. Results are cached, so re-runs are free.
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
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 60
    ordered = sorted(texts.items(), key=lambda kv: kv[1][0])
    must = [(t, c) for t, c in ordered if not intent.rule_intent(t)[1] or intent.rule_intent(t)[0] == "OTHER"]
    rest = [(t, c) for t, c in ordered if (t, c) not in must]
    room = max(limit - len(must), 0) if limit else len(rest)
    step = max(len(rest) // max(room, 1), 1)
    chosen = must + (rest[::step][:room] if limit else rest)
    print(f"{sum(len(v) for v in texts.values())} requests, {len(texts)} distinct texts; checking {len(chosen)} "
          f"({len(must)} always-included: rule-unsure or non-deletion)\n")
    bad = unsure = 0
    for t, cids in chosen:
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
    print(f"disagreements: {bad} of {len(chosen)} texts checked; rule-unsure among them: {unsure}")
    from orchestrate.llm import USAGE

    print("usage:", json.dumps(USAGE.report()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
