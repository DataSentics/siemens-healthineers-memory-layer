# What gets remembered: 43 messages, 2 runs of ours, 1 of Mem0 (28 s)

| Case | Kind | Expected | Ours (each run) | What we saved (run 1) | Mem0 would store |
| --- | --- | --- | --- | --- | --- |
| S1 | definition in passing | shared | ✓shared ✓shared | term the quiet rack: rack R12 (team) | Calls rack R12 the quiet rack; Wants to know how many events the quiet rack (R12) had last  |
| S2 | definition in passing | shared | ✓shared ✓shared | term memory errors: only the correctable DDR errors (not cache parity errors or  (team) | Is interested in correctable DDR memory errors; Wants to know how many correctable DDR memory errors occurre |
| S3 | correction | shared | ✓shared ✓shared | term comms errors: all ciod errors (any message from ciod reporting an error),  (team) | Defines 'comms errors' as all ciod errors, not just the sock |
| S4 | definition in passing | shared | ✓shared ✓shared | term hot racks: Racks R30 to R37. (team) | Hot racks are R30 to R37 for them; Wants to know which hot rack was the busiest last month |
| S5 | definition in passing | shared | ✓shared ✓shared | term node crash: any event with level FATAL from the KERNEL component (team) | Defines a node crash as any FATAL event from the KERNEL comp; Wants to know how many node crashes occurred in December |
| S6 | definition, not in the data | shared | ✓shared ✓shared | term somaris10: the software platform the CT scanners run on (team) | Their CT scanners run on the SOMARIS10 software platform |
| S7 | definition in passing | shared | ✓shared ✓shared | term link issues: events from the LINKCARD component (team) | When saying 'link issues', means events from the LINKCARD co; Asked about link issues (LINKCARD component events) from las |
| S8 | explicit remember | shared | ✓shared ✓shared | term memory errors: only the DDR errors, i.e. messages that contain 'ddr error'  (team) | For the user, memory errors are only DDR errors |
| S9 | relation | shared | ✓shared ✓shared | relation midplane: A midplane contains 16 node cards, so midplane problems incl (team) | Asking about the number of midplane problems on R37-M1 last ; Understands that a midplane contains 16 node cards, so midpl |
| P1 | standing wish | none | ✓none ✓none | – | Prefers answers to be kept to one sentence; Asked how many incidents there were last month |
| P2 | standing wish | none | ✓none ✓none | – | Always wants results shown per rack; Never needs totals; Asked how many FATAL events occurred last month |
| P3 | work focus | none | ✓none ✓none | – | Only looks after racks R30 to R37; Wants to focus on racks R30 to R37; Asked what broke last month |
| P4 | work fact | none | ✓none ✓none | – | Is the on-call engineer for the I/O nodes; Asked whether there were I/O node problems last month |
| P5 | standing wish | none | ✓none ✓none | – | Prefers dates to be given in ISO format; Asked when the last KERNDTLB alert was |
| P6 | private term | none | ✓none ✓none | – | Rack is R12; Wants to know how many errors their rack had last month |
| P7 | presentation preference | none | ✓none ✓none | – | Doesn't like long tables and prefers the top 5 results at mo; Wants to know which components logged the most errors last m |
| N1 | plain question | none | ✓none ✓none | – | Asked about the number of FATAL events on R37 last month |
| N2 | one-off limit | none | ✓none ✓none | – | Wants to see only the top 3 racks by events for last month |
| N3 | thanks | none | ✓none ✓none | – | – |
| N4 | hypothetical | none | ✓none ✓none | – | – |
| N5 | about someone else | none | ✓none ✓none | – | Petra thinks incidents should mean only FATAL events; Wants to know how many FATAL events occurred last month |
| N6 | small talk | none | ✓none ✓none | – | Is tired today after a long week; Asked about the number of alerts yesterday |
| N7 | temporary plan | none | ✓none ✓none | – | Is on holiday next week; Interested in which rack had the most events last month |
| N8 | one-off date | none | ✓none ✓none | – | Asked which rack had the most errors in the week of 19 Decem |
| N9 | one-off format | none | ✓none ✓none | – | – |
| N10 | question about a term | none | ✓none ✓none | – | Is asking whether rack R37 is the noisiest rack |
| N11 | secret | none | ✓none ✓none | – | Wants to check the errors for their portal account |
| N12 | health, temporary | none | ✓none ✓none | – | Has a doctor's appointment at 3; Asked about the number of incidents last month |
| N13 | someone else's plan | none | ✓none ✓none | – | Manager is named Jana; Manager Jana wants a report on incidents by Friday; Wants to know the number of incidents last month |
| N14 | complaint | none | ✓none ✓none | – | Thinks the log viewer is buggy again; Wants to know how many KERNEL errors occurred last month |
| N15 | one-off definition | none | ✓none ✓none | – | Wants 'incidents' to mean only FATAL events for a specific q; Asked how many FATAL incidents there were last month |
| N16 | one-off assumption | none | ✓none ✓none | – | Asked how many memory errors occurred, treating 'last month' |
| N17 | typo fix | none | ✓none ✓none | – | Corrected a typo: meant R37 instead of R38 |
| N18 | joke | none | ✓none ✓none | – | – |
| N19 | customer data | none | ✓none ✓none | – | Asked whether there was a FATAL event on Tuesday when patien; Patient Anna Novak's scan failed on Tuesday |
| N20 | like, work related | none | ✓none ✓none | – | Likes SOMARIS10; Favourite platform is SOMARIS10 |
| N21 | like, unrelated | none | ✓none ✓none | – | Likes apples; Asked about the number of alerts last month |
| N22 | background the directory has | none | ✓none ✓none | – | Joined R&D last week; Is new to the company; Wants to know the most common errors |
| P8 | what they treat as noise | none | ✓none ✓none | – | Thinks LINKCARD alerts are mostly noise; Asked how many LINKCARD alerts there were last month |
| N23 | dislike | none | ✓none ✓none | – | Hates Mondays; Asked about the number of incidents last month |
| N24 | opinion that changes nothing | none | ✓none ✓none | – | Thinks the old log viewer was nicer; Wants to know which rack had the most events last month |
| P9 | what they treat as important | none | ✓none ✓none | – | For work, only FATAL events matter; warnings are irrelevant; Asked about what happened on R37 last month |
| E5 | long-term goal | none | ✓none ✓none | – | Trying to bring down the number of node card problems this q; Interested in knowing the number of node card problems last  |

## Summary

- Ours, expected shared: 18 of 18 runs right
- Ours, expected none: 68 of 68 runs right
- Ours, something shared with the team that should not be: 0 (none)
- Mem0, would store something for a message that needs nothing: 30 of 34
- Mem0, would store something for a message that needs it: 9 of 9
