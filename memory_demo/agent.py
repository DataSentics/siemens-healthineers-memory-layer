"""The data agents the memory is validated on (PRD §8).

- build_agent: Petr's LangChain SQL agent plus memory middleware (one agent)
- build_supervisor: a supervisor in front of the same SQL agent, with a casebook of earlier good answers.
  Memory is looked up and learned once, on the supervisor; the SQL sub-agent gets the same memory block (FR18).

Both SQL agents get the BGL ontology: a summary in the prompt and a lookup tool.
"""

from __future__ import annotations

from typing import Any, NotRequired

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, dynamic_prompt
from langchain.tools import ToolRuntime, tool
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.tools.sql_database.tool import QuerySQLDatabaseTool
from langchain_community.utilities import SQLDatabase
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from . import models, ontology, sqlguard
from .bgl import TABLE
from .casebook import Casebook
from .api import memory_tools
from .middleware import MemoryContextMiddleware, MemoryMiddleware, UserContext, message_text
from .service import MemoryService

# Same instructions as the LangChain Hub prompt "langchain-ai/sql-agent-system-prompt" used in Petr's code,
# inlined to avoid the Hub dependency, plus the dataset date and the answer-first rule (FR7).
SQL_PROMPT = """You are an agent designed to interact with a SQL database.
Given an input question, create a syntactically correct {dialect} query to run, then look at the results of the query and return the answer.
Unless the user specifies a specific number of examples they wish to obtain, always limit your query to at most {top_k} results.
You can order the results by a relevant column to return the most interesting examples in the database.
Never query for all the columns from a specific table, only ask for the relevant columns given the question.
You have access to tools for interacting with the database.
Only use the below tools. Only use the information returned by the below tools to construct your final answer.
You MUST double check your query before executing it. If you get an error while executing a query, rewrite the query and try again.

DO NOT make any DML statements (INSERT, UPDATE, DELETE, DROP etc.) to the database.

The table you can query is described below, with a comment on every column, so you do not need to list the
tables or fetch the schema first. Use as few queries as possible, usually one.
When the memory section defines a term, select exactly what the definition says, with its exact values, names and
message texts.
When the user explains what a term means, build the filter from the real message wordings listed below (or look up
matching event_template values first), so it matches the actual errors and not other messages with similar words.

The data is a historical log. Treat {today} as today, so "last month" means the calendar month before {today}.
If a term in the question is ambiguous, do NOT ask a clarifying question: pick the most plausible interpretation,
answer, and say in one short line how you interpreted it (e.g. which filter you used). The user will correct you if needed.
Long-term memory is handled automatically after your answer: never say you cannot remember or save things."""

SUPERVISOR_PROMPT = """You are the supervisor of a data assistant over the Blue Gene/L supercomputer event log.
You do not write SQL yourself. Treat {today} as today.

For every question about the data:
1. Call search_casebook with the question to find similar questions that were answered well before.
2. Call ask_sql_agent once with the user's question as they asked it; the SQL agent sees the same memory section.
   If a casebook case fits, pass its SQL as example_sql.
3. Answer the user from the SQL agent's result: the numbers first, then one short line on how terms were
   interpreted. Follow the user's preferences from the memory section.

When the user asks what you remember about them, call show_my_memory.
Never ask a clarifying question first: answer with the most plausible interpretation and say which one you used.

Long-term memory is automatic: after your answer, a memory layer saves what the user taught you (definitions,
preferences) and the app shows them what was saved. So when the user tells you what a term means or asks you to
remember something, confirm it in one short sentence and use it. Never say you cannot remember or save things, and
do not describe how memory works. If the term is not in the event log, say so in one short line.
Answer the latest question on its own: do not comment on earlier answers in this conversation or call them wrong,
even if a term's meaning has changed since then."""


def _memory_tool(memory: MemoryService):
    """show_my_memory only (FR14): the demo app has its own Forget buttons and shows under every answer what each
    term meant."""
    return next(t for t in memory_tools(memory) if t.name == "show_my_memory")


@tool
def describe_bgl_entity(entity: str) -> str:
    """Look up a part of the machine or an event family in the ontology built from the data: a rack (R37), a
    midplane (R37-M1), a node card (R37-M1-N4), an alert label (KERNDTLB), a component (KERNEL) or a level
    (node card). Returns what it contains, what it is part of, its event count and the SQL filter."""
    return ontology.describe(entity)


class ReadOnlyQueryTool(QuerySQLDatabaseTool):
    """`sql_db_query`, but every query is checked by `sqlguard` before it runs (read-only, event table only)."""

    def _run(self, query: str, run_manager=None):
        if why := sqlguard.problem(query, self.db.dialect):
            return f"Error: query refused: {why}. Only read-only SELECT queries over {TABLE} are allowed."
        return super()._run(query, run_manager)


