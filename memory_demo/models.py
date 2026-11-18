"""Data model of the memory layer.

Memory is append-friendly: items are never edited in place. A correction creates a new item that
`supersedes` the old one; undo / forget flip `status` and leave a signal behind (K2/K3 lean, PRD §10).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal

# Memory keeps shared vocabulary only: what terms mean, and how components or event groups relate.
ItemType = Literal["term", "relation"]
# user: one person's item (personal). team: the team's shared meaning, made when enough teammates agree.
# department: the department's, made when enough of its teams agree. gold: official.
Origin = Literal["user", "team", "department", "gold"]
Scope = Literal["personal", "team", "department", "gold"]
SignalKind = Literal["author", "confirm", "correct", "shown", "dismiss", "undo", "withdraw", "adopt", "consent"]
Level = Literal["note", "warning"]
Relevance = Literal["high", "medium", "low"]

ITEM_TYPES = {"term", "relation"}  # rows of other types (from before memory kept vocabulary only) are not read


# Demo clock: the data is a 2005 log, so "today" is simulated. Time of day stays real so ordering within a day works.
_today: date | None = None


_last_stamp: datetime | None = None
_stamp_lock = threading.Lock()


def set_today(day: str | None) -> None:
    global _today, _last_stamp
    with _stamp_lock:
        _today = date.fromisoformat(day) if day else None
        _last_stamp = None  # the demo clock may move back (reset)


def months_later(day: str, n: int = 1) -> str:
    d = date.fromisoformat(day)
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1).isoformat()


def today() -> str:
    return (_today or datetime.now(timezone.utc).date()).isoformat()


def now_iso() -> str:
    """A timestamp that always increases within this process. "Latest wins" (consent choices, a person's newest
    meaning) is decided by comparing these, so two writes in the same instant must not tie, and the demo clock
    (a fixed date with the real time of day) must not jump back at midnight."""
    global _last_stamp
    with _stamp_lock:
        now = datetime.now(timezone.utc)
        if _today:
            now = datetime.combine(_today, now.timetz())
        if _last_stamp is not None and now <= _last_stamp:
            now = _last_stamp + timedelta(microseconds=1)
        _last_stamp = now
        return now.isoformat(timespec="microseconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def norm_term(term: str) -> str:
    return " ".join(term.lower().strip().strip("\"'").split())


@dataclass
class MemoryItem:
    item_type: ItemType
    meaning: str
    owner_email: str | None  # None for gold, team and department items
    owner_team: str | None  # None for department items
    owner_department: str | None
    term: str | None = None  # normalised key
    aliases: list[str] = field(default_factory=list)
    origin: Origin = "user"
    # May a user's own item count towards a team meaning, and be shown to teammates (without the owner's name) as
    # another meaning in use, when its owner shares? Always True now: only items saved before memory kept vocabulary
    # only (a name that made sense for its owner alone) carry False, and those stay unshared.
    shareable: bool = True
    status: Literal["active", "withdrawn"] = "active"
    supersedes: str | None = None
    item_id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
    last_used: str | None = None  # read from the signals (not stored): last time it was applied, taught or adopted

    def __post_init__(self) -> None:
        if self.term:
            self.term = norm_term(self.term)
        self.aliases = [norm_term(a) for a in self.aliases]


@dataclass
class Signal:
    item_id: str
    user_email: str
    team: str
    department: str
    kind: SignalKind
    term: str | None = None
    created_at: str = field(default_factory=now_iso)


@dataclass
class Alternative:
    """Another meaning the user could pick instead. `scope`: team, department (the user's own), other_department,
    gold, or colleagues (teammates' own meanings)."""

    item_id: str
    meaning: str
    scope: str
    holders: int = 0  # colleagues using it (colleagues only)
    untrustworthy: bool = False
    department: str | None = None  # other_department: whose


@dataclass
class UsedItem:
    """One memory item applied to an answer: one row in the drop-down (FR8) and in the trace (FR19)."""

    item_id: str
    item_type: ItemType
    term: str | None
    meaning: str
    scope: Scope
    support_label: str  # "you", "your team", "your department", "gold (official definition)"
    applied_as: str
    confidence: float | None  # None for the user's own items: nobody else's agreement is involved
    level: Level
    reason: str
    overrides: int = 0  # teammates who use a different meaning for themselves
    needed: int = 0  # teammates needed to replace the shared meaning
    untrustworthy: bool = False  # gold only: replaced by teams in many departments
    months_unused: int = 0  # full months since it was last used; confidence fades with them
    alternatives: list[Alternative] = field(default_factory=list)
    relevance: Relevance | None = None  # how well it fitted this question; rated after the answer
    matched_by: str | None = None  # set when found by meaning or spelling (Mem0 switch), not by exact wording
    user_wording: str | None = None  # what the user wrote for it in that case
    relevance_reason: str | None = None


@dataclass
class MemoryTrace:
    turn_id: str
    user_email: str
    question: str
    items: list[UsedItem] = field(default_factory=list)
    latency_ms: int = 0
    note: str | None = None  # e.g. Mem0 matching was on but unavailable for this turn

    @property
    def level(self) -> Level | None:
        if not self.items:
            return None
        return "warning" if any(i.level == "warning" for i in self.items) else "note"

    @property
    def confidence(self) -> float | None:
        return min((i.confidence for i in self.items if i.confidence is not None), default=None)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["level"], d["confidence"] = self.level, self.confidence
        return d


@dataclass
class MemoryWrite:
    """What the layer remembered in a turn: drives the 'I remembered this' notice with undo (FR13)."""

    # team: a new term, or enough teammates agreed: the team meaning changed. department: enough teams agreed.
    # same: the user said what memory already holds (another wording or a typo), so nothing new was saved.
    op: Literal["add", "update", "withdraw", "team", "department", "same"]
    item_id: str
    item_type: ItemType
    term: str | None
    meaning: str
    shared_with: str  # human-readable: "only you" / "your team" / "your department"
    superseded_id: str | None = None
    evidence: str | None = None  # correction / stated / inferred (from the extractor)
    note: str | None = None  # team updates: "3 of 5 colleagues use this meaning"
    folded_ids: list[str] = field(default_factory=list)  # team / department updates: items folded into it (for undo)
    same_as: str | None = None  # op "same": where the meaning already is ("your team", "R&D department", ...)

    def to_dict(self) -> dict:
        return asdict(self)
