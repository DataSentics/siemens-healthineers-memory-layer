# Plugging the shared memory into AskFST

The memory layer runs **inside AskFST's agent** (decided 18 Nov 2026): AskFST installs this package, and an agent
built with LangChain `create_agent` (like AskFST's Data Planner) adds one middleware and three tools. There is no
separate memory service: the memory reads and writes its tables from AskFST's own process.

```python
from memory_demo import config
from memory_demo.api import LocalMemory, memory_tools
from memory_demo.middleware import MemoryMiddleware

memory = LocalMemory(config.memory_service())    # once per app; settings from environment variables (below)
user_of = lambda runtime: runtime.context.user_email                # the signed-in user, from AskFST's own sign-in

agent = create_agent(
    model,
    tools=[*planner_tools, *memory_tools(memory, user_of=user_of)],
    middleware=[*planner_middleware, MemoryMiddleware(memory, user_of=user_of, sql_from=planner_sql)],
    context_schema=PlannerContext,
)
```

Install: `pip install shs_memory-0.1.0-py3-none-any.whl` (build it with `pip wheel . --no-deps`). Works with
`invoke`/`stream` and `ainvoke`/`astream`.

## What happens in a turn

1. **Before the first model call** the middleware calls `memory_context(question, user)`. The memory block it returns
   (what terms mean for this user, their preferences) is appended to the system prompt of every model call in the
   turn. A sub-agent that should see it too gets `MemoryContextMiddleware()` and the parent's `memory_context` in its
   input state (see `build_supervisor` in `memory_demo/agent.py`).
2. **After the answer** it calls `memory_learn(question, answer, sql, ...)`. The memory decides what to keep (one model
   call, see README "What gets remembered") and returns the writes.
3. The agent state then holds, for the UI:
   - `memory_trace`: the items applied, with level (yours / your team / your department / official), confidence,
     relevance and the other meanings in use (for a drop-down under the answer, FR8/FR19);
   - `memory_writes`: what was remembered, to show above the answer with an Undo button that calls
     `memory.call("memory_undo", write=w, user_email=...)` (FR13).

If memory fails (the warehouse is stopped, the model endpoint times out), the agent answers without memory and
`memory_trace["note"]` says so; the three tools answer "Memory is not available" instead of stopping the agent. Memory
never stops an answer.

**A hand-built LangGraph graph.** The middleware needs `create_agent`. In a graph built by hand (`StateGraph`), the same
two calls go into nodes: `memory.call("memory_context", question=..., user_email=...)` before the model node (append
`context` to the system prompt), and `memory.call("memory_learn", question=..., answer=..., user_email=..., trace=...,
sql=[...])` after the answer. We write these once we have seen AskFST's graph.

**What it costs per turn.** The lookup is two table reads (0.35–0.45 s on the warehouse in the demo). Learning is one
model call after the answer, 1–4 s (up to 9 s seen). Create one `LocalMemory` for the app, not one per request: it
keeps the warehouse connections open.

## What AskFST provides

| What | How |
| --- | --- |
| The user | `user_of(runtime)`: the signed-in user's email, from AskFST's own sign-in (on Databricks Apps, the `X-Forwarded-Email` header of the user's request), never from the model. It must match an email in the org directory (people → team → department); case and spaces do not matter. |
| The SQL that ran | `sql_from(messages)`: the SQL statements of this turn, last one final. Default: `sql_db_query` tool calls and tool messages whose artifact is `{"sql": [...]}`. Saved definitions copy their filter from it, so it was checked against the data. |
| Its tables | A text file describing AskFST's tables and columns, set as `DATA_DESCRIPTION_FILE` (example: `deploy/data_description.example.txt`). The extractor writes each term's SQL filter over these tables. |
| A UI (optional) | Show `memory_writes` with Undo, and `memory_trace` items in a drop-down. The demo app (`app.py`) shows one way. |

## Settings and access (in-process)

AskFST's app sets these environment variables:

| Variable | Value |
| --- | --- |
| `BACKEND` | `databricks` |
| `DATABRICKS_WAREHOUSE_ID` | the SQL warehouse for the memory tables |
| `MEMORY_CATALOG`, `MEMORY_SCHEMA` | where the memory tables live: AskFST's catalog, schema `shs_memory` |
| `ORG_SEED` | **`0`**. The org directory is filled by the HR / Entra sync; with any other value an empty directory would be filled with the demo's made-up org chart |
| `MEMORY_DB_CONNECTIONS` | `4` (warehouse connections kept open) |
| `LLM_PROVIDER`, `DATABRICKS_LLM_ENDPOINT` | `databricks`, the model for deciding what to remember (`databricks-claude-sonnet-5-5` in the demo) |
| `PERSONAL_MEMORY` | `store` |
| `DATA_DESCRIPTION_FILE` | AskFST's table description (above) |

AskFST's service principal needs: `USE CATALOG`, `USE SCHEMA`, `SELECT` and `MODIFY` on the memory tables
(`memory_items`, `memory_signals`, `casebook`) and `SELECT` on `org_people`, Can use on the SQL warehouse, and Can query
on the model endpoint. `scripts/setup_databricks.py --only-memory --app-sp <id>` creates the tables and grants them (it
also grants `MODIFY` on `org_people`, which only the demo's Admin tab needs).

**One process.** Two people teaching the same new term at the same moment are kept from creating two team meanings by
locks inside the process. AskFST is expected to run as one process (to be confirmed); with several workers or
replicas, that case needs a guard in the database.

## Access and identity

- The user comes from AskFST's sign-in through `user_of`, never from the model: `memory_tools` take the user from the
  runtime, so a prompt cannot make the model read someone else's memory.
- Ids sent by a caller are checked, not trusted: `memory_undo` only reverses a change that is the user's own (or
  their team's / department's) and still in place, and `use_meaning` only takes a meaning the user is offered.
- What a user teaches reaches colleagues' prompts, so it is stored as one line of at most 400 characters, and its
  SQL filter only if it is one plain condition (no second statement, subquery or comment). AskFST's SQL worker should
  still run read-only: memory text is user input.
- Errors returned to the caller never include table names, hosts or tokens; details go to the log.

## The calls

`memory.call(name, user_email=..., ...)`, or `await memory.acall(...)` in async code:

| Call | Called by | Arguments | Returns |
| --- | --- | --- | --- |
| `memory_context` | code, every turn | `question`, `user_email`, `fuzzy`, `exclude` | `context` (prompt block), `trace` |
| `memory_learn` | code, every turn | `question`, `answer`, `user_email`, `previous_answer`, `trace`, `sql`, `earlier_questions` | `writes`, `relevance` |
| `memory_undo` | code (Undo button) | `write`, `user_email` | `undone` |
| `show_memory` | model (`show_my_memory`) | `user_email` | the user's items, team, department, official, other departments |
| `explain_term` | model (`explain_term`) | `term`, `user_email` | what the term means here and the other meanings in use |
| `forget` | model (`forget_memory`) | `item_id`, `user_email` | the withdrawn item |
| `use_meaning` | code (a "Use this" button) | `rejected_id`, `chosen_id`, `user_email` | writes |
| `get_consent`, `set_consent` | code (settings) | `remember`, `share`, `user_email` | the consent |

AskFST and the demo app can point at the same memory schema; then what AskFST teaches shows up in the demo's Memory tab
and the other way round.

## Decisions

| # | Question | Decision |
| --- | --- | --- |
| 1 | Own service or code inside AskFST | **Inside AskFST** (18 Nov; AskFST has its own LangGraph agent). The MCP server built on 17 Nov was removed; it is in commit `c70926a` if ever needed. |
| 2 | How the memory knows the user | **From AskFST's sign-in** through `user_of`; it must tell users apart. |
| 3 | What the model may call | **Lookup and learning run from code every turn.** The model gets three tools (show, explain, forget). Undo, "use this" and consent are for buttons. |
| 4 | Describing AskFST's tables | **Open, see below.** Both ways work today: filters over a described table, or definitions in words only. |
| 5 | Where memory lives | **In AskFST's catalog, in its own schema** (`MEMORY_CATALOG` / `MEMORY_SCHEMA`, default schema name `shs_memory`). Never the demo's tables. Only reads the org directory there. |
| 6 | Learning after the answer | **Stays right after the answer** (2–8 s) until we know whether AskFST streams; see below. |
| 7 | "quiet rack" finds "the quiet rack" | **Yes, when at least two words follow the article**; "the system" does not fire on "system". |
| 8 | Hooking into AskFST's agent | **After seeing its code**: the middleware as is (`create_agent`) or two nodes (a hand-built graph). |

## Questions for Tomáš

1. **The agent:** is it built with `create_agent` or as a hand-built LangGraph `StateGraph`? That decides between the
   middleware as is and two nodes.
2. **Processes:** does AskFST run as one process (no several workers or replicas)? We expect one; see "One process".
3. **Sign-in:** where in AskFST's code the signed-in user's email is available per request, and how users sign in.
4. **Catalog:** the name of the catalog for the memory schema, and who grants access (USE CATALOG, USE SCHEMA,
   SELECT, and MODIFY on the four tables; `scripts/setup_databricks.py --only-memory` runs it all).
5. **People:** where the list of people, teams and departments comes from (HR or Entra ID), and who writes it into
   `org_people` (email, name, department, team, manager, start date).
6. **SQL filters:** does the SQL worker take a ready-made filter for a term, or write its own SQL from the meaning in
   words? If it takes filters: do AskFST's tables have column comments in Unity Catalog?
7. **Streaming:** does the UI show the answer while it is written? If not, learning should move to the background.
8. **UI:** is there room for the "Remembered" line with Undo, the memory drop-down and the consent choice?
9. **Official glossary:** does AskFST have one (terms with their meaning, and a filter where there is one)? A new
   memory schema starts without official meanings; the demo's (`seed/gold_glossary.json`) is about the BGL log only.
