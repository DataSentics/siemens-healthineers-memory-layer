"""The memory's calls (`api.LocalMemory`), and a create_agent agent that uses the memory only through
MemoryMiddleware and memory_tools, as AskFST would."""

import asyncio
import logging
import time
from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from memory_demo import seeding
from memory_demo import service as service_mod
from memory_demo.api import CALLS, FAILED, LocalMemory, MemoryServiceError, memory_tools
from memory_demo.extractor import MemoryOp, MemoryOps, TermRelevance
from memory_demo.middleware import MemoryMiddleware, UserContext
from memory_demo.org import Org
from memory_demo.service import MemoryService
from memory_demo.store import MemoryStore, SqliteExecutor

ROOT = Path(__file__).resolve().parents[1]
EMAIL = "{}@shs-demo.example".format
LUKAS, PETRA, MEI, TOMAS = EMAIL("lukas.brandt"), EMAIL("petra.lind"), EMAIL("mei.chen"), EMAIL("tomas.vrba")


def fake_extract(_llm, prev, msg, answer, applied, personal, **kwargs):
    ops = []
    if "for me memory errors" in msg.lower():
        ops = [MemoryOp(op="add", item_type="term", term="memory errors", meaning="only correctable DDR errors",
                        evidence="stated")]
    rated = [TermRelevance(item_id=a["item_id"], level="high", reason="asked") for a in applied if a.get("term")]
    return MemoryOps(ops=ops, relevance=rated)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "extract", fake_extract)
    org = Org.from_csv(ROOT / "seed" / "org.csv")
    store = MemoryStore(SqliteExecutor(str(tmp_path / "mem.db")))
    seeding.reset(store, org, step=2, start="2005-12-01")
    return MemoryService(store, org, llm=object())  # the extractor is faked; any non-None model enables learning


# ---- the calls --------------------------------------------------------------------------------------------------
def test_calls(svc):
    assert set(CALLS) == {"memory_context", "memory_learn", "memory_undo", "show_memory", "explain_term", "forget",
                          "use_meaning", "get_consent", "set_consent"}
    memory = LocalMemory(svc)
    ctx = memory.call("memory_context", question="memory errors on the noisy rack?", user_email=LUKAS)
    assert "rack R37" in ctx["context"] and {i["term"] for i in ctx["trace"]["items"]} == {
        "memory errors", "the noisy rack"}
    learned = memory.call("memory_learn", question="For me memory errors means only DDR errors", answer="16,718",
                          user_email=TOMAS, trace=ctx["trace"])
    assert [w["op"] for w in learned["writes"]] == ["add"]
    [item] = memory.call("explain_term", term="memory errors", user_email=PETRA)["items"]
    assert item["confidence"] == 0.67 and item["alternatives"][0]["scope"] == "colleagues"
    assert memory.call("memory_undo", write=learned["writes"][0], user_email=TOMAS) == {"undone": True}
    assert memory.call("explain_term", term="memory errors", user_email=PETRA)["items"][0]["confidence"] == 1.0
    assert memory.call("get_consent", user_email=MEI)["consent"] is None
    assert memory.call("set_consent", remember=True, share=False, user_email=MEI)["consent"] == {
        "remember": True, "share": False}
    view = memory.call("show_memory", user_email=LUKAS)
    assert {r["term"] for r in view["department"]} == {"comms errors"}
    assert memory.call("show_memory", user_email=f"  {LUKAS.upper()} ") == view  # case and spaces do not matter


def test_explain_term_does_not_count_as_use(svc):
    before = len(svc.store.ex.run("SELECT * FROM memory_signals"))
    LocalMemory(svc).call("explain_term", term="link card problems", user_email=PETRA)
    time.sleep(0.2)
    assert len(svc.store.ex.run("SELECT * FROM memory_signals")) == before  # the fading clock is not reset


def test_errors(svc, caplog):
    memory = LocalMemory(svc)
    with pytest.raises(MemoryServiceError, match="not in the org directory"):
        memory.call("memory_context", question="x", user_email="nobody@example.com")
    with pytest.raises(MemoryServiceError, match="user_email is required"):
        memory.call("show_memory", user_email=" ")
    with pytest.raises(MemoryServiceError, match="unknown memory call"):
        memory.call("drop_everything", user_email=LUKAS)
    lukas_item = memory.call("show_memory", user_email=LUKAS)["department"][0]
    with pytest.raises(MemoryServiceError, match="not one you can pick"):  # NotAllowed passes its message through
        memory.call("use_meaning", rejected_id=lukas_item["item_id"], chosen_id="no-such-item", user_email=LUKAS)

    def broken(*args, **kwargs):
        raise RuntimeError("table askar.shs_memory.memory_items not found on host adb-123")

    svc.view = broken
    with caplog.at_level(logging.ERROR), pytest.raises(MemoryServiceError) as e:
        memory.call("show_memory", user_email=LUKAS)
    assert str(e.value) == FAILED and "askar" not in str(e.value)  # no table or host names for the caller
    assert "memory_items" in caplog.text  # but the details are in the log


