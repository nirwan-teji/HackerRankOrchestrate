# Evaluation report

Reference: `dataset/sample_output.csv` (32 public cases, the only labelled data). No hidden label, golden answer or case-specific shortcut is used anywhere in the engine; the sample answers are only read by the scorer.

## 1. Case-level status accuracy

* **100.0%** (32/32) - COMPLETE / INCOMPLETE / NOT_APPLICABLE
* exact case match (status + every step identical): **90.6%**

## 2. Step-level, field-level and sequence accuracy

A step is identified by (operation, system_id, resource_id).

| metric | value |
|---|---|
| step precision | 99.5% |
| step recall | 98.4% |
| step F1 | 99.0% |
| sequence accuracy (identical ordered step list per case) | 90.6% |

Field accuracy over gold steps that were found:

| field | accuracy |
|---|---|
| phase | 98.4% |
| system_id | 98.4% |
| resource_id | 98.4% |
| operation | 98.4% |
| state | 98.4% |
| evidence_id | 98.4% |

Cases that are not an exact match (3):

* `case_18` missing [('VERIFY_RECORD_PRESENT', 'audit_record_6645')] extra []
* `case_20` missing [('VERIFY_RECORD_PRESENT', 'audit_record_5569')] extra []
* `case_23` missing [('VERIFY_RECORD_ABSENT', 'account_410')] extra [('VERIFY_RECORD_ABSENT', 'account_7015')]

## 3. Additional team-created evaluation cases

Synthetic mini-datasets with hand-derived expected answers (`code/evaluation/custom_cases.py`); they exercise behaviours the public samples do not.

| # | scenario | status | steps |
|---|---|---|---|
| 1 | historical-email: remaining search doc + active flow + restore risk | PASS | PASS |
| 2 | compliant: absent account, anonymised analytics, redacted support, blocked restore | PASS | PASS |
| 3 | late evidence ignored: a post-audit snapshot showing the profile must not flip the verdict | PASS | PASS |
| 4 | non-deletion request (correction) -> NOT_APPLICABLE with empty steps | PASS | PASS |
| 5 | multilingual request (French) with an exposed support message -> redact plan | PASS | PASS |

## Failures found and improvements made

The first end-to-end run on the 32 public cases scored **step F1 0.87** (status accuracy already 100%). The error
analysis below drove each change; the numbers are the sample step-F1 after the change.

| # | Failure observed | Root cause | Improvement | F1 after |
|---|---|---|---|---|
| 1 | Extra `legal_hold_*` / `object_19` / `tombstone_17` / old accounts in plans | Retention-register PDF has order numbers, not resource ids (I had invented ids); catalog links and pre-request evidence were treated as live resources | Registers are ignored (nothing citable); catalog links only bind an identity-less *observed* resource; evidence captured before `requested_at` is not state of this request | 0.90 |
| 2 | `audit_record_*` missing although present in a snapshot bundle | Gating used the bundle's *export* time; inner snapshot time (`captured_at`) was earlier than `audit_at` | State gating uses each observation's own time; citation uses the manifest time (never cite a file captured after `audit_at`) | 0.90 |
| 3 | Support / contact / marketing resources typed wrongly (REDACT vs DELETE) | Records were typed by id prefix only | Evidence-specific typing: legacy contact index -> SECONDARY_IDENTIFIER, opt-in contact lookup -> MARKETING_PROFILE, attachments -> SUPPORT_ATTACHMENT; e-mails inside message text count as exposed PII | 0.94 |
| 4 | Search documents showed no identifiers | Identifiers are nested under `document` in JSON/SQL records | Nested record flattening | 0.955 |
| 5 | Wrong `account_*` assigned to a case inside busy clusters (several cases share one `reported_at`) | Global positional pairing across unrelated clusters | Time windows by resource kind, claim consistency (COMPLETE<->SUCCESS, PARTIAL<->PARTIAL, NOT_STARTED<->FAILED), elimination of directly-owned resources, pairing only inside connected components | 0.976 |
| 6 | Identity-less (anonymised) analytics not linked | `clean` flag treated retained audit records as violations | Violation test delegated to the policy planner; early successful policy actions pair only with clean cases that are documented by the same evidence files | 0.99 |
| 7 | One request marked NOT_APPLICABLE wrongly | "check the latest state and confirm the profile is gone" has no deletion verb | Default to deletion context unless an explicit non-deletion cue / negation is present; optional LLM second opinion | 0.99 |
| 8 | Syslog lookups cross-linked (hidden-set diagnostic, not visible in samples) | RFC5424 fields were off by one, so every trace id was `-` | Fixed field positions; a lookup now only joins its own trace | 0.99 |
| 9 | 12 steps cited the literal string `"direct"` instead of an id - the grader would reject every one | The internal provenance tag (`direct` / `catalog`) was reused as an evidence hint | Citations are restricted to `evidence_*` / `activity_*` / `audit_*` shapes; the catalog link now carries its real `activity_id`; the validator rejects any other value and `main.py` additionally checks every citation against the participant-visible id set | 0.99 |
| 10 | Behind that bug: records whose only observation sits in a file captured *after* `audit_at` had nothing citable | State and citation were taken from the same observation | Citation is resolved separately - a same-state citable file, else a snapshot that proves absence, else the worker's own `activity_id`. Strict capture-time gating was tested as an alternative and rejected: it drops sample F1 from 0.990 to 0.974, so the grader treats the inner observation time as authoritative | 0.99 |

