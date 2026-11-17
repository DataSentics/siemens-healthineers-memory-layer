"""Memory as LangChain agent middleware (FR17).

- before_agent: retrieve once per turn and put the memory trace into the graph state
- wrap_model_call: append the memory block to the system prompt of every model call in the ReAct loop
- after_agent: decide what to remember from this turn

MemoryContextMiddleware is the inject-only part, for sub-agents of a supervisor.

This uses the same create_agent middleware hooks as AskFST's Data Planner, so it should attach there unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

if TYPE_CHECKING:  # the remote client imports this module without the server side
    from .service import MemoryService


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


def default_user(runtime) -> str:
    """The signed-in user's email: `runtime.context.user_email` (UserContext). Pass `user_of` for other contexts."""
    return runtime.context.user_email


def option(runtime, name: str, default):
    """An optional per-turn setting from the runtime context (exclude, fuzzy), if the context has it."""
    return getattr(runtime.context, name, default) if runtime.context is not None else default


class MemoryMiddleware(MemoryContextMiddleware):
    """In-process memory: the agent's app imports this package and reaches the memory tables itself.
    `user_of(runtime)`: the user's email (default: runtime.context.user_email).
    `sql_from(messages)`: the SQL that ran this turn (default: sql_db_query calls and sub-agent artifacts)."""

    def __init__(self, service: MemoryService, learn: bool = True, user_of=default_user, sql_from=None):
        super().__init__()
        self.service, self.learn, self.user_of, self.sql_from = service, learn, user_of, sql_from or _turn_sql

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        _, question, _ = _turn(state["messages"])
        if not question:
            return no_memory()
        context, trace = self.service.retrieve(self.user_of(runtime), question, tuple(option(runtime, "exclude", ())),
                                               fuzzy=option(runtime, "fuzzy", False))
        return {"memory_context": context, "memory_trace": trace.to_dict(), "memory_writes": []}

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        prev, question, answer = _turn(state["messages"])
        if not self.learn or not question:
            return {"memory_writes": [], "memory_relevance": {}}
        writes, relevance = self.service.learn(self.user_of(runtime), prev, question, answer,
                                               state.get("memory_trace"), sql=self.sql_from(state["messages"]),
                                               recent=_earlier_questions(state["messages"]))
        return {"memory_writes": [w.to_dict() for w in writes], "memory_relevance": relevance}
