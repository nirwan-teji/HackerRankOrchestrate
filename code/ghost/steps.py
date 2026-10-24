"""Policy -> ordered findings / remediation / verification steps (deletion_rules.csv semantics)."""
from __future__ import annotations

from dataclasses import dataclass

OP_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
PHASE = {op: ("FINDING" if op.startswith("REPORT") else "VERIFICATION" if op.startswith("VERIFY") else "REMEDIATION") for op in OP_ORDER}
STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}

DELETE_CATS = {"USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY", "USER_EXPORT",
               "MARKETING_PROFILE", "SUPPORT_ATTACHMENT"}
ANON_CATS = {"ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"}
REDACT_CATS = {"SUPPORT_MESSAGE", "EMAIL_DELIVERY_LOG", "ACCOUNT_TOMBSTONE"}
RETAIN_CATS = {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"}


@dataclass
class Step:
    system_id: str
    resource_id: str
    operation: str
    evidence_id: str = ""

    @property
    def phase(self) -> str:
        return PHASE[self.operation]

    def key(self):
        return (OP_ORDER.index(self.operation), self.system_id, self.resource_id)

    def as_dict(self, n: int) -> dict:
        return {"step_no": n, "phase": self.phase, "system_id": self.system_id, "resource_id": self.resource_id,
                "operation": self.operation, "state": STATE[self.operation], "evidence_id": self.evidence_id}


def plan_for(r) -> tuple[list[Step], list[Step], list[Step]]:
    """(findings, remediation, verification) for one resource state; evidence ids are attached by the caller."""
    f, m, v = [], [], []
    s = lambda op, ev="": Step(r.system, r.rid, op, ev)
    if r.cat in DELETE_CATS:
        if r.state in ("PRESENT", "EXPOSED"):
            f.append(s("REPORT_REMAINING_RECORD", r.evidence)); m.append(s("DELETE_RECORD"))
        v.append(s("VERIFY_RECORD_ABSENT", r.evidence))
    elif r.cat in ANON_CATS:
        if r.state in ("PRESENT", "EXPOSED"):
            f.append(s("REPORT_EXPOSED_PII", r.evidence)); m.append(s("ANONYMIZE_RECORD"))
        v.append(s("VERIFY_RECORD_ANONYMIZED", r.evidence))
    elif r.cat in REDACT_CATS:
        if r.state in ("PRESENT", "EXPOSED"):
            f.append(s("REPORT_EXPOSED_PII", r.evidence)); m.append(s("REDACT_RECORD"))
        v.append(s("VERIFY_RECORD_REDACTED", r.evidence))
    elif r.cat in RETAIN_CATS:
        v.append(s("VERIFY_RECORD_PRESENT", r.evidence))
    elif r.cat == "BACKUP_ARCHIVE":
        if r.state == "RESTORABLE":
            f.append(s("REPORT_RESTORE_RISK", r.evidence)); m.append(s("BLOCK_RESTORE"))
        v.append(s("VERIFY_RESTORE_BLOCKED", r.evidence))
    elif r.cat == "DATA_FLOW":
        if r.state == "ENABLED":
            f.append(s("REPORT_ACTIVE_DATA_FLOW", r.evidence)); m.append(s("DISABLE_DATA_FLOW"))
        v.append(s("VERIFY_DATA_FLOW_DISABLED", r.evidence))
    return f, m, v


def build_steps(resources) -> tuple[list[Step], str]:
    F, M, V = [], [], []
    for r in resources:
        f, m, v = plan_for(r)
        F += f; M += m; V += v
    status = "INCOMPLETE" if F else "COMPLETE"
    if status == "INCOMPLETE":  # remediation/verification describe future work -> no evidence ids
        for st in M + V:
            st.evidence_id = ""
    out = sorted(F, key=Step.key) + sorted(M, key=Step.key) + sorted(V, key=Step.key)
    return out, status


def to_json(steps: list[Step]) -> list[dict]:
    return [s.as_dict(i) for i, s in enumerate(steps, 1)]
