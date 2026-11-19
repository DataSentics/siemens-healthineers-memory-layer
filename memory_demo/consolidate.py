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
   A cheap check goes first (the same words up to case, punctuation, articles, plurals and two typos, with the same
   exact values: never R37 / R38, KERNDTLB / KERNSTLB or correctable / uncorrectable). It is also used when meanings
   are compared on read (`same_meaning`). When it is not enough, Mem0's update phase decides: its own prompt
   template and answer format (mem0 `get_update_memory_messages`), with `MERGE_PROMPT` as the custom update prompt.
   The existing meanings are those of everyone the user can see (own, teammates', team, department, gold), so a
   colleague's meaning in other words counts as the same one. Mem0's own rule merges a narrower or changed fact into
   the old one ("keep the one with more information"), which is right for one person's memory but would make one
   colleague's definition replace another's; the custom prompt says NONE only when both select exactly the same
   events, however they are written, and ADD otherwise. The answer only decides "same or not": what is stored stays
   with the memory rules (a user changes only their own memory). Evaluated in evals/merge_last_run.md.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from langchain_core.language_models import BaseChatModel

from .models import MemoryItem

log = logging.getLogger(__name__)
ARTICLES = {"a", "an", "the"}
MAX_ENTRIES = 15  # Mem0's add() compares with the 5 most similar memories; one term can have a few more meanings
WORD = re.compile(r"[^\W_]+")  # letters and digits of any script: "größer" and "内存错误" are words too
QUOTED = re.compile(r""""([^"]+)"|“([^”]+)”|(?<!\w)'([^']+)'(?!\w)""")  # "x", “x”, 'x' (not an apostrophe)


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
    """The same wording up to case, punctuation, articles, plurals and two typos, and the same exact values (a typo
    in a code like KERNDTLB is another code)."""
    return same_words(a.meaning, b.meaning, max_typos=2) and same_details(a.meaning, b.meaning)


def details(text: str) -> set[str]:
    """The exact values that pin a meaning down, lower-cased: codes and names in capitals (FATAL, KERNDTLB, LINKCARD),
    anything with a digit (R37, 2005, the parts of R12-M0-N4) and quoted texts."""
    text = text or ""
    quoted = {" ".join(next(g for g in m.groups() if g).lower().split()) for m in QUOTED.finditer(text)}
    return quoted | {w.lower() for w in WORD.findall(text)
                     if any(c.isdigit() for c in w) or (len(w) >= 2 and w.isupper())}


def same_details(a: str, b: str) -> bool:
    """Every exact value of each meaning appears in the other (as a word, or for a quoted text as written, ignoring
    case and spacing). "level FATAL" and "fatal events" agree; R37 / R38 and FATAL / FAILURE do not."""
    return _covered(details(a), b) and _covered(details(b), a)


def _covered(found: set[str], text: str) -> bool:
    low = " ".join((text or "").lower().split())
    present = set(WORD.findall(low))
    return all(d in present if WORD.fullmatch(d) else d in low for d in found)


# ---- the decision ------------------------------------------------------------------------------------------
@dataclass
class Resolution:
    term: str  # the term to store it under: an existing one when it is the same term spelled differently
    same: MemoryItem | None = None  # an existing item of that term that already has this meaning
    how: str | None = None  # "wording" or "mem0" (for tests and the trace)


MERGE_PROMPT = """You are a smart memory manager for the shared memory of a data assistant. Each memory is a
definition of a work term, written as "term: meaning". Colleagues state definitions in their own words, so one
definition can be written in many ways.

Compare each new fact with the existing memories of the same term:
- NONE: an existing memory selects exactly the same events, however it is written: other words or word order, typos,
  upper or lower case, a number or code written another way, a name written as a code or in words, or an explanation
  added or left out that does not change which events count.
- ADD: the new fact selects different events: narrower, broader, other codes, components, levels, numbers, message
  texts, time windows, or an extra condition. Add it as a new element and keep the existing memory unchanged.
- Never UPDATE or DELETE a memory because a new fact defines the term differently: different definitions of one term
  are kept side by side, since they can come from different colleagues.

Return only the existing memory that already holds the new fact, with event NONE, or, when none does, the new fact
with event ADD. Leave the other memories out."""


def resolve(term: str, meaning: str, known: list[MemoryItem], llm: BaseChatModel | None = None) -> Resolution:
    """`known`: term items the user's memory can see (own, teammates', team, department, other departments, gold).
    Without `llm`, only the wording check runs."""
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
    if llm is None or not entries:
        return Resolution(canon)
    return _ask(llm, new, entries[:MAX_ENTRIES], canon)


def _ask(llm: BaseChatModel, new: MemoryItem, entries: list[MemoryItem], canon: str) -> Resolution:
    """Mem0's update phase for one new fact against the term's meanings. NONE on one of them is "the same meaning"; an
    ADD, or an answer that does not single one out, keeps the user's meaning as their own."""
    from mem0.configs.prompts import get_update_memory_messages

    from .middleware import message_text

    memories = [{"id": str(i), "text": f"{canon}: {e.meaning}"} for i, e in enumerate(entries)]
    prompt = get_update_memory_messages(memories, [f"{canon}: {new.meaning}"], MERGE_PROMPT)
    try:
        text = message_text(llm.invoke([("human", prompt)]))
        events = json.loads(text[text.find("{"): text.rfind("}") + 1]).get("memory", [])
    except Exception as e:  # never let consolidation break a turn: store it as said
        log.warning("could not compare “%s” with memory, saving it as said: %s", canon, e)
        return Resolution(canon)
    events = [x for x in events if isinstance(x, dict)]
    kinds = [str(x.get("event") or "").upper() for x in events]
    same = [entries[int(x["id"])] for x, kind in zip(events, kinds)
            if kind == "NONE" and str(x.get("id")).isdigit() and int(x["id"]) < len(entries)]
    # An ADD means "new"; several different meanings marked NONE is Mem0's default "nothing changed" listing, which
    # does not say which one holds the fact.
    if "ADD" in kinds or len({e.meaning for e in same}) != 1:
        return Resolution(canon)
    return Resolution(canon, same[0], "mem0")
