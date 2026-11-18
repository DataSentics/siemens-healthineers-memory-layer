"""Do definitions kept in words give exact answers? A check on the BGL log with the real model.

Memory keeps what a term means in words only; the agent writes its own SQL from them. This runs the demo agent
(`build_agent`) and checks that its answers match, to the event, the counts of a reference SQL condition for the same
meaning:

- seeded: the pilot month's team / department meanings and the official glossary, asked by a user they apply to;
- taught: a user teaches a new term in conversation (said in passing, stated outright, or as a correction), the
  memory saves it, and a teammate then asks about it with another date range.

An answer passes when the reference count appears in it. Each run starts from a fresh memory (demo step 2) in a
temporary SQLite file; the BGL table is only read. Settings (the model) come from the repo's .env.

    python scripts/eval_exactness.py                      # this checkout; writes evals/exactness_last_run.md
    python scripts/eval_exactness.py --code <other checkout> --label "with filters" --out <file.md>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--code", default=str(HERE), help="checkout whose memory_demo package is tested")
ap.add_argument("--out", default=str(HERE / "evals" / "exactness_last_run.md"))
ap.add_argument("--label", default="definitions in words")
args = ap.parse_args()

DATA = HERE / "data" / "bgl.db"
MEMORY_DB = Path(tempfile.mkdtemp(prefix="exactness-")) / "memory.db"  # never the demo's own data/memory.db
os.environ.update({"BACKEND": "sqlite", "SQLITE_DATA": str(DATA), "SQLITE_MEMORY": str(MEMORY_DB),
                   "PERSONAL_MEMORY": "store", "ORG_SEED": "1"})
from dotenv import load_dotenv  # noqa: E402

load_dotenv(HERE / ".env")  # the model settings; values set above win
sys.path.insert(0, args.code)

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from memory_demo import config  # noqa: E402
from memory_demo.agent import build_agent  # noqa: E402
from memory_demo.middleware import UserContext  # noqa: E402

E = "{}@shs-demo.example".format
EVA, TOMAS, PETRA, LUKAS, JANA = (E(n) for n in ("eva.horak", "tomas.vrba", "petra.lind", "lukas.brandt", "jana.kral"))
MEI, QUINN, VERA = E("mei.chen"), E("quinn.nemec"), E("vera.wolf")

ALL = ""
NOV = " AND event_date >= '2005-11-01' AND event_date < '2005-12-01'"
DEC = " AND event_date >= '2005-12-01' AND event_date < '2006-01-01'"

# Reference conditions: the SQL filters the seed carried until 18 Nov (checked against the data then).
MEMORY_ERRORS = "message LIKE '%ddr error%' OR message LIKE '%parity error%' OR message LIKE 'CE sym%'"
COMMS = "message LIKE 'ciod: Error reading message prefix%' OR message LIKE 'ciod: failed to read message prefix%'"
NODE_CARD = ("(component IN ('DISCOVERY', 'HARDWARE', 'MONITOR') AND message LIKE '%ode card%') "
             "OR (node_card IS NOT NULL AND level IN ('FATAL', 'FAILURE'))")
JOB_FAILURES = "alert_label IN ('KERNRTSP', 'KERNTERM') OR event_template LIKE 'generating core%'"

SEEDED = [  # (who, question, reference condition, dates, what applies)
    (PETRA, "How many memory errors were there in December 2005?", MEMORY_ERRORS, DEC, "team"),
    (LUKAS, "How many comms errors were there in total?", COMMS, ALL, "department"),
    (PETRA, "How many node card problems were there in November 2005?", NODE_CARD, NOV, "team (relation)"),
    (LUKAS, "How many memory errors did the noisy rack have in total?", f"({MEMORY_ERRORS}) AND rack = 'R37'", ALL,
     "team + team"),
    (PETRA, "How many incidents were there in December 2005?", "level IN ('FATAL', 'FAILURE')", DEC, "team"),
    (VERA, "How many incidents were there in December 2005?", "level = 'FATAL' AND component = 'KERNEL'", DEC,
     "department"),
    (MEI, "How many incidents were there in December 2005?", "alert_label IS NOT NULL", DEC, "official"),
    (QUINN, "How many job failures were there in total?", JOB_FAILURES, ALL, "department"),
    (MEI, "How many fatal events were there in November 2005?", "level IN ('FATAL', 'FAILURE')", NOV, "official"),
    (MEI, "How many lustre problems were there in total?", "alert_label IN ('KERNMNTF', 'KERNMNT')", ALL, "official"),
    (MEI, "How many TLB errors were there in November 2005?", "alert_label = 'KERNDTLB'", NOV, "official"),
    (MEI, "How many rts crashes were there in total?", "alert_label IN ('KERNRTSP', 'KERNTERM')", ALL, "official"),
    (MEI, "How many core dumps were there in December 2005?", "event_template LIKE 'generating core%'", DEC,
     "official"),
    (MEI, "How many events did the io nodes report in December 2005?", "card_type = 'IO'", DEC, "official"),
]

TAUGHT = [  # how it is taught (one conversation), then a teammate's question
    {"term": "the quiet rack", "style": "in passing", "teacher": EVA,
     "teach": ["We call rack R12 the quiet rack. How many FATAL events did the quiet rack have in November 2005?"],
     "who": LUKAS, "question": "How many events did the quiet rack have in December 2005?",
     "ref": "rack = 'R12'", "dates": DEC},
    {"term": "alignment exceptions", "style": "stated", "teacher": TOMAS,
     "teach": ["By alignment exceptions I mean only the double-hummer and the floating point alignment exception "
               "messages, not the integer ones. How many alignment exceptions were there in December 2005?"],
     "who": PETRA, "question": "How many alignment exceptions were there in total?",
     "ref": "message LIKE '%double-hummer alignment exceptions%' OR message LIKE '%floating point alignment "
            "exceptions%'", "dates": ALL},
    {"term": "login failures", "style": "correction", "teacher": JANA,
     "teach": ["How many login failures were there in total?",
               "No, by login failures I mean only the ciod LOGIN chdir messages that failed. How many were there?"],
     "who": PETRA, "question": "How many login failures were there in December 2005?",
     "ref": "message LIKE 'ciod: LOGIN chdir%failed%'", "dates": DEC},
    {"term": "cache parity errors", "style": "stated", "teacher": LUKAS,
     "teach": ["For us cache parity errors are the messages saying 'instruction cache parity error corrected' or "
               "'data cache search parity error detected'. How many were there in total?"],
     "who": JANA, "question": "How many cache parity errors were there in November 2005?",
     "ref": "message LIKE '%instruction cache parity error corrected%' OR message LIKE '%data cache search parity "
            "error detected%'", "dates": NOV},
]

data = sqlite3.connect(DATA)


def reference(cond: str, dates: str) -> int:
    return data.execute(f"SELECT COUNT(*) FROM bgl_events WHERE ({cond}){dates}").fetchone()[0]


def numbers(text: str) -> set[int]:
    found = re.findall(r"\d{1,3}(?:[,   ]\d{3})+(?!\d)|\d+", text)
    return {int(re.sub(r"\D", "", n)) for n in found}


svc = config.memory_service()
svc.reset(step=2, start="2005-12-01")
agent = build_agent(config.llm(), config.data_db(), svc)


def turn(user: str, text: str, history: list | None = None) -> dict:
    sent = [*(history or []), HumanMessage(text)]
    t0 = time.time()
    try:
        out = agent.invoke({"messages": sent}, context=UserContext(user_email=user))
    except Exception as e:  # one broken turn must not end the run
        return {"answer": f"ERROR {type(e).__name__}: {e}", "sql": [], "applied": [], "writes": [], "messages": sent,
                "seconds": round(time.time() - t0, 1)}
    new = out["messages"][len(sent):]
    sql = [tc["args"].get("query", "") for m in new if isinstance(m, AIMessage) for tc in m.tool_calls
           if tc["name"] == "sql_db_query"]
    return {"answer": out["messages"][-1].content if isinstance(out["messages"][-1].content, str)
            else str(out["messages"][-1].content),
            "sql": sql, "seconds": round(time.time() - t0, 1), "messages": out["messages"],
            "applied": [(i.get("term"), i.get("scope")) for i in (out.get("memory_trace") or {}).get("items", [])],
            "writes": [(w["op"], w.get("term"), w.get("meaning"), w.get("shared_with"))
                       for w in out.get("memory_writes") or []]}


def check(user: str, question: str, cond: str, dates: str, history=None) -> dict:
    want = reference(cond, dates)
    r = turn(user, question, history)
    r.update(user=user.split("@")[0], question=question, reference=want, ok=want in numbers(r["answer"]))
    print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['user']}: {question}  want {want:,}  ({r['seconds']} s)", flush=True)
    if not r["ok"]:
        print("      answer:", r["answer"][:300].replace("\n", " "), "\n      sql:", r["sql"][-1:] , flush=True)
    return r


results = {"label": args.label, "code": args.code, "seeded": [], "taught": []}
started = time.time()
print(f"== {args.label}: seeded meanings ==", flush=True)
for who, question, cond, dates, level in SEEDED:
    r = check(who, question, cond, dates)
    r["level"] = level
    results["seeded"].append(r)

print(f"== {args.label}: taught in conversation ==", flush=True)
for case in TAUGHT:
    history: list = []
    teach = []
    for msg in case["teach"]:
        t = turn(case["teacher"], msg, history)
        history = t["messages"]
        teach.append({"message": msg, "answer": t["answer"], "writes": t["writes"], "sql": t["sql"]})
    saved = [w for t in teach for w in t["writes"] if w[0] in ("add", "update", "team", "department", "same")]
    print(f"      {case['teacher'].split('@')[0]} taught “{case['term']}” ({case['style']}): saved {saved}", flush=True)
    r = check(case["who"], case["question"], case["ref"], case["dates"])
    r.update(term=case["term"], style=case["style"], teacher=case["teacher"].split("@")[0], teach=teach, saved=saved)
    results["taught"].append(r)

for part in ("seeded", "taught"):
    for r in results[part]:
        r.pop("messages", None)
        for t in r.get("teach", []):
            t.pop("messages", None)
results["minutes"] = round((time.time() - started) / 60, 1)

out = Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.with_suffix(".json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
seeded_ok = sum(r["ok"] for r in results["seeded"])
taught_ok = sum(r["ok"] for r in results["taught"])
lines = [f"# Exactness check: {args.label}", "",
         f"Model from `.env`; demo agent `build_agent` over the local BGL table; {results['minutes']} min.", "",
         f"- Seeded meanings: **{seeded_ok} / {len(results['seeded'])}** answers exact",
         f"- Taught in conversation, then asked by a teammate: **{taught_ok} / {len(results['taught'])}** exact", "",
         "## Seeded meanings", "", "| | Who | Question | Applies | Reference | Answer |", "| --- | --- | --- | --- | --- | --- |"]
for r in results["seeded"]:
    lines.append(f"| {'✅' if r['ok'] else '❌'} | {r['user']} | {r['question']} | {r['level']} | {r['reference']:,} | "
                 f"{r['answer'][:160].replace('|', '/').replace(chr(10), ' ')} |")
lines += ["", "## Taught in conversation", ""]
for r in results["taught"]:
    lines += [f"### {'✅' if r['ok'] else '❌'} “{r['term']}” ({r['style']}, taught by {r['teacher']})", ""]
    for t in r["teach"]:
        lines.append(f"- {r['teacher']}: {t['message']}")
    lines.append(f"- Saved: {'; '.join(f'{w[0]} “{w[1]}” = {w[2]} ({w[3]})' for w in r['saved']) or 'nothing'}")
    lines.append(f"- {r['user']}: {r['question']} Reference **{r['reference']:,}**. Answer: "
                 f"{r['answer'][:200].replace(chr(10), ' ')}")
    if r["sql"]:
        lines.append(f"- SQL: `{r['sql'][-1]}`")
    lines.append("")
out.write_text("\n".join(lines), encoding="utf-8")
print(f"\nseeded {seeded_ok}/{len(results['seeded'])}, taught {taught_ok}/{len(results['taught'])}; wrote {out}")
