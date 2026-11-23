# Plugging the shared memory into AskFST

The memory runs as **its own MCP service** (a Databricks App, e.g. `mcp-shs-memory`), and **AskFST's text-to-SQL
agent** uses it through a light client: a middleware that looks meanings up before each answer and learns after it,
and three tools for the model. Mem0, the SQL connectors and the memory's model settings stay in the memory app, so the
agent's own dependencies barely change. The main agent is not touched (decided with Tomáš on 18 Nov 2026).

```python
from memory_demo.api import memory_tools
from memory_demo.middleware import MemoryMiddleware
from memory_demo.remote import MemoryClient

memory = MemoryClient("https://mcp-shs-memory-<id>.azure.databricksapps.com/mcp")  # once per app; OAuth via the SDK
user_of = lambda runtime: runtime.context.user_email  # the signed-in user, passed in with the request

sql_agent = create_agent(
    model,
    tools=[*sql_tools, *memory_tools(memory, user_of=user_of)],
    middleware=[*sql_middleware, MemoryMiddleware(memory, user_of=user_of, note_in_answer=True)],
    context_schema=SqlAgentContext,  # user_email; user_message too, when the user's own words come along
)
```

Install in the agent: `pip install shs_memory-0.1.0-py3-none-any.whl` (build it with `pip wheel . --no-deps`). It
needs only `langchain`, `httpx` and `databricks-sdk`. Works with `invoke`/`stream` and `ainvoke`/`astream`.

```
main agent (central team)
  └─ calls ─▶ text-to-SQL agent (Tomáš's endpoint) ── MemoryMiddleware + 3 tools ─┐
                                                                                  │ MCP over HTTPS
memory app (mcp-shs-memory) ◀─────────────────────────────────────────────────────┘
  ├─ Delta tables in AskFST's catalog, schema shs_memory (the memories)
  ├─ Mem0: its extraction step (what to keep), its update step (same meaning?) and a term
  │   index in the app's temp folder, rebuilt from the tables
  └─ model endpoint (Mem0's two steps) and embedding endpoint (Mem0's term index)
```

## What happens in a turn of the SQL agent