def _sql_tools(llm: BaseChatModel, db: SQLDatabase) -> list:
    tools = [ReadOnlyQueryTool(db=db, description=t.description) if t.name == "sql_db_query" else t
             for t in SQLDatabaseToolkit(db=db, llm=llm).get_tools()]
    assert any(isinstance(t, ReadOnlyQueryTool) for t in tools)
    return tools + [describe_bgl_entity]


def _sql_prompt(db: SQLDatabase):
    reference = "\n\n".join([db.get_table_info(), ontology.summary(), ontology.templates()])

    @dynamic_prompt
    def sql_prompt(request: ModelRequest) -> str:  # "today" follows the demo clock
        return SQL_PROMPT.format(dialect=db.dialect, top_k=5, today=models.today()) + "\n\n" + reference

    return sql_prompt


def build_agent(llm: BaseChatModel, db: SQLDatabase, memory: MemoryService, learn: bool = True):
    return create_agent(
        llm,
        _sql_tools(llm, db) + [_memory_tool(memory)],
        middleware=[_sql_prompt(db), MemoryMiddleware(memory, learn=learn)],  # outer first: memory appends
        context_schema=UserContext,
    )


# ---- supervisor + SQL agent + casebook ----------------------------------------------------------------------
def _turn_tool_messages(messages) -> list[ToolMessage]:
    last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    return [m for m in messages[last_human:] if isinstance(m, ToolMessage)]


class CasebookState(AgentState):
    casebook_saved: NotRequired[str | None]


class CasebookMiddleware(AgentMiddleware[CasebookState, UserContext]):
    """After each answer: a correction in this turn withdraws the previous case; SQL that ran becomes a new case,
    unless it used a personal meaning.
    Listed before MemoryMiddleware so its after_agent runs after memory's and sees this turn's writes."""

    state_schema = CasebookState

    def __init__(self, casebook: Casebook):
        super().__init__()
        self.casebook = casebook

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        email = runtime.context.user_email
        if any(w.get("evidence") == "correction" for w in state.get("memory_writes") or []):
            self.casebook.withdraw_latest(email)
        sql = [q for m in _turn_tool_messages(state["messages"]) if m.name == "ask_sql_agent"
               for q in (m.artifact or {}).get("sql", [])]
        # Cases are shared with the team, so an answer built on someone's own meaning of a term is not saved:
        # its SQL would hand that meaning to the team.
        own = any(i.get("scope") == "personal" and i.get("term") for i in (state.get("memory_trace") or {}).get("items", []))
        taught = any(w.get("term") and w.get("op") != "same" for w in state.get("memory_writes") or [])
        if not sql or own or taught:
            return {"casebook_saved": None}
        question = message_text(next(m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)))
        return {"casebook_saved": self.casebook.add(email, question, sql[-1]).case_id}


def build_supervisor(llm: BaseChatModel, db: SQLDatabase, memory: MemoryService, casebook: Casebook,
                     learn: bool = True):
    sql_agent = create_agent(
        llm, _sql_tools(llm, db), middleware=[_sql_prompt(db), MemoryContextMiddleware()], context_schema=UserContext,
    )

    @tool(response_format="content_and_artifact")
    def ask_sql_agent(question: str, runtime: ToolRuntime[UserContext], example_sql: str | None = None):
        """Hand a data question to the SQL agent, which queries the event log and returns the answer with the
        numbers. `example_sql`: SQL of a similar earlier question from the casebook, to reuse or adapt."""
        task = question
        if example_sql:
            task += ("\n\nA similar earlier question was answered well with this SQL. Reuse its filters, but "
                     f"recompute any dates from today:\n{example_sql}")
        out = sql_agent.invoke({"messages": [HumanMessage(task)], "memory_context": runtime.state.get("memory_context", "")},
                               context=runtime.context)
        sql = [tc["args"].get("query", "") for m in out["messages"] if isinstance(m, AIMessage)
               for tc in m.tool_calls if tc["name"] == "sql_db_query"]
        answer = next((message_text(m) for m in reversed(out["messages"]) if isinstance(m, AIMessage) and not m.tool_calls), "")
        return answer or "The SQL agent returned no answer.", {"sql": sql}

    @tool(response_format="content_and_artifact")
    def search_casebook(question: str, runtime: ToolRuntime[UserContext]):
        """Find similar questions that you or the user's team asked before and that were answered well,
        with the SQL that answered them."""
        cases = casebook.search(runtime.context.user_email, question)
        if not cases:
            return "No similar earlier questions.", {"cases": []}
        text = "\n\n".join(f"Q (asked {c['asked_on']}, by {c['source']}): {c['question']}\nSQL: {c['sql']}" for c in cases)
        return text, {"cases": cases}

    @dynamic_prompt
    def supervisor_prompt(request: ModelRequest) -> str:
        return SUPERVISOR_PROMPT.format(today=models.today())

    return create_agent(
        llm,
        [search_casebook, ask_sql_agent, _memory_tool(memory)],
        middleware=[supervisor_prompt, CasebookMiddleware(casebook), MemoryMiddleware(memory, learn=learn)],
        context_schema=UserContext,
    )
