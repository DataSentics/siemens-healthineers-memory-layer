"""Email -> team / department / manager lookup (PRD §8, TD S4).

At runtime AskFST only knows the company email; everything else comes from the org directory. Teams and departments
are never listed in code or stored as a list in memory: they are whatever the directory says today. A team that
appears overnight (the HR / Entra ID sync adds its people) works at once, with an empty team memory and its
department's meanings, and no one has to add it anywhere.

- Org: a snapshot of the directory (tests build one straight from the seed CSV).
- Directory: the directory table (Delta on Databricks, SQLite locally), re-read every `ttl` seconds, so changes made
  by the sync show up without a restart or redeploy.
"""

from __future__ import annotations

import csv
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Person:
    email: str
    name: str
    department: str
    team: str  # globally unique, e.g. "R&D / A"; Marketing has a single team = its department
    manager_email: str
    is_manager: bool
    joined: str


ORG_COLS = ["email", "name", "department", "team", "manager_email", "is_manager", "joined"]
DOMAIN = "shs-demo.example"
INSERT_ROWS = 40  # people per INSERT (280 parameters), so a long list never becomes one huge statement


class Org:
    """One snapshot of the directory."""

    def __init__(self, people: list[Person]):
        self._by_email = {p.email.lower(): p for p in people}

    @classmethod
    def from_csv(cls, path: str | Path) -> "Org":
        return cls(read_csv(path))

    def get(self, email: str) -> Person:
        try:
            return self._by_email[email.lower()]
        except KeyError:
            raise KeyError(f"unknown user {email!r}: not in the org directory") from None

    def known(self, email: str) -> bool:
        return email.lower() in self._by_email

    def members(self, team: str, on: str | None = None) -> list[Person]:
        """Members of `team` who had joined by the day `on` (ISO date; None: everyone)."""
        return [p for p in self._by_email.values() if p.team == team and (on is None or p.joined <= on)]

    def team_size(self, team: str, on: str) -> int:
        return len(self.members(team, on))

    def teams(self, on: str | None = None) -> dict[str, str]:
        """Team -> department, for every team with at least one member by `on`."""
        return {p.team: p.department for p in self._by_email.values() if on is None or p.joined <= on}

    def teams_in(self, department: str, on: str | None = None) -> list[str]:
        return sorted(t for t, d in self.teams(on).items() if d == department)

    def department_names(self) -> list[str]:
        return sorted({p.department for p in self._by_email.values()})

    def departments(self) -> int:
        return len(self.department_names())

    def people(self) -> list[Person]:
        return sorted(self._by_email.values(), key=lambda p: (p.department, p.team, not p.is_manager, p.name))


def new_team(org: Org, department: str, team: str, names: list[str], joined: str, domain: str = DOMAIN
             ) -> list[Person]:
    """The people of a new team as the HR sync would add them (demo). The first name is the manager.
    Raises ValueError with a message for the user when something is missing or the team already exists."""
    department, team = department.strip(), team.strip()
    names = [" ".join(n.split()) for n in names if n.strip()]
    if not department:
        raise ValueError("Name the department.")
    if not team:
        raise ValueError("Name the team.")
    if team in org.teams():
        raise ValueError(f"{team} already exists.")
    if not names:
        raise ValueError("Add at least one person.")
    taken = {p.email.lower() for p in org.people()}
    people, manager = [], None
    for n, name in enumerate(names):
        local = ".".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore")
                                    .decode().lower())) or "person"
        email, k = f"{local}@{domain}", 2
        while email in taken:
            email, k = f"{local}{k}@{domain}", k + 1
        taken.add(email)
        manager = manager or email
        people.append(Person(email, name, department, team, manager, n == 0, joined))
    return people


