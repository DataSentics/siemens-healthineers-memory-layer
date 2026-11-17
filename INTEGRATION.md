# Plugging the shared memory into AskFST

The memory layer runs as its own service: an MCP server on Databricks Apps (`mcp-shs-memory`). An agent built with
LangChain `create_agent` (like AskFST's Data Planner) adds one middleware and three tools, and needs nothing else from
this project: no memory tables, no model settings.

```python
from memory_demo.remote import MemoryClient, RemoteMemoryMiddleware, memory_tools

memory = MemoryClient("https://<mcp-shs-memory app URL>/mcp")      # OAuth from the Databricks SDK config
user_of = lambda runtime: runtime.context.user_email                # however AskFST knows the signed-in user

agent = create_agent(
    model,
    tools=[*planner_tools, *memory_tools(memory, user_of=user_of)],
    middleware=[*planner_middleware, RemoteMemoryMiddleware(memory, user_of=user_of, sql_from=planner_sql)],
    context_schema=PlannerContext,
)
```

Install the client: `pip install shs_memory-0.1.0-py3-none-any.whl` (it brings `langchain`, `mcp`, `httpx` and
`databricks-sdk`; build it with `pip wheel . --no-deps`). Works with `invoke`/`stream` and `ainvoke`/`astream`.

## What happens in a turn

1. **Before the first model call** the middleware calls `memory_context(question, user)`. The memory block it returns
   (what terms mean for this user, their preferences) is appended to the system prompt of every model call in the
   turn. A sub-agent that should see it too gets `MemoryContextMiddleware()` and the parent's `memory_context` in its
   input state (see `build_supervisor` in `memory_demo/agent.py`).
2. **After the answer** it calls `memory_learn(question, answer, sql, ...)`. The memory service decides what to keep
   (one model call, see README "What gets remembered") and returns the writes.
3. The agent state then holds, for the UI:
   - `memory_trace`: the items applied, with level (yours / your team / your department / official), confidence,
     relevance and the other meanings in use (for a drop-down under the answer, FR8/FR19);
   - `memory_writes`: what was remembered, to show above the answer with an Undo button that calls
     `memory.call("memory_undo", write=w, user_email=...)` (FR13).

If the memory service cannot be reached, the agent answers without memory and `memory_trace["note"]` says so. Memory
never stops an answer.

**What it costs per turn.** `MemoryClient` keeps one connection open and sends one request per call (the server keeps
no session, so no handshake is needed; a server that does want one gets the full MCP handshake automatically). Create
one `MemoryClient` for the app, not one per request. Measured against a local server: the first call 230 ms (it opens
the connection), every later call 6–10 ms plus the server's own work. The lookup is two table reads on the server
(0.35–0.45 s on the warehouse in the demo). Learning is one model call after the answer, 1–4 s (up to 9 s seen).

## What AskFST provides

| What | How |
| --- | --- |
| The user | `user_of(runtime)`: the signed-in user's email. It must be in the org directory (people → team → department). |
| The SQL that ran | `sql_from(messages)`: the SQL statements of this turn, last one final. Default: `sql_db_query` tool calls and tool messages whose artifact is `{"sql": [...]}`. Saved definitions copy their filter from it, so it was checked against the data. |
| Its tables | A text file describing AskFST's tables and columns, set as `DATA_DESCRIPTION_FILE` on the memory server (example: `deploy/mcp/data_description.example.txt`). The extractor writes each term's SQL filter over these tables. |
| A UI (optional) | Show `memory_writes` with Undo, and `memory_trace` items in a drop-down. The demo app (`app.py`) shows one way. |

## Access and identity

- The memory app accepts OAuth only (Databricks Apps). In a Databricks App, `MemoryClient` uses the app's own service
  principal automatically; locally it uses your CLI profile (`DATABRICKS_CONFIG_PROFILE`).
- AskFST's service principal needs **CAN USE** on `mcp-shs-memory`.
- The server sees who calls (`X-Forwarded-Email`, set by Databricks after OAuth). AskFST's service principal is listed
  in the server's `MEMORY_TRUSTED_CALLERS`, so it may act for its users and names the user in each call. Anyone else
  (a person connecting from Claude, the Playground or an IDE) only reaches their own memory.
- The model never names a user: `memory_tools` take the user from the runtime, so a prompt cannot make the model read
  someone else's memory.
- A request without an identity is refused. Only a server started for local use (`MEMORY_ALLOW_ANONYMOUS=1`)
  believes `user_email` as given. Not yet checked on Databricks: which header carries a service principal's id.
- Ids sent by a client are checked, not trusted: `memory_undo` only reverses a change that is the user's own (or
  their team's / department's) and still in place, and `use_meaning` only takes a meaning the user is offered.
