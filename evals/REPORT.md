# How well the memory layer works

Updated 19 Nov 2026. First where it stands now; then what changed since the first evaluation, and why; then that
evaluation of 18 Nov in full. The rules the memory follows are in `RULES.md`.

## Where it stands (19 Nov 2026)

The setup tested: AskFST's text-to-SQL agent uses the memory over MCP, through `MemoryClient`, `MemoryMiddleware` and
the three tools (here the memory app runs in a thread, on SQLite, with Claude Sonnet 5.5 and the embedding endpoint
`databricks-gte-large-en` on Databricks). Saving runs through Mem0's extraction phase and merging through Mem0's
update phase, both with our rules as their prompts; terms are found by our lookup in Mem0's vector store; which
meaning applies to whom is decided by fixed rules.

| Check | Latest result | Details |
| --- | --- | --- |
| Exact answers on the BGL log: 23 questions, 2 runs | **44 / 46** | `evals/exactness_mcp_last_run.md` |
| What gets saved: 43 labelled messages, 2 runs | **86 / 86**, nothing shared that should not be | `evals/capture_last_run.md` |
| Merging: 37 labelled cases, 2 runs | **36 / 38** same meanings merged, **36 / 36** different ones kept apart | `evals/merge_last_run.md` |
| Finding terms: 34 labelled questions | **26 / 34** | `evals/terms_last_run.md` |
| Behaviour scenarios: 14, 2 runs | **64 / 64** | `evals/scenarios_last_run.md` |
| Unit tests | **166 pass** | `tests/` |

