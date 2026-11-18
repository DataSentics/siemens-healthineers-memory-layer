"""Which memory applies to this user and this question, and how much the team agrees with it.

Four levels, closest wins: personal > team > department > gold.
- What a user teaches about an existing term is personal first. A brand-new term goes to the team at once
  (service.apply), if the user shares; otherwise it stays theirs.
- A team meaning changes only when enough teammates use another meaning for themselves (`needed`). Teammates see
  those meanings, without names, as other definitions in use, so they can pick the same one and converge.
- A department meaning forms when enough of the department's teams have the same team meaning (`needed_teams`).
  Teams with another meaning keep theirs: the team level comes first.
- Other departments' meanings are shown to everyone as other definitions in use, never applied.
- Confidence measures one thing: how many teammates use a different meaning, out of the number needed to replace
  the shared one. Nobody disagreeing is 100%; one short of replacing it is close to 0.
- Official (gold) terms replaced by teams or departments in many departments are marked untrustworthy and otherwise
  left alone.
- Meanings fade: confidence drops for every full month a personal, team or department meaning was not used, and after
  `DECAY_MONTHS` unused months it is no longer applied. Gold never fades; its owners maintain it.
- Consent: only teammates who agreed to share count towards changing the team meaning, or are shown as others.
- Two meanings are the same when their wording differs only in case, punctuation, articles, plurals or a couple of
  typos (`consolidate.same_meaning`).

The thresholds are placeholders for K9 / N4.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .consolidate import same_meaning
from .models import SHAREABLE_TYPES, Alternative, MemoryItem, UsedItem
from .org import Person

WARN_BELOW = 0.6  # K9: confidence under this shows a warning instead of a note
MIN_CONFIDENCE = 0.05
DECAY_MONTHS = 6  # a personal, team or department meaning unused this long is no longer applied


def months_unused(item: MemoryItem, today: str) -> int:
    """Full calendar months since the item was last used (or created, if never used since)."""
    last = (item.last_used or item.created_at)[:10]
    (ly, lm, ld), (ty, tm, td) = (map(int, last.split("-")), map(int, today[:10].split("-")))
    return max(0, (ty - ly) * 12 + (tm - lm) - (td < ld))


def fades(item: MemoryItem) -> bool:
    return item.origin != "gold" and item.item_type in SHAREABLE_TYPES


def freshness(item: MemoryItem, today: str) -> float:
    """1.0 when used this month, then one sixth less for each unused month; 0 means expired."""
    return max(0.0, 1 - months_unused(item, today) / DECAY_MONTHS) if fades(item) else 1.0


def expired(item: MemoryItem, today: str) -> bool:
    return fades(item) and months_unused(item, today) >= DECAY_MONTHS


def needed(team_size: int) -> int:
    """Teammates who must use another meaning before the team meaning changes: half the team, at least two."""
    return max(2, math.ceil(team_size / 2))


def needed_teams(teams: int) -> int:
    """Teams with the same meaning before it becomes the department's: half the teams, at least two (all of them, in a
    department with fewer than two teams)."""
    return max(min(2, teams), math.ceil(teams / 2))


def disagreement(overrides: int, need: int) -> str:
    """'1 colleague uses another meaning; 3 are needed to change it for the team'"""
    who = "1 colleague uses" if overrides == 1 else f"{overrides} colleagues use"
    return f"{who} another meaning; {need} are needed to change it for the team"


def confidence(overrides: int, need: int) -> float:
    return round(max(1 - overrides / need, MIN_CONFIDENCE), 2)


def untrustworthy_after(departments: int) -> int:
    """Departments that replaced an official term (in a team or for the whole department) before it is marked
    untrustworthy: half, at least 2."""
    return max(2, math.ceil(departments / 2))


def meaning_key(item: MemoryItem) -> str:
    """A label for one meaning: its wording, ignoring case and spacing. Equal keys always mean the same; for the full
    comparison use `same_meaning`."""
    return "text:" + " ".join(item.meaning.lower().split())


def group_meanings(items: list[MemoryItem]) -> dict[str, list[MemoryItem]]:
    """Items grouped by meaning (`same_meaning`), oldest first; each group is keyed by its first item's key."""
    groups: dict[str, list[MemoryItem]] = {}
    for it in sorted(items, key=lambda i: i.created_at):
        key = next((k for k, g in groups.items() if same_meaning(g[0], it)), None)
        groups.setdefault(key or meaning_key(it), []).append(it)
    return groups