def read_csv(path: str | Path) -> list[Person]:
    """People from a CSV with the columns ORG_COLS (`is_manager`: true / false; `joined`: an ISO date, or empty for
    always). A byte-order mark (Excel's "CSV UTF-8") and spaces around values are ignored."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [{c: (r.get(c) or "").strip() for c in ORG_COLS} for r in csv.DictReader(f)]
    return [Person(r["email"], r["name"], r["department"], r["team"], r["manager_email"],
                   r["is_manager"].lower() == "true", r["joined"]) for r in rows]


class Directory:
    """The org directory table. In production a nightly HR / Entra ID sync writes it and this class only reads it
    (`seed_csv=None`). In the demo the seed CSV fills it once, the Admin tab can add a team (standing in for the
    sync) and a reset puts the seed back. Reads are served from a snapshot that is refreshed every `ttl` seconds,
    and when an unknown email turns up (at most once every `miss_ttl` seconds, so a stream of unknown callers cannot
    make every request re-read the table)."""

    def __init__(self, ex, seed_csv: str | Path | None, ttl: float = 300, miss_ttl: float = 30):
        self.ex, self.seed_csv, self.ttl, self.miss_ttl = ex, seed_csv, ttl, miss_ttl
        self.lock = threading.Lock()
        from .store import ORG_DDL

        self.ex.run(ORG_DDL.replace("{T}", getattr(ex, "text_type", "STRING")))
        self._snap: Org | None = None
        self.read_at = 0.0
        self.read_at_wall = datetime.now(timezone.utc)
        if seed_csv and not self.snapshot().people():
            self.load_seed()

    # ---- reading -------------------------------------------------------------------------------------------
    def snapshot(self) -> Org:
        with self.lock:
            if self._snap is None or time.monotonic() - self.read_at >= self.ttl:
                self._read()
            return self._snap

    def refresh(self) -> Org:
        with self.lock:
            self._read()
            return self._snap

    def _read(self) -> None:
        rows = self.ex.run(f"SELECT {', '.join(ORG_COLS)} FROM org_people")
        self._snap = Org([Person(r["email"], r["name"], r["department"], r["team"], r["manager_email"],
                                 bool(r["is_manager"]), r["joined"]) for r in rows])
        self.read_at = time.monotonic()
        self.read_at_wall = datetime.now(timezone.utc)

    def get(self, email: str) -> Person:
        snap = self.snapshot()
        if not snap.known(email):  # maybe added by the sync since the last read
            with self.lock:
                if time.monotonic() - self.read_at >= self.miss_ttl:
                    self._read()
                snap = self._snap
        return snap.get(email)

    def known(self, email: str) -> bool:
        return self.snapshot().known(email)

    def members(self, team: str, on: str | None = None) -> list[Person]:
        return self.snapshot().members(team, on)

    def team_size(self, team: str, on: str) -> int:
        return self.snapshot().team_size(team, on)

    def teams(self, on: str | None = None) -> dict[str, str]:
        return self.snapshot().teams(on)

    def teams_in(self, department: str, on: str | None = None) -> list[str]:
        return self.snapshot().teams_in(department, on)

    def department_names(self) -> list[str]:
        return self.snapshot().department_names()

    def departments(self) -> int:
        return self.snapshot().departments()

    def people(self) -> list[Person]:
        return self.snapshot().people()

    # ---- writing (the demo's Admin tab and reset; scripts/pilot_admin.py until the HR sync exists) -----------
    def add_people(self, people: list[Person]) -> None:
        if not people:
            return
        for start in range(0, len(people), INSERT_ROWS):
            values, params = [], {}
            for n, p in enumerate(people[start:start + INSERT_ROWS]):
                values.append("(" + ", ".join(f":{c}{n}" for c in ORG_COLS) + ")")
                params.update({f"{c}{n}": v for c, v in asdict(p).items()})
            self.ex.run(f"INSERT INTO org_people ({', '.join(ORG_COLS)}) VALUES {', '.join(values)}", params)
        self.refresh()

    def replace(self, people: list[Person]) -> None:
        """The directory becomes exactly these people. Memories of people no longer listed stay in the tables."""
        self.ex.run("DELETE FROM org_people")
        self.add_people(people)

    def load_seed(self) -> None:
        """Back to the seed org chart (demo reset): teams added since are removed."""
        if not self.seed_csv:
            raise RuntimeError("this directory has no seed: it is filled by the HR sync and never wiped here")
        self.replace(read_csv(self.seed_csv))
