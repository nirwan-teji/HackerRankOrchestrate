# HackerRank Orchestrate (Oct 2026) - Ghost Data Still Exists

Deletion-audit agent: for each request it reconstructs the user's identifiers, the state of every copy of their data at
`audit_at`, and returns `COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE` with the ordered findings, remediation and verification steps.

* Solution, setup and architecture: [`code/README.md`](code/README.md)
* Predictions: [`output.csv`](output.csv) (518 rows) - regenerate with `python code/main.py`
* Evaluation: [`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md), usage: [`code/evaluation/usage_report.md`](code/evaluation/usage_report.md)
* Agent transcript: [`log.txt`](log.txt) (per `AGENTS.md`)
* Task statement: [`problem_statement.md`](problem_statement.md)
