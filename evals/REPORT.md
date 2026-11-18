# How well the memory layer works (18 Nov 2026)

With the memory, the SQL agent gave the exact count in **69 of 69** answers on Claude Sonnet 5.5 (66 of 69 on GPT),
against **29 of 69** with today's AskFST approach (the official glossary in the prompt) and **20 of 69** with no
memory. The memory saved the right things in 129 of 129 decisions on Claude, passed 92 of 93 behaviour checks, and
153 unit tests cover 89% of its code. Its weak spots: it finds few synonyms (2 of 9), a term's words inside another
phrase still trigger it ("uncorrectable memory errors"), and it can copy the agent's own SQL slip into a definition.

## How it was tested

- **The AskFST setup agreed on 18 Nov**: the memory runs as an MCP server (here in a thread, on SQLite), and a SQL agent
  uses it only through `MemoryClient`, `MemoryMiddleware` and the three tools. The user's own words come along as
  `user_message`. Mem0 matches terms with the Databricks endpoint `databricks-gte-large-en`.
- **Data**: the BGL log (4.7 million events). Every run starts from the demo's pilot month: 41 people in 4
  departments and 10 teams, 7 official terms, 8 team and department meanings.
- **Two models**, each running both the SQL agent and the memory: Claude Sonnet 5.5 on Databricks (what the memory app
  uses) and the GPT deployment in `.env`.
- Scripts: `scripts/eval_exactness.py`, `eval_capture.py`, `eval_terms.py`, `eval_scenarios.py`,
  `eval_summary.py`. Raw results: `evals/`.

## 1. Are the answers exact?

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

## 2. Does it remember the right things?

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

## 3. Does it find the terms in a question?

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

## 4. Does it behave as designed?

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

## 5. Unit tests

153 tests pass; they cover 89% of `memory_demo`, and 93–98% of the core: resolver, service, middleware, consolidation,
term matching, client. Less is covered in the warehouse code (`store.py`, 83%), which cannot run locally.

## Fixed while testing

- Typos matched across a whole phrase: "card problems" (inside "node card problems") matched the alias "linkcard
  problems", and "non-fatal events" matched "fatal events". A typo must now hold word by word.
- (Earlier on 18 Nov) Mem0 matched "quiet rack" to "noisy rack" (0.88); a phrase with the same head word and another
  modifier no longer counts as the term. "We call rack R12 the quiet rack" was saved as private in 1 of 6 tries; the
  extractor's instructions now say such names are not private (8 of 8 after).

## Weak spots

1. **Synonyms**: only close paraphrases are found (2 of 9 here). A teammate who says "kernel panics" instead of "rts
   crashes" gets the model's own guess, not the memory.
2. **A term inside another phrase** ("uncorrectable memory errors") gets the term's meaning.
3. **The agent's own mistakes can become memory**: a definition takes exact values from the SQL that ran, including a
   wrong "exactly equal". Fix to try: describe text matches the way the user put them.
4. **Preferences** are only as good as the model's instruction following (2 of 3 here for "one sentence").
5. **Model dependence**: saving decisions were perfect on Claude Sonnet 5.5 and 96% on GPT.
6. **Time**: a lookup took a median of 0.8 s here (SQLite plus the embedding call), 7–10 s the first time after a
   start while Mem0 builds its index; learning adds 1–4 s before the SQL agent returns. On the warehouse add about
   0.4 s per lookup.

## What this does not show

- **Databricks**: the warehouse, the deployed memory app and sign-in from a serving endpoint were not run.
- **AskFST itself**: its agent, data, users and real phrasing. Here the task the SQL agent got was the user's own
  question, and `user_message` was always passed; with the main agent's rewording, learning will be weaker.
- **Scale**: about 20 terms and 41 people. With hundreds of terms the Mem0 index takes longer to build and look-alikes
  get more chances to match.
- **Time**: fading over months and drift are covered by unit tests only.
- **Independence**: we wrote these questions and scenarios alongside the system. The colleague's timeline benchmark
  (`memory-benchmark-handoff`) is the stronger test once it exists. Every question here asks for a count, a strict but
  narrow measure.

## How to rerun

```bash
python scripts/eval_exactness.py --mode mcp --runs 3          # also: --mode none / glossary / memory
python scripts/eval_capture.py --runs 3
python scripts/eval_terms.py
python scripts/eval_scenarios.py --runs 3
python scripts/eval_summary.py
```

For Claude Sonnet 5.5 set `LLM_PROVIDER=databricks`, `DATABRICKS_LLM_ENDPOINT=databricks-claude-sonnet-5-5` and an
empty `LLM_REASONING_EFFORT`. Mem0 and Claude need a Databricks login.
