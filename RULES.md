# The memory's rules

Every rule the memory layer follows, written so that you can work out the expected result of a test before you run
it: who gets which meaning of a term, what is saved, where it goes, and when a team or department meaning changes.
Each rule says where it lives in the code and which test already checks it.

Almost everything here is fixed code. Two decisions are made by a model, and they are marked **(model)**: what in a
message is worth saving (R13) and whether two meanings are the same (R20). Test those with the evals named there, over
several runs, not with a single expected answer.

The numbers are the current defaults. They are placeholders for open questions (K9, N4) and are all in one place:
[Parameters](#parameters).

## Words

| Word | Meaning |
| --- | --- |
| Term | A word or name people use for something in the data: "memory errors", "the noisy rack". |
| Meaning | What a term stands for, one sentence in words (never SQL). One term can have several meanings in the organisation. |
| Own meaning | A user's own meaning of a term ("only you"). |
| Team / department meaning | A meaning shared by a whole team or department. |
| Official meaning | The official glossary (`gold` in the code). |
| Today | The date the rules are evaluated at: the demo clock (`DEMO_TODAY`, or the step's date, see [Demo data](#demo-data-for-tests)), else the real date. |
| Teammates | The people in the user's team in the org directory who had joined by today, the user included. |
| Sharing | A user who switched on both "remember" and "share" (R11). |

## 1. Which meaning a user gets

**R1. Closest level wins.** For each term the question mentions (R25 to R28), the user gets the first of these that
exists and is still in use (R8): their own meaning, their team's, their department's, the official one.
Code: `resolver.resolve`, `TermState.shared`. Tests: `test_memory.py::test_newcomer_gets_team_meaning`,
`test_memory.py::test_department_meaning_reaches_its_other_teams_but_team_meanings_do_not`.

**R2. Other departments' meanings are never applied.** They are listed as other definitions in use. If only other
departments define a term, nothing is applied and the user sees a note ("not defined for you") with those meanings.
Code: `resolver.resolve` (scope `unresolved`).

**R3. What a user can see.** Their own items; a teammate's own meaning only if that teammate is sharing; their own
team's meaning (never another team's); every department's meaning; the official glossary. Nothing a user cannot see
is ever applied, matched or offered. Code: `service._visible`. Tests:
`test_redteam.py::test_use_this_only_takes_meanings_the_user_can_see`,
`test_redteam.py::test_fuzzy_matching_only_sees_what_the_user_can_see`,
`test_memory.py::test_a_colleague_who_does_not_share_does_not_count`.

**R4. Other definitions in use.** Next to the applied meaning, the user sees, without names: teammates' own meanings
that differ from it (most holders first, with the number of holders), then the team's, the department's, the
official and other departments' meanings, each only if it differs from the applied one and from those already
listed. A user who applies their own meaning sees the shared ones but not teammates' own. Code:
`resolver.alternatives`. Test: `test_memory.py::test_viewer_is_anonymised`.

**R5. Teams come from the org directory, today.** A user's team and department are what `org_people` says now. Only
people who had joined by today count (team size, teammates). Someone who moved counts for the new team at once; a new
team works at once with an empty team memory and its department's meanings. Code: `org.Org.members`, `team_size`,
`teams_in`. Tests: `test_layers.py::test_someone_who_moved_team_counts_for_the_new_team`,
`test_layers.py::test_a_new_team_works_without_any_setup`.

## 2. Confidence, warnings and fading

**R6. Disagreement.** *n* = team size today, *T* = max(2, ⌈n/2⌉) = teammates needed to change the team meaning.
*k* = sharing teammates (the viewer included) whose latest own meaning of the term differs from the applied shared
meaning. Example: R&D / A has 4 people until 14 Dec 2005 (*T* = 2) and 5 from 15 Dec 2005, when Lukas joins (*T* = 3).
Code: `resolver.needed`, `TermState.overrides`. Test: `test_memory.py::test_threshold_is_half_the_team_at_least_two`.

**R7. Confidence** of an applied team, department or official meaning = max(0.05, (1 − k/T) × freshness), rounded to
two decimals. Freshness is 1 for official meanings (R8 otherwise). Below 0.6 the user sees a warning instead of a
note. Example with *T* = 3 and nothing unused: k = 0 → 1.0, k = 1 → 0.67 (note), k = 2 → 0.33 (warning). An own
meaning has no confidence; it shows "Yours" and how long it has been unused. Code: `resolver.confidence`,
`resolver.resolve`. Tests: `test_memory.py::test_override_stays_personal_and_lowers_the_team_s_confidence`,
`test_memory.py::test_different_meanings_lower_confidence_but_do_not_replace`.

**R8. Fading.** For own, team and department meanings: *m* = full calendar months since the meaning was last used
(or created, if never used). Used means applied in an answer, taught, made a team or department meaning, or confirmed
(signals `shown`, `author`, `adopt`, `confirm`). Freshness = max(0, 1 − m/6). At *m* ≥ 6 the meaning is no longer
applied, listed or counted, and the next level takes over. Official meanings never fade. Full months: (years × 12 +
months between the two dates), minus one if today's day of the month is before the last use's. Example: *link card
problems*, last used 20 Aug 2005, on 1 Jan 2006: m = 4, freshness 0.33. Code: `resolver.months_unused`, `freshness`,
`expired`; `store._LAST_USED`. Tests: `test_memory.py::test_meanings_fade_each_unused_month_and_expire_after_six`,
`test_memory.py::test_an_expired_meaning_is_no_longer_applied`,
`test_memory.py::test_an_unused_team_meaning_has_faded_and_using_it_refreshes_it`.

**R9. A dismissed warning** for a term is shown as a note to that user from then on. Code: `service.dismiss_warning`.
Test: `test_memory.py::test_dismissed_warning_becomes_note`.

**R10. Untrustworthy official terms.** An official term whose meaning is replaced (a team or department meaning that
differs from it) in at least max(2, ⌈D/2⌉) departments, *D* = number of departments, is marked untrustworthy. Nothing
else changes. Demo: 4 departments → 2. Code: `resolver.untrustworthy_terms`. Test:
`test_memory.py::test_official_term_replaced_in_two_departments_is_untrustworthy`.

## 3. What is saved

**R11. Consent.** Nothing is saved until the user switches on "remember". "Share" decides whether their own meanings
are shown to and counted for teammates; it only counts while "remember" is on. The last choice wins. Reading shared
and official meanings needs no consent. Turning "remember" off keeps what was saved but stops new memory; turning
"share" off stops the user's own meanings being shown to and counted for teammates. Code:
`service.set_consent`, `service.apply`. Tests: `test_memory.py::test_nothing_is_remembered_without_consent`,
`test_redteam.py::test_the_last_consent_choice_wins_even_within_a_second`.

**R12. Only shared vocabulary.** Memory keeps definitions of terms (what a word, name or abbreviation means at work)
and relations (how components or event groups relate). Nothing about the user themself is ever saved: how they want
answers, what they look after, likes and opinions, private life, a name only they use ("my rack"). Code:
`extractor.SYSTEM`, `models.ITEM_TYPES`. Test: `test_layers.py::test_only_vocabulary_is_saved_or_read`.

**R13. (model) What counts as a definition.** After each answer one model call (Mem0's extraction step with our rules)
decides. A definition said in passing counts, without "remember that" ("we call R12 the quiet rack"), and a
correction is the strongest case ("no, memory errors means only DDR"). Not saved: one-off details of a question (a
rack, a date range), questions and hypotheticals, other people's views, plans and deadlines, secrets, customer or
patient data, what the org directory already says, the assistant's own reading that the user did not state, jokes.
Only the latest user message can teach; earlier messages are context. Code: `extractor.extract`,
`extractor.MEM0_SYSTEM`. Tested by: `scripts/eval_capture.py` on the 43 labelled messages in
`evals/capture_cases.json` (each marked "shared" or "none"); last result 86 of 86 runs right.

**R14. A definition is words, kept exact.** One sentence, at most 400 characters (term at most 80), with every exact
value that pins it down: codes, component and level names, numbers, quoted message texts. Code: `service._clean`,
`extractor.SYSTEM`. Tested by: `scripts/eval_exactness.py` (answers built from the saved words give the exact count).

## 4. Where a saved meaning goes

These run only for a user with "remember" on, after R13 has found a definition.

**R15. A brand-new term goes to the team at once** when the user shares: if the user can see no team, department or
official meaning of the term, it becomes their team's meaning, and their own copy folds into it. Without sharing it
stays their own. Code: `service._teach`, `service._share_new_term`. Tests:
`test_memory.py::test_new_term_goes_straight_to_the_team`,
`test_memory.py::test_new_term_stays_personal_without_sharing_consent`.

**R16. A different meaning of a known term is the user's own** ("only you"). It replaces their previous own meaning
of that term, and it counts against the shared meaning (R6). Code: `service._teach`, `service._add_own`. Tests:
`test_memory.py::test_override_stays_personal_and_lowers_the_team_s_confidence`,
`test_memory.py::test_update_own_item_and_undo`.

**R17. Saying a meaning memory already holds is agreement.** Nothing new is stored. If it is the user's own meaning or
the shared one applied to them, the answer shows "Already in memory"; if it is the shared one and the user had an own
meaning, that own meaning is dropped, so the shared one applies again. Code:
`service._teach`. Tests: `test_layers.py::test_saying_it_again_with_a_typo_is_already_in_memory`,
`test_memory.py::test_going_back_to_the_team_meaning_drops_your_own`.

**R18. A team meaning changes when enough teammates agree.** Checked each time a sharing teammate saves a meaning of
the term. When *k* ≥ *T* (R6) and the largest group of teammates holding one same meaning has at least 2 people, that
meaning becomes the team's: their own copies fold into it, the old team meaning is retired, and teammates with yet
another meaning keep theirs. A tie between groups goes to the group whose meaning was saved most recently. Example:
R&D / A, *n* = 5, *T* = 3; three sharing teammates hold A, A and B → A becomes the team's ("2 of 5 colleagues use this
meaning"). Code: `service._update_team`. Tests: `test_memory.py::test_enough_teammates_change_the_team_meaning`,
`test_layers.py::test_the_demo_duplicate_two_wordings_become_one_team_meaning`.

**R19. A department meaning forms when half of its teams agree.** *t* = teams in the department with at least one
member today; needed = max(min(2, t), ⌈t/2⌉): 1 team → 1, 2 → 2, 3 → 2, 4 → 2, 5 → 3. Checked each time a team
meaning is created or changes. When that many teams have the same team meaning, it becomes the department's: those
teams' meanings fold into it (their members now get the department meaning, R1); teams with another meaning keep
theirs, since the team level comes first. In a one-team department every new team term is the department's at once.
Code: `service._update_department`, `resolver.needed_teams`. Tests: `test_layers.py::test_needed_teams`,
`test_layers.py::test_a_department_meaning_forms_when_half_the_teams_agree_and_undo_restores`,
`test_layers.py::test_a_team_can_keep_its_own_meaning_under_a_department_meaning`,
`test_layers.py::test_a_one_team_department_shares_new_terms_with_the_department`.

**R20. (model) Nothing is stored twice.** Before anything is stored, the new term and meaning are compared with the
term's meanings the user can see (R3), plus the other teams of their department, so that teams converge on one
wording:
- *Same term*: the same up to case, spacing, punctuation, articles, plurals, one typo, or a known alias
  ("SOMARIS 10" = "somaris10", "memroy errors" = "memory errors"). A synonym is a term of its own ("link issues" is not
  "link card problems").
- *Same meaning*, fixed rule first: the same words up to case, punctuation, articles, plurals and two typos, with the
  same exact values (codes in capitals, anything with a digit, quoted texts: R37 is never R38).
- *Same meaning*, otherwise (model): Mem0's update step decides. NONE for the one meaning that selects exactly the
  same events, however it is written ("rack 37" and R37), is "the same"; ADD, an answer that does not single out one
  meaning, or no readable answer is "different".

A match reuses the existing wording, so colleagues who mean the same thing are counted together (R6, R18). Code:
`consolidate.resolve`, `consolidate.MERGE_PROMPT`. Tests: `test_layers.py::test_wording_checks_merge_typos_but_not_different_things`,
`test_layers.py::test_a_synonym_stays_a_term_of_its_own`, `test_layers.py::test_a_colleague_s_meaning_in_other_words_counts_together`.
Tested by: `scripts/eval_merge.py` on the 37 labelled cases in `evals/merge_cases.json`; last result 36 of 38
same-meaning runs merged, 36 of 36 different ones kept apart.

## 5. Changing your mind

**R21. Forget.** A user can withdraw only their own items. Asking to forget a shared meaning records disagreement and
changes nothing. Forgetting twice is recorded once. Code: `service._withdraw`. Test:
`test_memory.py::test_forgetting_twice_is_recorded_once`.

**R22. Undo** reverses one change shown to that user, only while it is still as that change left it: a saved own
meaning is withdrawn (their previous one comes back); a forget is reversed; a new team or department meaning is
retired and the old meaning and the copies folded into it come back. Undoing twice changes nothing the second time.
Code: `service.undo`. Tests: `test_memory.py::test_undo_of_a_team_update_restores_the_old_meaning`,
`test_redteam.py::test_undo_twice_changes_nothing_the_second_time`,
`test_redteam.py::test_undo_cannot_remove_or_revive_items_the_user_has_no_right_to`.

**R23. Use this.** Picking another meaning from the list makes it the user's own, worded exactly the same, so it
counts with the colleagues who hold it; picking the shared meaning drops the user's own. Only meanings the user can
see (R3) can be picked. Code: `service.choose`. Tests:
`test_layers.py::test_picking_another_department_s_meaning_makes_it_your_team_s`,
`test_redteam.py::test_use_this_only_takes_meanings_the_user_can_see`.

**R24. Rejecting a meaning for one answer** ("wrong, regenerate") leaves it out of that answer only; the next level
applies. Test: `test_memory.py::test_regenerate_without_rejected_item_falls_back_to_gold`.

## 6. Which terms a question mentions

**R25. Exact wording.** A term counts as mentioned when the question contains the term or one of its aliases, as
whole words, ignoring case, with other spacing or hyphens ("somaris 10", "memory-errors"), a plural -s or -es, and
without a leading article if at least two words remain ("noisy rack" for "the noisy rack"). Code:
`resolver.mentions`. Tests: `test_memory.py::test_plural_and_alias_matching`,
`test_redteam.py::test_a_leading_article_is_optional_only_before_two_words`.

**R26. By meaning (Mem0, on by default).** Every 1 to 3 word window of the question that does not start or end with a
stop word is compared with every term and alias the user can see (embeddings from `databricks-gte-large-en`, cosine
at least 0.88). A window never stands for another term when it is itself a term, or names another thing of the same
kind (the same last word with a qualifier the term has and the window lacks: "quiet rack" is not "noisy rack", "R38
errors" is not "R37 errors"). Code: `semantic.TermIndex`. Tests:
`test_semantic.py::test_synonym_found_by_meaning_with_mem0`,
`test_semantic.py::test_another_thing_of_the_same_kind_is_never_a_match`.

**R27. By spelling.** For terms of at least 5 letters: a window with the same number of words as the term whose
spelling ratio is at least 0.85, and each
word at least 0.75 to the term's word, is a typo of it ("memroy errors", "noisy rak"). Code:
`semantic.spelling_matches`. Tests: `test_semantic.py::test_typos_match_by_spelling`,
`test_semantic.py::test_a_typo_must_hold_word_by_word`.

**R28. Known limits** (expected failures, not bugs to report): a term inside a longer phrase still matches
("uncorrectable memory errors" gets *memory errors*), and synonyms are found in 2 of 9 cases
(`evals/terms_last_run.md`). If the embedding endpoint fails, only exact wording and spelling are used, and the answer
says so.

## Demo data for tests

- **Org**: `seed/org.csv`, 41 people in 10 teams and 4 departments (R&D, Service, Customer Support with 3 teams each;
  Marketing with 1). The `joined` column decides team sizes over time.
- **Official glossary**: `seed/gold_glossary.json`. **Team and department meanings of the pilot month**:
  `seed/pilot_memory.json`.
- **Clock**: `seeding.reset(store, org, step, start)`. Step 1 = official glossary only, today = `start`. Step 2 = the
  glossary plus a month of team and department meanings, today = one month after `start`. The tests use
  `start="2005-12-01"`, so step 2 is evaluated on 1 Jan 2006.
- **Consent**: the pilot teams have agreed to remember and share; everyone else has not been asked (R11).

## How to test

- **The fixed rules**: `python -m pytest -q` (166 tests). Every rule above names its tests; a new case is easiest to
  add next to them, with `seeding.reset(..., step=2, start="2005-12-01")` and `MemoryService(store, org)` (no model:
  only the fixed rules run).
- **The model decisions**: `scripts/eval_capture.py` (R13), `scripts/eval_merge.py` (R20), `scripts/eval_terms.py`
  (R25 to R27), and end to end `scripts/eval_scenarios.py` (14 scenarios) and `scripts/eval_exactness.py` (exact
  counts on BGL). Results and how to rerun: `evals/REPORT.md`.
- **By hand in the demo app**: `TESTING.md`.

## Parameters

| Parameter | Value | Where |
| --- | --- | --- |
| Teammates needed to change a team meaning, *T* | max(2, ⌈n/2⌉) | `resolver.needed` |
| Teams needed for a department meaning | max(min(2, t), ⌈t/2⌉) | `resolver.needed_teams` |
| Departments that make an official term untrustworthy | max(2, ⌈D/2⌉) | `resolver.untrustworthy_after` |
| Warning below confidence | 0.6 | `resolver.WARN_BELOW` |
| Lowest confidence shown | 0.05 | `resolver.MIN_CONFIDENCE` |
| Months unused before a meaning expires | 6 | `resolver.DECAY_MONTHS` |
| Match by meaning: cosine at least | 0.88 | `semantic.MEANING_MIN` |
| Match by spelling: phrase / each word at least | 0.85 / 0.75 | `semantic.SPELLING_MIN`, `WORD_MIN` |
| Longest term / meaning | 80 / 400 characters | `service.MAX_TERM`, `MAX_MEANING` |
