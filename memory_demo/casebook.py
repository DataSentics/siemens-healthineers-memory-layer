"""Casebook (Michal's approach, PRD K1/K5): questions that were answered well, with the SQL that answered them.

A case is saved automatically after an answer that ran SQL. When the user's next message corrects that answer, or
they reject it (👎 / regenerate / pick another meaning), the case is withdrawn. So the casebook keeps answers nobody
objected to, with no curation. Cases are visible to the author and their team and are shown without names.
Similar questions are found by word overlap; good enough for a demo, embeddings later.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .models import new_id, now_iso
from .org import Org
from .store import MemoryStore

CASE_COLS = ["case_id", "question", "sql_text", "owner_email", "owner_team", "owner_department", "status", "created_at"]
_STOP = set("a an and are as at by did do does for from had has have how i in is it last many me month of on or our "
            "show tell that the there this to us was we were what when which who with you".split())


@dataclass
class Case:
    question: str
    sql_text: str
    owner_email: str
    owner_team: str
    owner_department: str
    status: str = "active"
    case_id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[^\W_][\w/]*", text.lower()) if w not in _STOP and len(w) > 1}


class Casebook:
    def __init__(self, store: MemoryStore, org: Org):
        self.ex, self.org = store.ex, org

    def add(self, email: str, question: str, sql: str) -> Case:
        p = self.org.get(email)
        case = Case(question=question, sql_text=sql, owner_email=p.email, owner_team=p.team,
                    owner_department=p.department)
        self.ex.run(f"INSERT INTO casebook ({', '.join(CASE_COLS)}) VALUES ({', '.join(':' + c for c in CASE_COLS)})",
                    asdict(case))
        return case

    def withdraw(self, case_id: str) -> None:
        self.ex.run("UPDATE casebook SET status = 'withdrawn' WHERE case_id = :c", {"c": case_id})

    def withdraw_latest(self, email: str) -> None:
        rows = self.ex.run("SELECT case_id FROM casebook WHERE owner_email = :e AND status = 'active' "
                           "ORDER BY created_at DESC LIMIT 1", {"e": email})
        if rows:
            self.withdraw(rows[0]["case_id"])

    def search(self, email: str, question: str, k: int = 3, min_score: float = 0.34) -> list[dict]:
        p = self.org.get(email)
        rows = self.ex.run("SELECT * FROM casebook WHERE status = 'active' AND (owner_email = :e OR owner_team = :t)",
                           {"e": p.email, "t": p.team})
        q = _words(question)
        scored = []
        for r in rows:
            w = _words(r["question"])
            score = len(q & w) / len(q | w) if q | w else 0.0
            if score >= min_score:
                scored.append({"question": r["question"], "sql": r["sql_text"], "asked_on": r["created_at"][:10],
                               "source": "you" if r["owner_email"] == p.email else "your team",
                               "score": round(score, 2)})
        return sorted(scored, key=lambda c: -c["score"])[:k]

    def all(self) -> list[dict]:
        return self.ex.run("SELECT question, owner_team, status, created_at FROM casebook ORDER BY created_at")
