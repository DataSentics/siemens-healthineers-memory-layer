# Exactness check: definitions in words

Model from `.env`; demo agent `build_agent` over the local BGL table; 4.2 min.

- Seeded meanings: **14 / 14** answers exact
- Taught in conversation, then asked by a teammate: **4 / 4** exact

## Seeded meanings

| | Who | Question | Applies | Reference | Answer |
| --- | --- | --- | --- | --- | --- |
| ✅ | petra.lind | How many memory errors were there in December 2005? | team | 64,077 | There were **64,077 memory errors** in December 2005.  I interpreted “memory errors” as correctable DDR errors, cache parity errors, and CE sym events. |
| ✅ | lukas.brandt | How many comms errors were there in total? | department | 59,995 | There were **59,995 comms errors** in total. |
| ✅ | petra.lind | How many node card problems were there in November 2005? | team (relation) | 50,538 | There were **50,538 node card problems** in November 2005.  *Interpretation used: the defined “node card problems” filter—matching node-card messages from DISCO |
| ✅ | lukas.brandt | How many memory errors did the noisy rack have in total? | team + team | 38,442 | The noisy rack (R37) had **38,442 memory errors** in total. |
| ✅ | petra.lind | How many incidents were there in December 2005? | team | 44,873 | There were **44,873 incidents** in December 2005. |
| ✅ | vera.wolf | How many incidents were there in December 2005? | department | 8,785 | There were **8,785 incidents** in December 2005. |
| ✅ | mei.chen | How many incidents were there in December 2005? | official | 17,797 | There were **17,797 incidents** in December 2005. |
| ✅ | quinn.nemec | How many job failures were there in total? | department | 1,734,072 | There were **1,734,072 job failures** in total. |
| ✅ | mei.chen | How many fatal events were there in November 2005? | official | 49,307 | There were **49,307 fatal events** in November 2005. |
| ✅ | mei.chen | How many lustre problems were there in total? | official | 32,251 | There were **32,251 Lustre problems** in total. I interpreted “Lustre problems” as events labeled **KERNMNTF** or **KERNMNT**. |
| ✅ | mei.chen | How many TLB errors were there in November 2005? | official | 11 | There were **11 TLB errors** in November 2005. |
| ✅ | mei.chen | How many rts crashes were there in total? | official | 27,321 | There were **27,321 rts crashes** in total, counting events labeled **KERNRTSP** or **KERNTERM**. |
| ✅ | mei.chen | How many core dumps were there in December 2005? | official | 0 | There were **0 core dumps** in December 2005. |
| ✅ | mei.chen | How many events did the io nodes report in December 2005? | official | 47,529 | I/O nodes reported **47,529 events** in December 2005. |

## Taught in conversation

### ✅ “the quiet rack” (in passing, taught by eva.horak)

- eva.horak: We call rack R12 the quiet rack. How many FATAL events did the quiet rack have in November 2005?
- Saved: add “quiet rack” = rack R12. (only you); team “quiet rack” = rack R12. (your team)
- lukas.brandt: How many events did the quiet rack have in December 2005? Reference **7,138**. Answer: The quiet rack (R12) had **7,138 events** in December 2005.
- SQL: `SELECT COUNT(*) AS event_count
FROM bgl_events
WHERE rack = 'R12'
  AND event_date >= '2005-12-01'
  AND event_date < '2006-01-01';`

### ✅ “alignment exceptions” (stated, taught by tomas.vrba)

- tomas.vrba: By alignment exceptions I mean only the double-hummer and the floating point alignment exception messages, not the integer ones. How many alignment exceptions were there in December 2005?
- Saved: add “alignment exceptions” = Log events whose message contains either "double-hummer" or "floating point alignment", excluding integer alignment-exception messages. (only you); team “alignment exceptions” = Log events whose message contains either "double-hummer" or "floating point alignment", excluding integer alignment-exception messages. (your team)
- petra.lind: How many alignment exceptions were there in total? Reference **563,489**. Answer: There were **563,489 alignment exceptions** in total.  (Counted events containing “double-hummer” or “floating point alignment,” excluding integer alignment-exception messages.)
- SQL: `SELECT COUNT(*) AS total_alignment_exceptions
FROM bgl_events
WHERE message LIKE '%double-hummer%'
   OR message LIKE '%floating point alignment%';`

### ✅ “login failures” (correction, taught by jana.kral)

- jana.kral: How many login failures were there in total?
- jana.kral: No, by login failures I mean only the ciod LOGIN chdir messages that failed. How many were there?
- Saved: add “login failures” = Events whose message starts with "ciod: LOGIN chdir(" and contains " failed:". (only you); team “login failures” = Events whose message starts with "ciod: LOGIN chdir(" and contains " failed:". (your team)
- petra.lind: How many login failures were there in December 2005? Reference **1,296**. Answer: There were **1,296 login failures** in December 2005.
- SQL: `SELECT COUNT(*) AS login_failure_count
FROM bgl_events
WHERE event_date >= '2005-12-01'
  AND event_date < '2006-01-01'
  AND message LIKE 'ciod: LOGIN chdir(%'
  AND message LIKE '% failed:%';`

### ✅ “cache parity errors” (stated, taught by lukas.brandt)

- lukas.brandt: For us cache parity errors are the messages saying 'instruction cache parity error corrected' or 'data cache search parity error detected'. How many were there in total?
- Saved: add “cache parity errors” = Messages saying 'instruction cache parity error corrected' or 'data cache search parity error detected'. (only you); team “cache parity errors” = Messages saying 'instruction cache parity error corrected' or 'data cache search parity error detected'. (your team)
- jana.kral: How many cache parity errors were there in November 2005? Reference **3,213**. Answer: There were **3,213 cache parity errors** in November 2005.
- SQL: `SELECT COUNT(*) AS cache_parity_errors
FROM bgl_events
WHERE event_date >= '2005-11-01'
  AND event_date < '2005-12-01'
  AND message IN ('instruction cache parity error corrected', 'data cache search parity error detected. attempting to correct');`
