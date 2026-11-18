"""The memory layer over MCP: the server's tools and identity rules, and a create_agent agent (as AskFST's
text-to-SQL agent) that uses the memory only through the client (MemoryClient + MemoryMiddleware + memory_tools) over
real HTTP."""

import asyncio
import socket
import threading
import time
from pathlib import Path

import anyio
import pytest
import uvicorn
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from mcp.shared.memory import create_connected_server_and_client_session

from memory_demo import seeding
from memory_demo import service as service_mod
from memory_demo.api import FAILED, MemoryServiceError, memory_tools
from memory_demo.extractor import MemoryOp, MemoryOps, TermRelevance
from memory_demo.mcp_server import build_server, resolve_user
from memory_demo.middleware import MemoryMiddleware, UserContext
from memory_demo.org import Org
from memory_demo.remote import MemoryClient
from memory_demo.service import MemoryService
from memory_demo.store import MemoryStore, SqliteExecutor

ROOT = Path(__file__).resolve().parents[1]
EMAIL = "{}@shs-demo.example".format
LUKAS, PETRA, MEI, TOMAS = EMAIL("lukas.brandt"), EMAIL("petra.lind"), EMAIL("mei.chen"), EMAIL("tomas.vrba")
APP_SP = "4a1b2c3d-0000-1111-2222-333344445555"  # Tomáš's agent app (a service principal), trusted


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


# ---- who the user is ------------------------------------------------------------------------------------------
def test_identity_rules():
    assert resolve_user(None, LUKAS, set(), require_identity=False) == LUKAS  # local run / tests: as given
    with pytest.raises(ValueError, match="required"):
        resolve_user(None, None, set(), require_identity=False)
    assert resolve_user(PETRA, None, set()) == PETRA  # a person calling for themself
    assert resolve_user(PETRA, PETRA.upper(), set()) == PETRA
    with pytest.raises(ValueError, match="only use your own memory"):
        resolve_user(PETRA, LUKAS, set())  # not someone else's
    assert resolve_user(APP_SP, LUKAS, {APP_SP}) == LUKAS  # a trusted agent app calling for its user
    with pytest.raises(ValueError, match="required when a service"):
        resolve_user(APP_SP, None, {APP_SP})


# ---- the tools, in one process ----------------------------------------------------------------------------------
def test_tools(svc):
    server = build_server(svc, require_identity=False)

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as s:
            names = {t.name for t in (await s.list_tools()).tools}
            assert names == {"memory_context", "memory_learn", "memory_undo", "show_memory", "explain_term", "forget",
                             "use_meaning", "get_consent", "set_consent", "health"}

            async def call(tool, **args):
                r = await s.call_tool(tool, args)
                assert not r.isError, r.content[0].text
                return r.structuredContent

            ctx = await call("memory_context", question="memory errors on the noisy rack?", user_email=LUKAS)
            assert "rack R37" in ctx["context"] and {i["term"] for i in ctx["trace"]["items"]} == {
                "memory errors", "the noisy rack"}
            learned = await call("memory_learn", question="For me memory errors means only DDR errors",
                                 answer="16,718", user_email=TOMAS, trace=ctx["trace"])
            assert [w["op"] for w in learned["writes"]] == ["add"]
            petra = await call("explain_term", term="memory errors", user_email=PETRA)
            [item] = petra["items"]
            assert item["confidence"] == 0.67 and item["alternatives"][0]["scope"] == "colleagues"
            await call("memory_undo", write=learned["writes"][0], user_email=TOMAS)
            assert (await call("explain_term", term="memory errors", user_email=PETRA))["items"][0]["confidence"] == 1.0
            assert (await call("get_consent", user_email=MEI))["consent"] is None
            assert (await call("set_consent", remember=True, share=False, user_email=MEI))["consent"] == {
                "remember": True, "share": False}
            view = await call("show_memory", user_email=LUKAS)
            assert {r["term"] for r in view["department"]} == {"comms errors"}
            r = await s.call_tool("memory_context", {"question": "x", "user_email": "nobody@example.com"})
            assert r.isError and "not in the org directory" in r.content[0].text
            assert (await call("health"))["status"] == "healthy"

    anyio.run(run)


