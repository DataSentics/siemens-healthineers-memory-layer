"""MemoryService: the memory layer behind one interface.

The agent middleware (through `api.LocalMemory`) and the demo UI talk to this class, so AskFST and the demo
share one memory (FR18).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict

from langchain_core.language_models import BaseChatModel

from . import consolidate, resolver, seeding
from .extractor import MemoryOp, extract, extract_structured
from .models import MemoryItem, MemoryTrace, MemoryWrite, Signal, new_id, norm_term, today
from .org import Person
from .store import MemoryStore

log = logging.getLogger(__name__)
MAX_TERM, MAX_MEANING = 80, 400  # characters; what a user teaches ends up in colleagues' prompts


class TermLocks:
    """One writer at a time per (department, term). Teaching a term is "read who uses which meaning, decide, write";
    two people doing that at the same moment would both find no team meaning and both create one. The lock covers a
    whole department, because a team's change can also create or fold into the department's meaning.

    Striped (a fixed number of locks, picked by hash) so it does not grow with the number of terms. It serialises
    writers inside this process, which is where they are (one memory service); a second process on the same tables
    is covered by "the latest one wins" on read."""

    def __init__(self, stripes: int = 64):
        self._locks = [threading.RLock() for _ in range(stripes)]

    def __call__(self, department: str, term: str | None) -> threading.RLock:
        return self._locks[hash((department, term)) % len(self._locks)]


class NotAllowed(ValueError):
    """The request names an item this user may not act on (or one that is not in the state the request assumes)."""


class MemoryService:
    """`org`: the org directory (org.Directory) or a fixed snapshot (org.Org, in tests).
    Memory holds shared vocabulary only (terms and relations); nothing about a user themself is saved."""

    def __init__(self, store: MemoryStore, org, llm: BaseChatModel | None = None, joke_filter: bool = True,
                 term_index=None, extraction: str = "mem0"):
        """`joke_filter`: definitions the extractor marks as jokes are not saved.
        `term_index`: a callable returning the Mem0 term index (built on first use, only if the switch is on).
        `extraction`: "mem0" (Mem0's extraction phase with our rules, `extractor.extract`) or "structured" (the same
        rules as one structured tool call, `extractor.extract_structured`, kept for comparison)."""
        self.store, self.org, self.llm, self.joke_filter = store, org, llm, joke_filter
        self.extraction = extraction
        self.term_index = term_index
        self._writing = TermLocks()

    def reset(self, step: int, start: str) -> str:
        """Demo states (see seeding)."""
        return seeding.reset(self.store, self.org, step, start)

    def teammates(self, viewer: Person) -> list[str]:
        """Everyone in the viewer's team today, from the org directory (so moves and new teams count at once)."""
        return sorted({p.email for p in self.org.members(viewer.team)} | {viewer.email})

    # ---- read path (before the model call) ---------------------------------------------------------------
    def retrieve(self, email: str, question: str, exclude: tuple[str, ...] = (), fuzzy: bool = True,
                 record: bool = True) -> tuple[str, MemoryTrace]:
        """`exclude`: item ids the user rejected for this answer ("wrong, regenerate").
        `fuzzy`: also find terms by meaning (Mem0) and spelling (on by default; needs `term_index`); falls back to
        exact wording if that fails.
        `record`: count the applied items as used (False for a lookup that answers nothing, like explain_term)."""
        t0 = time.perf_counter()
        viewer = self.org.get(email)
        mates = self.teammates(viewer)
        candidates = [c for c in self.store.candidate_items(viewer.email, mates) if c.item_id not in exclude]
        rows = self.store.user_signals(viewer.email, mates)
        dismissed = {r["term"] for r in rows if r["kind"] == "dismiss" and r["term"]}
        sharing = _sharing(_consents(rows))
        matches, note = {}, None
        if fuzzy and self.term_index:
            try:  # other teams' terms and aliases are in `candidates` (for counting only): never match through them
                seen = [c for c in candidates if c.term and _visible(viewer, c, _lower(mates), sharing)]
                matches = self.term_index().match(question, seen)
            except Exception as e:  # never let the switch break a turn
                log.warning("Mem0 term matching failed, using exact wording: %s", e)
                note = f"Mem0 matching was unavailable ({type(e).__name__}), so only exact wording was used."
        used = resolver.resolve(viewer, question, candidates, self.org.team_size(viewer.team, today()),
                                self.org.departments(), dismissed, today=today(),
                                sharing=sharing, matches=matches, teammates=_lower(mates))
        trace = MemoryTrace(turn_id=new_id(), user_email=viewer.email, question=question, items=used, note=note)
        # Soft signal: shown. Background evidence only (FR11).
        shown = [self._signal(viewer, u.item_id, "shown", u.term) for u in used if u.scope != "unresolved"]
        if record and shown:
            threading.Thread(target=self._record, args=(shown,), daemon=True).start()  # off the answer's path
        trace.latency_ms = int((time.perf_counter() - t0) * 1000)
        return resolver.context_block(used), trace

    def _record(self, signals: list[Signal]) -> None:
        try:
            self.store.add_signals(signals)
        except Exception as e:  # background evidence only; a lost write must not go unnoticed either
            log.warning("could not record %d 'shown' signals: %s", len(signals), e)

    # ---- write path (after the answer) -------------------------------------------------------------------
    def learn(self, email: str, previous_answer: str | None, user_message: str, answer: str,
              trace: dict | None, sql: list[str] | None = None, recent: list[str] | None = None
              ) -> tuple[list[MemoryWrite], dict[str, dict]]:
        """What to remember from this turn, and how relevant each applied term was to it (item id -> level, reason).
        `recent`: the user's earlier messages in this conversation, as context (Mem0 passes the last m messages)."""
        if self.llm is None:
            return [], {}
        # The trace comes back from the caller (the agent's state): keep only entries that look like ours.
        applied = [i for i in (trace or {}).get("items") or []
                   if isinstance(i, dict) and isinstance(i.get("item_id"), str) and isinstance(i.get("meaning"), str)]
        own = [asdict(i) for i in self.store.own_items(self.org.get(email).email)]
        decide = extract_structured if self.extraction == "structured" else extract
        result = decide(self.llm, previous_answer, user_message, answer, applied, own, sql=sql, recent=recent)
        ids = {a["item_id"] for a in applied if a.get("term") and a.get("scope") != "unresolved"}
        relevance = {r.item_id: {"level": r.level, "reason": r.reason} for r in result.relevance if r.item_id in ids}
        # apply() saves nothing without consent
        return self.apply(email, result.ops, applied), relevance

    def apply(self, email: str, ops: list[MemoryOp], applied: list[dict] | None = None) -> list[MemoryWrite]:
        """Only definitions are saved (terms and relations), never anything about the user themself, and never a
        definition marked as a joke. What a user teaches about an existing term is theirs first; a new term goes to
        their team at once.
        A term changes for the team only when enough teammates use the same other meaning (`_update_team`), and for
        the department when enough of its teams do (`_update_department`). Before anything is stored it is compared
        with what memory already holds, colleagues' meanings included (`consolidate`, Mem0's update phase), so another
        wording or a typo never becomes a second copy of the same thing."""
        viewer = self.org.get(email)
        mates = self.teammates(viewer)
        consents = _consents(self.store.user_signals(viewer.email, mates))
        consent, sharing = consents.get(viewer.email) or {}, _sharing(consents)
        if not consent.get("remember"):  # FR22: nothing is remembered before the user opts in
            return []
        applied_by_term = {norm_term(a["term"]): a for a in (applied or [])
                           if a.get("term") and a.get("scope") != "unresolved"}
        writes: list[MemoryWrite] = []
        for op in ops:
            if op.evidence == "inferred":  # only what the user said, never the model's own reading
                continue
            if self.joke_filter and op.is_joke and op.op != "withdraw":
                continue
            op = _clean(op)
            if op is None:  # no usable term or meaning
                continue
            # The target id comes from the model: it is only a hint, and never the reason another item is replaced.
            target = self.store.get_item(op.target_item_id) if op.target_item_id else None
            if op.op == "withdraw":
                if target and (w := self._withdraw(viewer, target, op.evidence)):
                    writes.append(w)
                continue
            # Mem0's update phase: the same term spelled differently, or a meaning memory already has?
            known = self._known_terms(viewer, mates, sharing)
            res = consolidate.resolve(op.term, op.meaning, known, self.llm)
            term, same = res.term, res.same
            if target is None and term in applied_by_term:
                target = self.store.get_item(applied_by_term[term]["item_id"])
            with self._writing(viewer.department, term):
                writes += self._teach(viewer, op, term, same, target, mates, bool(consent.get("share")))
        return writes

    def _teach(self, viewer: Person, op: MemoryOp, term: str, same: MemoryItem | None, target: MemoryItem | None,
               mates: list[str], shares: bool) -> list[MemoryWrite]:
        """Store what the user said `term` means, given what memory holds right now. Runs under the term's lock.
        `same`: an existing item that already has this meaning, as found before the lock was taken."""
        state = self._state(viewer, term, mates)
        if same is None and state is not None:  # someone may have saved this very meaning a moment ago
            said = MemoryItem(item_type=op.item_type, term=term, meaning=op.meaning, owner_email=None,
                              owner_team=None, owner_department=None)
            fresh = [state.mine, state.team, state.department, state.gold, *(g[0] for g in state.variants.values())]
            same = next((i for i in fresh if i is not None and consolidate.same_meaning(said, i)), None)
        if same is not None and state is not None:
            if state.mine is not None and consolidate.same_meaning(state.mine, same):
                return [self._same(state.mine, "only you")]
            if state.shared is not None and consolidate.same_meaning(state.shared, same):
                # The user's meaning is the shared one after all: drop their own, if they had one.
                if state.mine is not None:
                    w = self._withdraw(viewer, state.mine, op.evidence)
                    return [w] if w else []
                return [self._same(state.shared, _where(state.shared))]
        meaning = same.meaning if same else op.meaning
        prev = target if _replaces(target, viewer, term) else (state.mine if state else None)
        # a shared meaning of this term that the user disagrees with: kept as a background signal
        corrected = target if (target is not None and target.origin != "user" and target.term == term
                               and target.status == "active") else None
        # Filed under an existing term that is only one letter away: if that was no typo after all, the user's own
        # word must still find it.
        wording = norm_term(op.term)
        aliases = [wording] if consolidate.compact(wording) != consolidate.compact(term) else []
        item, write = self._add_own(viewer, op, term, meaning, prev, corrected, aliases=aliases)
        writes = [write]
        if not shares:  # the user's share switch: their definitions stay theirs
            return writes
        if state is None or state.shared is None:  # a new term: nothing to disagree with, so the team gets it
            writes += self._share_new_term(viewer, item)
        elif team := self._update_team(viewer, term, mates):
            writes.append(team)
            if dept := self._update_department(viewer, term):
                writes.append(dept)
        return writes

    def _add_own(self, viewer: Person, op: MemoryOp, term: str, meaning: str, prev: MemoryItem | None,
                 corrected: MemoryItem | None, aliases: list[str] | None = None) -> tuple[MemoryItem, MemoryWrite]:
        """Store the user's own item, replacing `prev` (their earlier one). `corrected`: a shared item they disagreed
        with (kept as a background signal)."""
        item = MemoryItem(item_type=op.item_type, term=term, meaning=meaning, owner_email=viewer.email,
                          owner_team=viewer.team, owner_department=viewer.department,
                          aliases=aliases or [], supersedes=prev.item_id if prev else None)
        signals = [self._signal(viewer, item.item_id, "author", item.term)]
        if corrected is not None:  # background evidence only
            signals.append(self._signal(viewer, corrected.item_id, "correct", corrected.term))
        self.store.commit(add=[item], status={prev.item_id: "withdrawn"} if prev else None, signals=signals)
        return item, MemoryWrite(op="update" if prev else "add", item_id=item.item_id, item_type=item.item_type,
                                 term=item.term, meaning=item.meaning, shared_with="only you",
                                 superseded_id=prev.item_id if prev else None, evidence=op.evidence)

    @staticmethod
    def _same(existing: MemoryItem, where: str) -> MemoryWrite:
        return MemoryWrite(op="same", item_id=existing.item_id, item_type=existing.item_type, term=existing.term,
                           meaning=existing.meaning, shared_with=where, same_as=where)

    def _known_terms(self, viewer: Person, mates: list[str], sharing: set[str] | None = None) -> list[MemoryItem]:
        """Term items a new item is compared with: what the user can see (own, sharing teammates', team, department,
        other departments, gold) plus the other teams of their department, so teams converge on one wording and the
        department meaning can form. Teams of other departments, and colleagues who do not share, stay out of it."""
        day = today()
        if sharing is None:
            sharing = _sharing(_consents(self.store.user_signals(viewer.email, mates)))
        return [i for i in self.store.candidate_items(viewer.email, mates)
                if i.term and not resolver.expired(i, day)
                and (_visible(viewer, i, _lower(mates), sharing)
                     or (i.origin == "team" and i.owner_department == viewer.department))]

    def _share_new_term(self, viewer: Person, item: MemoryItem) -> list[MemoryWrite]:
        """A term with no team, department or official meaning yet becomes the team's at once; the user's own copy
        folds into it. In a one-team department it becomes the department's too. Changing an existing meaning goes
        through `_update_team`."""
        new = self._team_item(viewer, item.term, item, old=None)
        self.store.commit(add=[new], status={item.item_id: "withdrawn"},
                          signals=[self._signal(viewer, new.item_id, "adopt", item.term)])
        writes = [MemoryWrite(op="team", item_id=new.item_id, item_type=new.item_type, term=new.term,
                              meaning=new.meaning, shared_with="your team", folded_ids=[item.item_id])]
        if dept := self._update_department(viewer, item.term):
            writes.append(dept)
        return writes

    @staticmethod
    def _team_item(viewer: Person, term: str, src: MemoryItem, old: MemoryItem | None) -> MemoryItem:
        """The team's meaning, built from one member's (not stored yet: the caller commits it with what it folds)."""
        return MemoryItem(
            item_type=src.item_type, term=term, aliases=src.aliases, meaning=src.meaning, origin="team", owner_email=None, owner_team=viewer.team, owner_department=viewer.department,
            supersedes=old.item_id if old else None,
        )

    def _state(self, viewer: Person, term: str, mates: list[str] | None = None) -> resolver.TermState | None:
        mates = mates or self.teammates(viewer)
        sharing = _sharing(_consents(self.store.user_signals(viewer.email, mates)))
        return resolver.term_states(viewer, self.store.term_items(term, mates), today(), sharing,
                                    _lower(mates)).get(term)

    # ---- consent (FR22) -----------------------------------------------------------------------------------
    def consent(self, email: str) -> dict[str, bool] | None:
        """{"remember": bool, "share": bool}, or None when the user has not been asked yet (nothing is remembered)."""
        viewer = self.org.get(email)
        return _consents(self.store.user_signals(viewer.email, [])).get(viewer.email)

    def set_consent(self, email: str, remember: bool, share: bool) -> None:
        """Opt in or out. Sharing only matters while remembering is on; turning either off keeps what was saved
        (Forget removes it) but stops new memory, and stops the user's own meanings counting for the team."""
        viewer = self.org.get(email)
        self.store.add_signals([
            self._signal(viewer, "-", "consent", f"remember:{'on' if remember else 'off'}"),
            self._signal(viewer, "-", "consent", f"share:{'on' if remember and share else 'off'}"),
        ])

    def _update_team(self, viewer: Person, term: str, mates: list[str] | None = None) -> MemoryWrite | None:
        """When enough teammates use another meaning (`resolver.needed`) and at least two of them the same one, that
        meaning becomes the team's. Their own copies fold into it; teammates with yet another meaning keep theirs."""
        state = self._state(viewer, term, mates)
        size = self.org.team_size(viewer.team, today())
        if state is None or state.overrides < resolver.needed(size):
            return None
        group = max(state.variants.values(), key=lambda g: (len(g), max(i.created_at for i in g)))
        if len(group) < 2:
            return None
        src = max(group, key=lambda i: i.created_at)
        old = state.team
        new = self._team_item(viewer, term, src, old)
        self.store.commit(add=[new], status=dict.fromkeys([i.item_id for i in group] + ([old.item_id] if old else []),
                                                          "withdrawn"),
                          signals=[self._signal(p, new.item_id, "adopt", term)
                                   for p in (self._person(i.owner_email) for i in group) if p])
        if old:  # the team's earlier answers about this term used the old meaning: not to be reused as examples
            self.store.withdraw_cases_about(viewer.team, term)
        return MemoryWrite(
            op="team", item_id=new.item_id, item_type=new.item_type, term=term, meaning=new.meaning,
            shared_with="your team", superseded_id=old.item_id if old else None,
            note=f"{len(group)} of {size} colleagues use this meaning", folded_ids=[i.item_id for i in group],
        )

    def _update_department(self, viewer: Person, term: str) -> MemoryWrite | None:
        """When enough of the department's teams (`resolver.needed_teams`) have the same team meaning, it becomes the
        department's. Those teams' meanings fold into it; teams with another meaning keep theirs."""
        day = today()
        dept = viewer.department
        teams = self.org.teams_in(dept, day)
        shared = [i for i in self.store.term_items(term, []) if not resolver.expired(i, day)]
        latest: dict[str, MemoryItem] = {}
        for i in shared:
            if i.origin == "team" and i.owner_team in teams:
                if i.owner_team not in latest or i.created_at > latest[i.owner_team].created_at:
                    latest[i.owner_team] = i
        if not latest:
            return None
        group = max(resolver.group_meanings(list(latest.values())).values(),
                    key=lambda g: (len(g), max(i.created_at for i in g)))
        if len(group) < resolver.needed_teams(len(teams)):
            return None
        old = max((i for i in shared if i.origin == "department" and i.owner_department == dept),
                  key=lambda i: i.created_at, default=None)
        src = max(group, key=lambda i: i.created_at)
        if old is not None and consolidate.same_meaning(old, src):
            return None
        new = MemoryItem(item_type=src.item_type, term=term, aliases=src.aliases, meaning=src.meaning,
                         origin="department", owner_email=None, owner_team=None, owner_department=dept,
                         supersedes=old.item_id if old else None)
        self.store.commit(add=[new], status=dict.fromkeys([i.item_id for i in group] + ([old.item_id] if old else []),
                                                          "withdrawn"),
                          signals=[self._signal(viewer, new.item_id, "adopt", term)])
        if old:
            for team in teams:
                self.store.withdraw_cases_about(team, term)
        return MemoryWrite(
            op="department", item_id=new.item_id, item_type=new.item_type, term=term, meaning=new.meaning,
            shared_with="your department", superseded_id=old.item_id if old else None,
            note=f"{len(group)} of {len(teams)} teams in {dept} use this meaning",
            folded_ids=[i.item_id for i in group],
        )

    def undo(self, email: str, write: dict) -> None:
        """Reverse one write that `learn`, `apply` or `choose` returned to this user.

        The write comes back from a client (a UI's Undo button), so it is not trusted: only its ids are used, each
        is checked against the store, and the item must still be in the state the write left it in. Raises
        NotAllowed otherwise, so a forged or repeated undo cannot retire or revive anything else."""
        viewer = self.org.get(email)
        op = write.get("op")
        if op == "same":  # nothing was saved
            return
        item_id = str(write.get("item_id") or "")
        item = self.store.get_item(item_id)
        if item is None:
            raise NotAllowed("Nothing to undo: no such item.")
        with self._writing(viewer.department, item.term):
            item = self.store.get_item(item_id)  # as it is now that no one else is writing this term
            mine = item.origin == "user" and item.owner_email == viewer.email
            if op in ("add", "update"):
                _require(mine and item.status == "active")
                status = {item.item_id: "withdrawn"}
                prev = self.store.get_item(item.supersedes) if item.supersedes else None
                if prev is not None and prev.origin == "user" and prev.owner_email == viewer.email:
                    status[prev.item_id] = "active"
            elif op == "withdraw":
                _require(mine and item.status == "withdrawn")
                status = {item.item_id: "active"}
            elif op in ("team", "department"):
                status = self._undo_shared(viewer, op, item, [str(i) for i in write.get("folded_ids") or []])
            else:
                raise NotAllowed("Nothing to undo: unknown kind of change.")
            self.store.commit(status=status, signals=[self._signal(viewer, item_id, "undo", write.get("term"))])

    def _undo_shared(self, viewer: Person, op: str, item: MemoryItem, folded_ids: list[str]) -> dict[str, str]:
        """The status changes that go back to the previous team (department) meaning and the copies that were folded
        into the new one."""
        if op == "team":
            _require(item.origin == "team" and item.owner_team == viewer.team and item.status == "active")
            mates = _lower(self.teammates(viewer))

            def was_folded(i: MemoryItem) -> bool:  # a teammate's own meaning of this term
                return i.origin == "user" and (i.owner_email or "").lower() in mates
        else:
            _require(item.origin == "department" and item.owner_department == viewer.department
                     and item.status == "active")

            def was_folded(i: MemoryItem) -> bool:  # a team meaning of this term in this department
                return i.origin == "team" and i.owner_department == viewer.department
        folded = [self.store.get_item(i) for i in folded_ids]
        _require(all(i is not None and i.term == item.term and i.status == "withdrawn" and was_folded(i)
                     for i in folded))
        old = self.store.get_item(item.supersedes) if item.supersedes else None  # from the store, not the client
        restore = folded_ids + ([old.item_id] if old is not None and old.origin == item.origin else [])
        return {**dict.fromkeys(restore, "active"), item.item_id: "withdrawn"}

    # ---- user controls (FR10, FR12, FR14, FR21) ----------------------------------------------------------
    def forget(self, email: str, item_id: str) -> MemoryWrite | None:
        viewer = self.org.get(email)
        if item := self.store.get_item(item_id):
            return self._withdraw(viewer, item)
        return None

    def feedback(self, email: str, item_id: str, accept: bool) -> None:
        viewer = self.org.get(email)
        item = self.store.get_item(item_id)
        if item is None:  # nothing to give feedback on
            return
        self.store.add_signals([self._signal(viewer, item_id, "confirm" if accept else "correct", item.term)])

    def choose(self, email: str, rejected_id: str, chosen_id: str) -> list[MemoryWrite]:
        """The user picked another meaning from the list: it becomes theirs, worded the same, so it counts together
        with the colleagues who use it. Picking the shared meaning drops the user's own."""
        viewer = self.org.get(email)
        mates = self.teammates(viewer)
        sharing = _sharing(_consents(self.store.user_signals(viewer.email, mates)))
        chosen = self.store.get_item(chosen_id)
        # The id comes from a client: it must be a meaning this user is actually offered, or picking it would copy
        # a colleague's unshared meaning (or another team's) into their memory.
        if (chosen is None or chosen.status != "active" or not chosen.term
                or resolver.expired(chosen, today()) or not _visible(viewer, chosen, _lower(mates), sharing)):
            raise NotAllowed("That meaning is not one you can pick.")
        op = MemoryOp(op="add", item_type=chosen.item_type, term=chosen.term, meaning=chosen.meaning,
                      target_item_id=rejected_id or None, evidence="stated")
        writes = self.apply(email, [op])
        self.store.add_signals([self._signal(viewer, chosen_id, "confirm", chosen.term)])
        return writes

    def dismiss_warning(self, email: str, term: str) -> None:
        self.store.add_signals([self._signal(self.org.get(email), "-", "dismiss", norm_term(term))])

    def untrustworthy(self) -> dict[str, list[str]]:
        """Official terms replaced in many departments, with those departments (admin view)."""
        flagged = resolver.untrustworthy_terms(self.store.replaced_gold(), self.org.departments())
        return {t: sorted(d) for t, d in sorted(flagged.items())}

    def view(self, email: str) -> dict[str, list[dict]]:
        """Memory viewer: own items, the team's and the department's shared meanings (anonymised), other departments'
        meanings and gold."""
        viewer = self.org.get(email)
        mates = self.teammates(viewer)
        items = self.store.candidate_items(viewer.email, mates)
        day = today()
        sharing = _sharing(_consents(self.store.user_signals(viewer.email, mates)))
        states = resolver.term_states(viewer, items, day, sharing, _lower(mates))
        flagged = resolver.untrustworthy_terms(items, self.org.departments())
        need = resolver.needed(self.org.team_size(viewer.team, day))
        out: dict[str, list[dict]] = {"personal": [], "team": [], "department": [], "gold": [], "other": []}
        for it in items:
            if resolver.expired(it, day):  # unused for too long: no longer applied, so not listed either
                continue
            if it.origin == "user" and it.owner_email == viewer.email:
                scope = "personal"
            elif it.origin == "team" and it.owner_team == viewer.team:
                scope = "team"
            elif it.origin == "department":
                scope = "department" if it.owner_department == viewer.department else "other"
            elif it.origin == "gold":
                scope = "gold"
            else:
                continue
            st = states.get(it.term) if it.term else None
            if st is not None and it is not {"personal": it, "team": st.team, "department": st.department,
                                             "gold": st.gold, "other": st.others.get(it.owner_department)}[scope]:
                continue  # an older twin of a shared meaning (two written at the same moment): only the latest counts
            shared = st.shared if st else None
            row = {"item_id": it.item_id, "type": it.item_type, "term": it.term, "meaning": it.meaning,
                   "support": (f"{it.owner_department} department" if scope == "other"
                               else resolver.support_label(scope)),
                   "department": it.owner_department, "created_at": it.created_at,
                   "shown_to_team": scope == "personal" and it.shareable and shared is not None,
                   "overrides": 0, "confidence": None, "replaced": False, "untrustworthy": False,
                   "months_unused": resolver.months_unused(it, day) if resolver.fades(it) else 0}
            if scope in ("team", "department", "gold") and st is not None:
                # a closer shared meaning (team over department over gold) that differs replaces this one for you
                row["replaced"] = it is not shared and shared is not None and not consolidate.same_meaning(it, shared)
                row["untrustworthy"] = scope == "gold" and it.term in flagged
                if it is shared:
                    row["overrides"] = st.overrides
                    row["confidence"] = round(max(resolver.confidence(st.overrides, need)
                                                  * resolver.freshness(it, day), resolver.MIN_CONFIDENCE), 2)
            out[scope].append(row)
        return out

    # ---- helpers -----------------------------------------------------------------------------------------
    def _withdraw(self, viewer: Person, target: MemoryItem, evidence: str | None = None) -> MemoryWrite | None:
        """Only the user's own items can be withdrawn. For a shared one, the disagreement is kept as a signal.
        An item that is already withdrawn is left alone: forgetting it twice (the model's forget tool, then the
        after-turn step reading the same request) must not log or show the removal twice."""
        if target.status != "active":
            return None
        if target.owner_email != viewer.email:
            self.store.add_signals([self._signal(viewer, target.item_id, "correct", target.term)])
            return None
        self.store.commit(status={target.item_id: "withdrawn"},
                          signals=[self._signal(viewer, target.item_id, "withdraw", target.term)])
        return MemoryWrite(op="withdraw", item_id=target.item_id, item_type=target.item_type, term=target.term,
                           meaning=target.meaning, shared_with="only you", evidence=evidence)

    def _person(self, email: str | None) -> Person | None:
        try:
            return self.org.get(email) if email else None
        except KeyError:  # no longer in the directory
            return None

    @staticmethod
    def _signal(viewer: Person, item_id: str, kind: str, term: str | None) -> Signal:
        return Signal(item_id=item_id, user_email=viewer.email, team=viewer.team, department=viewer.department,
                      kind=kind, term=term)