### Decisions that were tested and rejected
* **Strict capture-time gating** (ignore every file captured after `audit_at`): sample F1 0.974 vs 0.990, exact match 78% vs 91%. The gold answers use observations whose own timestamp precedes the cutoff even when the containing export was assembled later, so observation time decides the state and capture time only restricts citation.
* **LLM adjudication of ambiguous links** (gpt-oss-120b): 1/8 correct vs 7/8 for the positional rule (see section 4). Where the model genuinely helps is request-intent classification (4/4 on the self-test), so the LLM is used there and not for linking.
* **Rank-consistency linking** (assume the worker processes a cluster's cases in one fixed order across resource types): holds for the account events of cases 27/28/29 but is inverted for their analytics events, so it cannot replace the positional tie-break.
* **`CACHE_ENTRY` / `EMAIL_DELIVERY_LOG` resources**: `deletion_rules.csv` defines treatments for both, and `system_activity.csv` contains 284 cache and 368 e-mail-delivery events, but **no evidence file ever observes one**. They are decoys: a finding must cite evidence, so none can be raised. The public gold answers never mention them, which corroborates the reading.

### Known residual errors (public samples)
* `audit_record_6645` (case_18) and `audit_record_5569` (case_20) appear in **no** participant-visible file; the expected
  answer lists them as retained records. They cannot be derived, so these two steps are missed by construction.
* `account_410` (case_23): the account belongs to one of three equally-compatible NOT_STARTED cases in a cluster and only the
  deletion-worker activity names it; our deterministic tie-break (time order vs case order) picks a different account.

## Output contract checks

* samples: OK
* full run: 518 rows, sorted by case_id (natural order), 3172 steps, contract violations: 0
* full-run status mix: {'INCOMPLETE': 352, 'COMPLETE': 156, 'NOT_APPLICABLE': 10}

## 4. Uncertainty budget (how much of the answer is derived vs guessed)

A resource/case link is *derived* when elimination leaves exactly one compatible case, and *guessed* when several cases are equally compatible and the positional rule has to break the tie. The engine records every guess (`Auditor.guessed`), so the exposure is measurable rather than hidden:

| measure | value |
|---|---|
| evaluation cases containing >=1 guessed link | 151 / 518 |
| steps citing a guessed resource | 168 / 3172 (5.3%) |
| cases whose *status* would change without the guess | 0 |
| ambiguity groups (by number of interchangeable cases) | {2: 68, 6: 1, 3: 10, 4: 1} |
| guessed-link accuracy measured on the public samples | 7/8 correct (case_23 is the miss) |

So the guessing affects a bounded slice of the step score and none of the statuses. Groups of size 2 are a coin flip in the worst case; the measured sample accuracy is better than that because the positional order usually matches the worker's processing order.

### LLM adjudication of those guesses: measured, and it does not help

`GHOST_LLM_LINKS=1` routes each ambiguity group to the configured model, which sees the candidate resources and the per-case facts and proposes a pairing; the proposal is accepted only if it is a permutation of the group and passes the same deterministic constraints. It was evaluated against the public answers (`scripts/llm_ab.py`, openai/gpt-oss-120b on Groq, json_schema structured output):

| link chooser | correct links (8 scorable, in 5 groups) |
|---|---|
| positional rule (time order vs case order) | **7 / 8** |
| gpt-oss-120b adjudicator | **1 / 8** |

The model replaced a correct pairing with a wrong one in 4 of the 5 groups and was right only on `account_410` (case_23), the one place the positional rule fails. The deterministic validator cannot catch these swaps because both orderings satisfy every constraint; the constraints that *would* separate them are exactly what the data does not contain, so the model has no extra signal and effectively guesses. **Decision: `GHOST_LLM_LINKS` stays 0 in the submitted run.** The adjudicator is kept as a documented, tested ablation, not as a hidden dependency. (8 links is a small sample, but a 1/8 vs 7/8 gap is far outside noise.)

Outcomes of the adjudication layer in *this* run: layer disabled (deterministic run).

A rank-consistency derivation was also tried and rejected: the worker's per-case ordering is consistent for account events but *inverted* for the analytics events of cases 27/28, so it cannot replace the tie-break.

## Honest limitations

* Only 32 labelled cases exist, so hidden-set quality is estimated from them; families that occur only in the hidden set (screenshot/PDF single-artifact cases) are handled by the same rules but cannot be scored locally.
* Identity-less resources (404 account lookups, anonymised analytics rows) are linked to a case through deletion-worker activity windows, claim consistency and elimination; when several equally compatible cases remain the tie-break is positional (time order vs case order) and can be wrong - quantified in section 4.
* The public samples are both the tuning set and the only scoring set, so these metrics are optimistic for the hidden set. The uncertainty budget in section 4 is the honest lower bound on where hidden-set errors will come from.
