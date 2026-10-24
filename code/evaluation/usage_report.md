# Token usage and cost analysis

Final full-dataset run that produced `output.csv` (518 requests).

## Pipeline and where models are used

| stage | technique | model calls |
|---|---|---|
| log / SQL / JSON / TXT / PDF evidence parsing | deterministic parsers (`pdftotext` for PDFs) | 0 |
| screenshot (PNG) reading | local RapidOCR, word boxes cached under `ghost/vision_cache/` | 0 (no API) |
| identity resolution, resource linking, state reconstruction, policy, step ordering | deterministic code | 0 |
| LLM reviewer of risky plans (advisory, cannot change answers) | one cached call per risky case (159 in scope) | disabled for this run |
| request-intent check (deletion vs not) | rules first; LangChain LLM second opinion only for texts the rules flag as unsure, one call per *distinct* text, disk-cached | disabled for this run |

## Totals for this run

| item | value |
|---|---|
| model provider | none used |
| model name | n/a |
| model calls | 0 |
| cache hits | 0 |
| input tokens | 0 |
| output tokens | 0 |
| total tokens | 0 |
| average tokens per request | 0.00 |
| estimated total cost (USD) | 0.0000 |
| estimated cost per request (USD) | 0.000000 |
| wall-clock runtime for 518 cases | 3.4 s |
| peak RSS | 85 MB |

Per-model totals: no model was called, so every per-model and overall total is 0.

### Cost model

Set `PRICE_IN_PER_MTOK` / `PRICE_OUT_PER_MTOK` (USD per million tokens) before running to price a run; the free Groq tier used during development costs 0.

### Rate limits observed on the free tier (measured, not assumed)

The Groq console for `openai/gpt-oss-120b` showed a limit of about **30 requests/min** and about **8K tokens/min**. Our calls are small in requests but large in tokens (`gpt-oss` spends many hidden reasoning tokens: the measured reviewer check used 15,109 input + 12,222 output tokens for 10 calls, ~2.7K tokens per call). The token limit is therefore the binding one, and unthrottled runs hit HTTP 429. `LLM_TPM` (default off, 7000 in `.env.example`) enables a sliding 60-second token budget that estimates each call before sending it and records what it actually used; `LLM_RPM` caps requests; 400-class errors are never retried, 429s are retried with backoff; every response is cached on disk so an interrupted run resumes where it stopped.

| optional LLM stage | calls | est. tokens | est. time at ~7K tokens/min |
|---|---|---|---|
| reviewer of risky plans | 159 | ~429K | ~61 min |
| intent second opinion, rule-unsure texts only (default) | 3 | ~3K | <1 min |
| intent validation sample (`scripts/llm_intent_check.py`, default 60) | 60 | ~60K | ~9 min |
| intent on every distinct text (`GHOST_LLM_INTENT_ALL=1`) | 398 | ~398K | ~57 min |

`LLM_REASONING_EFFORT=low` should shrink the output side materially; it is measured by `scripts/llm_selftest.py`, which prints tokens per call and the projected time for each stage.