def _same_person(a: str | None, b: str) -> bool:
    return bool(a) and a.lower() == b.lower()


@dataclass
class TermState:
    """One term as one user sees it."""

    term: str
    mine: MemoryItem | None  # the user's own meaning
    team: MemoryItem | None  # the user's team's shared meaning
    gold: MemoryItem | None
    variants: dict[str, list[MemoryItem]] = field(default_factory=dict)  # teammates' own meanings (the user's too)
    department: MemoryItem | None = None  # the user's department's shared meaning
    others: dict[str, MemoryItem] = field(default_factory=dict)  # other departments' meanings, by department

    @property
    def shared(self) -> MemoryItem | None:
        return self.team or self.department or self.gold

    @property
    def overrides(self) -> int:
        return len({i.owner_email for group in self.variants.values() for i in group})


def term_states(viewer: Person, items: list[MemoryItem], today: str, sharing: set[str] | None = None,
                teammates: set[str] | None = None) -> dict[str, TermState]:
    """`sharing`: teammates who agreed to share their own meanings (None: everyone).
    `teammates`: emails of the viewer's team from the org directory (None: the team stored on each item)."""
    by_term: dict[str, list[MemoryItem]] = defaultdict(list)
    for it in items:
        if it.term and it.status == "active" and not expired(it, today):
            by_term[it.term].append(it)

    def teammate(i: MemoryItem) -> bool:
        if teammates is not None:
            return (i.owner_email or "").lower() in teammates
        return i.owner_team == viewer.team

    out = {}
    for term, its in by_term.items():
        mine = _latest(i for i in its if i.origin == "user" and _same_person(i.owner_email, viewer.email))
        team = _latest(i for i in its if i.origin == "team" and i.owner_team == viewer.team)
        dept = _latest(i for i in its if i.origin == "department" and i.owner_department == viewer.department)
        gold = _latest(i for i in its if i.origin == "gold")
        others = {}
        for i in its:
            if i.origin == "department" and i.owner_department != viewer.department:
                if i.owner_department not in others or i.created_at > others[i.owner_department].created_at:
                    others[i.owner_department] = i
        shared = team or dept or gold
        latest_per_person: dict[str, MemoryItem] = {}
        for i in its:
            if (i.origin == "user" and teammate(i) and i.shareable and i.item_type in SHAREABLE_TYPES
                    and (sharing is None or i.owner_email in sharing)
                    and (shared is None or not same_meaning(i, shared))):
                prev = latest_per_person.get(i.owner_email)
                if prev is None or i.created_at > prev.created_at:
                    latest_per_person[i.owner_email] = i
        out[term] = TermState(term, mine, team, gold, group_meanings(list(latest_per_person.values())), dept,
                              dict(sorted(others.items())))
    return out


def untrustworthy_terms(items: list[MemoryItem], departments: int) -> dict[str, set[str]]:
    """Official terms that teams or departments in at least `untrustworthy_after(departments)` departments have
    replaced with a different meaning."""
    gold = {i.term: i for i in items if i.origin == "gold" and i.status == "active"}
    replaced: dict[str, set[str]] = defaultdict(set)
    for i in items:
        if (i.origin in ("team", "department") and i.status == "active" and i.term in gold
                and not same_meaning(i, gold[i.term])):
            replaced[i.term].add(i.owner_department)
    return {t: d for t, d in replaced.items() if len(d) >= untrustworthy_after(departments)}


SUPPORT = {"personal": "you", "team": "your team", "department": "your department", "gold": "gold (official definition)",
           "unresolved": "not defined for you"}


def support_label(scope: str) -> str:
    return SUPPORT[scope]


def _flex(key: str) -> str:
    """Regex for one term key that also accepts other spacing: 'somaris10' matches 'somaris 10' and 'somaris-10',
    'memory errors' matches 'memory-errors'."""
    parts = []
    for word in key.split():
        pieces = re.findall(r"[a-z]+|[0-9]+|[^a-z0-9]+", word)
        parts.append(r"[\s-]?".join(re.escape(p) for p in pieces))
    return r"[\s-]?".join(parts)


def _pattern(keys: list[str]) -> re.Pattern | None:
    keys = [k for k in keys if k]
    if not keys:
        return None
    # "the quiet rack" is also mentioned as "quiet rack": a leading article is optional when at least two words are
    # left ("the system" must not fire on every question with "system" in it)
    keys += [rest for article, _, rest in (k.partition(" ") for k in keys)
             if article in ("the", "a", "an") and " " in rest]
    variants = set()
    for k in keys:
        variants.add(_flex(k))
        if k.endswith("s"):
            variants.add(_flex(k[:-1]))
    return re.compile(r"(?<![\w-])(?:" + "|".join(sorted(variants, key=len, reverse=True)) + r")(?:e?s)?(?![\w-])")