- What a user teaches reaches colleagues' prompts, so it is stored as one line of at most 400 characters, and its
  SQL filter only if it is one plain condition (no second statement, subquery or comment). AskFST's SQL worker should
  still run read-only: memory text is user input.
- Errors returned to the caller never include table names, hosts or tokens; details go to the server log.

## The tools

| Tool | Called by | Arguments | Returns |
| --- | --- | --- | --- |
| `memory_context` | code, every turn | `question`, `user_email`, `fuzzy`, `exclude` | `context` (prompt block), `trace` |
| `memory_learn` | code, every turn | `question`, `answer`, `user_email`, `previous_answer`, `trace`, `sql`, `earlier_questions` | `writes`, `relevance` |
| `memory_undo` | code (Undo button) | `write`, `user_email` | `undone` |
| `show_memory` | model (`show_my_memory`) | `user_email` | the user's items, team, department, official, other departments |
| `explain_term` | model (`explain_term`) | `term`, `user_email` | what the term means here and the other meanings in use |
| `forget` | model (`forget_memory`) | `item_id`, `user_email` | the withdrawn item |
| `use_meaning` | code (a "Use this" button) | `rejected_id`, `chosen_id`, `user_email` | writes |
| `get_consent`, `set_consent` | code (settings) | `remember`, `share`, `user_email` | the consent |
| `health` | anyone | | status, dataset date, people in the directory |

## Running the memory server

- **Databricks:** an app named `mcp-shs-memory` from this folder with `deploy/mcp/app.yaml` as its `app.yaml`. It needs
  the SQL warehouse (resource key `sql-warehouse`, Can use) and the model endpoint (Can query), and its service
  principal needs `USE CATALOG`, `USE SCHEMA`, `SELECT` and `MODIFY` on the memory tables and `org_people`
  (`scripts/setup_databricks.py --app-sp <id>`). The MCP URL is the app URL plus `/mcp`.
- **Locally:** `python -m memory_demo.mcp_server` serves `http://127.0.0.1:8000/mcp` (SQLite, settings from `.env`).

It shares its tables with the demo app, so what AskFST teaches shows up in the demo's Memory tab and the other way
round.

## In-process instead

If AskFST would rather reach the memory tables itself (one network hop less), it can import the layer:
`MemoryMiddleware(config.memory_service(), user_of=..., sql_from=...)` from `memory_demo.middleware`. Its app then
needs the table grants and a model endpoint for the extractor, and it runs this project's code inside AskFST.

## Decisions (17 Nov 2026)

| # | Question | Decision |
| --- | --- | --- |
| 1 | Own service or code inside AskFST | **Own service** (this MCP server). The in-process middleware stays as a fallback if the extra call proves too slow. |
| 2 | How the server knows the user | **AskFST's app is trusted to name the user** (its service principal in `MEMORY_TRUSTED_CALLERS`); everyone else only reaches their own memory. Target later: pass the user's own sign-in through. |
| 3 | What the model may call | **Lookup and learning run from code every turn.** The model gets three tools (show, explain, forget). Undo, "use this" and consent are for buttons. |
| 4 | Describing AskFST's tables | **Open, see below.** Both ways work today: filters over a described table, or definitions in words only. |
| 5 | Where memory lives | **In AskFST's catalog, in its own schema** (`MEMORY_CATALOG` / `MEMORY_SCHEMA`, default schema name `shs_memory`). Never the demo's tables. The server only reads the org directory there. |
| 6 | Learning after the answer | **Stays right after the answer** (2–8 s) until we know whether AskFST streams; see below. |
| 7 | "quiet rack" finds "the quiet rack" | **Yes, when at least two words follow the article**; "the system" does not fire on "system". |

## Questions for Tomáš

1. **Catalog:** the name of the catalog for the memory schema, and who grants the memory app access (USE CATALOG,
   USE SCHEMA, SELECT, and MODIFY on the four tables; `scripts/setup_databricks.py --only-memory` runs it all).
2. **People:** where the list of people, teams and departments comes from (HR or Entra ID), and who writes it into
   `org_people` (email, name, department, team, manager, start date).
3. **SQL filters:** does the SQL worker take a ready-made filter for a term, or write its own SQL from the meaning in
   words? If it takes filters: do AskFST's tables have column comments in Unity Catalog?
4. **Streaming:** does the UI show the answer while it is written? If not, learning should move to the background.
5. **Sign-in:** how users sign in to AskFST, and the application id of its service principal. Calling the `health`
   tool as the app shows the ids Databricks reports for it.
6. **UI:** is there room for the "Remembered" line with Undo, the memory drop-down and the consent choice?
