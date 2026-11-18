"""Finding terms in a question by meaning and spelling, with Mem0 as the index (switch "Find terms with Mem0").

Without it, a term counts as mentioned only when its exact wording (or an alias, or a plural) is in the question.
With it, two more ways count:
- **meaning (Mem0):** every term and alias is a Mem0 memory (text + embedding, tagged with the item it belongs to).
  Short word windows of the question (1-3 words) are embedded in one batch call and searched in Mem0's vector store;
  a window close enough to a term (cosine >= MEANING_MIN) mentions it, e.g. "RAM problems" -> memory errors.
- **spelling:** windows with the same number of words as a term and a spelling ratio >= SPELLING_MIN, e.g.
  "memroy errors" -> memory errors. Embeddings are poor at typos, so this is plain string similarity.

The tables stay the source of truth: the index only answers "which terms is this question about", and the resolver
then applies the same rules as always. The index can be lost (it lives in a temp folder) and rebuilds itself.
Thresholds were set on sample questions against databricks-gte-large-en (see README).
"""

from __future__ import annotations

import re
import uuid
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from .models import SHAREABLE_TYPES, MemoryItem

MEANING_MIN = 0.88  # below this, near-misses like "memory usage" ~ memory errors (0.84) would match
SPELLING_MIN = 0.85
WORD_MIN = 0.75  # every word on its own must be a typo of the term's word: "non-fatal" is not "fatal" (0.71)
NAMESPACE = "term-index"  # Mem0 user_id under which the term keys are stored
_STOP = set("a an and are as at by did do does for from had has have how i i'm in is it last many me month of on or "
            "our show tell that the there this to us was we were what when which who with you new here week about any "
            "please give list".split())


@dataclass
class Match:
    term: str
    how: str  # "meaning" or "spelling"
    wording: str  # what the user wrote
    key: str  # the term or alias it matched
    score: float

    def describe(self) -> str:
        if self.how == "meaning":
            return f"matched by meaning with Mem0: “{self.wording}” ≈ {self.key} ({self.score:.2f})"
        return f"matched by spelling: “{self.wording}” ≈ {self.key}"


def windows(question: str) -> list[str]:
    """1-3 word windows of the question that neither start nor end with a stop word."""
    words = re.findall(r"[^\W_](?:[\w/'-]*[^\W_])?", question.lower())  # words of any script, inner / ' - kept
    out = set()
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            w = words[i:i + n]
            if w[0] not in _STOP and w[-1] not in _STOP:
                out.add(" ".join(w))
    return sorted(out)


def _keys(item: MemoryItem) -> list[str]:
    return [k for k in [item.term, *item.aliases] if k]


def _content_words(text: str) -> list[str]:
    words = re.findall(r"[^\W_](?:[\w/'-]*[^\W_])?", text.lower())
    return [w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words if w not in _STOP]


def another_of_its_kind(gram: str, key: str) -> bool:
    """True when the question's words name another thing of the same kind as the term: the same head word with a
    modifier the term has and the words lack ("quiet rack" or plain "rack" vs "noisy rack", "R38 errors" vs "R37
    errors"). Embeddings put such pairs close together (quiet rack ~ noisy rack: 0.88 with databricks-gte-large-en),
    but they never mean the same. Synonyms keep matching: "RAM problems" ~ "RAM errors" differ in the head word."""
    g, k = _content_words(gram), _content_words(key)
    if not g or not k or g[-1] != k[-1]:
        return False
    return bool(set(k[:-1]) - set(g[:-1]))


def spelling_matches(question: str, items: list[MemoryItem]) -> dict[str, Match]:
    grams = windows(question)
    out: dict[str, Match] = {}
    for it in items:
        for key in _keys(it):
            if len(key) < 5:
                continue
            n = len(key.split())
            for g in grams:
                if len(g.split()) != n or g == key:
                    continue
                score = max(SequenceMatcher(None, g, key).ratio(), SequenceMatcher(None, g.rstrip("s"), key.rstrip("s")).ratio())
                if (score >= SPELLING_MIN and _typo_of(g, key)
                        and score > getattr(out.get(it.term), "score", 0)):
                    out[it.term] = Match(it.term, "spelling", g, key, round(score, 2))
    return out


def _typo_of(gram: str, key: str) -> bool:
    """Word by word, each word is a typo of the term's word ("memroy errors", "noisy rak"). The phrase as a whole can
    look close while one word is another word: "card problems" vs "linkcard problems", "non-fatal" vs "fatal"."""
    def close(a: str, b: str) -> bool:
        return max(SequenceMatcher(None, a, b).ratio(),
                   SequenceMatcher(None, a.rstrip("s"), b.rstrip("s")).ratio()) >= WORD_MIN
    return all(close(a, b) for a, b in zip(gram.split(), key.split()))


class TermIndex:
    """Mem0 (local Qdrant) holding one memory per term key, so terms can be found by meaning."""

    def __init__(self, mem0: Any, embedder: Any):
        self.m, self.embedder = mem0, embedder
        self.indexed: set[tuple[str, str]] = set()  # (item_id, key)
        self.lock = threading.Lock()

    def sync(self, items: list[MemoryItem]) -> None:
        """Add the keys of any term not indexed yet (new terms, new team meanings). Withdrawn items never match,
        because matches are limited to the items the user can currently see."""
        with self.lock:
            new = [(it, key) for it in items if it.term and it.item_type in SHAREABLE_TYPES
                   for key in _keys(it) if (it.item_id, key) not in self.indexed]
            if not new:
                return
            # One embedding call for all new keys, written straight into Mem0's vector store: Mem0's add() embeds one
            # memory per call, which made the first lookup take over 30 s on a serving endpoint.
            vectors = self.embedder.embed_documents([key for _, key in new])
            self.m.vector_store.insert(
                vectors=vectors,
                payloads=[{"data": key, "user_id": NAMESPACE, "item_id": it.item_id, "term": it.term, "key": key}
                          for it, key in new],
                ids=[str(uuid.uuid4()) for _ in new],
            )
            self.indexed.update((it.item_id, key) for it, key in new)

    def meaning_matches(self, question: str, items: list[MemoryItem]) -> dict[str, Match]:
        known = {key.lower() for it in items for key in _keys(it)}
        # A window that is itself a term the user can see is found by its own wording; it never stands for another
        # term ("quiet rack" is not "noisy rack", however close their embeddings are).
        grams = [g for g in windows(question) if g not in known]
        if not grams:
            return {}
        visible = {it.item_id for it in items}
        vectors = self.embedder.embed_documents(grams)  # one call for all windows
        out: dict[str, Match] = {}
        with self.lock:
            for gram, vec in zip(grams, vectors):
                for hit in self.m.vector_store.search(query=gram, vectors=vec, limit=3,
                                                       filters={"user_id": NAMESPACE}):
                    meta = hit.payload or {}
                    term, score, key = meta.get("term"), float(hit.score), meta.get("key") or ""
                    if (meta.get("item_id") in visible and score >= MEANING_MIN and gram != key
                            and not another_of_its_kind(gram, key)
                            and score > getattr(out.get(term), "score", 0)):
                        out[term] = Match(term, "meaning", gram, key, round(score, 2))
        return out

    def match(self, question: str, items: list[MemoryItem]) -> dict[str, Match]:
        self.sync(items)
        found = self.meaning_matches(question, items)
        for term, m in spelling_matches(question, items).items():
            found.setdefault(term, m)  # meaning first; spelling for typos embeddings miss
        return found
