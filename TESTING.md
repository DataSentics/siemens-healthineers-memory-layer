# Testing the memory demo

Run these on the deployed app: https://shs-memory-demo-2052394040785539.19.azure.databricksapps.com
(or locally with `streamlit run app.py`). A full pass takes about 45 minutes, mostly waiting for answers.

**How to read the results**
- The model writes its own SQL, so counts can differ a little between runs. What must always hold is the **memory
  behaviour**: who sees which meaning, at which level (Yours, Your team, Your department, Gold), with which confidence.
- Turns take about 30 s on the deployed app. Wait for the status to say **Done** before you click anything.
- After **Reset memory** or **Load pilot month**, wait for the spinner to finish before typing.
- Open **SQL** under an answer to see the query the agent wrote.

**The rules being tested**
- Levels: personal > your team > your department > gold. Other departments' meanings are shown to everyone and never
  applied. A team's meanings stay in the team.
- Nobody has to say "remember that": a definition or correction stated in passing is saved, with a "Remembered"
  line and Undo. One-off details and the assistant's own guesses are not saved. Memory keeps shared vocabulary only:
  nothing about the user themself is saved (preferences, what they look after, likes, opinions, a name only they
  use).
- Nothing is stored twice: another spelling of a term, other capitals, typos, or the same meaning in other words
  match what is already there ("Already in memory", or the existing wording is reused).
- A new term goes to the team at once. A team meaning changes once half the team (at least 2) uses the same other
  meaning; R&D / A has 5 people in January 2006, so 3 are needed. A department meaning forms once half of its teams
  (at least 2) have the same meaning; R&D has 3 teams, so 2 are needed.
- Confidence = how many teammates use another meaning, out of the number needed: 100% petrol, 67% orange, 33% berry
  (a warning). Your own items show **Yours**.
- Teams come from the org directory, which the app re-reads every few minutes; a new team needs no setup.

## Before you start

1. Sidebar → **Load pilot month**. The dataset date reads **1 Jan 2006**.
2. Sidebar → **Agent** = *Supervisor with casebook*.
3. As Lukas Brandt, **Memory** tab:
   - *Your team* lists 5 meanings: memory errors, node card problems, the noisy rack and incidents at 100%, and link
     card problems at 33%, "unused for 4 months".
   - *Your department, R&D* lists comms errors at 100%.
   - *Official glossary* lists 7. Incidents is tagged **Untrustworthy**, with "a closer level (your team or
     department) uses its own meaning instead".
   - *Other departments* lists Customer Support (job failures) and Service (incidents).
   - *Personal* is empty.

## A. The main scenario: no "remember that", and nothing stored twice

| # | As | Do | Expect |
|---|---|---|---|
| A1 | Tomas Vrba | *SOMARIS10 is the software platform our CT scanners run on. Did it log anything last month?* | Above the answer: **Remembered · somaris10 … · your team**, with Undo. He never said "remember". |
| A2 | Petra Lind | *Did SOMARIS10 have any problems last month?* | SOMARIS10 applied at **100% · Your team**; Memory tab → Your team lists it. Mei Chen (another department) does not get it. |
| A3 | Petra | *I really like SOMARIS10, it is my favourite platform.* Then *I think LINKCARD alerts are mostly noise.* Then *From now on, answer me in one sentence.* | No "Remembered" line for any of them: a like, an opinion and a wish about answers are about Petra, not vocabulary. The team's SOMARIS10 meaning is unchanged, and her Memory tab shows none of them. |
| A4 | Tomas | *How many memory errors did we have last month?* | Team meaning at **100% confidence**, with a relevance rating next to it. |
| A5 | Tomas | *For me, memory errors means only the correctable DDR errors, without cache parity errors or CE sym events.* | The answer is redone with a lower count. **Remembered · only you** above it. Memory tab → Personal: "colleagues see it as another meaning, without your name". |
| A6 | Petra | *How many memory errors did we have last month?* | Team meaning at **67%** (orange), "1 colleague uses another meaning; 3 are needed to change it for the team". Under *Other definitions in use*: Tomas's meaning, "1 colleague in your team uses this". No name. |
| A7 | Petra | Instead of **Use this**, type it with typos: *by memroy errors I mean just the corectable DDR erors, no parity errors and no CE sym events* | **Remembered · memory errors: only the correctable DDR errors, … · only you**. The wording is Tomas's: hers was matched to the meaning already in use, not saved as a second one. |
| A8 | Lukas Brandt | *How many memory errors did we have last month?* | The drop-down label turns into a warning: **33%** (berry), "2 colleagues in your team use this". |
| A9 | Lukas | **Use this** on the colleagues' meaning | **Team meaning updated · memory errors … · your team · 3 of 5 colleagues use this meaning**. The redone answer shows the new team meaning at 100%. |
| A10 | Eva Horak | Same question | The new meaning at 100%, as *your team*. Memory tab: exactly **one** memory errors under Your team; Tomas, Petra and Lukas have none left under Personal. |
| A11 | Eva | *memory errors means only the correctable DDR errors without parity errors or CE sym events* | **Already in memory · memory errors … · your team**, with no Undo: nothing new was saved. |

