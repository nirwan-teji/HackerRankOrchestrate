"""Check that the configured LLM actually works, before trusting it in a full run.

    python scripts/llm_selftest.py

Reads .env (LLM_PROVIDER / LLM_MODEL / <PROVIDER>_API_KEY). Runs four checks and prints token usage:
  1. plain completion            - the provider, key and model name are valid
  2. structured output           - the model can fill a pydantic schema (some open models cannot)
  3. intent classification       - on four requests whose correct answer is known
  4. link adjudication           - one real ambiguity group from the dataset, validated by the engine's own rules
Nothing here writes output.csv; it only tells you whether to enable GHOST_LLM_INTENT / GHOST_LLM_LINKS.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

KNOWN = [
    ("Please erase the account linked to this email address.", "DELETE"),
    ("I'd like to download my account information in a portable format. Please keep the account as it is.", "OTHER"),
    ("Je demande la suppression définitive du compte associé à cette adresse e-mail.", "DELETE"),
    ("Can you tell me what personal information you hold about me? This is an information request, not a request to erase it.", "OTHER"),
]


def main() -> int:
    from orchestrate.config import get_settings

    s = get_settings()
    print(f"provider={s.provider} model={s.model} vision={s.vision_model} rpm={s.rpm} cache={s.cache}")
    key = os.getenv(f"{s.provider.upper().replace('-', '_')}_API_KEY") or os.getenv("GROQ_API_KEY")
    if not key:
        print("no API key found in the environment for this provider - set it in .env first")
        return 1

    from orchestrate.llm import USAGE, ask

    # 1. plain completion
    try:
        out = ask("You are terse.", "Reply with exactly: OK")
        print(f"[1/4] completion            : OK ({out.strip()[:40]!r})")
    except Exception as e:
        print(f"[1/4] completion            : FAILED {type(e).__name__}: {str(e)[:200]}")
        return 1

    # 2. structured output
    from pydantic import BaseModel

    class Probe(BaseModel):
        answer: bool
        reason: str

    try:
        p = ask("Answer strictly as JSON.", "Is 7 greater than 3?", schema=Probe)
        from orchestrate.llm import _WORKING

        print(f"[2/4] structured output     : {'OK' if p.answer else 'returned false (model is weak at JSON)'} (mode: {_WORKING.get(s.model, '?')})")
    except Exception as e:
        print(f"[2/4] structured output     : FAILED {type(e).__name__}: {str(e)[:200]}")
        print("      -> leave GHOST_LLM_INTENT=0 / GHOST_LLM_LINKS=0, or switch LLM_MODEL to one with tool-calling support")
        return 1

    # 3. intent classification against known answers
    from ghost import intent

    wrong = []
    for text, want in KNOWN:
        try:
            got = intent.llm_intent(text)
        except Exception as e:
            got = f"error:{type(e).__name__}"
        if got != want:
            wrong.append((text[:48], want, got))
    print(f"[3/4] intent classification : {len(KNOWN) - len(wrong)}/{len(KNOWN)} correct")
    for t, want, got in wrong:
        print(f"      MISS {t!r} wanted {want} got {got}")

    # 4. link adjudication on a real ambiguity group
    from ghost.adjudicate import adjudicate
    from ghost.data import Dataset
    from ghost.engine import Auditor

    ds = Dataset.load(ROOT / "dataset")
    aud = Auditor(ds, ds.requests("sample_deletion_requests.csv") + ds.requests())
    groups = aud.ambiguities[:2]
    if groups:
        _, trail = adjudicate(aud, groups)
        print(f"[4/4] link adjudication     : {json.dumps(trail, indent=2)[:900]}")
    else:
        print("[4/4] link adjudication     : no ambiguity groups to test")

    u = USAGE.report()
    print("\nusage:", json.dumps(u))
    if u["model_calls"]:
        per_call = (u["input_tokens"] + u["output_tokens"]) / u["model_calls"]
        print(f"\nplanning: ~{per_call:,.0f} tokens per call (cache hits excluded). With LLM_TPM={s.tpm or 'off'}:")
        for label, calls in (("intent: rule-unsure texts only (default)", 3), ("intent: 60-text validation sample", 60), ("intent: all 398 distinct texts", 398), ("reviewer, ~159 risky cases", 159)):
            minutes = calls * per_call / s.tpm if s.tpm else calls / max(s.rpm, 1)
            print(f"   {label:42s} {calls:4d} calls ~ {calls * per_call / 1000:7,.0f}K tokens ~ {minutes:5.0f} min")
        print("   also check console.groq.com -> Usage for a DAILY token cap; the disk cache lets an interrupted run resume where it stopped.")
    print("\nIf 1-3 passed, enable the LLM layer in .env:\n  GHOST_LLM_INTENT=1\n  GHOST_LLM_INTENT_ALL=1   # classify every request text, not only unsure ones\n  GHOST_LLM_LINKS=1\nthen re-run:  python code/main.py --input sample_deletion_requests.csv --output /tmp/s.csv --eval dataset/sample_output.csv\nand compare the score with the deterministic run before keeping it on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
