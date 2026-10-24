"""One command for the submission: preflight -> evidence-gated LLM layers -> full run -> reports -> package -> checklist.

    python scripts/final_run.py                 # deterministic core only (no API key needed)
    python scripts/final_run.py --llm           # also try the LLM layers, each kept ONLY if it does not lower the sample score
    python scripts/final_run.py --llm --review  # plus the advisory LLM reviewer on risky cases (~1 h on Groq's free tier)

Design: the deterministic engine produces the answer. Each LLM layer is a candidate that must earn its place: it is switched on,
the 32 public sample cases are re-scored, and it is kept only if status accuracy and step F1 are not lower than with it off.
Link adjudication is not offered here because it was measured at 1/8 vs 7/8 (see evaluation_report.md). The reviewer is
advisory (it cannot change output.csv), so it is not gated - it only costs time.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
ARGS = set(sys.argv[1:])

# sensible limits for Groq's free tier before any LLM module reads its settings
os.environ.setdefault("LLM_TPM", "7000")
for flag in ("GHOST_LLM_INTENT", "GHOST_LLM_INTENT_ALL", "GHOST_LLM_LINKS", "GHOST_LLM_REVIEW"):
    os.environ[flag] = "0"  # start from the deterministic baseline regardless of .env


def say(msg: str) -> None:
    print(msg, flush=True)


def sample_score() -> dict:
    from ghost import intent
    from ghost.run import run
    from ghost.scoring import load, score

    intent._CACHE.clear()  # labels are memoised per process; a new configuration must re-classify
    _, _, rows = run(ROOT / "dataset", "sample_deletion_requests.csv")
    s = score({r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in rows}, load(ROOT / "dataset" / "sample_output.csv"))
    return {k: round(s[k], 4) for k in ("status_accuracy", "step_f1", "exact_case_match")}


KNOWN_WRONG = {"case_18", "case_20", "case_23"}  # sample plans that differ from the answer key


def reviewer_gate(max_false_alarm_rate: float = 0.2) -> tuple[bool, str]:
    """The reviewer cannot change answers, but a noisy one is worse than none: it would bury real issues in false alarms.
    Keep it only if it raises flags on at most 20% of the sample plans that match the key."""
    from ghost import review
    from ghost.run import run

    os.environ["GHOST_LLM_REVIEW"] = "0"
    aud, results, _ = run(ROOT / "dataset", "sample_deletion_requests.csv")
    todo = {c: r for c, r in results.items() if review.in_scope(aud, r)}
    out = review.review_all(aud, todo)
    if any(v.verdict == "error" for v in out.values()):
        return False, "model calls failed: " + next(v.reason for v in out.values() if v.verdict == "error")[:90]
    right = [c for c in out if c not in KNOWN_WRONG]
    fa = sum(out[c].verdict == "concerns" for c in right)
    rate = fa / max(len(right), 1)
    return rate <= max_false_alarm_rate, f"false alarms {fa}/{len(right)} on correct plans ({rate:.0%}, limit {max_false_alarm_rate:.0%})"


def not_worse(a: dict, b: dict) -> bool:
    return a["status_accuracy"] >= b["status_accuracy"] and a["step_f1"] >= b["step_f1"]


def main() -> int:
    t0 = time.time()
    say("== 1. preflight")
    from ghost.data import Dataset

    Dataset.load(ROOT / "dataset")  # exits with a clear message if any evidence file is missing
    say("   dataset complete (all 95 evidence files present)")
    if "--llm" in ARGS:
        from orchestrate.config import get_settings

        st = get_settings()
        key = os.getenv(f"{st.provider.upper()}_API_KEY")
        if not key:
            say(f"   no {st.provider.upper()}_API_KEY in the environment/.env -> running deterministic only")
            ARGS.discard("--llm"); ARGS.discard("--review")
        else:
            say(f"   LLM: provider={st.provider} model={st.model} tpm={st.tpm} reasoning_effort={st.reasoning_effort or 'default'}")

    say("== 2. baseline (deterministic) on the 32 public samples")
    base = sample_score()
    say(f"   {base}")

    chosen = {}
    if "--llm" in ARGS:
        say("== 3. evidence-gated LLM layers")
        os.environ["GHOST_LLM_INTENT"] = "1"
        try:
            got = sample_score()
            keep = not_worse(got, base)
        except Exception as e:  # provider trouble must never break the submission run
            got, keep = {"error": f"{type(e).__name__}: {str(e)[:100]}"}, False
        say(f"   intent second opinion: {got} -> {'KEEP' if keep else 'DROP'}")
        os.environ["GHOST_LLM_INTENT"] = "1" if keep else "0"
        chosen["GHOST_LLM_INTENT"] = os.environ["GHOST_LLM_INTENT"]
        say("   link adjudication: not offered (measured 1/8 correct vs 7/8 for the deterministic rule)")
    if "--review" in ARGS:
        ok, note = reviewer_gate()
        say(f"   advisory reviewer on the sample risky cases: {note} -> {'KEEP' if ok else 'DROP'}")
        if ok:
            os.environ["GHOST_LLM_REVIEW"] = "1"
            chosen["GHOST_LLM_REVIEW"] = "1"

    say("== 4. full run + reports (this is the run that produces output.csv)")
    env = {**os.environ, **chosen}
    r = subprocess.run([sys.executable, str(ROOT / "code" / "main.py")], cwd=ROOT, env=env, capture_output=True, text=True)
    say("   " + "\n   ".join(l for l in r.stdout.splitlines() if l.strip()))
    if r.returncode:
        say(r.stderr[-2000:]); return r.returncode
    r = subprocess.run([sys.executable, str(ROOT / "code" / "evaluation" / "run_eval.py")], cwd=ROOT, env=env, capture_output=True, text=True)
    say("   " + r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-1500:])
    if r.returncode:
        return r.returncode

    if "--skip-tests" not in ARGS:
        say("== 5. tests")
        t = subprocess.run([sys.executable, "-m", "pytest", "-q", "code/tests", "tests"], cwd=ROOT, env={**os.environ, "GHOST_LLM_INTENT": "0", "GHOST_LLM_REVIEW": "0"}, capture_output=True, text=True)
        say("   " + (t.stdout.strip().splitlines() or ["(no output)"])[-1])
        if t.returncode:
            say(t.stdout[-2000:]); return t.returncode

    say("== 6. package")
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "package_submission.py"), str(ROOT / "output.csv")], cwd=ROOT, capture_output=True, text=True)
    say("   " + p.stdout.strip().replace("\n", "\n   "))

    sub = ROOT / "submission"
    say("\n== SUBMISSION CHECKLIST")
    for f in ("code.zip", "output.csv", "log.txt"):
        fp = sub / f
        say(f"   [{'x' if fp.exists() else ' '}] submission/{f}" + (f"  ({fp.stat().st_size / 1024:,.0f} KB)" if fp.exists() else "  MISSING"))
    say("   [ ] agent-trace.zip  - export it from YOUR coding tool (see agent_trace_instructions.md); I cannot do this for you")
    say("   [ ] log.txt contains your own turns from your local tool, appended (never rewritten)")
    say("   [ ] upload at the URL in AGENTS.md section 4.1, then do the 30-minute AI judge interview (camera on)")
    say(f"\nLLM layers used for this output: {chosen or 'none (deterministic run)'}   total time {time.time() - t0:,.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
