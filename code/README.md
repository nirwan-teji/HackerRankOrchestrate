# Ghost Data Still Exists - deletion-audit agent

Decides, for every request, whether a person's data is really gone (`COMPLETE`), still exposed (`INCOMPLETE`) or the
request never authorised deletion (`NOT_APPLICABLE`), and emits the ordered findings -> remediation -> verification steps.

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate        # Python 3.10+
pip install -r code/requirements.txt
cp .env.example .env                                       # only needed for the optional LLM layer
```
`pdftotext` (poppler) is used for PDFs if present, otherwise `pypdf`. Screenshot OCR results are cached in
`code/ghost/vision_cache/`, so no OCR engine or API key is needed to reproduce the submission.

## Run
```bash
python scripts/final_run.py [--llm] [--review]   # everything for the submission: gated LLM layers, run, reports, tests, package
python code/main.py                       # reads dataset/, writes output.csv at the repo root, validates the contract
python code/main.py --dataset D --input sample_deletion_requests.csv --output /tmp/s.csv --eval D/sample_output.csv
python code/evaluation/run_eval.py        # regenerates evaluation/evaluation_report.md and usage_report.md
python code/evaluation/custom_cases.py    # the team-written evaluation scenarios
python scripts/llm_selftest.py            # verify provider/key/model before enabling the LLM layer
python scripts/llm_ab.py                  # model vs positional link accuracy on the public samples
python scripts/llm_intent_check.py        # where the model and the intent rules disagree
python scripts/llm_review_check.py        # what the LLM reviewer says about the public sample plans
pytest -q code/tests                      # unit + regression tests
```
The full 518-case run takes ~3 s and is deterministic (no randomness, stable ordering, cached OCR).

## How it works (the model describes, code decides)
```
classify_intent -> resolve_identity -> link_resources -> reconstruct_state -> plan_steps -> review_plan   (LangGraph, ghost/pipeline.py)
```
1. **Intent** (`intent.py`): multilingual rules decide whether the text authorises deletion; optional LangChain LLM second
   opinion (`GHOST_LLM_INTENT=1`) for unsure texts only. A `CLASSIFY_REQUEST ... non-deletion` activity corroborates.
2. **Identity** (`identity.py`): request e-mail -> user via current identity + `user_audit_log` (old/new e-mail, phone,
   e-mail hash); every identifier the person ever had is searched for.
3. **Evidence** (`logparse.py`, `evidence.py`, `documents.py`, `vision.py`): every file type (7 log dialects, SQL replicas via
   sqlite, JSON bundles/exports, text handovers, PDFs, OCR'd screenshots) becomes uniform observations
   `(time, resource, category, state, identifiers)`. Trace ids join `*_started` with `*_completed` log lines.
4. **Linking** (`engine.py`, `linking.py`): resources are tied to the subject by direct identifiers; identity-less records
   (404 lookups, anonymised rows) are attached through deletion-worker activity windows around `reported_at`, claim
   consistency (COMPLETE<->SUCCESS ...), elimination and deterministic pairing inside a cluster.
5. **State at `audit_at`**: latest observation by its own time <= `audit_at` and >= `requested_at`; a file may be *cited* only
   if its manifest capture time <= `audit_at`; absence can be proven by a populated snapshot that covers the system.
   Workflow statuses and activity results are treated as claims, never as proof.
6. **Uncertainty** (`linking.py`): a link is *derived* when elimination leaves one candidate and *guessed* when several
   cases are interchangeable. Guesses are recorded (`Auditor.guessed`) and counted in the evaluation report (5.3% of steps,
   0 statuses). An LLM adjudicator exists (`adjudicate.py`, `GHOST_LLM_LINKS=1`) but is **off**: measured on the public
   samples it got 1/8 links right vs 7/8 for the positional rule, so it is kept as a documented ablation only.
7. **Policy** (`steps.py`): `deletion_rules.csv` semantics -> DELETE / REDACT / ANONYMIZE / RETAIN / restore block / flow
   disable; steps are ordered by phase, operation table order, `system_id`, `resource_id`; `validate.py` enforces the contract.
8. **LLM reviewer** (`review.py`, `GHOST_LLM_REVIEW=1`): for the ~30% of cases with a guessed link or an unestablished state, a model
   checks the plan against the policy table and raises flags. Advisory only - a test proves it cannot change `output.csv`.

## How the LLM layers are chosen
Each LLM layer is a candidate that must earn its place against the 32 public answers (`scripts/final_run.py`):

| layer | role | gate | measured |
|---|---|---|---|
| intent second opinion | decides deletion-vs-not for the 3 texts the rules are unsure about | kept only if sample status accuracy and step F1 do not drop | 4/4 on the self-test |
| link adjudication | choose between interchangeable resource/case pairings | not offered | 1/8 vs 7/8 for the rule - off |
| advisory reviewer | flag inconsistent plans in risky cases; cannot change answers | kept only if <=20% false alarms on correct sample plans | 7/9 false alarms before the prompt fix; re-measure |

The deterministic engine therefore owns every answer, and a model only contributes where it was shown not to hurt.

## Layout
`main.py` entry point - `ghost/` engine - `orchestrate/` provider-agnostic LangChain client (cache, retry, usage) -
`evaluation/` reports + custom cases - `prompts/` LLM prompts (see `ghost/intent.py`).

## Configuration
Everything is environment-driven (`.env`): `LLM_PROVIDER`, `LLM_MODEL` (groq / openai / anthropic / google_genai / ollama),
`LLM_RPM`, `GHOST_LLM_INTENT` (request-intent second opinion), `GHOST_LLM_INTENT_ALL` (classify every text),
`GHOST_LLM_LINKS` (adjudicate ambiguous links - leave 0, see above). No secrets are stored in the repository. With every flag off the run is
fully deterministic and makes zero model calls; with them on, model output that the evidence contradicts is discarded,
so the pipeline degrades to the deterministic answer rather than to a worse one.