**Pass if** nothing one person teaches about an existing term reaches anyone else until 3 of 5 agree, confidence goes
100% → 67% → 33%, the change reaches the whole team at once, and there is never a second copy of the same meaning.

## B. Departments and scope

| # | As | Do | Expect |
|---|---|---|---|
| B1 | Ben Weber (R&D / B) | *How many comms errors and memory errors did we have last month?* | comms errors at **100% · Your department**. memory errors: nothing from R&D / A (a team's meaning stays in the team). |
| B2 | Elena Dvorak (R&D / C, manager) | Consent card → **Save**. Then *In our team the noisy rack is rack R37. How many events did the noisy rack have last month?* | **Now your department's · the noisy rack … · your department · 2 of 3 teams in R&D use this meaning**. Her team agreed with R&D / A, and that is half of R&D's teams. |
| B3 | Ben | *How many events did the noisy rack have last month?* | **Your department**, 100%. |
| B4 | Lukas | Memory tab | The noisy rack moved from *Your team* to *Your department, R&D*. |
| B5 | Mei Chen (Customer Support / B) | *How many incidents did we have last month?* | **Gold**, tagged **Untrustworthy**, at 100%. Under *Other definitions in use*: the **Service department**'s meaning. Nothing from R&D / A's team. |
| B6 | Mei | *Any comms errors last month?* | The drop-down says **“comms errors” is only defined in other departments**, listing the **R&D department**'s meaning. It is not applied (her answer is the model's own reading). **Use this** is disabled until she turns memory on. |
| B7 | Lukas | *How many job failures did we have last month?* | Only defined in other departments: **Customer Support department**. **Use this**: **Remembered · job failures … · your team** (new for R&D / A), and the answer is redone with it. |

## C. Changing your mind

| # | As | Do | Expect |
|---|---|---|---|
| C1 | Jana Kral | *How many incidents last month?* Then *For me incidents means only FATAL events.* | **Remembered · only you**. |
| C2 | Jana | Ask about incidents again, open the drop-down, **Use this** on the team meaning | **Removed** from memory: she is back on the team meaning. Petra's confidence for incidents is back to 100%. |
| C3 | Jana | Teach it again, then **Memory** tab → **Forget** | It disappears, and nobody's confidence is affected any more. |
| C4 | Lukas | After A9: **Undo** on "Team meaning updated" | The old team meaning is back for everyone; Tomas, Petra and Lukas have their own copies again. |
| C5 | Elena | After B2: **Undo** on "Now your department's" | Ben no longer gets the noisy rack; R&D / A and R&D / C have it as their team meaning again. |

## D. Rejecting an answer and warnings