def mentions(item: MemoryItem, question: str) -> bool:
    p = _pattern([item.term, *item.aliases])
    return bool(p and p.search(question.lower()))


def resolve(
    viewer: Person,
    question: str,
    candidates: list[MemoryItem],
    team_size: int,
    departments: int,
    dismissed_terms: set[str] = frozenset(),
    facts: list[MemoryItem] = (),
    today: str | None = None,
    sharing: set[str] | None = None,
    matches: dict | None = None,
    teammates: set[str] | None = None,
) -> list[UsedItem]:
    """`candidates`: the viewer's own items, gold, every team's and department's shared meanings and teammates' own
    terms. `facts`: the viewer's own facts relevant to this question (personal memory search).
    `sharing`: teammates who agreed to share their own meanings (None: everyone).
    `matches`: terms found by meaning or spelling (semantic.Match per term), on top of exact wording.
    `teammates`: the viewer's team from the org directory (None: the team stored on each item)."""
    from .models import today as demo_today

    today = today or demo_today()
    used: list[UsedItem] = []

    # Personal preferences apply to every answer (FR15).
    for it in candidates:
        if it.item_type == "preference" and it.status == "active" and _same_person(it.owner_email, viewer.email):
            used.append(_own(it, f"applied your preference: {it.meaning}", "Your own preference."))
    for it in facts:
        used.append(_own(it, f"used what you told me: {it.meaning}", "Something you told me earlier."))

    flagged = untrustworthy_terms(candidates, departments)
    need = needed(team_size)
    for term, st in term_states(viewer, candidates, today, sharing, teammates).items():
        group = ([i for i in (st.mine, st.team, st.department, st.gold) if i] + list(st.others.values())
                 + [i for g in st.variants.values() for i in g])
        fuzzy = None
        if not any(mentions(i, question) for i in group):
            fuzzy = (matches or {}).get(term)
            if fuzzy is None:
                continue
        if st.mine:
            item = _own(st.mine, f"interpreted “{term}” as {st.mine.meaning}", "Your own definition; it comes first.")
            item.alternatives = alternatives(st, flagged, applied=st.mine, colleagues=False)
            item.months_unused = months_unused(st.mine, today)
            if fuzzy:
                item.matched_by, item.user_wording = fuzzy.describe(), fuzzy.wording
            if item.months_unused:
                item.reason += f" Not used for {plural_months(item.months_unused)}; it expires after {DECAY_MONTHS}."
            used.append(item)
            continue
        if st.shared is None:
            if st.others:  # only other departments define it: shown, never applied
                used.append(UsedItem(
                    item_id="", item_type="term", term=term, meaning="", scope="unresolved",
                    support_label=support_label("unresolved"), applied_as="not applied", confidence=None,
                    level="note", reason="Neither you, your team, your department nor the official glossary define "
                                         "this term. Other departments do; pick one with Use this to keep it.",
                    alternatives=alternatives(st, flagged, applied=None),
                    matched_by=fuzzy.describe() if fuzzy else None, user_wording=fuzzy.wording if fuzzy else None,
                ))
            continue  # otherwise only a teammate's own new term they do not share
        win = st.shared
        scope = "team" if win is st.team else "department" if win is st.department else "gold"
        k = st.overrides
        unused = months_unused(win, today) if fades(win) else 0
        conf = round(max(confidence(k, need) * freshness(win, today), MIN_CONFIDENCE), 2)
        level = "warning" if conf < WARN_BELOW else "note"
        reason = _reason(scope, k, need, st, flagged)
        if unused:
            reason += (f" Not used for {plural_months(unused)}: confidence drops each unused month, and the meaning "
                       f"expires after {DECAY_MONTHS}.")
        if level == "warning" and term in dismissed_terms:
            level, reason = "note", reason + " (You chose not to be warned about this term.)"
        used.append(UsedItem(
            item_id=win.item_id, item_type=win.item_type, term=term, meaning=win.meaning, scope=scope, support_label=support_label(scope), applied_as=f"interpreted “{term}” as {win.meaning}",
            confidence=conf, level=level, reason=reason, overrides=k, needed=need, months_unused=unused,
            untrustworthy=scope == "gold" and term in flagged, alternatives=alternatives(st, flagged, applied=win),
            matched_by=fuzzy.describe() if fuzzy else None, user_wording=fuzzy.wording if fuzzy else None,
        ))
    return used


