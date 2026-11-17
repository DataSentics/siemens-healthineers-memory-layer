"""The "Find terms with Mem0" switch: terms found by meaning (a real Mem0 with a controlled embedder) and spelling."""

import hashlib
import os
from pathlib import Path

import pytest

os.environ["MEM0_TELEMETRY"] = "False"

from memory_demo import config, seeding  # noqa: E402
from memory_demo.models import MemoryItem  # noqa: E402
from memory_demo.org import Org  # noqa: E402
from memory_demo.semantic import TermIndex, spelling_matches, windows  # noqa: E402
from memory_demo.service import MemoryService  # noqa: E402
from memory_demo.store import MemoryStore, SqliteExecutor  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LUKAS = "lukas.brandt@shs-demo.example"
DIMS = 16
SAME = {"ram problems": "memory errors"}  # what the fake embedder treats as meaning the same


from langchain_core.embeddings import Embeddings  # noqa: E402


class FakeEmbeddings(Embeddings):
    """Equal vectors for the phrases in SAME, otherwise a hash: different phrases are far apart."""

    def _vec(self, text):
        text = SAME.get(text.lower().strip(), text.lower().strip())
        h = hashlib.sha256(text.encode()).digest()
        v = [b - 127.5 for b in h[:DIMS]]
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def svc(tmp_path):
    org = Org.from_csv(ROOT / "seed" / "org.csv")
    store = MemoryStore(SqliteExecutor(str(tmp_path / "mem.db")))
    seeding.reset(store, org, step=2, start="2005-12-01")
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    emb = FakeEmbeddings()
    index = TermIndex(config.mem0_client(emb, GenericFakeChatModel(messages=iter([])), DIMS, tmp_path / "idx"), emb)
    return MemoryService(store, org, term_index=lambda: index)


def terms(svc, q, fuzzy=True):
    return {u.term: u for u in svc.retrieve(LUKAS, q, fuzzy=fuzzy)[1].items if u.term}


def test_windows_skip_stop_words():
    assert "memory errors" in windows("How many memory errors did we have last month?")
    assert "how many" not in windows("How many memory errors did we have last month?")


def test_typos_match_by_spelling():
    it = MemoryItem(item_type="term", term="memory errors", aliases=["ram errors"], meaning="x",
                    owner_email=None, owner_team=None, owner_department=None)
    assert spelling_matches("How many memroy errors last month?", [it])["memory errors"].how == "spelling"
    assert not spelling_matches("What was the memory usage on R37?", [it])


def test_switch_off_changes_nothing(svc):
    assert "memory errors" not in terms(svc, "How many RAM problems last month?", fuzzy=False)
    assert "memory errors" not in terms(svc, "How many memroy errors last month?", fuzzy=False)


def test_synonym_found_by_meaning_with_mem0(svc):
    u = terms(svc, "How many RAM problems last month?")["memory errors"]
    assert u.scope == "team" and u.user_wording == "ram problems" and "Mem0" in u.matched_by
    context, _ = svc.retrieve(LUKAS, "How many RAM problems last month?", fuzzy=True)
    assert "(the user wrote “ram problems”)" in context  # the agent is told what the user meant


def test_typo_found_by_spelling(svc):
    u = terms(svc, "How many memroy errors last month?")["memory errors"]
    assert u.matched_by.startswith("matched by spelling")


def test_exact_wording_needs_no_match_note(svc):
    assert terms(svc, "How many memory errors last month?")["memory errors"].matched_by is None


def test_a_failing_index_falls_back_to_exact_wording(svc):
    def broken():
        raise ConnectionError("embedding endpoint down")

    svc.term_index = broken
    _, trace = svc.retrieve(LUKAS, "How many memory errors and RAM problems?", fuzzy=True)
    assert [i.term for i in trace.items if i.term] == ["memory errors"]
    assert "unavailable (ConnectionError)" in trace.note
