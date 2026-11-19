# Merging: 37 cases, 2 runs (25 s)

Each cell: one mark per run. Memory layer: one (matched to the expected meaning), other (matched to another meaning of the term) or two (kept apart). Mem0 default: its event for the pair (NONE or UPDATE keep one memory, ADD keeps both, DELETE removes the old one); not run on cases with several meanings.

| Case | Kind | Expected | Memory layer (Mem0 update phase, our prompt) | Mem0 default |
| --- | --- | --- | --- | --- |
| S1 | reworded | same | ✓one ✓one | ✓NONE ✓NONE |
| S2 | explanation dropped | same | ✓one ✓one | ✓NONE ✓NONE |
| S3 | explanation added | same | ✓one ✓one | ✓UPDATE ✓UPDATE |
| S4 | written differently: rack number | same | ✗two ✗two | ✓NONE ✓NONE |
| S5 | written differently: component name | same | ✓one ✓one | ✓NONE ✓NONE |
| S6 | written differently: number format | same | ✓one ✓one | ✓NONE ✓NONE |
| S7 | written differently: quotes dropped | same | ✓one ✓one | ✓NONE ✓NONE |
| S8 | written differently: lower case | same | ✓one ✓one | ✓NONE ✓NONE |
| S9 | reordered | same | ✓one ✓one | ✓NONE ✓NONE |
| S10 | reordered | same | ✓one ✓one | ✓NONE ✓NONE |
| S11 | typo | same | ✓one ✓one | ✓NONE ✓NONE |
| S12 | written differently: mixed case | same | ✓one ✓one | ✓NONE ✓NONE |
| S13 | written differently: location in words | same | ✓one ✓one | ✓UPDATE ✓UPDATE |
| S14 | explanation added | same | ✓one ✓one | ✓UPDATE ✓UPDATE |
| S15 | reworded | same | ✓one ✓one | ✓NONE ✓NONE |
| D1 | other number | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D2 | other number | different | ✓two ✓two | ✓ADD ✓ADD |
| D3 | opposite | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D4 | narrower | different | ✓two ✓two | ✗NONE ✗NONE |
| D5 | broader | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D6 | broader | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D7 | extra condition | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D8 | other number | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D9 | other time window | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D10 | other field | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D11 | extra condition | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D12 | narrower | different | ✓two ✓two | ✗NONE ✗NONE |
| D13 | narrower | different | ✓two ✓two | ✗NONE ✗NONE |
| D14 | extra condition | different | ✓two ✓two | ✗UPDATE ✗UPDATE |
| D15 | opposite | different | ✓two ✓two | ✓ADD ✓ADD |
| D16 | other kind | different | ✓two ✓two | ✓ADD ✓ADD |
| M1 | several meanings | same | ✓one ✓one | – |
| M2 | several meanings | same | ✓one ✓one | – |
| M3 | several meanings | same | ✓one ✓one | – |
| M4 | several meanings | different | ✓two ✓two | – |
| M5 | several meanings | same | ✓one ✓one | – |
| M6 | several meanings | different | ✓two ✓two | – |

## Summary

| | Same meaning merged | Different meanings kept apart |
| --- | --- | --- |
| Memory layer (Mem0 update phase, our prompt) | 36 / 38 | 36 / 36 |
| Mem0 default | 30 / 30 | 6 / 32 |

- Mem0 default, expected same: NONE 24, UPDATE 6
- Mem0 default, expected different: ADD 6, NONE 6, UPDATE 20
