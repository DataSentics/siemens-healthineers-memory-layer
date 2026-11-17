"""Use the shared memory from another agent over MCP (the server is memory_demo.mcp_server). This is the part
AskFST imports: it needs the server's URL and nothing else from this project (no tables, no model settings).

    from memory_demo.remote import MemoryClient, RemoteMemoryMiddleware, memory_tools

    memory = MemoryClient("https://mcp-shs-memory-<id>.azure.databricksapps.com/mcp")  # OAuth from the Databricks SDK
    agent = create_agent(model, tools=[*your_tools, *memory_tools(memory)],
                         middleware=[RemoteMemoryMiddleware(memory)])
    agent.invoke({"messages": [...]}, context=YourContext(user_email="..."))

- RemoteMemoryMiddleware: every turn, `memory_context` before the first model call (its block is appended to the
  system prompt of every model call in the turn) and `memory_learn` after the answer. The trace and the writes land in
  the agent state (memory_trace, memory_writes) for the UI. If the memory service is down, the agent answers without
  memory instead of failing.
- memory_tools: show_my_memory, explain_term and forget_memory for the model, always for the signed-in user. The model
  never passes a user, so it cannot be talked into reading someone else's memory.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain.tools import ToolRuntime, tool
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from .middleware import (MemoryContextMiddleware, _earlier_questions, _turn, _turn_sql, default_user, no_memory,
                         option)

log = logging.getLogger(__name__)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class DatabricksAuth(httpx.Auth):
    """A bearer token from the Databricks SDK config (the app's service principal, a CLI profile or env vars), asked
    for on every request so the SDK refreshes it when it expires. Databricks Apps accept OAuth tokens only."""

    def __init__(self, workspace_client):
        self.ws = workspace_client

    def auth_flow(self, request):
        request.headers.update(self.ws.config.authenticate())
        yield request


class MemoryServiceError(RuntimeError):
    """The memory service refused or failed a call; the message says why."""


class MemoryClient:
    """Calls the memory MCP server. On Databricks it signs in with OAuth from the Databricks SDK config (the app's
    service principal, a CLI profile or env vars; Databricks Apps accept OAuth tokens only). Local URLs need no auth.

    One connection is kept open and each call is a single request. The memory server keeps no session between
    requests (stateless streamable HTTP), so a tool call needs no handshake first; setting up a new client and
    session for every call cost about 250 ms, almost all of it creating the HTTP client. If a server does insist on
    a session, the client notices once and uses the full MCP handshake from then on.

    Safe to share between threads. `headers`: extra request headers (tests). `transport`: an httpx transport (tests)."""

    def __init__(self, url: str, workspace_client=None, headers: dict[str, str] | None = None, timeout: float = 90,
                 transport: httpx.BaseTransport | None = None):
        self.url, self.headers, self.timeout = url, headers or {}, timeout
        self.ws = workspace_client
        self.databricks = urlparse(url).hostname not in LOCAL_HOSTS
        self._transport = transport
        self._http: httpx.Client | None = None
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._direct = True  # one request per call; False once a server turned out to need a session

    def _auth(self):
        if not self.databricks:
            return None
        from databricks.sdk import WorkspaceClient

        self.ws = self.ws or WorkspaceClient()
        return DatabricksAuth(self.ws)

    def _client(self) -> httpx.Client:
        with self._lock:
            if self._http is None:
                self._http = httpx.Client(
                    headers={"Accept": "application/json, text/event-stream", **self.headers},
                    timeout=httpx.Timeout(self.timeout), auth=self._auth(), transport=self._transport)
            return self._http

    def close(self) -> None:
        with self._lock:
            if self._http is not None:
                self._http.close()
                self._http = None

    # ---- calling a tool ------------------------------------------------------------------------------------
    def call(self, tool_name: str, **args: Any) -> dict[str, Any]:
        """Call one tool and return its structured result. Raises MemoryServiceError when the server refuses or
        fails the call."""
        args = {k: v for k, v in args.items() if v is not None}
        if self._direct:
            try:
                return self._post(tool_name, args)
            except _NeedsSession as e:
                log.info("the memory server needs an MCP session (%s); using the full handshake from now on", e)
                self._direct = False
        return self._with_session(tool_name, args)

    async def acall(self, tool_name: str, **args: Any) -> dict[str, Any]:
        """`call` for async agents: runs in a worker thread, so the kept-open connection is shared whatever event
        loop the caller is on."""
        return await asyncio.to_thread(lambda: self.call(tool_name, **args))

    def _post(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
                "params": {"name": tool_name, "arguments": args}}
        for attempt in (1, 2):
            try:
                response = self._client().post(self.url, json=body)
                break
            except httpx.RemoteProtocolError:  # the kept-open connection was closed by the other side: once more
                if attempt == 2:
                    raise
        if response.status_code in (401, 403):
            raise MemoryServiceError(f"the memory service refused the caller (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise _NeedsSession(f"HTTP {response.status_code}")
        message = _message(response)
        if "error" in message:  # a protocol error, e.g. "missing session id"; tool errors come back as results
            raise _NeedsSession(str(message["error"].get("message", message["error"]))[:200])
        return _result(tool_name, message.get("result") or {})

    def _with_session(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """The full MCP way: new session, handshake, call. On its own event loop in a worker thread, so it also
        works when the caller is inside a running loop."""
        async def run():
            http = create_mcp_http_client(headers=self.headers, timeout=httpx.Timeout(self.timeout),
                                          auth=self._auth())
            async with http, streamable_http_client(self.url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, args)
            return _result(tool_name, result.model_dump())

        with ThreadPoolExecutor(1) as pool:
            return pool.submit(asyncio.run, run()).result()


class _NeedsSession(Exception):
    """The server did not accept a tool call without an MCP session."""


def _message(response: httpx.Response) -> dict[str, Any]:
    """The JSON-RPC reply, whether the server answered with JSON or with a one-message event stream."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1]) if data else {}
    return response.json()


def _result(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    text = "".join(c.get("text") or "" for c in result.get("content") or [] if isinstance(c, dict))
    if result.get("isError"):
        raise MemoryServiceError(text or f"{tool_name} failed")
    structured = result.get("structuredContent")
    return structured if structured is not None else json.loads(text or "{}")


class RemoteMemoryMiddleware(MemoryContextMiddleware):
    """Memory for any create_agent agent, through the MCP server.
    `user_of(runtime)`: the signed-in user's email (default: runtime.context.user_email).
    `sql_from(messages)`: the SQL that ran this turn, so saved definitions get the filter that was checked against the
    data (default: sql_db_query calls and sub-agent artifacts with an "sql" list)."""

    def __init__(self, client: MemoryClient, learn: bool = True, user_of=default_user, sql_from=None):
        super().__init__()
        self.client, self.learn, self.user_of, self.sql_from = client, learn, user_of, sql_from or _turn_sql

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
            return {"memory_writes": [], "memory_relevance": {}}
        return {"memory_writes": result["writes"], "memory_relevance": result["relevance"]}

    # ---- sync agents --------------------------------------------------------------------------------------
    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        if not _turn(state["messages"])[1]:  # no user message: nothing to look up
            return no_memory()
        try:
            return self._context_update(self.client.call("memory_context", **self._context_args(state, runtime)))
        except Exception as e:  # memory must never stop the agent from answering
            return self._context_update(None, e)

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        if not self.learn or not _turn(state["messages"])[1]:
            return None
        try:
            return self._learn_update(self.client.call("memory_learn", **self._learn_args(state, runtime)))
        except Exception as e:
            return self._learn_update(None, e)

    # ---- async agents (ainvoke / astream) -----------------------------------------------------------------
    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        if not _turn(state["messages"])[1]:
            return no_memory()
        try:
            return self._context_update(await self.client.acall("memory_context", **self._context_args(state, runtime)))
        except Exception as e:
            return self._context_update(None, e)

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        if not self.learn or not _turn(state["messages"])[1]:
            return None
        try:
            return self._learn_update(await self.client.acall("memory_learn", **self._learn_args(state, runtime)))
        except Exception as e:
            return self._learn_update(None, e)


def memory_tools(client: MemoryClient, user_of=default_user) -> list:
    """Tools for the model, always for the signed-in user (from the runtime context, never from the model)."""

    @tool
    def show_my_memory(runtime: ToolRuntime) -> str:
        """Show what the assistant remembers for the current user: their own definitions and preferences, what their
        team and department agreed on (without names), the official glossary and other departments' meanings. Use it
        when the user asks what you remember about them or their team."""
        view = client.call("show_memory", user_email=user_of(runtime))
        names = {"personal": "yours", "team": "your team", "department": "your department", "gold": "official",
                 "other": "another department"}
        lines = [f"[{names[scope]}] {r['term'] or r['type']}: {r['meaning']}"
                 + (f" ({r['department']})" if scope == "other" else "") + (f" id={r['item_id']}" if scope == "personal"
                                                                            else "")
                 for scope in names for r in view.get(scope, [])]
        return "\n".join(lines) or "Nothing remembered yet."

    @tool
    def explain_term(term: str, runtime: ToolRuntime) -> str:
        """Explain what a term means for the current user and which other meanings are in use (their team's, their
        department's, the official one, other departments', colleagues' own without names). Use it when the user asks
        what a term means or why it was interpreted a certain way."""
        result = client.call("explain_term", term=term, user_email=user_of(runtime))
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
        result = client.call("forget", item_id=item_id, user_email=user_of(runtime))
        return ("Forgotten." if result["forgotten"]
                else "Nothing to forget: that item is not the user's own, or it is already forgotten.")

    return [show_my_memory, explain_term, forget_memory]
