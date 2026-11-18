"""The "Find terms with Mem0" switch: terms found by meaning (a real Mem0 with a controlled embedder) and spelling."""

import hashlib
import os
from pathlib import Path

import pytest

os.environ["MEM0_TELEMETRY"] = "False"

from memory_demo import config, seeding  # noqa: E402
from memory_demo.models import MemoryItem  # noqa: E402
from memory_demo.org import Org  # noqa: E402
from memory_demo.semantic import TermIndex, another_of_its_kind, spelling_matches, windows  # noqa: E402
from memory_demo.service import MemoryService  # noqa: E402
from memory_demo.store import MemoryStore, SqliteExecutor  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LUKAS = "lukas.brandt@shs-demo.example"
DIMS = 16
SAME = {"ram problems": "memory errors", "quiet rack": "the noisy rack", "rack": "the noisy rack"}
# what the fake embedder treats as meaning the same (the last two are false friends the guard must catch)


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


def test_another_thing_of_the_same_kind_is_never_a_match():
    """Real run 18 Nov (databricks-gte-large-en): "quiet rack" matched "noisy rack" by meaning (0.88), so rack R37's
    meaning was applied to a question about R12."""
    assert another_of_its_kind("quiet rack", "the noisy rack") and another_of_its_kind("rack", "noisy rack")
    assert another_of_its_kind("r38 errors", "r37 errors") and another_of_its_kind("quiet rack rack", "noisy rack")
    assert not another_of_its_kind("ram problems", "ram errors")  # a synonym: another head word
    assert not another_of_its_kind("ddr memory errors", "memory errors")  # narrower, same thing named


def test_false_friends_found_by_meaning_are_not_applied(svc):
    assert "the noisy rack" not in terms(svc, "How many events on the quiet rack?")
    assert "the noisy rack" not in terms(svc, "Which rack logged the most?")
    assert "the noisy rack" in terms(svc, "How many events on the noisy rack?")  # its own wording still works


def test_a_typo_must_hold_word_by_word():
    """Eval 18 Nov: "card problems" (inside "node card problems") matched the alias "linkcard problems", and
    "non-fatal events" matched "fatal events", both by spelling of the whole phrase."""
    link = MemoryItem(item_type="term", term="link card problems", aliases=["linkcard problems"], meaning="x",
                      owner_email=None, owner_team=None, owner_department=None)
    fatal = MemoryItem(item_type="term", term="fatal events", meaning="x", owner_email=None, owner_team=None,
                       owner_department=None)
    assert not spelling_matches("Node card problems on R37 last month?", [link])
    assert not spelling_matches("How many non-fatal events in November?", [fatal])
    assert spelling_matches("How many fatal evnets in November?", [fatal])["fatal events"].how == "spelling"
