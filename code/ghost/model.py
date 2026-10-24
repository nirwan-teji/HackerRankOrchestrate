"""Shared types and vocabulary for the audit engine."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# resource-id prefix -> (system_id, data_category)
PREFIX = {
    "account": ("account_service", "USER_PROFILE"),
    "search": ("search_service", "SEARCH_INDEX_ENTRY"),
    "analytics": ("analytics_platform", "ANALYTICS_PROFILE"),
    "support": ("support_platform", "SUPPORT_MESSAGE"),
    "export": ("export_service", "USER_EXPORT"),
    "marketing": ("marketing_platform", "MARKETING_PROFILE"),
    "audit_record": ("security_service", "SECURITY_AUDIT_LOG"),
    "backup": ("backup_service", "BACKUP_ARCHIVE"),
    "data_flow": ("integration_service", "DATA_FLOW"),
    "tombstone": ("identity_service", "ACCOUNT_TOMBSTONE"),
    "legal_hold": ("legal_service", "LEGAL_HOLD_RECORD"),
    "email_delivery": ("email_delivery_service", "EMAIL_DELIVERY_LOG"),
    "cache": ("cache_service", "CACHE_ENTRY"),
    "object": ("object_storage", "SUPPORT_ATTACHMENT"),
}

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HASH_RX = re.compile(r"\b[0-9a-f]{64}\b")
USERREF_RX = re.compile(r"\buser_\d+\b")
RID_RX = re.compile(r"\b(account|search|analytics|support|export|marketing|audit_record|backup|data_flow|tombstone|legal_hold|email_delivery|cache|object)_\d+\b")


def rid_prefix(rid: str) -> str:
    m = re.match(r"(.*)_\d+$", rid)
    return m.group(1) if m else rid


def system_of(rid: str) -> str:
    return PREFIX.get(rid_prefix(rid), ("", ""))[0]


def category_of(rid: str) -> str:
    return PREFIX.get(rid_prefix(rid), ("", ""))[1]


def norm(v) -> str:
    """Canonical identifier form: lowercase, trimmed, phone punctuation kept."""
    return str(v).strip().lower() if v not in (None, "", "null") else ""


@dataclass
class Obs:
    """One observation of one resource at one time, taken from one evidence file."""

    file_id: str
    t: str  # ISO-8601 observation time
    rid: str
    state: str  # PRESENT | ABSENT | EXPOSED | MASKED | ENABLED | DISABLED | RESTORABLE | BLOCKED
    system: str = ""
    cat: str = ""
    idents: frozenset = frozenset()  # normalised identifiers visible on the record
    attrs: dict = field(default_factory=dict)
    kind: str = "record"  # record | lookup | scope-absence

    def __post_init__(self):
        if not self.system:
            self.system = system_of(self.rid)
        if not self.cat:
            self.cat = category_of(self.rid)
