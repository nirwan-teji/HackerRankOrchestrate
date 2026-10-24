"""Team-authored evaluation cases (synthetic, fictional, hand-derived expectations).

Each scenario builds a tiny dataset directory (same file formats as the real one) and states the exact answer a
correct auditor must give. They cover behaviours the 32 public samples do not: historical e-mail resolution,
restore/data-flow ordering, evidence that postdates audit_at, and a non-deletion request.
Run:  python code/evaluation/custom_cases.py     (also executed by run_eval.py)
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.run import run  # noqa: E402

RULES = [("rule_01", "USER_PROFILE", "Always", "DELETE"), ("rule_03", "SEARCH_INDEX_ENTRY", "Always", "DELETE"),
         ("rule_05", "SUPPORT_MESSAGE", "shared context", "REDACT"), ("rule_07", "ANALYTICS_PROFILE", "attributable", "ANONYMIZE"),
         ("rule_12", "SECURITY_AUDIT_LOG", "within retention", "RETAIN"), ("rule_14", "BACKUP_ARCHIVE", "restore blocked", "RETAIN")]


def _csv(path: Path, header: list[str], rows: list[tuple]):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


def build(tmp: Path, *, users, audit, request, evidence: dict[str, tuple[str, str]]):
    """evidence: filename -> (captured_at, content). Files are placed under evidence/logs or evidence/records."""
    (tmp / "evidence" / "logs").mkdir(parents=True); (tmp / "evidence" / "records").mkdir(parents=True)
    _csv(tmp / "user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"], users)
    _csv(tmp / "user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"], audit)
    _csv(tmp / "system_activity.csv", ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id", "reported_result", "details"], [])
    _csv(tmp / "deletion_rules.csv", ["rule_id", "data_category", "condition", "required_action"], RULES)
    man = []
    for i, (name, (cap, body)) in enumerate(evidence.items(), 1):
        sub = "logs" if name.endswith(".log") else "records"
        (tmp / "evidence" / sub / name).write_text(body)
        man.append((f"evidence_{i:02d}", f"evidence/{sub}/{name}", "synthetic", cap))
    _csv(tmp / "evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], man)
    hdr = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
    _csv(tmp / "sample_deletion_requests.csv", hdr, [])
    _csv(tmp / "deletion_requests.csv", hdr, [request])


def L(ts, svc, tr, ev, **kv):  # one logfmt line
    return f"{ts} level=INFO service={svc} trace_id={tr} event={ev} " + " ".join(f"{k}={v}" for k, v in kv.items())


U1 = ("user_901", "Ada Okoye", "ada.new@example.org", "+44 7700 900123", "h" * 64, "2026-06-01T00:00:00Z")
AUD1 = ("audit_900", "user_901", "2026-06-01T00:00:00Z", "email", "ada.old@example.org", "ada.new@example.org")
REQ = lambda email, text="Please delete my account and personal data.", rep="2026-09-02T10:00:00Z", at="2026-09-10T12:00:00Z": (
    "case_901", email, "2026-09-01T10:00:00Z", "COMPLETE", rep, at, text)

SCENARIOS = []

# 1. historical e-mail, remaining search doc, active data flow and a restorable backup -> full plan, correct ordering
SCENARIOS.append(dict(
    name="historical-email: remaining search doc + active flow + restore risk",
    users=[U1], audit=[AUD1], request=REQ("ada.old@example.org"),
    evidence={"trace.log": ("2026-09-10T11:00:00Z", "\n".join([
        L("2026-09-10T10:00:00Z", "account_service", "t1", "profile_lookup_started", resource_id="account_7001", lookup_key="ada.old@example.org"),
        L("2026-09-10T10:00:01Z", "account_service", "t1", "profile_lookup_completed", resource_id="account_7001", rows=0, http_status=404),
        L("2026-09-10T10:05:00Z", "search_service", "t2", "index_query_started", resource_id="search_7002", lookup_key="ada.new@example.org"),
        L("2026-09-10T10:05:01Z", "search_service", "t2", "index_query_completed", resource_id="search_7002", result="hit", total_hits=1, email="ada.new@example.org", customer_reference="null"),
        L("2026-09-10T10:10:00Z", "integration_service", "t3", "connector_config_read_started", resource_id="data_flow_7003", lookup_key="user_901"),
        L("2026-09-10T10:10:01Z", "integration_service", "t3", "connector_config_read_completed", resource_id="data_flow_7003", enabled="true", last_delivery_status="delivered", customer_email="null", customer_reference="user_901"),
        L("2026-09-10T10:15:00Z", "backup_service", "t4", "restore_policy_read_started", resource_id="backup_7004", lookup_key="user_901"),
        L("2026-09-10T10:15:01Z", "backup_service", "t4", "restore_policy_read_completed", resource_id="backup_7004", archive_present="true", restore_allowed="true", customer_email="null", customer_reference="user_901"),
        L("2026-09-10T10:20:00Z", "security_service", "t5", "audit_record_read_started", resource_id="audit_record_7005", lookup_key="user_901"),
        L("2026-09-10T10:20:01Z", "security_service", "t5", "audit_record_read_completed", resource_id="audit_record_7005", rows=1, subject_reference="user_901", event_code="ACCOUNT_DELETION_RECORDED", retention_until="2031-12-31T23:59:59Z"),
    ]))},
    status="INCOMPLETE",
    ops=[("REPORT_REMAINING_RECORD", "search_7002"), ("REPORT_ACTIVE_DATA_FLOW", "data_flow_7003"), ("REPORT_RESTORE_RISK", "backup_7004"),
         ("DISABLE_DATA_FLOW", "data_flow_7003"), ("BLOCK_RESTORE", "backup_7004"), ("DELETE_RECORD", "search_7002"),
         ("VERIFY_RECORD_ABSENT", "account_7001"), ("VERIFY_RECORD_ABSENT", "search_7002"), ("VERIFY_RECORD_PRESENT", "audit_record_7005"),
         ("VERIFY_DATA_FLOW_DISABLED", "data_flow_7003"), ("VERIFY_RESTORE_BLOCKED", "backup_7004")]))

# 2. everything compliant -> COMPLETE, verification-only, evidence cited
SCENARIOS.append(dict(
    name="compliant: absent account, anonymised analytics, redacted support, blocked restore",
    users=[U1], audit=[AUD1], request=REQ("ada.new@example.org"),
    evidence={"trace.log": ("2026-09-10T11:00:00Z", "\n".join([
        L("2026-09-10T10:00:00Z", "account_service", "t1", "profile_lookup_started", resource_id="account_7001", lookup_key="ada.new@example.org"),
        L("2026-09-10T10:00:01Z", "account_service", "t1", "profile_lookup_completed", resource_id="account_7001", rows=0, http_status=404),
        L("2026-09-10T10:02:00Z", "analytics_platform", "t2", "analytics_profile_read_started", resource_id="analytics_7006", lookup_key="user_901"),
        L("2026-09-10T10:02:01Z", "analytics_platform", "t2", "analytics_profile_read_completed", resource_id="analytics_7006", customer_reference="null", email="null", phone="null", email_hash="null", audience_cohort="general"),
        L("2026-09-10T10:03:00Z", "support_platform", "t3", "support_ticket_read_started", resource_id="support_7007", lookup_key="user_901"),
        L("2026-09-10T10:03:01Z", "support_platform", "t3", "support_ticket_read_completed", resource_id="support_7007", customer_reference="user_901", requester_name="null", requester_email="null", requester_phone="null"),
        L("2026-09-10T10:04:00Z", "backup_service", "t4", "restore_policy_read_started", resource_id="backup_7004", lookup_key="user_901"),
        L("2026-09-10T10:04:01Z", "backup_service", "t4", "restore_policy_read_completed", resource_id="backup_7004", archive_present="true", restore_allowed="false", customer_email="null", customer_reference="user_901"),
    ]))},
    status="COMPLETE",
    ops=[("VERIFY_RECORD_ABSENT", "account_7001"), ("VERIFY_RECORD_REDACTED", "support_7007"), ("VERIFY_RECORD_ANONYMIZED", "analytics_7006"), ("VERIFY_RESTORE_BLOCKED", "backup_7004")]))

# 3. evidence captured AFTER audit_at must not change the answer
SCENARIOS.append(dict(
    name="late evidence ignored: a post-audit snapshot showing the profile must not flip the verdict",
    users=[U1], audit=[AUD1], request=REQ("ada.new@example.org", at="2026-09-10T12:00:00Z"),
    evidence={
        "before.log": ("2026-09-10T11:00:00Z", "\n".join([
            L("2026-09-10T10:00:00Z", "account_service", "t1", "profile_lookup_started", resource_id="account_7001", lookup_key="ada.new@example.org"),
            L("2026-09-10T10:00:01Z", "account_service", "t1", "profile_lookup_completed", resource_id="account_7001", rows=0, http_status=404)])),
        "after.log": ("2026-09-20T11:00:00Z", "\n".join([
            L("2026-09-20T10:00:00Z", "account_service", "t9", "profile_lookup_started", resource_id="account_7001", lookup_key="ada.new@example.org"),
            L("2026-09-20T10:00:01Z", "account_service", "t9", "profile_lookup_completed", resource_id="account_7001", rows=1, http_status=200, email="ada.new@example.org", customer_reference="user_901")]))},
    status="COMPLETE", ops=[("VERIFY_RECORD_ABSENT", "account_7001")]))

# 4. a request that is not a deletion request
SCENARIOS.append(dict(
    name="non-deletion request (correction) -> NOT_APPLICABLE with empty steps",
    users=[U1], audit=[AUD1], request=REQ("ada.new@example.org", text="My surname is misspelled. Please correct it without removing any of my records."),
    evidence={"trace.log": ("2026-09-10T11:00:00Z", L("2026-09-10T10:00:00Z", "search_service", "t2", "index_query_completed", resource_id="search_7002", result="hit", email="ada.new@example.org"))},
    status="NOT_APPLICABLE", ops=[]))

# 5. same-language robustness: French deletion request with an exposed support message
SCENARIOS.append(dict(
    name="multilingual request (French) with an exposed support message -> redact plan",
    users=[U1], audit=[AUD1], request=REQ("ada.new@example.org", text="Je demande la suppression définitive de mon compte et de mes données personnelles."),
    evidence={"trace.log": ("2026-09-10T11:00:00Z", "\n".join([
        L("2026-09-10T10:00:00Z", "support_platform", "t3", "support_ticket_read_started", resource_id="support_7007", lookup_key="ada.new@example.org"),
        L("2026-09-10T10:00:01Z", "support_platform", "t3", "support_ticket_read_completed", resource_id="support_7007", customer_reference="null", requester_name="Ada", requester_email="ada.new@example.org", requester_phone="null")]))},
    status="INCOMPLETE", ops=[("REPORT_EXPOSED_PII", "support_7007"), ("REDACT_RECORD", "support_7007"), ("VERIFY_RECORD_REDACTED", "support_7007")]))


def run_scenarios(verbose=True):
    results = []
    for sc in SCENARIOS:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            build(d, users=sc["users"], audit=sc["audit"], request=sc["request"], evidence=sc["evidence"])
            _, res, rows = run(d, "deletion_requests.csv")
        r = rows[0]
        got = [(s["operation"], s["resource_id"]) for s in json.loads(r["steps_json"])]
        ok_status, ok_steps = r["deletion_status"] == sc["status"], got == sc["ops"]
        results.append({"name": sc["name"], "status_ok": ok_status, "steps_ok": ok_steps, "got": got, "expected": sc["ops"], "got_status": r["deletion_status"]})
        if verbose:
            print(("PASS" if ok_status and ok_steps else "FAIL"), sc["name"])
            if not (ok_status and ok_steps):
                print("   status", r["deletion_status"], "expected", sc["status"]); print("   got     ", got); print("   expected", sc["ops"])
    return results


if __name__ == "__main__":
    out = run_scenarios()
    sys.exit(0 if all(r["status_ok"] and r["steps_ok"] for r in out) else 1)
