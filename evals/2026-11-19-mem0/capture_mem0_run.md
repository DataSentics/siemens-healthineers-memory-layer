# What gets remembered: 43 messages, 2 runs of ours, 1 of Mem0 (17 s)

Extraction: mem0.

| Case | Kind | Expected | Ours (each run) | What we saved (run 1) | Mem0 would store |
| --- | --- | --- | --- | --- | --- |
| S1 | definition in passing | shared | ✓shared ✓shared | term the quiet rack: rack R12 (team) | Calls rack R12 the quiet rack; Asked about the number of events the quiet rack (R12) had la |
| S2 | definition in passing | shared | ✓shared ✓shared | term memory errors: only the correctable DDR errors (team) | Interested in correctable DDR memory errors; Wants to know how many correctable DDR memory errors occurre |
| S3 | correction | shared | ✓shared ✓shared | term comms errors: all ciod errors, not only the socket errors between compute  (team) | Defines 'comms errors' as all ciod errors, not just the sock |
| S4 | definition in passing | shared | ✓shared ✓shared | term hot racks: racks R30 to R37 (team) | Hot racks are R30 to R37 for them; Wants to know which hot rack was the busiest last month |
| S5 | definition in passing | shared | ✓shared ✓shared | term node crash: any event with level FATAL from the KERNEL component (team) | Defines a node crash as any FATAL event from the KERNEL comp; Wants to know how many node crashes occurred in December |
| S6 | definition, not in the data | shared | ✓shared ✓shared | term somaris10: the software platform the CT scanners run on (team) | SOMARIS10 is the software platform their CT scanners run on |
| S7 | definition in passing | shared | ✓shared ✓shared | term link issues: events from the LINKCARD component (team) | Uses the term 'link issues' to mean events from the LINKCARD; Wants to know about link issues (LINKCARD component events)  |
| S8 | explicit remember | shared | ✓shared ✓shared | term memory errors: only the DDR errors (messages that contain 'ddr error') (team) | Considers memory errors to be only DDR errors |
| S9 | relation | shared | ✓shared ✓shared | relation midplane: a midplane contains 16 node cards (team); term midplane problems: all problems of the midplane itself and of its node cards (team) | Asked about the number of midplane problems on R37-M1 last m; Knows that a midplane contains 16 node cards, so midplane pr |
| P1 | standing wish | none | ✓none ✓none | – | Prefers answers to be kept to one sentence; Asked about the number of incidents last month |
| P2 | standing wish | none | ✓none ✓none | – | Always wants results shown per rack; Never needs totals; Asked how many FATAL events occurred last month |
| P3 | work focus | none | ✓none ✓none | – | Only looks after racks R30 to R37; Wants to focus on racks R30 to R37; Asked what broke last month |
| P4 | work fact | none | ✓none ✓none | – | Is the on-call engineer for the I/O nodes; Asked whether there were I/O node problems last month |
| P5 | standing wish | none | ✓none ✓none | – | Prefers dates to always be given in ISO format; Asked when the last KERNDTLB alert was |
| P6 | private term | none | ✓none ✓none | – | Rack is R12; Asked about the number of errors rack R12 had last month |
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
| N10 | question about a term | none | ✓none ✓none | – | Asked whether R37 is the noisiest rack |
| N11 | secret | none | ✓none ✓none | – | Wants to check errors for their portal account |
| N12 | health, temporary | none | ✓none ✓none | – | Has a doctor's appointment at 3; Asked about the number of incidents last month |
| N13 | someone else's plan | none | ✓none ✓none | – | Manager is named Jana; Manager Jana wants a report on incidents by Friday; Wants to know the number of incidents last month |
| N14 | complaint | none | ✓none ✓none | – | Is experiencing bugs with the log viewer; Wants to know how many KERNEL errors occurred last month |
| N15 | one-off definition | none | ✓none ✓none | – | – |
| N16 | one-off assumption | none | ✓none ✓none | – | Wants to know how many memory errors occurred, treating 'las |
| N17 | typo fix | none | ✓none ✓none | – | Was asking about R37 (corrected from R38 due to a typo) |
| N18 | joke | none | ✓none ✓none | – | – |
| N19 | customer data | none | ✓none ✓none | – | Asking about Patient Anna Novak's scan that failed on Tuesda |
| N20 | like, work related | none | ✓none ✓none | – | Likes SOMARIS10; Favourite platform is SOMARIS10 |
| N21 | like, unrelated | none | ✓none ✓none | – | Likes apples; Asked about the number of alerts last month |
| N22 | background the directory has | none | ✓none ✓none | – | Joined R&D last week; Is new to the company/team; Interested in learning about the most common errors |
| P8 | what they treat as noise | none | ✓none ✓none | – | Thinks LINKCARD alerts are mostly noise; Asked how many LINKCARD alerts there were last month |
| N23 | dislike | none | ✓none ✓none | – | Hates Mondays; Asked about the number of incidents last month |
| N24 | opinion that changes nothing | none | ✓none ✓none | – | Thinks the old log viewer was nicer; Wants to know which rack had the most events last month |
| P9 | what they treat as important | none | ✓none ✓none | – | Only FATAL events matter for their work, warnings are irrele; Interested in what happened on R37 last month |
| E5 | long-term goal | none | ✓none ✓none | – | Is trying to bring the number of node card problems down thi; Wants to know the number of node card problems last month |

## Summary

- Ours, expected shared: 18 of 18 runs right
- Ours, expected none: 68 of 68 runs right
- Ours, something shared with the team that should not be: 0 (none)
- Mem0, would store something for a message that needs nothing: 29 of 34
- Mem0, would store something for a message that needs it: 9 of 9
