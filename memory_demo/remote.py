"""The client side of the memory MCP server (memory_demo.mcp_server): what AskFST's text-to-SQL agent imports. It needs
the server's URL and nothing else from this project (no tables, no model settings, no Mem0).

    from memory_demo.remote import MemoryClient
    from memory_demo.api import memory_tools
    from memory_demo.middleware import MemoryMiddleware

    memory = MemoryClient("https://mcp-shs-memory-<id>.azure.databricksapps.com/mcp")  # OAuth from the Databricks SDK
    sql_agent = create_agent(model, tools=[*sql_tools, *memory_tools(memory)],
                             middleware=[MemoryMiddleware(memory, note_in_answer=True)])

`MemoryClient.call(name, user_email=..., **args)` has the same shape as `api.LocalMemory.call`, so the middleware and
the tools work with either; it raises `api.MemoryServiceError` when the server refuses or fails a call.
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

from .api import MemoryServiceError

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


class MemoryClient:
    """Calls the memory MCP server. On Databricks it signs in with OAuth from the Databricks SDK config (the app's
    service principal, a CLI profile or env vars; Databricks Apps accept OAuth tokens only). Local URLs need no auth.

    One connection is kept open and each call is a single request. The memory server keeps no session between
    requests (stateless streamable HTTP), so a tool call needs no handshake first; setting up a new client and
    session for every call cost about 250 ms, almost all of it creating the HTTP client. If a server does insist on
    a session, the client notices once and uses the full MCP handshake from then on (that needs the `mcp` package).

    Create one per app and share it: it is safe across threads. `headers`: extra request headers (tests).
    `transport`: an httpx transport (tests)."""

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
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
        except ImportError:
            raise MemoryServiceError("the memory server needs an MCP session: install the `mcp` package") from None

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