The two exact-answer misses: "login failures", taught as a correction, was saved narrower than taught (the old
structured extractor did the same in 1 of 2 runs), and once the SQL agent ran the right SQL for "core dumps" but left
the count out of its answer. For comparison, on 18 Nov (3 runs, 69 answers each) the memory gave 69 of 69 exact counts,
against 29 of 69 with the official glossary in the prompt (today's AskFST approach) and 20 of 69 with no memory
(`evals/2026-11-18/`).

### Weak spots

1. **Synonyms**: only close paraphrases are found (2 of 9 here). A teammate who says "kernel panics" instead of "rts
   crashes" gets the model's own guess, not the memory.
2. **A term inside another phrase** ("uncorrectable memory errors") gets the term's meaning.
3. **The agent's own mistakes can become memory**: a definition takes exact values from the SQL that ran, including a
   wrong "exactly equal". Fix to try: describe text matches the way the user put them. A correction can also be saved
   narrower than taught ("login failures", 1 of 2 runs on 19 Nov).
4. **Merging rests on one model answer**: nothing checks Mem0's "same" afterwards, so a wrong one would count a
   user towards a meaning they do not hold (none in 36 runs with different meanings here), and borderline cases such
   as "rack 37" for R37 can go either way between runs (a missed merge only keeps a second wording). The cases and the
   prompt for definitions were written by the same person.
5. **Model dependence**: on 18 Nov saving decisions were perfect on Claude Sonnet 5.5 and 96% on GPT; GPT was not
   rerun after the changes since.
6. **Time**: a lookup took a median of 0.8 s here (SQLite plus the embedding call), 7–10 s the first time after a
   start while Mem0 builds its index; learning adds 1–4 s before the SQL agent returns. On the warehouse add about
   0.4 s per lookup.

### What this does not show

- **Databricks**: the warehouse, the deployed memory app and sign-in from a serving endpoint were not run.
- **AskFST itself**: its agent, data, users and real phrasing. Here the task the SQL agent got was the user's own
  question, and `user_message` was always passed; with the main agent's rewording, learning will be weaker.
- **Scale**: about 20 terms and 41 people. With hundreds of terms the Mem0 index takes longer to build and look-alikes
  get more chances to match.
- **Time**: fading over months and drift are covered by unit tests only.
- **Independence**: we wrote these questions and scenarios alongside the system. The colleague's timeline benchmark
  (`memory-benchmark-handoff`) is the stronger test once it exists. Every question here asks for a count, a strict but
  narrow measure.

### How to rerun

```bash
python scripts/eval_exactness.py --mode mcp --runs 2   # evals/exactness_mcp_last_run.md; also --mode none / glossary
python scripts/eval_capture.py --runs 2                # evals/capture_last_run.md; --extraction structured: the old call
python scripts/eval_merge.py --runs 2                  # evals/merge_last_run.md
python scripts/eval_terms.py                           # evals/terms_last_run.md
python scripts/eval_scenarios.py --runs 2              # evals/scenarios_last_run.md
python scripts/eval_summary.py                         # every exactness run, the dated folders included
```

For Claude Sonnet 5.5 set `LLM_PROVIDER=databricks`, `DATABRICKS_LLM_ENDPOINT=databricks-claude-sonnet-5-5` and an
empty `LLM_REASONING_EFFORT`. Mem0 and Claude need a Databricks login.

## What changed since 18 Nov, and why

Newest first. Raw results of the comparisons: `evals/2026-11-19-mem0/`.

### 19 Nov, later: saving through Mem0's extraction phase; Mem0's search tried and not used

Two more parts of the job were tried through Mem0 and compared with the current results on the same data (Claude
Sonnet 5.5, 2 runs each), to keep only what does at least as well.

**Saving: now through Mem0's extraction phase.** After each answer the memory call runs as Mem0 runs it: the turn goes
in as Mem0 formats it (`parse_messages`, "Input:"), our rules are its custom fact extraction prompt, and the answer is
Mem0's list of facts ("term: meaning", "relation component: meaning", "forget: term"), read with Mem0's helpers. The
rules themselves are word for word the same; a fact has no field for a joke or for "the assistant only inferred it",
so the rules say not to return those. The ratings of the applied terms come in the same JSON answer. The structured
tool call stays available for comparison (`MEMORY_EXTRACTION=structured`).

| Check (same data, 2 runs) | Structured call | Mem0 extraction, before ratings were added | Mem0 extraction with ratings (now) |
| --- | --- | --- | --- |
| What gets saved: 43 messages | 86 / 86 | 86 / 86 | **86 / 86**, nothing shared that should not be |
| Behaviour scenarios: 14 | 64 / 64 | 64 / 64 | **64 / 64** |
| Exact answers on BGL: 23 questions (17 seeded, 6 taught in conversation) | 45 / 46 | 46 / 46 | **44 / 46** |

The exact-answer misses: "login failures", taught as a correction, was saved narrower than taught in 1 of 2 runs, with
the structured call and with the final setup alike; and once the agent ran the right SQL for "core dumps" but left the
count out of its answer, which is the SQL agent, not memory. For the 9 definitions in the capture cases, both wrote
the same or nearly the same meaning. Results: `evals/2026-11-19-mem0/capture_structured_run.md`, `evals/2026-11-19-mem0/capture_mem0_run.md`,
`evals/capture_last_run.md`; `evals/2026-11-19-mem0/scenarios_structured_run.md`, `evals/2026-11-19-mem0/scenarios_mem0_run.md`,
`evals/scenarios_last_run.md`; `evals/2026-11-19-mem0/exactness_mcp_structured_run.md`, `evals/2026-11-19-mem0/exactness_mcp_mem0_run.md`,
`evals/exactness_mcp_last_run.md`.

**Finding terms and storage: Mem0's `search()` tried, not used.** Every term was written with Mem0's `add()` and found
with its `search()`, then the same checks applied, on the term questions:

| Finding terms (34 questions) | Right | Median lookup | Index built in |
| --- | --- | --- | --- |
| Today: our lookup in Mem0's vector store (1-3 word windows of the question, one embedding call) | 26 | 0.8 s | 10 s |
| Mem0 `search()` for each window | 26 (the same matches) | **3.8 s** | 37 s |
| Mem0 `search()` with the whole question, each term a memory "term: meaning" | **25** (1 of 9 synonyms) | 0.8 s | 20 s |

`search()` embeds one query per call, so searching each window takes five times as long for the same result. Searched
with the whole question, terms unrelated to it score 0.60 to 0.69 (for example "incidents" for "kernel crashes"), as
high as real synonyms, so no cutoff separates them; the 0.70 used was chosen on these same questions, so even 25 is
optimistic. Moving storage to Mem0's own Databricks store (a Delta table with a Vector Search index) needs a Vector
Search endpoint and was not tried; on its own it would change no result. The code for both was removed after the
comparison. Results: `evals/2026-11-19-mem0/terms_mem0_windows_run.md`, `evals/2026-11-19-mem0/terms_mem0_question_run.md`.

### 19 Nov: merging through Mem0's update phase

When the wording check cannot tell whether a user's meaning is one memory already has, Mem0's update phase now
decides: its own prompt template and answer format (`get_update_memory_messages`), with a custom update prompt for
definitions (`consolidate.MERGE_PROMPT`). It is given the term's meanings of everyone the user can see, and answers
NONE for one only when both select exactly the same events, however they are written, and ADD otherwise. This
replaces the strict check from 18 Nov (a certain model plus matching exact values) and the extractor's own "same as"
hint, so one place decides every merge. Its answer only says "same or not": what is stored still follows the memory
rules.

**Why**: on 31 hand-labelled pairs of meanings (`evals/merge_cases.json`, 2 runs, Claude Sonnet 5.5) the strict check
merged only 20 of 30 runs where the meanings were the same. It missed "rack 37" for R37, "the link card component" for
LINKCARD and "1,000" for 1000. Mem0's update phase with its default prompt merged nearly everything: it kept 5 of 32
different meanings apart and overwrote the rest ("rack R37" became "rack R38"), which is right for one person changing
their mind but not for colleagues. The same Mem0 step with a prompt for definitions got all of them right:

| Merge decision (31 pairs, 2 runs) | Same meaning merged | Different meanings kept apart |
| --- | --- | --- |
| Strict check (18 Nov) | 20 / 30 | 32 / 32 |
| Strict check without the exact-values part | 24 / 30 | 32 / 32 |
| Mem0 update phase, default prompt | 30 / 30 | 5 / 32 |
| Mem0 update phase, prompt for definitions | 30 / 30 | 32 / 32 |

**After the change**, rerun on Claude Sonnet 5.5:

| Check | Result |
| --- | --- |
| Merging: the 31 pairs plus 6 cases where the term has several meanings and the right one must be picked, 2 runs | **36 / 38** same meanings merged, **36 / 36** different ones kept apart (Mem0 default on the pairs: 30 / 30 and 6 / 32) |
| Behaviour scenarios: 14, 2 runs | **64 / 64** |
| What gets saved: 43 messages, 2 runs | **86 / 86**; nothing shared that should not be (a first run had 85 / 86: once the extractor returned nothing for "By memory errors I mean only the correctable DDR errors"; 10 reruns of that message saved it every time) |
| Unit tests | 165 pass |

The two misses are one pair, "rack 37" for "rack R37", kept apart in both runs. Asked the same question 4 more times,
the model said "the same" 3 times: it is a borderline case for it. With "rack R38" also in memory (case M3) it matched
R37 in both runs. Results: `evals/merge_last_run.md`.

`scripts/eval_capture.py` now gives every run its own memory: it named them by the clock, which on Windows changes
only every few milliseconds, so runs of one message started together could share a memory and see each other's
definitions. That can only have lowered earlier scores, since the runs of one message start far apart in a normal run.

### 18 Nov, later: shared vocabulary only, strict merging

Two changes after the runs below, rerun on Claude Sonnet 5.5 (GPT not rerun):

- **Only shared vocabulary is saved**: definitions of terms and relations, nothing about the user themself
  (preferences, facts about their work, likes, a name only they use, jokes). No model decides any more whether a saved
  item may be shared: every saved item is a definition, and the user's share switch alone decides.
- **Merging is strict**: two meanings count as the same only when the model is certain and their exact values (codes
  and names in capitals, numbers, quoted message texts) match. (Replaced on 19 Nov by Mem0's update phase, above.)

| Check | Result |
| --- | --- |
| What gets saved: 43 messages, 2 runs (the 10 that used to be saved for the user only now expect nothing) | **86 / 86**; nothing shared that should not be |
| Behaviour scenarios: 14, 2 runs (5, 7, 10 and 12 rewritten for the change) | **64 / 64** |
| Unit tests | 165 pass (coverage not remeasured) |

Mem0's default extraction prompt would store something for 30 of the 34 messages that need nothing. The 18 Nov
evaluation below describes the runs before the change; its preference rows (scenarios 5 and 10) no longer apply.

## The evaluation of 18 Nov

With the memory, the SQL agent gave the exact count in **69 of 69** answers on Claude Sonnet 5.5 (66 of 69 on GPT),
against **29 of 69** with today's AskFST approach (the official glossary in the prompt) and **20 of 69** with no
memory. The memory saved the right things in 129 of 129 decisions on Claude, passed 92 of 93 behaviour checks, and
153 unit tests cover 89% of its code. Its weak spots: it finds few synonyms (2 of 9), a term's words inside another
phrase still trigger it ("uncorrectable memory errors"), and it can copy the agent's own SQL slip into a definition.

### How it was tested

- **The AskFST setup agreed on 18 Nov**: the memory runs as an MCP server (here in a thread, on SQLite), and a SQL agent
  uses it only through `MemoryClient`, `MemoryMiddleware` and the three tools. The user's own words come along as
  `user_message`. Mem0 matches terms with the Databricks endpoint `databricks-gte-large-en`.
- **Data**: the BGL log (4.7 million events). Every run starts from the demo's pilot month: 41 people in 4
  departments and 10 teams, 7 official terms, 8 team and department meanings.
- **Two models**, each running both the SQL agent and the memory: Claude Sonnet 5.5 on Databricks (what the memory app
  uses) and the GPT deployment in `.env`.
- Scripts: `scripts/eval_exactness.py`, `eval_capture.py`, `eval_merge.py`, `eval_terms.py`, `eval_scenarios.py`,
  `eval_summary.py`. Raw results of these exactness runs: `evals/2026-11-18/`; the other raw results were
  replaced by later runs.

### 1. Are the answers exact?

23 questions, 3 runs each (69 answers per setup). An answer passes when it contains the exact count of the reference
SQL condition for the meaning that should apply to the person asking.

| Setup | Team / department meanings | Official terms | Taught in conversation | All |
| --- | --- | --- | --- | --- |
| Memory over MCP, Claude Sonnet 5.5 | 27 / 27 | 24 / 24 | 18 / 18 | **69 / 69** (100%) |
| Memory over MCP, GPT | 27 / 27 | 24 / 24 | 15 / 18 | **66 / 69** (96%) |
| Official glossary in the prompt, Claude | 2 / 27 | 24 / 24 | 3 / 18 | **29 / 69** (42%) |
| Official glossary in the prompt, GPT | 0 / 27 | 24 / 24 | 5 / 18 | **29 / 69** (42%) |
| No memory, Claude | 3 / 27 | 13 / 24 | 4 / 18 | **20 / 69** (29%) |
| No memory, GPT | 0 / 27 | 9 / 24 | 6 / 18 | **15 / 69** (22%) |

- **Team and department meanings** (9 questions) are where the memory pays off: the same word means different things
  in R&D, Service and Customer Support, and only the memory gives each person theirs. They include a typo ("memroy
  errors") and a synonym found by Mem0 ("RAM problems"), both exact in every run.
- **Official terms** (8 questions, one by alias) are covered by the glossary too.
- **Taught in conversation** (5 terms, 6 questions): one person teaches a term (in passing, stated outright, as a
  correction, or as their own meaning against the team's), then they or a teammate ask in a new conversation with
  another date range.
- **The 3 misses (GPT)**: twice "cache parity errors" were saved as "messages *exactly equal* to …", copied from the
  teaching turn's own SQL, which compared whole messages; the real messages go on ("… attempting to correct"), so 7
  events were missed. Once "alignment exceptions" were summed from the counts inside the messages instead of counting
  messages; the question allows both readings.

### 2. Does it remember the right things?

43 labelled messages (definitions, standing wishes, facts about the user's work, and things that must not be saved:
plain and one-off questions, hypotheticals, other people's views, small talk, likes, secrets, patient data, jokes),
3 runs each.

| Model | Right | Shared with the team when it should not be |
| --- | --- | --- |
| Claude Sonnet 5.5 | **129 / 129** | 0 |
| GPT | 124 / 129 | 1 (a personal "only FATAL events matter to me") |

On GPT the misses were "Hot racks are R30 to R37 for us" saved as a personal fact instead of a team term (2 runs), and a
joke saved as a personal note (2 runs). For comparison, Mem0's own extraction prompt would have stored something for
19 of the 24 messages that need nothing.

### 3. Does it find the terms in a question?

34 labelled questions against the demo memory, real embeddings. Right = exactly the expected terms found.

| Kind | Questions | Exact wording only | With Mem0 |
| --- | --- | --- | --- |
| exact wording, plural | 6 | 6 | 6 |
| alias | 5 | 5 | 5 |
| typo | 4 | 0 | 4 |
| synonym | 9 | 0 | 2 |
| look-alike (must not match) | 8 | 8 | 8 |
| trap (term's words, other meaning) | 2 | 1 | 1 |
| **all** | 34 | 20 | **26** |

- **Synonyms**: Mem0 found "RAM problems" (0.90) and "memory faults" (0.93), not "kernel panics" (0.87), "core files"
  (0.79), "communication errors" or "loud rack". The threshold of 0.88 stays: look-alikes score 0.84 ("memory usage"),
  0.86 ("link card resets") and 0.88 ("quiet rack" vs "noisy rack"), so a lower one would apply wrong meanings.
  Embeddings cannot tell "loud rack" (a synonym) from "quiet rack" (an antonym) of "noisy rack".
- **Trap left**: "uncorrectable memory errors" contains "memory errors", so the team's meaning (correctable errors) is
  applied to a question about the opposite.

### 4. Does it behave as designed?

14 scripted scenarios through the MCP setup, real model (Claude Sonnet 5.5), 31 checks each run on what memory holds
afterwards, what was applied and what the answer says. 3 runs: **92 of 93 checks passed.**

| # | Scenario | Passed |
| --- | --- | --- |
| 1 | A definition said in passing goes to the teller's team, not to other departments | 12 / 12 |
| 2 | A teammate's question uses it, exact, and the answer notes the memory used | 9 / 9 |
| 3 | One person's own meaning stays theirs; the team keeps its own at 67% confidence | 12 / 12 |
| 4 | When 3 of 5 teammates mean the same in their own words, the team meaning changes; a fourth gets it | 9 / 9 |
| 5 | A preference stays personal and shapes that person's answers | 8 / 9 |
| 6 | Nothing is saved for someone who never consented | 6 / 6 |
| 7, 8 | A joke does not change the team's meaning; a like is not saved | 6 / 6 |
| 9 | Another department's meaning is shown, not applied | 6 / 6 |
| 10 | The model forgets a preference on request, with its tool | 3 / 3 |
| 11 | Undo takes a new term back out of the team | 3 / 3 |
| 12 | Someone outside the directory is refused; teammates' own items stay private | 6 / 6 |
| 13 | An instruction hidden in a definition ("ignore all previous instructions…") does not take over a colleague's answer, which stays exact | 6 / 6 |
| 14 | With the memory app down, the agent still answers and says memory was unavailable | 6 / 6 |

The miss: after "answer me in one sentence", one of three answers had two sentences (the count, then how it read
"fatal events"). The preference was applied; the model did not fully follow it.

### 5. Unit tests

153 tests pass; they cover 89% of `memory_demo`, and 93–98% of the core: resolver, service, middleware, consolidation,
term matching, client. Less is covered in the warehouse code (`store.py`, 83%), which cannot run locally.

### Fixed while testing

- Typos matched across a whole phrase: "card problems" (inside "node card problems") matched the alias "linkcard
  problems", and "non-fatal events" matched "fatal events". A typo must now hold word by word.
- (Earlier on 18 Nov) Mem0 matched "quiet rack" to "noisy rack" (0.88); a phrase with the same head word and another
  modifier no longer counts as the term. "We call rack R12 the quiet rack" was saved as private in 1 of 6 tries; the
  extractor's instructions now say such names are not private (8 of 8 after).
