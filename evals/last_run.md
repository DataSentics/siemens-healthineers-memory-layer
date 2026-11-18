# What gets remembered: 43 messages, 3 runs of ours, 1 of Mem0 (43 s)

| Case | Kind | Expected | Ours (each run) | What we saved (run 1) | Mem0 would store |
| --- | --- | --- | --- | --- | --- |
| S1 | definition in passing | shared | ✓shared ✓shared ✓shared | term the quiet rack: Rack R12. (team) | Calls rack R12 the 'quiet rack'; Wants to know how many events rack R12 (the quiet rack) had  |
| S2 | definition in passing | shared | ✓shared ✓shared ✓shared | term memory errors: only the correctable DDR errors (not parity errors or CE sym (team) | Is interested in correctable DDR memory errors; Wants to know how many correctable DDR memory errors occurre |
| S3 | correction | shared | ✓shared ✓shared ✓shared | term comms errors: all ciod errors (any message from ciod reporting an error),  (team) | Defines 'comms errors' as all ciod errors, not just the sock |
| S4 | definition in passing | shared | ✓shared ✓shared ✓shared | term hot racks: Racks R30 to R37. (team) | Hot racks are R30 to R37 for them; Wants to know which hot rack was the busiest last month |
| S5 | definition in passing | shared | ✓shared ✓shared ✓shared | term node crash: any event with level FATAL from the KERNEL component (team) | Defines a node crash as any FATAL event from the KERNEL comp; Wants to know how many node crashes occurred in December |
| S6 | definition, not in the data | shared | ✓shared ✓shared ✓shared | term somaris10: The software platform the user's CT scanners run on. (team) | SOMARIS10 is the software platform their CT scanners run on |
| S7 | definition in passing | shared | ✓shared ✓shared ✓shared | term link issues: Events from the LINKCARD component. (team) | Link issues refers to events from the LINKCARD component; Asked about link issues from last month |
| S8 | explicit remember | shared | ✓shared ✓shared ✓shared | term memory errors: Only DDR errors: messages that contain 'ddr error' (not pari (team) | Considers memory errors to be only DDR errors |
| S9 | relation | shared | ✓shared ✓shared ✓shared | relation midplane: A midplane contains 16 node cards, so midplane problems incl (team) | Asking about the number of midplane problems on R37-M1 last ; Understands that a midplane contains 16 node cards, so midpl |
| P1 | standing wish | personal | ✓personal ✓personal ✓personal | preference : Keep answers to one sentence. | Prefers answers to be kept to one sentence; Asked about the number of incidents last month |
| P2 | standing wish | personal | ✓personal ✓personal ✓personal | preference : Always show results broken down per rack; the user never nee | Always wants results shown per rack; Never needs totals; Asked how many FATAL events occurred last month |
| P3 | work focus | personal | ✓personal ✓personal ✓personal | fact : The user looks after racks R30 to R37 only.; preference : Focus answers on racks R30 to R37. | Only looks after racks R30 to R37; Wants to focus on racks R30 to R37; Wants to know what broke last month |
| P4 | work fact | personal | ✓personal ✓personal ✓personal | fact : The user is the on-call engineer for the I/O nodes. | Is the on-call engineer for the I/O nodes; Asked whether there were I/O node problems last month |
| P5 | standing wish | personal | ✓personal ✓personal ✓personal | preference : Always give dates in ISO format. | Prefers dates to be given in ISO format; Asked about when the last KERNDTLB alert was |
| P6 | private term | personal | ✓personal ✓personal ✓personal | term my rack: The rack the user looks after: rack R12. | Rack is R12; Wants to know how many errors rack R12 had last month |
| P7 | presentation preference | personal | ✓personal ✓personal ✓personal | preference : Show at most 5 rows in tables (top 5 at most); the user does | Doesn't like long tables, prefers the top 5 results at most; Wants to know which components logged the most errors last m |
| N1 | plain question | none | ✓none ✓none ✓none | – | Interested in the number of FATAL events on R37 last month |
| N2 | one-off limit | none | ✓none ✓none ✓none | – | Wants to see only the top 3 racks by events for last month |
| N3 | thanks | none | ✓none ✓none ✓none | – | – |
| N4 | hypothetical | none | ✓none ✓none ✓none | – | – |
| N5 | about someone else | none | ✓none ✓none ✓none | – | Petra thinks incidents should mean only FATAL events; Wants to know how many FATAL events occurred last month |
| N6 | small talk | none | ✓none ✓none ✓none | – | Is tired today after a long week; Asked about the number of alerts yesterday |
| N7 | temporary plan | none | ✓none ✓none ✓none | – | Is on holiday next week; Interested in which rack had the most events last month |
| N8 | one-off date | none | ✓none ✓none ✓none | – | Asked which rack had the most errors in the week of 19 Decem |
| N9 | one-off format | none | ✓none ✓none ✓none | – | – |
| N10 | question about a term | none | ✓none ✓none ✓none | – | Asked whether R37 is the noisiest rack |
| N11 | secret | none | ✓none ✓none ✓none | – | Wants to check the errors for their portal account |
| N12 | health, temporary | none | ✓none ✓none ✓none | – | Has a doctor's appointment at 3; Wants to know how many incidents there were last month |
| N13 | someone else's plan | none | ✓none ✓none ✓none | – | Manager is named Jana; Jana wants a report on incidents by Friday; Wants to know the number of incidents last month |
| N14 | complaint | none | ✓none ✓none ✓none | – | Finds the log viewer buggy; Wants to know how many KERNEL errors occurred last month |
| N15 | one-off definition | none | ✓none ✓none ✓none | – | Wants to know how many FATAL incidents there were last month |
| N16 | one-off assumption | none | ✓none ✓none ✓none | – | – |
| N17 | typo fix | none | ✓none ✓none ✓none | – | Corrected a typo, meant R37 not R38 |
| N18 | joke | none | ✓none ✓none ✓none | – | – |
| N19 | customer data | none | ✓none ✓none ✓none | – | Asked whether there was a FATAL event on Tuesday when patien; Patient Anna Novak's scan failed on Tuesday |
| N20 | like, work related | none | ✓none ✓none ✓none | – | Likes SOMARIS10; Favourite platform is SOMARIS10 |
| N21 | like, unrelated | none | ✓none ✓none ✓none | – | Likes apples; Asked about the number of alerts last month |
| N22 | background the directory has | none | ✓none ✓none ✓none | – | Joined R&D last week; Is new to the company; Wants to know the most common errors |
| P8 | what they treat as noise | personal | ✓personal ✓personal ✓personal | fact : The user treats LINKCARD alerts as mostly noise. | Thinks LINKCARD alerts are mostly noise; Asked how many LINKCARD alerts there were last month |
| N23 | dislike | none | ✓none ✓none ✓none | – | Hates Mondays; Asked about the number of incidents last month |
| N24 | opinion that changes nothing | none | ✓none ✓none ✓none | – | Thinks the old log viewer was nicer; Interested in finding out which rack had the most events las |
| P9 | what they treat as important | personal | ✓personal ✓personal ✓personal | preference : Only cares about FATAL events; warnings are irrelevant and s | Only FATAL events matter for their work; warnings are irrele; Asked about what happened on R37 last month |
| E5 | long-term goal | either | ✓none ✓none ✓none | – | Is trying to bring down the number of node card problems thi; Wants to know the number of node card problems last month |

## Summary

- Ours, expected shared: 27 of 27 runs right
- Ours, expected personal: 27 of 27 runs right
- Ours, expected none: 72 of 72 runs right
- Ours, expected either: 3 of 3 runs right
- Ours, something shared with the team that should not be: 0 (none)
- Mem0, would store something for a message that needs nothing: 19 of 24
- Mem0, would store something for a message that needs it: 18 of 18
