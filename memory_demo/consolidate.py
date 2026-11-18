"""Write-time consolidation: is what the user just taught already in memory, worded differently?

This is the update phase of Mem0 (Chhikara et al. 2025, "Mem0: Building Production-Ready AI Agents with Scalable
Long-Term Memory", section 2.1 and Algorithm 1): each new memory is compared with the most similar existing memories,
and an LLM decides whether it is new (ADD), changes one (UPDATE / DELETE) or is already there (NOOP). Our memory is
structured (a term and its meaning, per scope), so the question splits in two:

1. Is the term one we already know, spelled differently? ("SOMARIS 10", "Somaris10", "memroy errors", "memory error")
   Only spelling counts: case, spacing, punctuation, articles, plurals, one typo per word, or a known alias. A
   different word with the same meaning ("link issues" next to "link card problems") is a synonym and stays a term of
   its own, so the user's own word keeps working.
2. Does that term already have this meaning, worded differently? Then the new item copies the existing wording
   exactly, so the rest of the layer (team counts, folding into the team meaning) sees one meaning, not two.
   A cheap check goes first (the same words up to case, punctuation, articles, plurals and two typos; never different
   numbers like R37 / R38 or different words like correctable / uncorrectable). When it is not enough, the LLM decides
   from the user's own words, as in Mem0. The cheap check is also used when meanings are compared on read
   (`same_meaning`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from .models import MemoryItem

log = logging.getLogger(__name__)
ARTICLES = {"a", "an", "the"}
MAX_ENTRIES = 15  # Mem0 compares with the top s=10 similar memories; one term can have a few more meanings
WORD = re.compile(r"[^\W_]+")  # letters and digits of any script: "größer" and "内存错误" are words too


def words(text: str) -> list[str]:
    return [w for w in WORD.findall((text or "").lower()) if w not in ARTICLES]


def compact(text: str) -> str:
    """'SOMARIS 10' -> 'somaris10', 'memory errors' -> 'memoryerror': no spacing, punctuation or final plural."""
    return _stem("".join(words(text)))


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def _one_edit(a: str, b: str) -> bool:
    """At most one inserted, deleted, replaced or swapped letter."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def _typo(a: str, b: str) -> bool:
    """Two different words that are one typo apart. Numbers must match exactly, short words exactly, and so must
    words outside plain Latin letters: one character more in "不可纠正" or "Möller" is another word, not a typo."""
    return (a.isascii() and b.isascii() and re.sub(r"\D", "", a) == re.sub(r"\D", "", b)
            and min(len(a), len(b)) >= 4 and _one_edit(a, b))


def same_words(a: str, b: str, max_typos: int) -> bool:
    """Same words in the same order, ignoring case, punctuation, articles and plurals, with up to `max_typos` words
    one typo apart."""
    wa, wb = words(a), words(b)
    if not wa or len(wa) != len(wb):  # nothing to compare is not "the same"
        return False
    typos = 0
    for x, y in zip(wa, wb):
        if x == y or _stem(x) == _stem(y):
            continue
        if not _typo(x, y):
            return False
        typos += 1
    return typos <= max_typos


def same_term(a: str, b: str) -> bool:
    """'somaris10' = 'SOMARIS 10' = 'Somaris-10'; 'memroy errors' = 'memory errors'; 'R37 errors' != 'R38 errors'."""
    return bool(a and b) and (compact(a) == compact(b) or same_words(a, b, max_typos=1))


def same_meaning(a: MemoryItem, b: MemoryItem) -> bool:
    """The same wording up to case, punctuation, articles, plurals and two typos."""
    return same_words(a.meaning, b.meaning, max_typos=2)


# ---- the decision ------------------------------------------------------------------------------------------
@dataclass
class Resolution:
    term: str  # the term to store it under: an existing one when it is the same term spelled differently
    same: MemoryItem | None = None  # an existing item of that term that already has this meaning
    how: str | None = None  # "wording" or "llm" (for tests and the trace)


class SameAs(BaseModel):
    same_meaning_id: str | None = Field(None, description="Id of the entry that means what the user means, or null.")


SYSTEM = """You keep the shared memory of a data assistant free of duplicates. A user just said what a term means, and
memory already has one or more meanings for that same term. Decide whether one of them is what the user means.

same_meaning_id: the id of the entry that means what the user means: only worded differently or with typos. Judge
by what the user said: the meanings were written down by an assistant and can differ in form for the same intent. A
meaning is different when the user's words ask for different events
(narrower, broader or other ones). Null if none of them is.

Always answer by calling the SameAs tool."""


def resolve(term: str, meaning: str, known: list[MemoryItem], llm: BaseChatModel | None = None,
            decided: frozenset[str] | set[str] = frozenset(), said: str | None = None) -> Resolution:
    """`known`: term items the user's memory can see (own, teammates', team, department, other departments, gold).
    `decided`: terms applied this turn. The extractor already compared the user's meaning with their meanings in use
    (the applied one and its alternatives), so the LLM is not asked about those meanings again.
    `said`: the user's message, so the LLM judges what they meant rather than how it was written down."""
    known = [k for k in known if k.term]
    new = MemoryItem(item_type="term", term=term, meaning=meaning, owner_email=None, owner_team=None,
                     owner_department=None)
    canon = new.term
    if any(k.term == canon or canon in k.aliases for k in known):
        canon = next(k.term for k in known if k.term == canon or canon in k.aliases)
    else:
        spelled = [k for k in known if any(same_term(canon, key) for key in (k.term, *k.aliases))]
        if not spelled:  # a new term (or a synonym of one): nothing to compare its meaning with
            return Resolution(canon)
        canon = max(spelled, key=lambda k: SequenceMatcher(None, compact(canon), compact(k.term)).ratio()).term
    entries = [k for k in known if k.term == canon]
    same = next((e for e in entries if same_meaning(new, e)), None)
    if same is not None:
        return Resolution(canon, same, "wording")
    if llm is None or canon in decided or not entries:
        return Resolution(canon)
    return _ask(llm, new, entries[:MAX_ENTRIES], canon, said)


def _ask(llm: BaseChatModel, new: MemoryItem, entries: list[MemoryItem], canon: str, said: str | None) -> Resolution:
    lines = "\n".join(f"[{e.item_id}] meaning: {e.meaning}" for e in entries)
    human = (f"Term: {canon}\nWhat the user said: {(said or 'not available')[:1500]}\n"
             f"As written down: meaning: {new.meaning}\n\n"
             f"Meanings already in memory:\n{lines}")
    try:
        msg = llm.bind_tools([SameAs]).invoke([("system", SYSTEM), ("human", human)])
        call = next((tc for tc in getattr(msg, "tool_calls", []) or [] if tc["name"] == "SameAs"), None)
        answer = SameAs(**call["args"]) if call else SameAs()
    except Exception as e:  # never let consolidation break a turn: store it as said
        log.warning("could not compare “%s” with memory, saving it as said: %s", canon, e)
        return Resolution(canon)
    same = {e.item_id: e for e in entries}.get(answer.same_meaning_id or "")
    return Resolution(canon, same, "llm" if same else None)
