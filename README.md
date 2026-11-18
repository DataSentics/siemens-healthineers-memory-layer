# Shared memory layer demo

A thin version of the memory layer from the PRD ("Shared Memory Layer for AskFST", v0.2) on top of a simple
LangChain SQL agent (based on Petr's code) over the public **BGL** log (Blue Gene/L, Loghub, CC-BY-4.0).

It also has our own versions of the three things Michal's prototype is known for, built from the PRD's
description because his code is not available yet: **several agents** (a supervisor in front of the SQL agent),
a **casebook** of earlier good answers, an **ontology** built from the data, and **Mem0** for personal memory.
Compare them with his code once it is handed over (K1).

## What is in here

| Path | What |
| --- | --- |
| `memory_demo/bgl.py` | BGL parsing; table schema and column comments |
| `memory_demo/models.py` | Memory items, signals, the memory trace (FR19), writes (FR13) |
| `memory_demo/resolver.py` | Levels `personal > team > department > gold`, other departments shown but never applied, when a team meaning changes, confidence → note/warning, untrustworthy official terms (FR4–FR10) |
| `memory_demo/extractor.py` | After-turn write decision, one structured LLM call (FR1–FR3; Mem0's extraction phase): saves what the user states, without "remember that"; personal facts only when they would change an answer |
| `memory_demo/consolidate.py` | Before saving, the same term spelled differently and the same meaning in other words are recognised (Mem0's update phase), so nothing is stored twice |
| `memory_demo/org.py` | The org directory: a table (Delta on Databricks, SQLite locally) re-read every few minutes; teams and departments are whatever it says today |
| `memory_demo/service.py` | `MemoryService`: retrieve / learn / undo / forget / feedback / viewer |
| `memory_demo/api.py` | The memory's calls (`memory_context`, `memory_learn`, `memory_undo`, `show_memory`, `explain_term`, `forget`, ...), `LocalMemory` to run them in-process (the demo, the MCP server), and `memory_tools`: show / explain / forget for the model |
| `memory_demo/mcp_server.py` | The memory as an MCP server (its own Databricks App, `deploy/mcp/app.yaml`): the calls as tools, who may call for whom (`MEMORY_TRUSTED_CALLERS`), safe error messages. See `INTEGRATION.md` |
| `memory_demo/remote.py` | `MemoryClient`: what AskFST's text-to-SQL agent uses to reach the MCP server (one kept-open connection, OAuth from the Databricks SDK). With `middleware.py` and `api.py` it is the light client in the `shs-memory` wheel: no Mem0, no SQL connectors |
| `memory_demo/middleware.py` | `MemoryMiddleware` for `create_agent` (AskFST: on the text-to-SQL agent, through `MemoryClient`): retrieve once per turn, inject into every model call, learn after the answer (from the user's own words when they come along), optionally note the memory used in the answer; answers without memory if memory fails (FR17) |
| `memory_demo/agent.py` | `build_agent`: SQL agent (`SQLDatabaseToolkit` + `create_agent`, Hub SQL prompt inlined, answer-first rule). `build_supervisor`: supervisor → casebook search + SQL sub-agent; memory is looked up and learned once on the supervisor and passed to the sub-agent |
| `memory_demo/casebook.py` | Questions answered well before, with their SQL. Saved after each answer that ran SQL (unless it used someone's own meaning), withdrawn when the answer is corrected or rejected; visible to the author and their team, without names |
| `memory_demo/ontology.py` | Ontology built from the data, no human review (A9): rack → midplane → node card → card, alert families by component, example messages. Summary in the SQL prompt plus a `describe_bgl_entity` tool |
| `memory_demo/personal.py` | Mem0 adapter for personal memory (preferences, facts): Mem0 stores and searches, our extractor decides what to write |
| `memory_demo/store.py` | Tables `memory_items`, `memory_signals`, `casebook`, `org_people`; same SQL on SQLite and a Databricks SQL warehouse |
| `seed/` | Mocked org chart (41 fictional people, emails on `shs-demo.example`; loaded into the directory table once), gold glossary, the pilot month of teams and departments |
| `app.py` | Streamlit demo: chat, memory drop-down, "I remembered" notice with undo, memory viewer, internal signals page |
| `scripts/` | Local SQLite ingest, Parquet export, Databricks setup, org generator, ontology builder |

## How memory levels work

Four levels; the closest one wins: **personal > your team > your department > gold** (the official glossary).
**Other departments'** meanings are visible to everyone (Memory tab, and under "Other definitions in use" in an
answer) but never applied unless the user picks one with **Use this**. A team's own meanings stay inside the team.

- **Definitions in words, no SQL.** Memory stores what a term means, never a SQL filter: the agent writes its own
  query from the words (18 Nov). So the words keep every exact detail that pins a meaning down: codes, labels,
  names, numbers, and the texts a message contains (in quotes). The SQL that ran this turn, when the agent passes
  it, helps word a definition exactly. `scripts/eval_exactness.py` checks on BGL that answers built from the words
  match the counts of the old filters.
- **No "remember that" needed.** After each answer one memory call (Mem0's extraction phase) saves what the user
  states: a definition said in passing ("we call R12 the quiet rack"), a correction ("no, memory errors means only
  DDR"), a standing wish ("from now on one sentence"), a fact about them. One-off details of a question and the
  assistant's own guesses are not saved. The call also sees the user's earlier messages in the conversation, as
  Mem0 does. Every save shows the "Remembered" line above the answer, with **Undo** (FR13).
- **Only definitions are shared; personal facts only when they change answers.** A fact about the user's work that
  changes how they want answers ("I look after racks R30 to R37", "LINKCARD alerts are mostly noise") is saved for
  them only, without a term, so it never changes what a term means, and comes with an answer only when the question
  shares a word with it. Likes and opinions that would not change an answer ("I like SOMARIS10", "I like apples"),
  background the org directory has (team, start date), other people's views and plans, temporary things, secrets
  and customer or patient data are not saved at all.
- **Nothing is stored twice** (Mem0's update phase, `consolidate.py`). Before a term is saved it is compared with
  what memory holds: the same term spelled differently ("SOMARIS 10", "Somaris-10", "memroy errors", "memory
  error"), and the same meaning in other words (case, punctuation, articles, plurals, up to two typos). Different numbers (R37 / R38) or different words (correctable / uncorrectable) are never merged that
  way. When the term is the same but those checks cannot tell whether the meaning is, an LLM decides, given the
  user's own words, as in Mem0. A different word for the same thing ("link issues" next to "link card problems") is
  a synonym and stays a term of its own, so the user's word keeps working. A match reuses the existing wording, so colleagues who mean the same thing are counted together; restating what memory holds shows **Already in memory** and saves nothing. This fixes the 5 Nov
  duplicate: "Only correctable DDR errors, …" and "only the correctable DDR errors, …" were counted as two
  meanings, so Tomas's copy stayed next to the new team meaning.
- **Hardening (red-team review, 16 Nov, `tests/test_redteam.py`).** Undo and "Use this" check every id against the
  store (a forged or repeated request cannot retire, revive or copy anything that is not the user's to touch); a wrong
  target from the model never replaces another item; saved text is one bounded line; matching works for non-English text (German, Chinese) and never treats a one-character
  difference there as a typo; timestamps always increase, so "latest wins" has no ties; the directory is not re-read
  for every unknown caller, and an agent on a real directory never fills or wipes it (`ORG_SEED=0`).
- **Storage under load (17 Nov, `tests/test_store.py`).** Warehouse requests go through a pool of connections, so
  users do not queue behind one another. Every change is one unit of work (`MemoryStore.commit`): a transaction on
  SQLite, and on the warehouse one MERGE that adds the new meaning and retires the old ones together (signals follow
  in a second statement). One writer at a time per department and term, so two people teaching at the same moment
  cannot both create the team meaning. Checked on the real warehouse in a throwaway schema.
- **A brand-new term goes to the team at once** (if the user shares): there is no team meaning to disagree with.
- **What a user teaches about an existing term is theirs** ("only you"), and so are preferences and facts.
- **A team meaning changes only when enough teammates agree.** A team of *n* people (who had joined by the dataset
  date) needs *T* = max(2, ⌈n/2⌉) teammates using another meaning for themselves, at least two of them the same one.
  Then that meaning becomes the team's, their own copies fold into it, and the old one is retired. R&D / A has 5
  people in January 2006, so *T* = 3.
- **A department meaning forms when half of its teams agree** (at least two; all of them in a one-team department,
  so Marketing's new terms are the department's at once). Those teams' meanings fold into it; teams with another
  meaning keep theirs, since the team level comes first. The pilot month seeds R&D *comms errors* (R&D / A and B),
  Service *incidents* (Service / A and B) and Customer Support *job failures* (A and C).
- **Teammates see each other's own meanings of a shared term, without names**, under "Other definitions in use", so
  they can pick the same one. **Use this** copies the wording, so picks always match. A new term from someone who does
  not share stays fully private.
- **Confidence measures disagreement and time.** With *k* teammates using another meaning and *m* full months since
  the meaning was last used, confidence is (1 − k/T) × (1 − m/6): 100% (petrol), 67% (orange), 33% (berry, a
  warning below 60%). Official definitions only have the first part (they don't fade). The user's own items show
  **Yours** and how long they have been unused.
- **Relevance** is separate: after each answer the memory call rates every applied term high / medium / low for how
  well it fitted the question (the assistant's own judgement, reason on hover; petrol / orange / berry text).
- **Untrustworthy:** an official term replaced by teams or departments in at least half of the departments (2 of 4)
  is marked, and nothing else changes. The pilot month seeds this for *incidents* (R&D through R&D / A, and Service).
- **Fading:** a personal, team or department meaning loses a sixth of its confidence for every full month it is not
  used, and after 6 unused months it is no longer applied (the next level takes over again). Using it resets the
  clock; "used" means applied in an answer, taught, adopted or confirmed (from the signals). Gold never fades. The
  pilot month seeds *link card problems*, last used in August 2005, so it is at 33% in January.
- **Consent (FR22):** nothing is remembered until the user opts in, with two choices: remember what I teach, and
  share my own definitions with my team. Only teammates who share are counted or shown as other meanings. Reading
  shared and official meanings works without consent. The pilot teams have agreed during their month; everyone else
  sees a small consent card in the chat. Stored as `consent` signals.
- **Teams come from the org directory** (`org.py`): a table, `org_people`, that an HR or Entra ID sync keeps up to
  date. The app re-reads it every 5 minutes (`ORG_REFRESH_S`), and at once when an unknown email signs in. Nothing in
  the memory layer lists teams: a team added overnight works at once, with an empty team memory and its department's
  meanings, and team sizes and thresholds follow the directory. Teammates are matched by email, so someone who moved
  team counts for the new one. The Admin tab can add a team the way the sync would; Reset memory restores the seed
  org chart. A renamed team is a new team (its old memory stays under the old name), which needs a stable team id
  from HR to fix.
- **Find terms with Mem0 (on by default since 18 Nov; sidebar switch, or `fuzzy=False` in an agent's context):**
  switched off, a term counts only when the question uses its exact wording, an alias or a plural (other spacing and
  hyphens count: "somaris 10"). Switched on, terms are also found **by meaning**: every term and alias is a Mem0 memory (embedding from `databricks-gte-large-en`), and 1-3
  word windows of the question are matched against them (cosine >= 0.88; "RAM problems" -> memory errors 0.90, while
  "memory usage" stays out at 0.84). Typos are found **by spelling** (ratio >= 0.85; "incidnets", "noisy rak"),
  since embeddings are poor at those. Mem0 only finds the terms; the rules still decide which meaning applies. The
  index lives in a temp folder and is rebuilt from the tables; building it takes about 10 s on the first lookup after a
  start, then a lookup adds about 0.8 s. If the embedding endpoint fails, the turn falls back to exact wording
  and says so. `memory_demo/semantic.py`.

This is the PRD's confirmed hierarchy (*personal > team > department > gold*, with *other departments* shown but not
applied).

Placeholders that belong to open questions (N4, K9): the thresholds *T* and `needed_teams`, `WARN_BELOW=0.6` and the
untrustworthy rule, all in `resolver.py`. The storage model is deliberately simple (K2 is still open): team and
department items are rows with `origin = 'team'` / `'department'`, so the memory tables did not change.

## Configuration

| Variable | Local default | Databricks App |
| --- | --- | --- |
| `BACKEND` | `sqlite` | `databricks` |
| `SQLITE_DATA`, `SQLITE_MEMORY` | `data/bgl.db`, `data/memory.db` | – |
| `DATABRICKS_WAREHOUSE_ID`, `DEMO_CATALOG`, `DEMO_SCHEMA` | – | from app resources / `app.yaml` |
| `LLM_PROVIDER` | `azure` | `azure` (or `databricks` + `DATABRICKS_LLM_ENDPOINT`) |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `OPENAI_API_VERSION`, `AZURE_OPENAI_DEPLOYMENT` | in `.env` | secrets as app resources |
| `LLM_REASONING_EFFORT` | unset. Set `none` for GPT-5-family deployments: with tools they accept only `none` on chat completions, and it keeps turns at about 10 s | same |
| `LLM_TEMPERATURE` | unset (model default). Reasoning models reject any other value | same |
| `MEMORY_CATALOG`, `MEMORY_SCHEMA` | – | where the memory tables live when not next to the demo data (default: `DEMO_CATALOG` / `DEMO_SCHEMA`) |
| `MEMORY_DB_CONNECTIONS` | – | `4`: warehouse connections the memory layer uses side by side |
| `ORG_SEED` | `1` | `1` for the demo; `0` in an agent on a real directory (never fills or wipes `org_people`) |
| `ORG_CSV`, `ORG_REFRESH_S` | `seed/org.csv` (fills the directory table when it is empty), `300` (seconds between re-reads of the directory) | same |
| `DEMO_TODAY` | `2005-12-01` | same. The demo clock starts here ("last month" = November 2005); the sidebar's **+1 month** moves it to 2006-01-01 |
| `JOKE_FILTER` | `1` | `1` |
| `PERSONAL_MEMORY` | `store` | `store`. `mem0` keeps preferences and facts in Mem0 (local Qdrant in `data/mem0`) |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `EMBEDDING_DIMS` | for Mem0 (term matching, on by default; and `mem0`); dims default `1536` (text-embedding-3-small) | – (or `DATABRICKS_EMBEDDING_ENDPOINT`, default `databricks-gte-large-en`, with `LLM_PROVIDER=databricks`) |

The sidebar switches between **Supervisor with casebook** (default) and **Single SQL agent**. Both use the
same memory, so if the supervisor misbehaves on stage, switch to the single agent and carry on.
Mem0 on Databricks would need a persistent vector store (Mem0 has a Databricks Vector Search provider); the app
keeps `PERSONAL_MEMORY=store` there for now.

## Run locally

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python scripts/ingest_sqlite.py ../data/raw/bgl/BGL.log data/bgl.db
.venv/Scripts/python scripts/build_ontology.py data/bgl.db   # only if the data changes; seed/ontology.json is checked in
.venv/Scripts/python -m pytest -q
cp .env.example .env   # then fill in the Azure OpenAI values yourself
.venv/Scripts/streamlit run app.py
```

The memory as an MCP server, locally (no Databricks sign-in, so `user_email` is believed as given; never on a shared
host): `MEMORY_ALLOW_ANONYMOUS=1 .venv/Scripts/python -m memory_demo.mcp_server`, then
`MemoryClient("http://127.0.0.1:8000/mcp")`.

**What gets remembered, measured.** `scripts/eval_capture.py` runs the 43 labelled messages in
`evals/capture_cases.json` (definitions, standing wishes, work facts, and things that must not be saved: plain and
one-off questions, hypotheticals, other people's views and plans, small talk, likes and opinions that change no
answer, secrets, patient data, jokes) through the real extractor and memory rules, several times each, and through
Mem0's default extraction prompt for comparison. It writes `evals/last_run.md`. On 12 Nov 2026 (Claude Sonnet 5.5,
3 runs): 129 of 129 runs right, nothing shared that should not be; Mem0's default prompt would have stored something
for 21 of the 24 messages that need nothing.
The messages were written by the same person who wrote the prompt, so a sample of real AskFST questions is the next
test.

## Deploy to Databricks

Deployed on 4 Nov 2026 to `adb-2052394040785539.19.azuredatabricks.net`:

| Thing | Value |
| --- | --- |
| Data and memory | `askar.shs_memory_demo` (your own catalog; `agentic_sql` needs its owner to grant USE CATALOG): `bgl_events` (4,747,963 rows), `memory_items`, `memory_signals`, `casebook`, `org_people` (the org directory) |
| SQL warehouse | `apps-warehouse` (`af6307115e184b61`, serverless) |
| Model | `databricks-claude-sonnet-5-5` (served in the workspace; no keys in the app) |
| App | `shs-memory-demo`, https://shs-memory-demo-2052394040785539.19.azure.databricksapps.com |
| Code | `/Workspace/Users/askar.kassymgaliyev@datasentics.com/shs-memory-demo` |

Steps (the CLI is `databricks`, v0.295+; in Git Bash set `MSYS_NO_PATHCONV=1` so `/Workspace/...` paths survive):

1. `databricks auth login --host https://<workspace> --profile siemens` in your own terminal.
2. `python scripts/export_parquet.py data/bgl.db data/bgl_events.parquet`
3. `python scripts/setup_databricks.py --profile siemens --catalog askar --schema shs_memory_demo --warehouse-id <id>`
   builds `bgl_events` (with table and column comments) and the memory tables.
4. Create the app with two resources: the SQL warehouse under the key `sql-warehouse` (Can use) and the serving
   endpoint `databricks-claude-sonnet-5-5` (Can query).
5. Grant the app's service principal `USE CATALOG` on the catalog, `USE SCHEMA` and `SELECT` on the schema, and
   `MODIFY` on the memory tables and `org_people` (`setup_databricks.py --app-sp <application id>` runs these grants).
   On an existing deployment, `setup_databricks.py ... --app-sp <application id> --only-org` creates only
   `org_people` and its grant; the app fills it from `seed/org.csv` on its next start.
6. Upload only the app's files (`app.py`, `app.yaml`, `requirements.txt`, `memory_demo/`, `seed/`, `.streamlit/`)
   with `databricks sync <folder> /Workspace/Users/<you>/shs-memory-demo --full`, then
   `databricks apps deploy shs-memory-demo --source-code-path /Workspace/Users/<you>/shs-memory-demo`.
   Never upload `data/` (1.7 GB) or `.env`.

Claude served by Databricks rejects forced tool calls and sometimes returns its text as JSON-encoded blocks; the
extractor offers its schema as a normal tool and `middleware.message_text` reads every format.

## Demo script

About 20 minutes; steps 3b, 8 and 11 are optional. Turns take about 30 s on the deployed app. The **Guide** tab
explains the levels, what gets remembered, confidence, relevance, fading, consent and tags; tags also explain
themselves on hover.

**Setup.** Sidebar **Reset memory**: official glossary only, the seed org chart, dataset date **1 Dec 2005**, nobody
has used the assistant yet (Eva sees the consent card).

**0. Guide tab (30 s).** *"Four levels: what you teach, what your team agreed, what your department agreed, the
official glossary. Other departments' meanings are visible to everyone but never applied."* Confidence = how much
the team agrees, relevance = whether the term fitted the question.

**1. A month passes.** Sidebar **Load pilot month**: the date jumps to **1 Jan 2006** and a notice says "A month
later". *"Teams in R&D, Service and Customer Support have used it for a month; what they agreed on is there."*

**2. The newcomer** (Lukas Brandt, joined 15 Dec)
*"I'm new here. How many memory errors and comms errors did we have last month?"* Both right on the first try, at
**100%**: memory errors from *Your team*, comms errors from *Your department*, no names. *"He gets what his team and
department agreed, not what any one person typed."*

**3. Nobody has to say "remember"** (Tomas Vrba)
*"SOMARIS10 is the software platform our CT scanners run on. Did it log anything last month?"* Above the answer:
**Remembered · somaris10 … · your team**, with Undo. Switch to Petra, *"Did SOMARIS10 have any problems last
month?"*: applied at **100% · Your team**. *"A new term has nothing to disagree with, so the team gets it."*

**3b. Not everything is worth saving** (Petra) *"I really like SOMARIS10."* No "Remembered" line: it would not
change an answer. Then *"I think LINKCARD alerts are mostly noise."* **Remembered · only you**: that one changes
answers, and it stays hers.

**4. Changing a team term only for yourself** (Tomas)
*"How many memory errors did we have last month?"*: team meaning, 100%. Then *"For me, memory errors means only the
correctable DDR errors, without cache parity errors or CE sym events."* 16,718, **Remembered · only you**.

**5. A colleague's confidence drops, and her typos don't create a duplicate** (Petra)
Same question: team meaning at **67%** (orange), "1 colleague uses another meaning; 3 are needed to change it for the
team". Instead of **Use this**, type it with typos: *"by memroy errors I mean just the corectable DDR erors, no parity
errors and no CE sym events"*. **Remembered · only you**, in Tomas's wording: *"this is the bug from the last demo:
the same meaning written differently became two. Now it is matched to the one already in use, as Mem0 does."*

**6. Enough of the team agrees** (Lukas)
Same question: a **warning at 33%** (berry). **Use this** on the colleagues' meaning: **Team meaning updated ·
memory errors · 3 of 5 colleagues use this meaning**. Memory tab: one team meaning, no leftovers.

**7. Everyone gets the new meaning** (Eva Horak, who taught the old one)
Same question: the new meaning at 100%. Say it again in other words, *"memory errors means only the correctable DDR
errors without parity errors or CE sym events"*: **Already in memory**, nothing saved.

**8. Unused meanings fade** (Petra)
*"How many link card problems did we have last month?"* A **warning at 33%**: "Not used for 4 months". Ask again:
it was just used, so 100%.

**9. A department meaning forms** (Elena Dvorak, R&D / C)
Consent card → **Save**. *"In our team the noisy rack is rack R37. How many events did the noisy rack have last
month?"* **Now your department's · the noisy rack · 2 of 3 teams in R&D use this meaning**: R&D / A already had it.
Switch to Ben Weber (R&D / B): *"How many events did the noisy rack have last month?"*: **Your department**, 100%.

**10. Other departments are visible** (Mei Chen, Customer Support / B)
*"How many incidents did we have last month?"*: the official definition, **Untrustworthy**, and under *Other
definitions in use* the **Service department**'s meaning, never applied on its own.

**11. A team appears overnight** (Admin tab)
*Org directory* → Department R&D, team *R&D / D*, people *Nora Lange*, *Milan Zeman* → **Add team** (this stands in
for the nightly HR sync). Pick Nora in the sidebar, **Save** the consent card, *"How many comms errors did we have
last month?"*: **Your department**, 100%. *"Nobody set anything up for this team."*

Only the latest answer offers "Use this" and "Wrong, regenerate". If the supervisor misbehaves on stage, switch the
sidebar to **Single SQL agent**: the memory is the same. After a rehearsal, **Reset memory** again.
