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

### Known residual errors (public samples)
* `audit_record_6645` (case_18) and `audit_record_5569` (case_20) appear in **no** participant-visible file; the expected
  answer lists them as retained records. They cannot be derived, so these two steps are missed by construction.
* `account_410` (case_23): the account belongs to one of three equally-compatible NOT_STARTED cases in a cluster and only the
  deletion-worker activity names it; our deterministic tie-break (time order vs case order) picks a different account.

## Output contract checks

* samples: OK
* full run: 518 rows, sorted by case_id (natural order), 3172 steps, contract violations: 0
* full-run status mix: {'INCOMPLETE': 352, 'COMPLETE': 156, 'NOT_APPLICABLE': 10}

## Honest limitations

* Only 32 labelled cases exist, so hidden-set quality is estimated from them; families that occur only in the hidden set (screenshot/PDF single-artifact cases) are handled by the same rules but cannot be scored locally.
* Identity-less resources (404 account lookups, anonymised analytics rows) are linked to a case through deletion-worker activity windows, claim consistency and elimination; when several equally compatible cases remain the tie-break is positional (time order vs case order) and can be wrong.
