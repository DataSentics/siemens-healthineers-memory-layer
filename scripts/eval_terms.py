"""Does the lookup find the right terms in a question? Labelled questions against the demo's memory (step 2), with
Mem0 matching on and off, real Databricks embeddings (`databricks-gte-large-en`, needs a Databricks login).

Categories: exact wording, plural and alias (found without Mem0); typos (found by spelling); synonyms (found by meaning
with Mem0); look-alikes that must not match ("quiet rack" is not "noisy rack"); and traps where the term's words are
in the question but it means something else ("uncorrectable memory errors").

    python scripts/eval_terms.py [--out evals/terms_last_run.md]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--out", default=str(HERE / "evals" / "terms_last_run.md"))
args = ap.parse_args()
os.environ.update({"BACKEND": "sqlite", "SQLITE_MEMORY": str(Path(tempfile.mkdtemp()) / "memory.db"),
                   "ORG_SEED": "1", "DEMO_TODAY": "2005-12-01",
                   "MEM0_INDEX_PATH": tempfile.mkdtemp(prefix="term-index-"),
                   "DATABRICKS_EMBEDDING_ENDPOINT": "databricks-gte-large-en", "EMBEDDING_DIMS": "1024"})
from dotenv import load_dotenv  # noqa: E402

load_dotenv(HERE / ".env")
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")  # the Windows console is cp1252; matches print "≈"
from memory_demo import config  # noqa: E402

E = "{}@shs-demo.example".format
LUKAS, MEI = E("lukas.brandt"), E("mei.chen")  # R&D / A, Customer Support / B

CASES = [  # (category, user, question, terms that should be found)
    ("exact", LUKAS, "How many memory errors last month?", {"memory errors"}),
    ("exact", LUKAS, "Any memory error on R37?", {"memory errors"}),
    ("exact", LUKAS, "How many events on the noisy rack?", {"the noisy rack"}),
    ("exact", LUKAS, "How many events on noisy rack in December?", {"the noisy rack"}),
    ("exact", MEI, "How many TLB errors in November?", {"TLB errors"}),
    ("exact", LUKAS, "Node card problems on R37 last month?", {"node card problems", }),
    ("alias", LUKAS, "Show the RAM errors for December", {"memory errors"}),
    ("alias", LUKAS, "How many link errors yesterday?", {"comms errors"}),
    ("alias", MEI, "How many kernel crashes in total?", {"rts crashes"}),
    ("alias", MEI, "How many failed jobs last week?", {"job failures"}),
    ("alias", MEI, "How many fatals in November?", {"fatal events"}),
    ("typo", LUKAS, "How many memroy errors last month?", {"memory errors"}),
    ("typo", MEI, "How many incidnets in December?", {"incidents"}),
    ("typo", LUKAS, "Events on the noisy rak last week?", {"the noisy rack"}),
    ("typo", MEI, "How many lustre problms in total?", {"lustre problems"}),
    ("synonym", LUKAS, "How many RAM problems last month?", {"memory errors"}),
    ("synonym", LUKAS, "How many memory faults in December?", {"memory errors"}),
    ("synonym", MEI, "How many kernel panics in total?", {"rts crashes"}),
    ("synonym", MEI, "How many filesystem mounting problems were there?", {"lustre problems"}),
    ("synonym", MEI, "How many core files were written in December?", {"core dumps"}),
    ("synonym", LUKAS, "How many communication errors last month?", {"comms errors"}),
    ("synonym", MEI, "How many translation lookaside buffer errors?", {"TLB errors"}),
    ("synonym", LUKAS, "Any node board problems last month?", {"node card problems"}),
    ("synonym", LUKAS, "How many events on the loud rack?", {"the noisy rack"}),
    ("look-alike", LUKAS, "What was the memory usage on R37?", set()),
    ("look-alike", LUKAS, "How many events on the quiet rack?", set()),
    ("look-alike", LUKAS, "Which rack logged the most events?", set()),
    ("look-alike", LUKAS, "How many disk errors last month?", set()),
    ("look-alike", LUKAS, "How many errors on rack R38?", set()),
    ("look-alike", MEI, "What is the network latency on the I/O cards?", set()),
    ("look-alike", MEI, "How many warnings last week?", set()),
    ("look-alike", LUKAS, "How many link card resets?", set()),
    ("trap", LUKAS, "How many uncorrectable memory errors last month?", set()),
    ("trap", MEI, "How many non-fatal events in November?", set()),
]

svc = config.memory_service(with_llm=False)
svc.reset(step=2, start="2005-12-01")
t0 = time.time()
svc.retrieve(LUKAS, "warm up the index", fuzzy=True)  # builds the Mem0 index
build_s = round(time.time() - t0, 1)

rows, timing = [], []
for cat, user, q, want in CASES:
    found = {}
    for fuzzy in (False, True):
        t = time.perf_counter()
        _, trace = svc.retrieve(user, q, fuzzy=fuzzy, record=False)
        if fuzzy:
            timing.append((time.perf_counter() - t) * 1000)
        found[fuzzy] = {i.term.lower(): (i.matched_by or "wording") for i in trace.items if i.term}
        if trace.note:
            print("note:", trace.note)
    want = {w.lower() for w in want}
    rows.append((cat, user.split("@")[0], q, want, found[False], found[True]))
    ok = set(found[True]) == want
    print(f"{'OK  ' if ok else 'MISS'} [{cat}] {q}  want {sorted(want)}  got {found[True]}", flush=True)

by_cat: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
for cat, _, _, want, off, on in rows:
    by_cat[cat].append((set(off) == want, set(on) == want))
lines = ["# Finding terms in a question", "",
         f"Demo memory, step 2; Mem0 index built in {build_s} s; with Mem0 a lookup took median "
         f"{sorted(timing)[len(timing) // 2]:.0f} ms (in-process, SQLite). A question counts as right when exactly "
         "the expected terms are found (nothing missing, nothing extra).", "",
         "| Category | Questions | Right, exact wording only | Right, with Mem0 |", "| --- | --- | --- | --- |"]
for cat, oks in by_cat.items():
    lines.append(f"| {cat} | {len(oks)} | {sum(o for o, _ in oks)} | {sum(m for _, m in oks)} |")
lines.append(f"| **all** | {len(rows)} | {sum(set(r[4]) == r[3] for r in rows)} | {sum(set(r[5]) == r[3] for r in rows)} |")
lines += ["", "| Category | Who | Question | Expected | Found with Mem0 |", "| --- | --- | --- | --- | --- |"]
for cat, who, q, want, _, on in rows:
    mark = "" if set(on) == want else " ❌"
    got = "; ".join(f"{t} ({how})" for t, how in on.items()) or "–"
    lines.append(f"| {cat} | {who} | {q} | {', '.join(sorted(want)) or '–'} | {got}{mark} |")
Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\nwrote {args.out}")
