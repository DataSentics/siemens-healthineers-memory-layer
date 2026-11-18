"""End-to-end through create_agent with a fake model: memory is injected and the trace/writes reach the stream."""

import sqlite3
from pathlib import Path

from langchain_community.utilities import SQLDatabase
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from memory_demo import agent as agent_mod
from memory_demo import seeding, service
from memory_demo.extractor import MemoryOp, MemoryOps, TermRelevance
from memory_demo.middleware import UserContext
from memory_demo.org import Org
from memory_demo.service import MemoryService
from memory_demo.store import MemoryStore, SqliteExecutor

ROOT = Path(__file__).resolve().parents[1]
LUKAS = "lukas.brandt@shs-demo.example"


SEEN: list = []


class RecordingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        SEEN.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_memory_injected_and_streamed(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "bgl.db")
    con.execute("CREATE TABLE bgl_events (event_id INTEGER, message TEXT)")
    con.commit()
    db = SQLDatabase.from_uri(f"sqlite:///{tmp_path / 'bgl.db'}")

    org = Org.from_csv(ROOT / "seed" / "org.csv")
    store = MemoryStore(SqliteExecutor(str(tmp_path / "mem.db")))
    seeding.reset(store, org, step=2, start="2005-12-01")
    llm = RecordingModel(messages=iter([AIMessage(content="There were 33,662 memory errors on R37.")]))
    svc = MemoryService(store, org, llm=llm)

    def fake_extract(_llm, prev, user_message, answer, applied, personal, **kwargs):
        assert "memory errors" in user_message and answer.startswith("There were")
        rated = [TermRelevance(item_id=a["item_id"], level="high", reason="asked about it") for a in applied if a.get("term")]
        return MemoryOps(ops=[MemoryOp(op="add", item_type="preference", meaning="show numbers per node card",
                                       evidence="explicit")], relevance=rated + [TermRelevance(item_id="nope", level="low", reason="x")])

    monkeypatch.setattr(service, "extract", fake_extract)
    graph = agent_mod.build_agent(llm, db, svc)

    updates = {}
    for chunk in graph.stream({"messages": [HumanMessage("memory errors on the noisy rack last month?")]},
                              context=UserContext(user_email=LUKAS), stream_mode="updates"):
        for node, upd in chunk.items():
            updates[node] = upd

    system = SEEN[-1][0].content
    assert "## Memory" in system and "rack R37" in system and "'ddr error'" in system
    assert system.startswith("You are an agent") and "Treat 2006-01-01 as today" in system  # step 2 = a month later
    trace = next(u["memory_trace"] for u in updates.values() if u and "memory_trace" in u)
    assert {i["term"] for i in trace["items"]} == {"memory errors", "the noisy rack"}
    writes = next(u["memory_writes"] for u in updates.values() if u and u.get("memory_writes"))
    assert writes[0]["meaning"] == "show numbers per node card" and writes[0]["shared_with"] == "only you"
    relevance = next(u["memory_relevance"] for u in updates.values() if u and u.get("memory_relevance"))
    assert set(relevance) == {i["item_id"] for i in trace["items"]}  # ids not applied this turn are dropped
    assert {r["level"] for r in relevance.values()} == {"high"}
    print(sorted(updates))


def test_message_text_reads_claude_json_blocks():
    from memory_demo.middleware import message_text
    blocks = '[{"type": "reasoning", "summary": [{"type": "summary_text", "text": ""}]}, {"type": "text", "text": "44,873 incidents"}]'
    assert message_text(AIMessage(blocks)) == "44,873 incidents"
    assert message_text(AIMessage([{"type": "text", "text": "a"}, {"type": "reasoning"}])) == "a"
    assert message_text(AIMessage("[{not json")) == "[{not json"
