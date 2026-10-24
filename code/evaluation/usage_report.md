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
| wall-clock runtime for 518 cases | 3.5 s |
| peak RSS | 84 MB |

Per-model totals: no model was called, so every per-model and overall total is 0.

### Cost model

Set `PRICE_IN_PER_MTOK` / `PRICE_OUT_PER_MTOK` (USD per million tokens) before running to price a run; the free Groq tier used during development costs 0. With `GHOST_LLM_INTENT=1` the number of calls is bounded by the number of *distinct* ambiguous request texts (about 15 in the public data, ~1k tokens each), independent of the 518 cases, so the worst case is a few tens of thousands of tokens and well within free-tier TPM/RPM limits (`LLM_RPM` throttles requests).