# ---- an agent that uses the memory ------------------------------------------------------------------------------
SEEN: list = []


class Model(GenericFakeChatModel):
    """Records the messages of every model call; answers from a script."""

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        SEEN.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_an_agent_gets_memory(svc):
    memory = LocalMemory(svc)
    replies = iter([AIMessage("", tool_calls=[{"name": "show_my_memory", "args": {}, "id": "t1"}]),
                    AIMessage("There were 33,662 memory errors on R37.")])
    agent = create_agent(Model(messages=replies), tools=memory_tools(memory),
                         middleware=[MemoryMiddleware(memory)], context_schema=UserContext)
    SEEN.clear()
    out = agent.invoke({"messages": [HumanMessage("memory errors on the noisy rack last month?")]},
                       context=UserContext(user_email=LUKAS))
    system = SEEN[0][0].content
    assert "## Memory" in system and "rack R37" in system and "'ddr error'" in system  # injected into the prompt
    assert "## Memory" in SEEN[1][0].content  # and into every model call of the turn
    shown = next(m for m in out["messages"] if m.type == "tool")
    assert "[your department] comms errors" in shown.content and "keep answers short" not in shown.content
    assert {i["term"] for i in out["memory_trace"]["items"]} == {"memory errors", "the noisy rack"}
    assert set(out["memory_relevance"]) == {i["item_id"] for i in out["memory_trace"]["items"]}


def test_learning_and_undo_with_async_agents(svc):
    memory = LocalMemory(svc)
    agent = create_agent(Model(messages=iter([AIMessage("16,718.")])), tools=[],
                         middleware=[MemoryMiddleware(svc)], context_schema=UserContext)  # a bare service works too
    out = asyncio.run(agent.ainvoke({"messages": [HumanMessage("For me memory errors means only DDR errors")]},
                                    context=UserContext(user_email=TOMAS)))
    [write] = out["memory_writes"]
    assert write["op"] == "add" and write["shared_with"] == "only you"
    assert memory.call("explain_term", term="memory errors", user_email=PETRA)["items"][0]["confidence"] == 0.67
    assert memory.call("memory_undo", write=write, user_email=TOMAS) == {"undone": True}  # the Undo button
    assert memory.call("explain_term", term="memory errors", user_email=PETRA)["items"][0]["confidence"] == 1.0


def test_the_agent_answers_when_memory_fails(svc):
    def broken(*args, **kwargs):
        raise RuntimeError("warehouse is stopped")

    svc.retrieve = svc.view = svc.learn = broken
    replies = iter([AIMessage("", tool_calls=[{"name": "show_my_memory", "args": {}, "id": "t1"}]),
                    AIMessage("42 events.")])
    agent = create_agent(Model(messages=replies), tools=memory_tools(svc), middleware=[MemoryMiddleware(svc)],
                         context_schema=UserContext)
    out = agent.invoke({"messages": [HumanMessage("memory errors?")]}, context=UserContext(user_email=LUKAS))
    assert out["messages"][-1].content == "42 events." and out["memory_writes"] == []
    assert "Memory was unavailable" in out["memory_trace"]["note"]
    tool_reply = next(m for m in out["messages"] if m.type == "tool")
    assert tool_reply.content == f"Memory is not available: {FAILED}"


def test_terms_are_found_by_meaning_by_default(svc):
    """Mem0 term matching is on unless a caller turns it off (fuzzy=False), both for a direct call and in an agent."""
    class Index:
        asked: list = []

        def match(self, question, items):
            self.asked.append(question)
            return {}

    index = Index()
    svc.term_index = lambda: index
    memory = LocalMemory(svc)
    memory.call("memory_context", question="any RAM trouble?", user_email=LUKAS)
    memory.call("memory_context", question="exact only", user_email=LUKAS, fuzzy=False)
    agent = create_agent(Model(messages=iter([AIMessage("none")])), tools=[], middleware=[MemoryMiddleware(memory)],
                         context_schema=UserContext)
    agent.invoke({"messages": [HumanMessage("RAM trouble last month?")]}, context=UserContext(user_email=LUKAS))
    assert index.asked == ["any RAM trouble?", "RAM trouble last month?"]
