"""Personal memory in Mem0 (PRD K1/K5): the never-shared part of a user's memory, i.e. preferences and facts.

Shared knowledge (terms and relations with their scope, support and signals) stays in MemoryStore; Mem0 holds what
only its owner ever sees and finds it by semantic search. Our extractor already decides what to write, so Mem0's own
LLM extraction is off (`infer=False`); Mem0 is the store and the retriever.

The adapter returns MemoryItem objects, so the resolver and the UI treat both stores the same way.
"""

from __future__ import annotations

from typing import Any

from .models import MemoryItem, now_iso
from .org import Person


class Mem0Personal:
    def __init__(self, client: Any):  # a mem0.Memory
        self.m = client

    def add(self, owner: Person, item_type: str, meaning: str) -> MemoryItem:
        meta = {"type": item_type, "created_at": now_iso()}
        r = self.m.add(meaning, user_id=owner.email, metadata=meta, infer=False)
        return self._item({"id": r["results"][0]["id"], "memory": meaning, "metadata": meta}, owner)

    def update(self, item: MemoryItem, meaning: str) -> None:
        # Mem0 drops metadata on update unless it is passed again
        self.m.update(item.item_id, meaning, metadata={"type": item.item_type, "created_at": now_iso()})

    def delete(self, item_id: str) -> None:
        self.m.delete(item_id)

    def clear(self, emails: list[str]) -> None:  # demo reset; Mem0 only lists memories per user
        for email in emails:
            for r in self.m.get_all(user_id=email, limit=10_000)["results"]:
                self.m.delete(r["id"])

    def get(self, item_id: str, owner: Person) -> MemoryItem | None:
        r = self.m.get(item_id)
        return self._item(r, owner) if r and r.get("user_id") == owner.email else None

    def all(self, owner: Person) -> list[MemoryItem]:
        return [self._item(r, owner) for r in self.m.get_all(user_id=owner.email)["results"]]

    def relevant_facts(self, owner: Person, question: str, k: int = 3) -> list[MemoryItem]:
        hits = self.m.search(question, user_id=owner.email, filters={"type": "fact"}, limit=k, rerank=False)
        return [self._item(r, owner) for r in hits["results"]]

    @staticmethod
    def _item(r: dict, owner: Person) -> MemoryItem:
        meta = r.get("metadata") or {}
        return MemoryItem(
            item_type=meta.get("type", "fact"), meaning=r["memory"], owner_email=owner.email, owner_team=owner.team,
            owner_department=owner.department, shareable=False, item_id=r["id"],
            created_at=meta.get("created_at") or r.get("created_at") or now_iso(),
        )