def test_explain_term_does_not_count_as_use(svc):
    server = build_server(svc, require_identity=False)
    before = len(svc.store.ex.run("SELECT * FROM memory_signals"))

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as s:
            await s.call_tool("explain_term", {"term": "link card problems", "user_email": PETRA})

    anyio.run(run)
    time.sleep(0.2)
    assert len(svc.store.ex.run("SELECT * FROM memory_signals")) == before  # the fading clock is not reset


# ---- over HTTP, from an agent that only has the client ---------------------------------------------------------
@pytest.fixture
def url(svc):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_server(svc, trusted={APP_SP}, require_identity=False).streamable_http_app(),
                                           host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(5)


SEEN: list = []


class Model(GenericFakeChatModel):
    """Records the messages of every model call; answers from a script."""

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        SEEN.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_an_agent_gets_memory_through_mcp(url):
    client = MemoryClient(url)
    replies = iter([AIMessage("", tool_calls=[{"name": "show_my_memory", "args": {}, "id": "t1"}]),
                    AIMessage("There were 33,662 memory errors on R37.")])
    agent = create_agent(Model(messages=replies), tools=memory_tools(client),
                         middleware=[MemoryMiddleware(client)], context_schema=UserContext)
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


def test_learning_through_mcp_and_async_agents(url):
    client = MemoryClient(url)
    agent = create_agent(Model(messages=iter([AIMessage("16,718.")])), tools=[],
                         middleware=[MemoryMiddleware(client)], context_schema=UserContext)
    out = asyncio.run(agent.ainvoke({"messages": [HumanMessage("For me memory errors means only DDR errors")]},
                                    context=UserContext(user_email=TOMAS)))
    assert [w["op"] for w in out["memory_writes"]] == ["add"] and out["memory_writes"][0]["shared_with"] == "only you"
    petra = client.call("explain_term", term="memory errors", user_email=PETRA)
    assert petra["items"][0]["confidence"] == 0.67  # Tomas's own meaning now counts for his team


def test_identity_over_http(url):
    as_petra = MemoryClient(url, headers={"x-forwarded-email": PETRA})
    assert as_petra.call("get_consent")["consent"] == {"remember": True, "share": True}  # her own, no user needed
    with pytest.raises(MemoryServiceError, match="only use your own memory"):
        as_petra.call("show_memory", user_email=LUKAS)
    as_app = MemoryClient(url, headers={"x-forwarded-email": APP_SP})
    assert as_app.call("show_memory", user_email=LUKAS)["personal"] == []  # the trusted agent app, for Lukas


def test_the_agent_answers_without_memory_when_the_service_is_down():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # nothing listens here
    client = MemoryClient(f"http://127.0.0.1:{port}/mcp", timeout=3)
    agent = create_agent(Model(messages=iter([AIMessage("42 events.")])), tools=[],
                         middleware=[MemoryMiddleware(client)], context_schema=UserContext)
    out = agent.invoke({"messages": [HumanMessage("memory errors?")]}, context=UserContext(user_email=LUKAS))
    assert out["messages"][-1].content == "42 events." and out["memory_writes"] == []
    assert "Memory was unavailable" in out["memory_trace"]["note"]


# ---- the client keeps one connection open and sends one request per call ---------------------------------------
import contextlib  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

import httpx  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

from memory_demo.remote import DatabricksAuth  # noqa: E402


class Watch:
    """ASGI wrapper that notes every HTTP request: its JSON-RPC method and the client's TCP port."""

    def __init__(self, app):
        self.app, self.methods, self.ports = app, [], []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        self.ports.append(scope["client"][1])
        chunks = []

        async def tee():
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                with contextlib.suppress(ValueError):
                    self.methods.append(json.loads(b"".join(chunks) or b"{}").get("method", scope["method"]))
            return message

        await self.app(scope, tee, send)


@contextlib.contextmanager
def serving(app):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(5)