| # | As | Do | Expect |
|---|---|---|---|
| D1 | Anyone in R&D / A | Ask about memory errors, open the drop-down → **Wrong, regenerate** | The answer is redone **without** that meaning (the model's own guess, or Gold if there is one). Confidence does not change: only someone's own meaning counts as disagreement. |
| D2 | Lukas, at the A8 warning | **Don't warn me again** | A toast. Next time the same item is a quiet note. |

## E. Things that must *not* become memory

Use any user and check that no "Remembered" line appears above the answer.

| # | Say | Why nothing should be saved |
|---|---|---|
| E1 | *How many FATAL events were there on R37 last month?* | A plain question: R37 is a one-off detail, not a standing rule. |
| E2 | *Haha, memory errors are just the coffee machine.* | A joke. |
| E3 | After an answer that used a team meaning: *Thanks, that's right.* | It is already the team meaning. |
| E4 | *Show me only the top 3 this time.* | A one-off limit for this question. |

## F. Agents, casebook and ontology

| # | As | Do | Expect |
|---|---|---|---|
| F1 | Eva | *How many TLB errors were there last month, by rack?* | **Admin** → Casebook gains one active case. |
| F2 | Petra (same team) | *How many TLB errors did we have last month per rack?* | **Casebook: 1 similar question** from *your team*, no name. |
| F3 | Mei Chen | Same question | No casebook hit: cases stay in the team. |
| F4 | – | After A5 | **Admin** shows no case for Tomas's redone answer: it used his own meaning, and cases are shared with the team. |
| F5 | Anyone | *Which node cards on R37-M1 had the most events?* | Node cards like `R37-M1-N4`, R37-M1-NC on top. |
| F6 | – | Sidebar → *Single SQL agent*, repeat A6 | Same memory behaviour, no casebook. The fallback if the supervisor misbehaves on stage. |

## G. Fading

| # | As | Do | Expect |
|---|---|---|---|
| G1 | Petra | *How many link card problems did we have last month?* | A **warning at 33%**: "Not used for 4 months: confidence drops each unused month, and the meaning expires after 6." |
| G2 | Petra | Same question again | **100%**: it was just used, so the clock reset. |
| G3 | Anyone | Memory tab → Official glossary | Gold terms never show "unused": they don't fade. |

## H. Consent

| # | As | Do | Expect |
|---|---|---|---|
| H1 | – | **Reset memory**, then as Eva open Chat | A small card: "Memory for Eva · nothing is remembered until you choose", with two ticks and Save. |
| H2 | Mei Chen (after **Load pilot month**) | Don't answer the card; say *For me incidents means FATAL events only.* | No "Remembered" line: nothing is saved without consent. The answer still uses the official meaning. |
| H3 | Mei | Tick remember, untick share, **Save**; say it again | **Remembered · only you**. Her own meaning is never shown to or counted for Customer Support / B. |
| H4 | Mei | Memory tab | Two toggles at the top, matching what she chose; change them there. |
| H5 | Tomas (pilot team) | – | No card: the pilot teams agreed during their month. Turning his share toggle off removes his own meanings from his colleagues' counts and lists. |

## I. A team that appears overnight

| # | As | Do | Expect |
|---|---|---|---|
| I1 | – | **Admin** → *Org directory*: Department **R&D**, Team name *R&D / D*, People *Nora Lange* and *Milan Zeman* (one per line) → **Add team** | "Added R&D / D to R&D with 2 people". This stands in for the nightly HR sync. |
| I2 | Nora Lange (R&D / D, manager) | Pick her in the sidebar; consent card → **Save**. *How many comms errors did we have last month?* | comms errors at **100% · Your department**: a brand-new team gets its department's meanings with no setup. |
| I3 | Nora | *We call racks R30 to R37 the hot racks. How many events did the hot racks have last month?* | **Remembered · hot racks … · your team**. Milan Zeman gets it too; nobody in R&D / A does. |
| I4 | – | **Organisation** tab | R&D / D is listed under R&D with both people. |
| I5 | – | **Reset memory** | R&D / D is gone again (the seed org chart). |

## After testing

Sidebar → **Reset memory**, so the demo starts clean (the script starts there, then loads the pilot month).

## What to note when something fails

The user, the exact question, the count, the drop-down label, the confidence and relevance tags, and the SQL (copy it
from **SQL**).
