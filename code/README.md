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
python code/main.py                       # reads dataset/, writes output.csv at the repo root, validates the contract
python code/main.py --dataset D --input sample_deletion_requests.csv --output /tmp/s.csv --eval D/sample_output.csv
python code/evaluation/run_eval.py        # regenerates evaluation/evaluation_report.md and usage_report.md
python code/evaluation/custom_cases.py    # the team-written evaluation scenarios
pytest -q code/tests                      # unit + regression tests
```
The full 518-case run takes ~3 s and is deterministic (no randomness, stable ordering, cached OCR).

## How it works (the model describes, code decides)
```
classify_intent -> resolve_identity -> link_resources -> reconstruct_state -> plan_steps      (LangGraph, ghost/pipeline.py)
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
6. **Policy** (`steps.py`): `deletion_rules.csv` semantics -> DELETE / REDACT / ANONYMIZE / RETAIN / restore block / flow
   disable; steps are ordered by phase, operation table order, `system_id`, `resource_id`; `validate.py` enforces the contract.

## Layout
`main.py` entry point - `ghost/` engine - `orchestrate/` provider-agnostic LangChain client (cache, retry, usage) -
`evaluation/` reports + custom cases - `prompts/` LLM prompts (see `ghost/intent.py`).

## Configuration
Everything is environment-driven (`.env`): `LLM_PROVIDER`, `LLM_MODEL` (groq / openai / anthropic / google_genai / ollama),
`LLM_RPM`, `GHOST_LLM_INTENT`. No secrets are stored in the repository.