def test_one_request_per_call_on_one_connection(svc):
    watch = Watch(build_server(svc, require_identity=False).streamable_http_app())
    with serving(watch) as url:
        client = MemoryClient(url)
        for _ in range(5):
            assert client.call("memory_context", question="memory errors?", user_email=LUKAS)["context"]
        with pytest.raises(MemoryServiceError, match="not in the org directory"):
            client.call("show_memory", user_email="nobody@example.com")  # a tool error is not a protocol error
        assert watch.methods == ["tools/call"] * 6  # no initialize, no notifications
        assert len(set(watch.ports)) == 1  # the same TCP connection every time
        t0 = time.perf_counter()
        for _ in range(10):
            client.call("health")
        assert (time.perf_counter() - t0) / 10 < 0.1  # it was about 0.25 s per call
        client.close()
        assert client.call("health")["status"] == "healthy"  # reopens on its own


def test_async_calls_from_different_event_loops_share_the_client(svc):
    watch = Watch(build_server(svc, require_identity=False).streamable_http_app())
    with serving(watch) as url:
        client = MemoryClient(url)
        for _ in range(3):  # a new loop each time, as separate asyncio.run calls make

            async def two():
                return await asyncio.gather(client.acall("health"), client.acall("get_consent", user_email=LUKAS))

            health, consent = asyncio.run(two())
            assert health["status"] == "healthy" and consent["consent"] == {"remember": True, "share": True}
        assert set(watch.methods) == {"tools/call"} and len(set(watch.ports)) <= 2  # two at once: at most two


