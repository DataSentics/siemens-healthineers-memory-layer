"""Memory as LangChain agent middleware (FR17).

- before_agent: retrieve once per turn and put the memory trace into the graph state
- wrap_model_call: append the memory block to the system prompt of every model call in the ReAct loop
- after_agent: decide what to remember from this turn

MemoryMiddleware makes its calls through `api.LocalMemory`. MemoryContextMiddleware is the inject-only part, for
sub-agents of a supervisor.

This uses the same create_agent middleware hooks as AskFST's Data Planner, so it should attach there unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .api import as_memory, default_user

log = logging.getLogger(__name__)


@dataclass
class UserContext:
    user_email: str
    exclude: tuple[str, ...] = ()  # memory items the user rejected for this answer (regenerate)
    fuzzy: bool = False  # also find terms by meaning (Mem0) and spelling


class MemoryState(AgentState):
    memory_context: NotRequired[str]
    memory_trace: NotRequired[dict[str, Any]]
    memory_writes: NotRequired[list[dict[str, Any]]]
    memory_relevance: NotRequired[dict[str, dict[str, str]]]  # item id -> level, reason (rated after the answer)


def message_text(msg) -> str:
    """The text of a model message. Content can be a string, a list of blocks, or (Claude served by Databricks)
    the list of blocks JSON-encoded as a string; reasoning blocks are left out."""
    c = msg.content
    if isinstance(c, str):
        if not (c.lstrip().startswith("[{") and '"type"' in c):
            return c
        try:
            c = json.loads(c)
        except ValueError:
            return c
    parts = []
    for p in c:
        if isinstance(p, dict):
            if p.get("type", "text") == "text":
                parts.append(p.get("text", ""))
        else:
            parts.append(str(p))
    return "".join(parts)



def _last_human(messages) -> int | None:
    return max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=None)


def _turn(messages) -> tuple[str | None, str, str]:
    """(previous final answer, current user message, current final answer) of the last turn. The user message is
    empty when the agent was started without one (a sub-agent, a scheduled run): memory then stays out of the turn."""
    last_human = _last_human(messages)
    if last_human is None:
        answer = next((m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls), None)
        return None, "", message_text(answer) if answer else ""
    prev = next((m for m in reversed(messages[:last_human]) if isinstance(m, AIMessage) and not m.tool_calls), None)
    answer = next((m for m in reversed(messages[last_human:]) if isinstance(m, AIMessage) and not m.tool_calls), None)
    return (message_text(prev) if prev else None), message_text(messages[last_human]), (message_text(answer) if answer else "")


def _earlier_questions(messages, n: int = 6) -> list[str]:
    """The user's messages before the current one, oldest first: context for the memory extractor."""
    humans = [message_text(m) for m in messages if isinstance(m, HumanMessage)]
    return humans[:-1][-n:]


def _turn_sql(messages) -> list[str]:
    """SQL run in the last turn: direct sql_db_query calls, or what a SQL sub-agent reports in its artifact."""
    last_human = _last_human(messages)
    if last_human is None:
        return []
    sql = []
    for m in messages[last_human:]:
        if isinstance(m, AIMessage):
            sql += [tc["args"].get("query", "") for tc in m.tool_calls if tc["name"] == "sql_db_query"]
        elif isinstance(m, ToolMessage) and isinstance(m.artifact, dict):
            sql += m.artifact.get("sql", [])
    return sql


class MemoryContextMiddleware(AgentMiddleware[MemoryState, UserContext]):
    """Inject only: for sub-agents that get the memory block the supervisor already looked up (FR18)."""

    state_schema = MemoryState

    @staticmethod
    def _with_memory(request: ModelRequest) -> ModelRequest:
        context = request.state.get("memory_context")
        if context:
            base = request.system_prompt or ""
            request = request.override(system_message=SystemMessage(content=f"{base}\n\n{context}"))
        return request

    def wrap_model_call(self, request: ModelRequest, handler):
        return handler(self._with_memory(request))

    async def awrap_model_call(self, request: ModelRequest, handler):  # agents run with ainvoke / astream
        return await handler(self._with_memory(request))


