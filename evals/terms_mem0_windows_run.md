# Finding terms in a question

Demo memory, step 2; Mem0 index built in 37.3 s; with Mem0 a lookup took median 3752 ms (in-process, SQLite). A question counts as right when exactly the expected terms are found (nothing missing, nothing extra).

| Category | Questions | Right, exact wording only | Right, with Mem0 |
| --- | --- | --- | --- |
| exact | 6 | 6 | 6 |
| alias | 5 | 5 | 5 |
| typo | 4 | 0 | 4 |
| synonym | 9 | 0 | 2 |
| look-alike | 8 | 8 | 8 |
| trap | 2 | 1 | 1 |
| **all** | 34 | 20 | 26 |

| Category | Who | Question | Expected | Found with Mem0 |
| --- | --- | --- | --- | --- |
| exact | lukas.brandt | How many memory errors last month? | memory errors | memory errors (wording) |
| exact | lukas.brandt | Any memory error on R37? | memory errors | memory errors (wording) |
| exact | lukas.brandt | How many events on the noisy rack? | the noisy rack | the noisy rack (wording) |
| exact | lukas.brandt | How many events on noisy rack in December? | the noisy rack | the noisy rack (wording) |
| exact | mei.chen | How many TLB errors in November? | tlb errors | tlb errors (wording) |
| exact | lukas.brandt | Node card problems on R37 last month? | node card problems | node card problems (wording) |
| alias | lukas.brandt | Show the RAM errors for December | memory errors | memory errors (wording) |
| alias | lukas.brandt | How many link errors yesterday? | comms errors | comms errors (wording) |
| alias | mei.chen | How many kernel crashes in total? | rts crashes | rts crashes (wording) |
| alias | mei.chen | How many failed jobs last week? | job failures | job failures (wording) |
| alias | mei.chen | How many fatals in November? | fatal events | fatal events (wording) |
| typo | lukas.brandt | How many memroy errors last month? | memory errors | memory errors (matched by spelling: “memroy errors” ≈ memory errors) |
| typo | mei.chen | How many incidnets in December? | incidents | incidents (matched by spelling: “incidnets” ≈ incidents) |
| typo | lukas.brandt | Events on the noisy rak last week? | the noisy rack | the noisy rack (matched by spelling: “noisy rak” ≈ noisy rack) |
| typo | mei.chen | How many lustre problms in total? | lustre problems | lustre problems (matched by meaning with Mem0: “lustre problms” ≈ lustre problems (0.97)) |
| synonym | lukas.brandt | How many RAM problems last month? | memory errors | memory errors (matched by meaning with Mem0: “ram problems” ≈ ram errors (0.90)) |
| synonym | lukas.brandt | How many memory faults in December? | memory errors | memory errors (matched by meaning with Mem0: “memory faults” ≈ memory errors (0.93)) |
| synonym | mei.chen | How many kernel panics in total? | rts crashes | – ❌ |
| synonym | mei.chen | How many filesystem mounting problems were there? | lustre problems | – ❌ |
| synonym | mei.chen | How many core files were written in December? | core dumps | – ❌ |
| synonym | lukas.brandt | How many communication errors last month? | comms errors | – ❌ |
| synonym | mei.chen | How many translation lookaside buffer errors? | tlb errors | – ❌ |
| synonym | lukas.brandt | Any node board problems last month? | node card problems | – ❌ |
| synonym | lukas.brandt | How many events on the loud rack? | the noisy rack | – ❌ |
| look-alike | lukas.brandt | What was the memory usage on R37? | – | – |
| look-alike | lukas.brandt | How many events on the quiet rack? | – | – |
| look-alike | lukas.brandt | Which rack logged the most events? | – | – |
| look-alike | lukas.brandt | How many disk errors last month? | – | – |
| look-alike | lukas.brandt | How many errors on rack R38? | – | – |
| look-alike | mei.chen | What is the network latency on the I/O cards? | – | – |
| look-alike | mei.chen | How many warnings last week? | – | – |
| look-alike | lukas.brandt | How many link card resets? | – | – |
| trap | lukas.brandt | How many uncorrectable memory errors last month? | – | memory errors (wording) ❌ |
| trap | mei.chen | How many non-fatal events in November? | – | – |
