# Solution

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r code/requirements.txt
cp .env.example .env   # set LLM_PROVIDER / LLM_MODEL / API key (env vars only)
```
Switch model globally by editing `LLM_PROVIDER` / `LLM_MODEL` (groq, openai, anthropic, google_genai, ollama).

## Run
```bash
python code/main.py --input <input.csv> --output <output.csv>
```
Responses are cached in `.cache/` (deterministic, free re-runs; `LLM_CACHE=0` to disable). Temperature 0, fixed seed.

## Architecture
guard (prompt-injection flags) -> retrieve (BM25) -> extract (LLM, structured facts) -> decide (ordered rule table) -> validate (enum/schema).
The model describes; code decides. See `orchestrate/`.

## Evaluation
`orchestrate/evaluate.py` compares strategies on the sample file. Results table: _TODO_.

## Operational analysis
`main.py` prints model calls, tokens, cache hits, runtime. _TODO: cost/RPM notes._
