"""The memory's calls, and the model's tools on top of them.

The calls (`CALLS`) run where the memory lives: in the memory MCP server (`mcp_server`, which wraps them in a
`LocalMemory`), or in-process in the demo app. An agent reaches them through something with
`call(name, user_email=..., **args)` / `await acall(...)`: a `remote.MemoryClient` (AskFST: the MCP server) or a
`LocalMemory(service)`. Both return plain JSON-shaped dicts, safe to keep in the agent state, and raise
`MemoryServiceError` with a message meant for the caller.

    memory = MemoryClient(url)                              # or LocalMemory(config.memory_service()) in-process
    agent = create_agent(model, tools=[*tools, *memory_tools(memory, user_of=...)],
                         middleware=[MemoryMiddleware(memory, user_of=...)])

An Undo button calls `memory.call("memory_undo", write=w, user_email=...)`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Callable

from langchain.tools import ToolRuntime, tool

if TYPE_CHECKING:
    from .service import MemoryService

log = logging.getLogger(__name__)
FAILED = "The memory service could not complete this request."


class MemoryServiceError(RuntimeError):
    """The memory refused or failed a call; the message says why and is safe to show."""


# ---- the calls: (service, user email, arguments) -> JSON-shaped dict -----------------------------------------------
def memory_context(service: MemoryService, email: str, question: str, fuzzy: bool = True,
                   exclude: list[str] | None = None) -> dict[str, Any]:
    context, trace = service.retrieve(email, question, tuple(exclude or ()), fuzzy=fuzzy)
    return {"context": context, "trace": trace.to_dict()}


def memory_learn(service: MemoryService, email: str, question: str, answer: str, previous_answer: str | None = None,
                 trace: dict[str, Any] | None = None, sql: list[str] | None = None,
                 earlier_questions: list[str] | None = None) -> dict[str, Any]:
    writes, relevance = service.learn(email, previous_answer, question, answer, trace, sql=sql or [],
                                      recent=earlier_questions or [])
    return {"writes": [w.to_dict() for w in writes], "relevance": relevance}


def memory_undo(service: MemoryService, email: str, write: dict[str, Any]) -> dict[str, Any]:
    service.undo(email, dict(write))
    return {"undone": True}


def show_memory(service: MemoryService, email: str) -> dict[str, Any]:
    return service.view(email)


def explain_term(service: MemoryService, email: str, term: str) -> dict[str, Any]:
    _, trace = service.retrieve(email, term, record=False)  # asking what a term means is not using it
    items = [asdict(i) for i in trace.items if i.term]
    return {"term": term, "items": items, "found": bool(items)}


def forget(service: MemoryService, email: str, item_id: str) -> dict[str, Any]:
    w = service.forget(email, item_id)
    return {"forgotten": w.to_dict() if w else None}


def use_meaning(service: MemoryService, email: str, rejected_id: str, chosen_id: str) -> dict[str, Any]:
    return {"writes": [w.to_dict() for w in service.choose(email, rejected_id, chosen_id)]}


def get_consent(service: MemoryService, email: str) -> dict[str, Any]:
    return {"consent": service.consent(email)}


def set_consent(service: MemoryService, email: str, remember: bool, share: bool) -> dict[str, Any]:
    service.set_consent(email, remember, share)
    return {"consent": service.consent(email)}


CALLS: dict[str, Callable[..., dict[str, Any]]] = {f.__name__: f for f in (
    memory_context, memory_learn, memory_undo, show_memory, explain_term, forget, use_meaning, get_consent,
    set_consent)}


def known_user(service: MemoryService, email: str) -> None:
    """Refuse someone who is not in the org directory (it is re-read for a person the HR sync just added)."""
    from .service import NotAllowed

    try:
        service.org.get(email)
    except KeyError:
        raise NotAllowed(f"{email} is not in the org directory") from None


class LocalMemory:
    """The memory calls. The user comes from the caller (the agent's sign-in), as `user_email`; it is never taken from
    the model. Someone not in the org directory is refused. Errors other than a refusal are logged here and reach the
    caller as one generic message, so table names, hosts and tokens never end up in a prompt or a UI."""

    def __init__(self, service: MemoryService):
        self.service = service

    def call(self, name: str, user_email: str | None = None, **args) -> dict[str, Any]:
        from .service import NotAllowed

        email = (user_email or "").strip().lower()
        if not email:
            raise MemoryServiceError("user_email is required")
        if name not in CALLS:
            raise MemoryServiceError(f"unknown memory call {name!r}")
        try:
            known_user(self.service, email)
            result = CALLS[name](self.service, email, **args)
        except NotAllowed as e:
            raise MemoryServiceError(str(e)) from None
        except Exception as e:
            log.exception("memory call %s failed for %s", name, email)
            raise MemoryServiceError(FAILED) from e
        # Through JSON: plain lists and dicts that fit any agent state or checkpointer, never live objects.
        return json.loads(json.dumps(result))

    async def acall(self, name: str, **args) -> dict[str, Any]:
        """`call` for async agents (ainvoke / astream): the blocking work runs in a worker thread."""
        return await asyncio.to_thread(self.call, name, **args)


def as_memory(memory):
    """Something to make memory calls on: a `remote.MemoryClient` (the MCP server) or a LocalMemory as given; a bare
    MemoryService is wrapped in a LocalMemory."""
    return memory if hasattr(memory, "call") and hasattr(memory, "acall") else LocalMemory(memory)


# ---- the model's tools -------------------------------------------------------------------------------------------
def default_user(runtime) -> str:
    """The signed-in user's email: `runtime.context.user_email`. Pass `user_of` for other contexts."""
    return runtime.context.user_email


SCOPE_NAMES = {"personal": "yours", "team": "your team", "department": "your department", "gold": "official",
               "other": "another department"}


def memory_tools(memory, user_of=default_user) -> list:
    """show_my_memory, explain_term and forget_memory for the model. `memory`: a `remote.MemoryClient` (the calls go
    to the memory MCP server), a LocalMemory or a MemoryService. The user always comes from the runtime (`user_of`),
    never from the model, so a prompt cannot make the model read someone else's memory. A failing memory call answers
    with a short message instead of stopping the agent."""
    memory = as_memory(memory)

    def ask(name: str, runtime, **args) -> dict[str, Any] | str:
        try:
            return memory.call(name, user_email=user_of(runtime), **args)
        except MemoryServiceError as e:
            return f"Memory is not available: {e}"
        except Exception:
            log.exception("memory tool %s failed", name)
            return f"Memory is not available: {FAILED}"

    @tool
    def show_my_memory(runtime: ToolRuntime) -> str:
        """Show what the assistant remembers for the current user: their own definitions, what their
        team and department agreed on (without names), the official glossary and other departments' meanings. Use it
        when the user asks what you remember about them or their team."""
        view = ask("show_memory", runtime)
        if isinstance(view, str):
            return view
        lines = [f"[{r.get('support') or SCOPE_NAMES[scope]}] {r['term']}: {r['meaning']}"
                 + (f" id={r['item_id']}" if scope == "personal" else "")
                 for scope in SCOPE_NAMES for r in view.get(scope, [])]
        return "\n".join(lines) or "Nothing remembered yet."

    @tool
    def explain_term(term: str, runtime: ToolRuntime) -> str:
        """Explain what a term means for the current user and which other meanings are in use (their team's, their
        department's, the official one, other departments', colleagues' own without names). Use it when the user asks
        what a term means or why it was interpreted a certain way."""
        result = ask("explain_term", runtime, term=term)
        if isinstance(result, str):
            return result
        if not result["found"]:
            return f"Memory has no meaning for “{term}”."
        lines = []
        for it in result["items"]:
            if it["scope"] == "unresolved":
                lines.append(f"“{it['term']}” is not defined for this user; other departments use:")
            else:
                conf = f", {it['confidence']:.0%} confidence" if it.get("confidence") is not None else ""
                lines.append(f"“{it['term']}” = {it['meaning']} ({it['support_label']}{conf})")
            lines += [f"  - also in use: {a['meaning']} ({a.get('department') or a['scope']})"
                      for a in it.get("alternatives") or []]
        return "\n".join(lines)

    @tool
    def forget_memory(item_id: str, runtime: ToolRuntime) -> str:
        """Forget one of the current user's own memory items. Get the id from show_my_memory first. Shared meanings
        cannot be forgotten this way."""
        result = ask("forget", runtime, item_id=item_id)
        if isinstance(result, str):
            return result
        return ("Forgotten." if result["forgotten"]
                else "Nothing to forget: that item is not the user's own, or it is already forgotten.")

    return [show_my_memory, explain_term, forget_memory]
