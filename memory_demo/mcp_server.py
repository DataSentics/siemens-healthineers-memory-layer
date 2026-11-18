"""The memory layer as an MCP server (streamable HTTP at /mcp), its own Databricks App. AskFST's text-to-SQL agent
uses it through `memory_demo.remote` and never installs the memory's own dependencies (Mem0, the SQL connectors).

Two kinds of tools:
- For the agent's code, every turn (not for the model to choose): `memory_context` before the model call (the memory
  block for the system prompt, and the trace), `memory_learn` after the answer (what to remember), and `memory_undo`.
  `MemoryMiddleware` with a `remote.MemoryClient` calls them.
- For the model, when the user asks: `show_memory`, `explain_term`, `forget`, `use_meaning`, `get_consent`,
  `set_consent`. `api.memory_tools` wraps them so the model never chooses whose memory it reads.

Who the user is: on Databricks Apps every request carries the caller's identity (X-Forwarded-Email and friends, set
by the platform after OAuth). A person calling for themself only reaches their own memory. A trusted service (an agent
app's service principal, listed in MEMORY_TRUSTED_CALLERS, comma-separated) calls for its users and names the user in
`user_email`. A request without those headers is refused, unless the server was started for local use
(MEMORY_ALLOW_ANONYMOUS=1, or require_identity=False in tests): then `user_email` is taken as given.

    python -m memory_demo.mcp_server      # 0.0.0.0:$DATABRICKS_APP_PORT (default 8000), path /mcp
"""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, Any

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import models
from .api import LocalMemory

if TYPE_CHECKING:
    from .service import MemoryService

IDENTITY_HEADERS = ("x-forwarded-email", "x-forwarded-preferred-username", "x-forwarded-user")

INSTRUCTIONS = """Shared memory of a data assistant: what terms mean for this user, their team, their department and the
official glossary, plus the user's own preferences. Call memory_context before answering and memory_learn after
answering (from code, every turn). Call show_memory, explain_term or forget when the user asks about their memory."""


def caller_of(ctx: Context) -> tuple[str | None, set[str]]:
    """The authenticated caller, as Databricks Apps reports it after OAuth: (email or other main id, every id the
    platform gave). (None, empty) when there are no identity headers."""
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        return None, set()
    ids = [v.strip().lower() for v in (request.headers.get(h) for h in IDENTITY_HEADERS) if v and v.strip()]
    return (ids[0] if ids else None), set(ids)


def resolve_user(caller: str | None, user_email: str | None, trusted: set[str], require_identity: bool = True,
                 caller_ids: set[str] | None = None) -> str:
    """Whose memory this call may use. `caller_ids`: every id the platform reported for the caller (a service
    principal may show up under its application id in one header only)."""
    asked = (user_email or "").strip().lower() or None
    ids = {i for i in (caller, *(caller_ids or ())) if i}
    if not ids:
        if require_identity:  # never "whoever the caller says they are"
            raise ValueError("No caller identity: this server only answers behind Databricks Apps sign-in.")
        if not asked:
            raise ValueError("user_email is required")
        return asked
    if ids & trusted:  # an agent app calling for its users
        if not asked:
            raise ValueError("user_email is required when a service calls for its users")
        return asked
    if asked and asked != caller:
        raise ValueError("You can only use your own memory.")
    return caller


class ClockSync:
    """Demo clock (DEMO_TODAY set): follow the dataset date of the shared memory, read at most once a minute, so the
    server agrees with the demo app. Without DEMO_TODAY the real date is used."""

    def __init__(self, service: MemoryService, start: str | None, every: float = 60):
        self.service, self.start, self.every, self.at = service, start, every, 0.0
        self.lock = threading.Lock()

    def __call__(self) -> None:
        if not self.start:
            return
        with self.lock:
            if time.monotonic() - self.at < self.every:
                return
            day = self.service.store.latest_day()
            models.set_today(max(day, self.start) if day else self.start)
            self.at = time.monotonic()


