"""Resolve a request email to a user and every identifier that user has ever had."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .data import Dataset, canon


@dataclass
class Subject:
    user_id: str
    name: str
    emails: set = field(default_factory=set)
    phones: set = field(default_factory=set)
    hashes: set = field(default_factory=set)

    @property
    def keys(self) -> set:
        """Every canonical identifier token that ties a record to this person."""
        return {self.user_id, *self.emails, *self.phones, *self.hashes}


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


class IdentityIndex:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.by_user = {r["user_id"]: r for r in ds.identities}
        self.hist: dict[str, list[dict]] = {}
        for a in ds.audit_log:
            self.hist.setdefault(a["user_id"], []).append(a)
        self.owner: dict[str, set] = {}  # email/phone token -> users that ever held it
        for uid, r in self.by_user.items():
            for tok in (r["email"], r["phone"], r["email_hash"]):
                self.owner.setdefault(canon(tok), set()).add(uid)
        for a in ds.audit_log:
            for tok in (a["old_value"], a["new_value"]):
                self.owner.setdefault(canon(tok), set()).add(a["user_id"])

    def resolve(self, email: str) -> Subject | None:
        e = canon(email)
        users = self.owner.get(e) or self.owner.get(canon(email_hash(e)), set())
        if not users:
            return None
        # prefer the user whose *current* email is the request email, else the one who held it most recently
        cur = [u for u in users if canon(self.by_user[u]["email"]) == e] if users else []
        uid = sorted(cur or users, key=lambda u: self._last_touch(u, e))[-1]
        return self.subject(uid)

    def _last_touch(self, uid: str, e: str) -> str:
        ts = [a["event_time"] for a in self.hist.get(uid, []) if e in (canon(a["old_value"]), canon(a["new_value"]))]
        return max(ts or [self.by_user[uid]["updated_at"]])

    def subject(self, uid: str) -> Subject:
        r = self.by_user[uid]
        s = Subject(uid, r["name"], {canon(r["email"])}, {canon(r["phone"])}, {canon(r["email_hash"])})
        for a in self.hist.get(uid, []):
            tgt = {"email": s.emails, "phone": s.phones, "email_hash": s.hashes}.get(a["field_name"])
            if tgt is not None:
                tgt.update({canon(a["old_value"]), canon(a["new_value"])})
        for em in list(s.emails):
            s.hashes.add(email_hash(em))
        s.emails.discard("")
        s.phones.discard("")
        s.hashes.discard("")
        return s