def no_memory() -> dict[str, Any]:
    """The state update of a turn that memory stays out of."""
    return {"memory_context": "", "memory_trace": {"items": []}, "memory_writes": []}


def nothing_learned() -> dict[str, Any]:
    """The state update after a turn that taught memory nothing."""
    return {"memory_writes": [], "memory_relevance": {}}


def option(runtime, name: str, default):
    """An optional per-turn setting from the runtime context (exclude, fuzzy), if the context has it."""
    return getattr(runtime.context, name, default) if runtime.context is not None else default


class MemoryMiddleware(MemoryContextMiddleware):
    """Memory for a create_agent agent: `memory_context` before the first model call (its block is appended to the
    system prompt of every model call in the turn) and `memory_learn` after the answer. The trace and the writes land
    in the agent state (memory_trace, memory_writes, memory_relevance) for the UI.

    `memory`: a `LocalMemory` (a bare MemoryService is wrapped in one).
    `user_of(runtime)`: the signed-in user's email (default: runtime.context.user_email).
    `sql_from(messages)`: the SQL that ran this turn, so saved definitions get the filter that was checked against the
    data (default: sql_db_query calls and sub-agent artifacts with an "sql" list).

    If memory fails, the agent answers without it and memory_trace["note"] says so: memory never stops an answer."""

    def __init__(self, memory, learn: bool = True, user_of=default_user, sql_from=None):
        super().__init__()
        self.memory = as_memory(memory)
        self.learn, self.user_of, self.sql_from = learn, user_of, sql_from or _turn_sql

    # ---- the two calls, shared by the sync and async hooks -------------------------------------------------
    def _context_args(self, state, runtime) -> dict[str, Any]:
        _, question, _ = _turn(state["messages"])
        return {"question": question, "user_email": self.user_of(runtime), "fuzzy": option(runtime, "fuzzy", False),
                "exclude": list(option(runtime, "exclude", ()))}

    def _learn_args(self, state, runtime) -> dict[str, Any]:
        prev, question, answer = _turn(state["messages"])
        return {"question": question, "answer": answer, "user_email": self.user_of(runtime), "previous_answer": prev,
                "trace": state.get("memory_trace"), "sql": self.sql_from(state["messages"]),
                "earlier_questions": _earlier_questions(state["messages"])}

    @staticmethod
    def _context_update(result: dict | None, error: Exception | None = None) -> dict[str, Any]:
        if error is not None:
            log.warning("memory_context failed, answering without memory: %s", error)
            note = f"Memory was unavailable ({type(error).__name__}), so this answer does not use it."
            return {"memory_context": "", "memory_trace": {"items": [], "note": note}, "memory_writes": []}
        return {"memory_context": result["context"], "memory_trace": result["trace"], "memory_writes": []}

    @staticmethod
    def _learn_update(result: dict | None, error: Exception | None = None) -> dict[str, Any]:
        if error is not None:
            log.warning("memory_learn failed, nothing remembered this turn: %s", error)
            return nothing_learned()
        return {"memory_writes": result["writes"], "memory_relevance": result["relevance"]}

    # ---- sync agents --------------------------------------------------------------------------------------
    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        if not _turn(state["messages"])[1]:  # no user message: nothing to look up
            return no_memory()
        try:
            return self._context_update(self.memory.call("memory_context", **self._context_args(state, runtime)))
        except Exception as e:  # memory must never stop the agent from answering
            return self._context_update(None, e)

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        if not self.learn or not _turn(state["messages"])[1]:
            return nothing_learned()
        try:
            return self._learn_update(self.memory.call("memory_learn", **self._learn_args(state, runtime)))
        except Exception as e:
            return self._learn_update(None, e)

    # ---- async agents (ainvoke / astream) -----------------------------------------------------------------
    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        if not _turn(state["messages"])[1]:
            return no_memory()
        try:
            return self._context_update(await self.memory.acall("memory_context", **self._context_args(state, runtime)))
        except Exception as e:
            return self._context_update(None, e)

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        if not self.learn or not _turn(state["messages"])[1]:
            return nothing_learned()
        try:
            return self._learn_update(await self.memory.acall("memory_learn", **self._learn_args(state, runtime)))
        except Exception as e:
            return self._learn_update(None, e)