def _require(ok: bool) -> None:
    if not ok:
        raise NotAllowed("Nothing to undo: that change is not yours to undo, or it was already undone.")


def _visible(viewer: Person, item: MemoryItem, mates: set[str], sharing: set[str]) -> bool:
    """May this user be shown this term item: their own, a teammate's that is shared, their team's, any department's
    or the official one. Other teams' meanings and colleagues' unshared items are not."""
    if item.origin == "user":
        return item.owner_email == viewer.email or (
            item.shareable and (item.owner_email or "").lower() in mates and item.owner_email in sharing)
    if item.origin == "team":
        return item.owner_team == viewer.team
    return True


def _replaces(target: MemoryItem | None, viewer: Person, term: str) -> bool:
    """May a new item of `term` replace `target`: only the user's own active item of that same term. A wrong id from
    the model must never remove something else."""
    return (target is not None and target.status == "active" and target.origin == "user"
            and target.owner_email == viewer.email and target.term == term)


def _one_line(text: str, limit: int) -> str:
    """Collapse line breaks and runs of spaces, and cut at `limit` characters."""
    return " ".join(text.split())[:limit].rstrip()


def _clean(op: MemoryOp) -> MemoryOp | None:
    """What a user teaches is put into prompts (their own and their colleagues'), so it is stored as one bounded line
    of text. None when there is no usable term or meaning."""
    if op.op == "withdraw":
        return op
    meaning = _one_line(op.meaning or "", MAX_MEANING)
    term = _one_line(op.term, MAX_TERM) if op.term is not None else None
    if not meaning or not consolidate.words(term or ""):
        return None
    return op.model_copy(update={"term": term, "meaning": meaning})


def _where(item: MemoryItem) -> str:
    """Where an existing shared meaning lives, as the user reads it."""
    return {"team": "your team", "department": "your department", "gold": "the official glossary"}.get(
        item.origin, "only you")


def _lower(emails: list[str]) -> set[str]:
    return {e.lower() for e in emails}


def _consents(rows: list[dict]) -> dict[str, dict[str, bool]]:
    """Latest consent choice per user, from 'consent' signals (term = 'remember:on', 'share:off', ...)."""
    out: dict[str, dict[str, bool]] = {}
    for r in rows:  # ordered by time, so later choices win
        if r["kind"] == "consent" and r["term"] and ":" in r["term"]:
            aspect, value = r["term"].split(":", 1)
            out.setdefault(r["user_email"], {"remember": False, "share": False})[aspect] = value == "on"
    return out


def _sharing(consents: dict[str, dict[str, bool]]) -> set[str]:
    return {e for e, c in consents.items() if c.get("remember") and c.get("share")}