def build_server(service: MemoryService, trusted: set[str] = frozenset(), demo_today: str | None = None,
                 host: str = "127.0.0.1", port: int = 8000, require_identity: bool = True) -> FastMCP:
    """`require_identity=False` only for local runs and tests: `user_email` is then believed as given."""
    trusted = {t.strip().lower() for t in trusted if t.strip()}
    clock = ClockSync(service, demo_today)
    memory = LocalMemory(service)  # the calls themselves, the org-directory check and safe error messages
    server = FastMCP(
        "shs-memory", instructions=INSTRUCTIONS, host=host, port=port, stateless_http=True, json_response=True,
        # Databricks Apps puts its own proxy (and OAuth) in front; the Host header is the app's domain.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def run(ctx: Context, user_email: str | None, name: str, **args) -> dict[str, Any]:
        """Resolve the user, then run the (blocking) call off the event loop. A refusal or failure reaches the caller
        as a tool error with a message that never names tables, hosts or tokens (see LocalMemory)."""
        caller, ids = caller_of(ctx)
        email = resolve_user(caller, user_email, trusted, require_identity, ids)

        def call():
            clock()
            return memory.call(name, user_email=email, **args)

        return await anyio.to_thread.run_sync(call)

    # ---- for the agent's code, every turn -------------------------------------------------------------------
    @server.tool()
    async def memory_context(ctx: Context, question: str, user_email: str | None = None, fuzzy: bool = True,
                             exclude: list[str] | None = None) -> dict[str, Any]:
        """Before answering: the memory section to append to the system prompt for this question, and the trace of
        what was applied. `exclude`: item ids the user rejected for this answer. `fuzzy`: also find terms by meaning
        (Mem0) and spelling; on by default."""
        return await run(ctx, user_email, "memory_context", question=question, fuzzy=fuzzy, exclude=exclude)

    @server.tool()
    async def memory_learn(ctx: Context, question: str, answer: str, user_email: str | None = None,
                           previous_answer: str | None = None, trace: dict[str, Any] | None = None,
                           sql: list[str] | None = None, earlier_questions: list[str] | None = None) -> dict[str, Any]:
        """After answering: decide what to remember from this turn and save it (nothing without the user's consent).
        `question`: the user's words. `trace`: what memory_context returned this turn. `sql`: the SQL that ran, last
        one final (optional; it helps word a definition exactly). Returns the writes and how relevant each applied
        term was."""
        return await run(ctx, user_email, "memory_learn", question=question, answer=answer,
                         previous_answer=previous_answer, trace=trace, sql=sql, earlier_questions=earlier_questions)

    @server.tool()
    async def memory_undo(ctx: Context, write: dict[str, Any], user_email: str | None = None) -> dict[str, Any]:
        """Undo one write that memory_learn (or use_meaning) returned."""
        return await run(ctx, user_email, "memory_undo", write=write)

    # ---- for the model, when the user asks ---------------------------------------------------------------------
    @server.tool()
    async def show_memory(ctx: Context, user_email: str | None = None) -> dict[str, Any]:
        """What the assistant remembers for the user: their own items, their team's and department's shared meanings
        (without names), the official glossary and other departments' meanings."""
        return await run(ctx, user_email, "show_memory")

    @server.tool()
    async def explain_term(ctx: Context, term: str, user_email: str | None = None) -> dict[str, Any]:
        """What a term means for the user and which other meanings are in use (team, department, official, other
        departments, colleagues without names). Does not count as using the term."""
        return await run(ctx, user_email, "explain_term", term=term)

    @server.tool()
    async def forget(ctx: Context, item_id: str, user_email: str | None = None) -> dict[str, Any]:
        """Forget one of the user's own items (from show_memory). Shared meanings cannot be forgotten this way; the
        disagreement is kept as a signal instead."""
        return await run(ctx, user_email, "forget", item_id=item_id)

    @server.tool()
    async def use_meaning(ctx: Context, rejected_id: str, chosen_id: str, user_email: str | None = None
                          ) -> dict[str, Any]:
        """The user picks another meaning in use (an alternative from memory_context or explain_term) instead of the
        one applied. It becomes theirs and counts with the colleagues who use it. Answer again afterwards."""
        return await run(ctx, user_email, "use_meaning", rejected_id=rejected_id, chosen_id=chosen_id)

    @server.tool()
    async def get_consent(ctx: Context, user_email: str | None = None) -> dict[str, Any]:
        """The user's memory consent: {"remember": bool, "share": bool}, or null when they were never asked (then
        nothing is remembered)."""
        return await run(ctx, user_email, "get_consent")

    @server.tool()
    async def set_consent(ctx: Context, remember: bool, share: bool, user_email: str | None = None
                          ) -> dict[str, Any]:
        """Turn remembering on or off, and sharing the user's own definitions with their team (without their name)."""
        return await run(ctx, user_email, "set_consent", remember=remember, share=share)

    @server.tool()
    async def health(ctx: Context) -> dict[str, Any]:
        """Is the memory service up: the date it works with, people in the org directory, and who the platform says
        is calling (the caller's own ids: what to put in MEMORY_TRUSTED_CALLERS for an agent app)."""
        caller, ids = caller_of(ctx)

        def fn():
            clock()
            return {"status": "healthy", "today": models.today(), "people": len(service.org.people()),
                    "caller": caller, "caller_ids": sorted(ids), "trusted": bool(ids & trusted)}
        return await anyio.to_thread.run_sync(fn)

    return server


def main() -> None:
    from . import config

    service = config.memory_service()
    trusted = set(os.getenv("MEMORY_TRUSTED_CALLERS", "").split(","))
    server = build_server(service, trusted, demo_today=os.getenv("DEMO_TODAY"), host="0.0.0.0",
                          port=int(os.getenv("DATABRICKS_APP_PORT", "8000")),
                          require_identity=os.getenv("MEMORY_ALLOW_ANONYMOUS") != "1")
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
