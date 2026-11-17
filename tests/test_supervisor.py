"""Supervisor + SQL agent + casebook, driven by a scripted model (no LLM)."""

import sqlite3
from pathlib import Path

from langchain_community.utilities import SQLDatabase
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from memory_demo import agent as agent_mod
from memory_demo import service
from memory_demo.casebook import Casebook
from memory_demo.extractor import MemoryOp, MemoryOps
from memory_demo.middleware import UserContext
from memory_demo.org import Org
from memory_demo.service import MemoryService
from memory_demo.store import MemoryStore, SqliteExecutor

ROOT = Path(__file__).resolve().parents[1]
LUKAS = "lukas.brandt@shs-demo.example"
SYSTEMS: list[str] = []


class Scripted(BaseChatModel):
    """Supervisor: casebook → SQL agent → answer. SQL agent: one query → answer."""

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = messages[0].content
        SYSTEMS.append(system)
        last = messages[-1]
        if system.startswith("You are the supervisor"):
            if isinstance(last, HumanMessage):
                msg = AIMessage("", tool_calls=[{"name": "search_casebook", "args": {"question": last.content}, "id": "c"}])
            elif last.name == "search_casebook":
                q = next(m.content for m in messages if isinstance(m, HumanMessage))
                msg = AIMessage("", tool_calls=[{"name": "ask_sql_agent", "args": {"question": q}, "id": "s"}])
            else:
                msg = AIMessage(f"Supervisor: {last.content}")
        elif isinstance(last, ToolMessage):
            msg = AIMessage(f"{last.content} events")
        else:
            msg = AIMessage("", tool_calls=[{"name": "sql_db_query", "args": {"query": "SELECT COUNT(*) FROM bgl_events"},
                                             "id": "q"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_supervisor_routes_shares_memory_and_fills_casebook(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "bgl.db")
    con.execute("CREATE TABLE bgl_events (event_id INTEGER, message TEXT)")
    con.execute("INSERT INTO bgl_events VALUES (1, 'x')")
    con.commit()
    db = SQLDatabase.from_uri(f"sqlite:///{tmp_path / 'bgl.db'}")
    org = Org.from_csv(ROOT / "seed" / "org.csv")
    store = MemoryStore(SqliteExecutor(str(tmp_path / "mem.db")))
    svc = MemoryService(store, org, llm=Scripted())
    svc.reset(step=2, start="2005-12-01")
    casebook = Casebook(store, org)
    ops = iter([[], [MemoryOp(op="add", item_type="term", term="memory errors", meaning="only DDR errors",
                              sql_hint="message LIKE '%ddr error%'", evidence="correction")]])
    monkeypatch.setattr(service, "extract", lambda *a, **k: MemoryOps(ops=next(ops, [])))
    graph = agent_mod.build_supervisor(Scripted(), db, svc, casebook)

    def ask(text):
        updates = {}
        for chunk in graph.stream({"messages": [HumanMessage(text)]}, context=UserContext(user_email=LUKAS),
                                  stream_mode="updates"):
            for node, upd in chunk.items():
                updates.setdefault(node, []).append(upd)
        return updates

    SYSTEMS.clear()
    first = ask("How many memory errors on the noisy rack last month?")
    sql_system = next(s for s in SYSTEMS if s.startswith("You are an agent"))
    assert "## Memory" in sql_system and "rack = 'R37'" in sql_system  # memory reached the sub-agent
    assert "## Ontology of the machine" in sql_system
    tool_msgs = [m for u in first["tools"] for m in u["messages"]]
    assert next(m for m in tool_msgs if m.name == "ask_sql_agent").artifact == {"sql": ["SELECT COUNT(*) FROM bgl_events"]}
    saved = first["CasebookMiddleware.after_agent"][0]["casebook_saved"]
    assert saved and casebook.search(LUKAS, "memory errors on the noisy rack")[0]["source"] == "you"

    # The same question again finds the case; this turn's correction withdraws it. The corrected answer is not
    # saved: it uses Lukas's own meaning, and cases are shared with the team.
    second = ask("No, memory errors on the noisy rack means only DDR errors")
    found = next(m for u in second["tools"] for m in u["messages"] if m.name == "search_casebook")
    assert found.artifact["cases"][0]["question"] == "How many memory errors on the noisy rack last month?"
    statuses = {r["question"]: r["status"] for r in casebook.all()}
    assert statuses["How many memory errors on the noisy rack last month?"] == "withdrawn"
    assert "No, memory errors on the noisy rack means only DDR errors" not in statuses
    assert second["CasebookMiddleware.after_agent"][0]["casebook_saved"] is None