def alternatives(st: TermState, flagged: dict[str, set[str]], applied: MemoryItem | None,
                 colleagues: bool = True) -> list[Alternative]:
    """Other meanings in use, without names: teammates' own (most used first), then the team's, the department's,
    the official one and other departments', each only when it differs from the applied one and from the others.
    Teammates who went back to one of the shared meanings are counted there."""
    shared: list[tuple[MemoryItem, Alternative]] = []
    seen = [applied] if applied else []
    levels = [(st.team, "team"), (st.department, "department"), (st.gold, "gold")]
    levels += [(it, "other_department") for it in st.others.values()]
    for it, scope in levels:
        if it is None or any(same_meaning(it, s) for s in seen):
            continue
        seen.append(it)
        shared.append((it, Alternative(it.item_id, it.meaning, scope, untrustworthy=scope == "gold" and st.term in flagged,
                                       department=it.owner_department if scope == "other_department" else None)))
    own: list[Alternative] = []
    if colleagues:
        for g in sorted(st.variants.values(), key=lambda g: (-len(g), _neg_time(max(i.created_at for i in g)))):
            hit = next((a for it, a in shared if same_meaning(it, g[0])), None)
            if hit is not None:
                hit.holders = len(g)
            elif applied is None or not same_meaning(applied, g[0]):
                own.append(Alternative(g[0].item_id, g[0].meaning, "colleagues", holders=len(g)))
    return own + [a for _, a in shared]


def plural_months(n: int) -> str:
    return f"{n} month{'' if n == 1 else 's'}"


def _own(it: MemoryItem, applied_as: str, reason: str) -> UsedItem:
    return UsedItem(
        item_id=it.item_id, item_type=it.item_type, term=it.term, meaning=it.meaning, scope="personal", support_label="you", applied_as=applied_as, confidence=None, level="note", reason=reason,
    )


def _reason(scope: str, k: int, need: int, st: TermState, flagged: dict[str, set[str]]) -> str:
    whose = {"team": "Your team's meaning.", "department": "Your department's meaning.", "gold": "Official definition."}
    parts = [whose[scope], "Nobody in your team uses another one." if k == 0 else disagreement(k, need).capitalize() + "."]
    if scope == "team" and st.department is not None and not same_meaning(st.team, st.department):
        parts.append("It replaces your department's meaning for your team.")
    if scope in ("team", "department") and st.gold is not None and not same_meaning(st.shared, st.gold):
        parts.append(f"It replaces the official definition for your {scope}.")
    if scope == "gold" and st.term in flagged:
        parts.append(f"Teams in {len(flagged[st.term])} departments have replaced this official definition.")
    return " ".join(parts)


def _latest(items) -> MemoryItem | None:
    return max(items, key=lambda i: i.created_at, default=None)


def _neg_time(ts: str) -> str:
    # newer first inside sort(); ISO timestamps sort lexicographically
    return "".join(chr(0x10FFFF - ord(c)) for c in ts)


def context_block(used: list[UsedItem]) -> str:
    """Memory section appended to the agent's system prompt (injected by the middleware, FR17)."""
    used = [u for u in used if u.scope != "unresolved"]  # shown to the user, never applied
    if not used:
        return ""
    lines = [
        "## Memory: how this user and their colleagues use terms",
        "Apply these interpretations when you write SQL and when you phrase the answer: select exactly what each "
        "definition says, with the exact values, names and texts it gives. They override your own assumptions about "
        "what a term means. When another agent or tool writes the SQL for you, pass the definitions of the terms in "
        "its task on to it, word for word. Do not mention this memory section; the UI shows it.",
    ]
    prefs = [u for u in used if u.item_type == "preference"]
    facts = [u for u in used if u.item_type == "fact" and not u.term]
    terms = [u for u in used if u not in prefs and u not in facts]
    for u in terms:
        line = f"- “{u.term}” means: {u.meaning}"
        if u.user_wording:
            line = f"- “{u.term}” (the user wrote “{u.user_wording}”) means: {u.meaning}"
        lines.append(line)
    if facts:
        lines.append("What this user told you earlier:")
        lines += [f"- {u.meaning}" for u in facts]
    if prefs:
        lines.append("User preferences (apply to the form of the answer):")
        lines += [f"- {u.meaning}" for u in prefs]
    return "\n".join(lines)
