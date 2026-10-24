"""A/B test: does LLM link adjudication beat the positional tie-break, measured against the public sample answers?

    python scripts/llm_ab.py

Only ambiguity groups that contain at least one *public sample* case can be scored (their true resources are in
dataset/sample_output.csv); hidden-case groups cannot. For every such group it prints the positional pick, the model's
pick and the truth, then the overall sample score with the LLM layer off vs on. Keep GHOST_LLM_LINKS=1 only if the
model is at least as accurate as the positional rule here.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

os.environ["GHOST_LLM_LINKS"] = "0"  # build the deterministic baseline first

from ghost.adjudicate import adjudicate  # noqa: E402
from ghost.data import Dataset  # noqa: E402
from ghost.engine import Auditor  # noqa: E402
from ghost.pipeline import audit_case, build_graph  # noqa: E402
from ghost.scoring import load, score  # noqa: E402
from ghost.steps import to_json  # noqa: E402


def sample_score(aud: Auditor, sample_ids: list[str], gold) -> dict:
    graph = build_graph(aud)
    pred = {}
    for cid in sample_ids:
        r = audit_case(graph, cid)
        pred[cid] = (r.status, to_json(r.steps))
    s = score(pred, gold)
    return {k: s[k] for k in ("status_accuracy", "step_f1", "exact_case_match")}


def main() -> int:
    ds = Dataset.load(ROOT / "dataset")
    sample, evalset = ds.requests("sample_deletion_requests.csv"), ds.requests()
    sample_ids = [c["case_id"] for c in sample]
    gold = load(ROOT / "dataset" / "sample_output.csv")
    truth = {cid: {s["resource_id"] for s in steps} for cid, (_, steps) in gold.items()}

    base = Auditor(ds, sample + evalset)
    groups = [g for g in base.ambiguities if any(c in truth for c in g.case_ids)]
    print(f"{len(base.ambiguities)} ambiguity groups in total; {len(groups)} contain a public sample case and can be scored\n")
    if not groups:
        return 0

    from ghost.adjudicate import VARIANTS

    results: dict[str, tuple[int, int]] = {}
    for name, prompt in VARIANTS.items():
        swaps, trail = adjudicate(base, groups, system=prompt)
        if all(str(t.get("outcome", "")).startswith(("error", "llm unavailable", "adjudication failed")) for t in trail):
            print(f"[{name}] every model call failed ({trail[0].get('outcome')}); run scripts/llm_selftest.py first")
            return 1
        pos_ok = llm_ok = n = 0
        print(f"\n=== prompt variant: {name} ===")
        for g, t in zip(groups, trail):
            proposed = t.get("proposed") or {}
            print(f"group {g.prefix} {g.case_ids}  outcome: {t['outcome']}")
            for cid in g.case_ids:
                if cid not in truth:
                    continue
                true_rid = next((r for r in g.resource_ids if r in truth[cid]), None)
                pos, llm = g.chosen.get(cid), proposed.get(cid, g.chosen.get(cid))
                n += 1; pos_ok += pos == true_rid; llm_ok += llm == true_rid
                print(f"   {cid}: truth={true_rid}  positional={pos} {'ok' if pos == true_rid else 'WRONG'}  llm={llm} {'ok' if llm == true_rid else 'WRONG'}")
        results[name] = (llm_ok, n)
        print(f"-> {name}: LLM {llm_ok}/{n}   positional {pos_ok}/{n}")
    print("\nSUMMARY (correct links out of 8):  positional", pos_ok, " | ", "  ".join(f"{k}: {v[0]}" for k, v in results.items()))
    llm_ok = max(v[0] for v in results.values())
    best = max(results, key=lambda k: results[k][0])
    print("best prompt variant:", best)
    os.environ["GHOST_ADJ_PROMPT"] = best

    off = sample_score(base, sample_ids, gold)
    os.environ["GHOST_LLM_LINKS"] = "1"
    on = sample_score(Auditor(ds, sample + evalset), sample_ids, gold)
    print("\nsample score, LLM links OFF:", json.dumps(off))
    print("sample score, LLM links ON :", json.dumps(on))
    verdict = "KEEP GHOST_LLM_LINKS=1" if llm_ok >= pos_ok and on["step_f1"] >= off["step_f1"] else "LEAVE GHOST_LLM_LINKS=0 (the model is not better than the positional rule)"
    print("\nverdict:", verdict)
    print(f"caution: only {n} links are scorable, so a one-link difference is noise. Treat a tie as 'no evidence the model helps'; "
          "keep it on only if it is strictly better, or if you value having the model in the loop and the score did not drop.")
    from orchestrate.llm import USAGE

    print("usage:", json.dumps(USAGE.report()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