1. **Before the first model call** the middleware calls `memory_context` with the request the SQL agent received
   (the main agent's task). The memory block it returns (what the terms mean for this user) is
   appended to the system prompt of every model call in the turn. Terms are found by their wording, and by meaning
   with Mem0 ("RAM problems" finds *memory errors*).
2. **After the answer** it calls `memory_learn`. Learning uses the user's own words when the request carries them
   (`user_message` in the agent's context, or `user_message_of=`); otherwise it uses the request itself, and the main
   agent's wording is then saved as if the user had said it. One model call, Mem0's extraction step, decides what
   to keep; when a meaning is worded unlike any memory holds for that term, Mem0's update step decides whether it is
   one of them.
3. **The answer says what memory did** (`note_in_answer=True`): it ends with "**Memory used:** …" and
   "**Remembered:** …". The SQL agent has no UI of its own, so this is how the user sees it, if the main agent passes
   the text on. The agent state also holds `memory_trace` and `memory_writes` for anything that wants them.

If memory fails (the memory app or the warehouse is down, a model times out), the agent answers without memory and
`memory_trace["note"]` says so; the three tools answer "Memory is not available". Memory never stops an answer.

**What it costs per turn.** The lookup is one request to the memory app: two table reads on the warehouse (0.35–0.45 s
in the demo) plus one embedding call for Mem0 (about 0.9 s). The first lookup after the memory app starts builds the
Mem0 index (about 7 s for the demo's terms). Learning is one model call after the answer, 1–4 s, and one more when
a new meaning has to be compared with those memory holds.

## What AskFST provides

| What | How |
| --- | --- |
| The user | `user_of(runtime)`: the signed-in user's email, passed to the SQL agent with each request, never from the model. It must match an email in the org directory (people → team → department); case and spaces do not matter. |
| The user's own words (recommended) | `user_message` in the context (or `user_message_of=`), so learning reads the user and not the main agent's rewording. |
| Its identity | The service principal the SQL agent signs in as gets **Can use** on the memory app, and its id goes into the memory app's `MEMORY_TRUSTED_CALLERS`: only listed callers may name a user. The `health` tool shows the ids the platform reports for a caller. |
| The SQL that ran (optional) | `sql_from(messages)`: the SQL statements of this turn. Default: `sql_db_query` tool calls and tool messages whose artifact is `{"sql": [...]}`. It only helps word a definition exactly. |

## The memory app

A Databricks App whose name starts with `mcp-`, running `python -m memory_demo.mcp_server` with the settings in
`deploy/mcp/app.yaml`:

| Variable | Value |
| --- | --- |
| `BACKEND` | `databricks` |
| `DATABRICKS_WAREHOUSE_ID` | the SQL warehouse (resource `sql-warehouse`) |
| `MEMORY_CATALOG`, `MEMORY_SCHEMA` | where the memory tables live: AskFST's catalog, schema `shs_memory` |
| `ORG_SEED` | **`0`**. The org directory is filled by the HR / Entra sync; with any other value an empty directory would be filled with the demo's made-up org chart |
| `MEMORY_DB_CONNECTIONS` | `4` (warehouse connections kept open) |
| `LLM_PROVIDER`, `DATABRICKS_LLM_ENDPOINT` | `databricks`, the model that decides what to remember (`databricks-claude-sonnet-5-5` in the demo) |
| `DATABRICKS_EMBEDDING_ENDPOINT`, `EMBEDDING_DIMS` | `databricks-gte-large-en`, `1024`: Mem0's embeddings (the match thresholds were set on this model) |
| `MEMORY_TRUSTED_CALLERS` | the id of the service principal the SQL agent signs in as |
| `MEMORY_EXTRACTION` | leave unset (`mem0`: Mem0's extraction phase with our rules) |

The memory app's service principal needs `USE CATALOG`, `USE SCHEMA`, `SELECT` and `MODIFY` on the memory tables
(`memory_items`, `memory_signals`, `casebook`; the casebook is written when a team's meaning changes), only `SELECT` on
`org_people`, Can use on the SQL warehouse, and Can query on the model endpoint and the embedding endpoint.

### Deploying it

Run by someone who may create a schema in AskFST's catalog and grant on it, from a clone of this repository with
`pip install -r requirements.txt` (the CLI is `databricks` v0.295+; in Git Bash set `MSYS_NO_PATHCONV=1` first so
`/Workspace/...` paths survive):

1. **Sign in:** `databricks auth login --host https://<workspace> --profile <profile>`.
2. **Create the app** `mcp-shs-memory` (Compute → Apps → Create app → Custom) with three app resources: the SQL
   warehouse under the key `sql-warehouse` (Can use), and the serving endpoints `databricks-claude-sonnet-5-5` and
   `databricks-gte-large-en` (Can query).
3. **Tables and grants:** `databricks apps get mcp-shs-memory --profile <profile>` shows the app's
   `service_principal_client_id`; then
   `python scripts/setup_databricks.py --profile <profile> --catalog <catalog> --schema shs_memory --warehouse-id <id> --only-memory --app-sp <service_principal_client_id>`
   creates `memory_items`, `memory_signals`, `casebook` and `org_people` and runs the grants above.
4. **People:** until the HR / Entra ID sync writes `org_people`, load the pilot's people from a CSV with the columns of
   `seed/org.csv`:
   `python scripts/pilot_admin.py --profile <profile> --warehouse-id <id> --catalog <catalog> people pilot_people.csv`
   shows what would change, and `--yes` writes it (the CSV becomes the whole directory). Keep the file out of this
   repository. The memory app refuses anyone it cannot find there.
5. **Settings:** set `MEMORY_CATALOG` in `deploy/mcp/app.yaml`; `MEMORY_TRUSTED_CALLERS` comes in step 7.
6. **Deploy:** `python scripts/stage_memory_app.py --profile <profile> --workspace-path /Workspace/Users/<you>/mcp-shs-memory`
   puts the app's folder together outside the repository (`app.yaml`, `requirements.txt`, `memory_demo/`; never data
   or `.env`) and prints the two commands to run: `databricks sync <folder> <workspace path> --full` and
   `databricks apps deploy mcp-shs-memory --source-code-path <workspace path>`. (Synced from inside the repository,
   nothing would be uploaded: `databricks sync` skips what git ignores, and `build/` is ignored.) Repeat this step
   after every change to the code or the settings.
7. **Trust the SQL agent:** give the service principal the SQL agent signs in as **Can use** on the memory app. The
   `health` tool, called as that principal, lists the ids the platform reports for it (`caller_ids`); put its
   application id into `MEMORY_TRUSTED_CALLERS` and deploy again (step 6).
8. **Consent:** `python scripts/pilot_admin.py <same options as step 4> consent <email> <email> ... [--share]` switches
   memory on for the pilot users (nothing is remembered before); `--off` switches it off.
9. **Check:** from your machine,
   `MemoryClient("https://mcp-shs-memory-<id>.azure.databricksapps.com/mcp", WorkspaceClient(profile="<profile>")).call("health")`
   shows `people` (the directory's size) and today's date.

**Where things live.** The memories are Delta tables in AskFST's catalog. Mem0 holds no memories: its index of term
names sits in the memory app's temp folder and is rebuilt from the tables after each start. The memory app runs as one
process, so two people teaching the same new term at the same moment are kept apart by its locks.

## Access and identity

- The user comes from the request through `user_of`, never from the model: `memory_tools` take the user from the
  runtime, so a prompt cannot make the model read someone else's memory.
- The memory app only lets a trusted caller (`MEMORY_TRUSTED_CALLERS`) name a user; a person calling it directly only
  reaches their own memory, and a request without Databricks sign-in is refused.
- Ids sent by a caller are checked, not trusted: `memory_undo` only reverses a change that is the user's own (or
  their team's / department's) and still in place, and `use_meaning` only takes a meaning the user is offered.
- What a user teaches reaches colleagues' prompts, so it is stored as one line of plain words, at most 400 characters.
  The memory never hands SQL to the agent. The SQL agent should still run read-only: memory text is user input.
- Errors returned to the caller never include table names, hosts or tokens; details go to the memory app's log.

## The calls (MCP tools of the memory app)

`memory.call(name, user_email=..., ...)`, or `await memory.acall(...)` in async code:

| Call | Called by | Arguments | Returns |
| --- | --- | --- | --- |
| `memory_context` | the middleware, every turn | `question`, `user_email`, `fuzzy` (default on), `exclude` | `context` (prompt block), `trace` |
| `memory_learn` | the middleware, every turn | `question`, `answer`, `user_email`, `previous_answer`, `trace`, `sql`, `earlier_questions` | `writes`, `relevance` |
| `memory_undo` | code (an Undo button) | `write`, `user_email` | `undone` |
| `show_memory` | model (`show_my_memory`) | `user_email` | the user's items, team, department, official, other departments |
| `explain_term` | model (`explain_term`) | `term`, `user_email` | what the term means here and the other meanings in use |
| `forget` | model (`forget_memory`) | `item_id`, `user_email` | the withdrawn item |
| `use_meaning` | code (a "Use this" button) | `rejected_id`, `chosen_id`, `user_email` | writes |
| `get_consent`, `set_consent` | code (settings) | `remember`, `share`, `user_email` | the consent |
| `health` | anyone | – | the date, people in the directory, the caller's ids |

The demo app and the memory app can point at the same memory schema; then what AskFST teaches shows up in the demo's
Memory tab and the other way round.

## Decisions

| # | Question | Decision |
| --- | --- | --- |
| 1 | Own service or code inside AskFST | **Own MCP service** (18 Nov meeting): it keeps Mem0 and the SQL connectors out of AskFST's environment, which it shares with the main agent; a few seconds more per turn are fine at this stage. |
| 2 | Which agent | **The text-to-SQL agent only.** The main agent belongs to a central team and stays untouched until the memory has proved itself. |
| 3 | How the memory knows the user | **From the request** through `user_of`; the memory app trusts only the SQL agent's service principal to name a user. |
| 4 | What the model may call | **Lookup and learning run from code every turn** (the middleware). The model gets three tools (show, explain, forget). |
| 5 | SQL filters in memory | **No: definitions in words only.** The agent writes its own SQL; the words keep the exact details (on BGL, 69 of 69 answers exact on Claude Sonnet 5.5 and 66 of 69 on GPT on 18 Nov; 44 of 46 after the Mem0 changes of 19 Nov, both misses explained in `evals/REPORT.md`). |
| 6 | Where memory lives | **In AskFST's catalog, in its own schema** (`shs_memory`). Never the demo's tables. |
| 7 | Finding terms by meaning | **On by default (Mem0)**, in the memory app. A phrase with the same head word and another modifier ("quiet rack" vs "noisy rack") is never taken for a term. |
| 8 | Showing memory to the user | **In the answer text** (`note_in_answer`), as Tomáš suggested: bold "Memory used" / "Remembered" lines. |
| 9 | Consent | Nothing is saved before a user opts in. For the pilot, switch it on for the pilot users (`scripts/pilot_admin.py consent`); users can change it themselves with `set_consent`. |
| 10 | How much of Mem0 | **Its extraction and update phases** (19 Nov): what to save and whether two meanings are the same are decided in Mem0's format, with our rules as its custom prompts. Not its `search()` or its store: tried on the term questions, `search()` found fewer terms or took five times as long (`evals/REPORT.md`), and the store would need a Vector Search endpoint. The tables stay the source of truth, and the level rules stay ours. |

## Questions for Tomáš

1. **Input:** what exactly does the text-to-SQL endpoint receive: the user's own message, the main agent's rewording,
   or the whole conversation? If only a rewording, can the user's message come along (for `user_message`)?
2. **User:** does each request carry the signed-in user's email, and where (custom inputs, a header)?
3. **The agent:** is the SQL agent built with `create_agent` (the middleware as is) or its own graph (then the two
   calls go into its code)?
4. **Answer text:** does the SQL agent's answer reach the user as it is, so the "Memory used" line is seen?
5. **Access:** the service principal the SQL agent signs in as, the catalog for the memory schema, and who runs the
   grants.
6. **People:** where people, teams and departments come from (HR or Entra ID), and who writes them into `org_people`
   (for the pilot, `scripts/pilot_admin.py people` loads a CSV).
7. **Official glossary:** should the hand-kept list of about 100 terms in the planner prompt become the official level?