def test_a_server_that_needs_a_session_is_reported_not_worked_around():
    """Our server keeps no session. Before 18 Nov the client switched for good to a full MCP handshake after any
    failed request, which needs the `mcp` package the light client does not install: one bad answer turned memory
    off until a restart."""
    stateful = FastMCP("needs-session", stateless_http=False, json_response=True,
                       transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @stateful.tool()
    def health() -> dict:
        """Up?"""
        return {"status": "healthy"}

    watch = Watch(stateful.streamable_http_app())
    with serving(watch) as url:
        client = MemoryClient(url)
        for _ in range(2):
            with pytest.raises(MemoryServiceError):
                client.call("health")
        assert "initialize" not in watch.methods  # no handshake, no switch: each call is one request


def reply(result=None, error=None, status=200, sse=False):
    body = {"jsonrpc": "2.0", "id": 1, **({"error": error} if error else {"result": result})}
    if sse:
        return httpx.Response(status, text=f"event: message\ndata: {json.dumps(body)}\n\n",
                              headers={"content-type": "text/event-stream"})
    return httpx.Response(status, json=body)


def test_the_databricks_token_is_asked_for_on_every_request():
    class Config:
        n = 0

        def authenticate(self):
            self.n += 1
            return {"Authorization": f"Bearer token-{self.n}"}

    ws = type("WS", (), {"config": Config()})()
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return reply({"structuredContent": {"ok": True}, "content": [], "isError": False}, sse=len(seen) == 2)

    client = MemoryClient("https://mcp-shs-memory-1.azure.databricksapps.com/mcp", workspace_client=ws,
                          transport=httpx.MockTransport(handler))
    assert client.call("health") == {"ok": True} and client.call("health") == {"ok": True}  # JSON, then event stream
    assert seen == ["Bearer token-1", "Bearer token-2"]  # refreshed by the SDK, never cached here
    assert isinstance(client._client().auth, DatabricksAuth)


def test_a_refused_caller_and_a_dropped_connection():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return reply({"structuredContent": {"n": len(calls)}, "content": [], "isError": False})

    client = MemoryClient("http://127.0.0.1:9/mcp", transport=httpx.MockTransport(handler))
    assert client.call("health") == {"n": 2}  # the stale connection is retried once
    denied = MemoryClient("http://127.0.0.1:9/mcp", transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(MemoryServiceError, match="HTTP 403"):
        denied.call("health")


def test_a_brief_outage_is_retried_once_and_changes_nothing_after(monkeypatch):
    """A 503 while the memory app redeploys: one retry; if it is still down, a clear error; and the next call,
    once the app is back, goes through as usual."""
    import memory_demo.remote as remote_mod

    monkeypatch.setattr(remote_mod, "RETRY_WAIT_S", 0)
    answers = [503, 200, 503, 503, 500, 200]
    seen = []

    def handler(request):
        status = answers[len(seen)]
        seen.append(status)
        if status != 200:
            return httpx.Response(status, text="<html>upstream unavailable</html>")
        return reply({"structuredContent": {"ok": True}, "content": [], "isError": False})

    client = MemoryClient("http://127.0.0.1:9/mcp", transport=httpx.MockTransport(handler))
    assert client.call("health") == {"ok": True}  # 503, then 200
    with pytest.raises(MemoryServiceError, match="HTTP 503"):
        client.call("health")  # 503 twice
    with pytest.raises(MemoryServiceError, match="HTTP 500"):
        client.call("health")  # a 500 is not retried
    assert client.call("health") == {"ok": True} and seen == answers


def test_unreachable_or_garbled_answers_are_memory_errors():
    down = MemoryClient("http://127.0.0.1:9/mcp", transport=httpx.MockTransport(
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
    with pytest.raises(MemoryServiceError, match="could not be reached"):
        down.call("health")
    html = MemoryClient("http://127.0.0.1:9/mcp", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text="<html>sign in</html>")))
    with pytest.raises(MemoryServiceError, match="not an MCP reply"):
        html.call("health")


# ---- AskFST's text-to-SQL agent: its input is the main agent's task ----------------------------------------------
def test_a_sql_agent_learns_from_the_user_s_words_and_notes_memory_in_the_answer(url):
    """The main agent hands the SQL agent a task; the user's own words come along in the context. The lookup uses the
    task, learning uses the user's words, and the answer says which memory it used and what it remembered."""
    client = MemoryClient(url)
    agent = create_agent(Model(messages=iter([AIMessage("16,718 memory errors on R37.")])), tools=[],
                         middleware=[MemoryMiddleware(client, note_in_answer=True)], context_schema=UserContext)
    task = "Count memory errors on the noisy rack in December 2005."
    out = agent.invoke({"messages": [HumanMessage(task)]},
                       context=UserContext(user_email=TOMAS, user_message="For me memory errors means only DDR errors"))
    assert {i["term"] for i in out["memory_trace"]["items"]} == {"memory errors", "the noisy rack"}  # from the task
    assert [w["op"] for w in out["memory_writes"]] == ["add"]  # learned from the user's own words
    answer = out["messages"][-1].content
    assert answer.startswith("16,718 memory errors on R37.") and "**Memory used:** “memory errors” =" in answer
    assert "(your team)" in answer and "**Remembered:** “memory errors” = only correctable DDR errors (only you)" in answer
    assert len([m for m in out["messages"] if m.type == "ai"]) == 1  # the answer was replaced, not repeated


def test_without_the_user_s_words_the_sql_agent_learns_from_its_task(url):
    client = MemoryClient(url)
    agent = create_agent(Model(messages=iter([AIMessage("16,718.")])), tools=[],
                         middleware=[MemoryMiddleware(client)], context_schema=UserContext)
    out = agent.invoke({"messages": [HumanMessage("For me memory errors means only DDR errors. Count them.")]},
                       context=UserContext(user_email=TOMAS))
    assert [w["op"] for w in out["memory_writes"]] == ["add"]
    assert out["messages"][-1].content == "16,718."  # no note unless asked for


def test_errors_over_mcp_do_not_leak_internals(svc):
    def broken(*args, **kwargs):
        raise RuntimeError("table askar.shs_memory.memory_items not found on host adb-123")

    svc.view = broken
    with serving(build_server(svc, require_identity=False).streamable_http_app()) as url:
        with pytest.raises(MemoryServiceError) as e:
            MemoryClient(url).call("show_memory", user_email=LUKAS)
    assert FAILED in str(e.value) and "askar" not in str(e.value) and "adb-123" not in str(e.value)


BLOCK_SERVER_PACKAGES = """
import sys

SERVER = {"mem0", "databricks_langchain", "sqlalchemy", "sqlglot", "mcp", "uvicorn", "langchain_community",
          "langchain_openai", "streamlit"}


class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in SERVER or name.startswith("databricks.sql"):
            raise ImportError("blocked: " + name)


sys.meta_path.insert(0, Block())
from memory_demo.remote import MemoryClient
from memory_demo.api import memory_tools
from memory_demo.middleware import MemoryMiddleware
print("ok")
"""


def test_the_client_needs_none_of_the_server_s_packages():
    """What AskFST's agent imports must not pull in Mem0, the SQL connectors or the MCP server."""
    out = subprocess.run([sys.executable, "-c", BLOCK_SERVER_PACKAGES], cwd=ROOT, capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr
