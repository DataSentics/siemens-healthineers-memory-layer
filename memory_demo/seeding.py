"""Demo states: step 1 = only gold, step 2 = gold + a month of team and department know-how (PRD §7 flows 6 and 7).

Each step also sets the demo clock: step 1 is `start`, step 2 is a month later. Both put the org directory back to
the seed org chart (teams added in the Admin tab disappear).
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import SHAREABLE_TYPES, MemoryItem, Signal, months_later, set_today
from .org import Org
from .store import MemoryStore

SEED = Path(__file__).resolve().parents[1] / "seed"


def _signal(org: Org, item_id: str, email: str, kind: str, term: str | None, at: str | None = None) -> Signal:
    p = org.get(email)
    s = Signal(item_id=item_id, user_email=p.email, team=p.team, department=p.department, kind=kind, term=term)
    if at:
        s.created_at = at
    return s


def load_gold(store: MemoryStore, org: Org) -> int:
    rows = json.loads((SEED / "gold_glossary.json").read_text(encoding="utf-8"))
    items = []
    for r in rows:
        author = org.get(r["author"])
        items.append(MemoryItem(
            item_type="term", term=r["term"], aliases=r.get("aliases", []), meaning=r["meaning"],
            sql_hint=r.get("sql_hint"), origin="gold", owner_email=None, owner_team=author.team,
            owner_department=author.department,
        ))
    store.add_items(items)
    return len(rows)


def load_pilot(store: MemoryStore, org: Org, personal=None) -> int:
    rows = json.loads((SEED / "pilot_memory.json").read_text(encoding="utf-8"))["items"]
    items, signals, teams = [], [], set()
    for r in rows:
        if r["scope"] == "personal":
            author = org.get(r["author"])
            if personal and r["type"] not in SHAREABLE_TYPES:  # preferences go to Mem0 when it is on
                personal.add(author, r["type"], r["meaning"])
                continue
            item = MemoryItem(
                item_type=r["type"], term=r.get("term"), aliases=r.get("aliases", []), meaning=r["meaning"],
                sql_hint=r.get("sql_hint"), owner_email=author.email, owner_team=author.team,
                owner_department=author.department,
            )
        elif r["scope"] == "team":  # nobody owns it; who taught and confirmed it stays as background signals
            author = org.get(r["taught_by"])
            assert author.team == r["team"], r
            item = MemoryItem(
                item_type=r["type"], term=r.get("term"), aliases=r.get("aliases", []), meaning=r["meaning"],
                sql_hint=r.get("sql_hint"), origin="team", owner_email=None, owner_team=author.team,
                owner_department=author.department,
            )
        else:  # a department's shared meaning: the teams in `teams` agreed on it
            author = org.get(r["taught_by"])
            assert author.department == r["department"] and author.team in r["teams"], r
            item = MemoryItem(
                item_type=r["type"], term=r.get("term"), aliases=r.get("aliases", []), meaning=r["meaning"],
                sql_hint=r.get("sql_hint"), origin="department", owner_email=None, owner_team=None,
                owner_department=author.department,
            )
            teams.update(r["teams"])
        if day := r.get("taught_on"):  # an older item, last used on that day
            item.created_at = f"{day}T10:00:00+00:00"
        items.append(item)
        signals.append(_signal(org, item.item_id, author.email, "author", item.term, at=item.created_at))
        signals += [_signal(org, item.item_id, e, "confirm", item.term, at=item.created_at)
                    for e in r.get("confirmed_by", [])]
        teams.add(author.team)
    # The pilot teams have used the assistant for a month, so they agreed to remember and share (FR22).
    assert all(org.members(t) for t in teams), teams
    for p in org.people():
        if p.team in teams:
            signals += [_signal(org, "-", p.email, "consent", "remember:on"),
                        _signal(org, "-", p.email, "consent", "share:on")]
    store.add_items(items)  # two statements in all, not two per item
    store.add_signals(signals)
    return len(rows)


def reset(store: MemoryStore, org: Org, step: int, start: str, personal=None) -> str:
    """`org`: an org.Directory is put back to the seed org chart; a fixed org.Org is used as it is."""
    emails = [p.email for p in org.people()]  # before the reset, so people of added teams are cleared too
    if getattr(org, "seed_csv", None):  # a Directory that owns its seed; a real directory is left alone
        org.load_seed()
    store.clear()
    if personal:
        personal.clear(emails)
    set_today(start)
    n_gold = load_gold(store, org)
    if step == 1:
        return f"Step 1: memory cleared, {n_gold} gold terms loaded."
    set_today(months_later(start))
    n_pilot = load_pilot(store, org, personal)
    return f"Step 2: {n_gold} gold terms + {n_pilot} items from the pilot month."
