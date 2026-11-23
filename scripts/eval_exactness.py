"""Do the answers come out exact? The memory layer on the BGL log with the real model, against baselines.

Each question has a reference SQL condition for the meaning that should apply to the asker; an answer passes when the
reference count appears in it. Setups (`--mode`):

- `mcp`: AskFST as agreed on 18 Nov. The memory runs as an MCP server (here in a thread), and a SQL agent uses it only
  through MemoryClient + MemoryMiddleware + the three tools; the user's own words come along as `user_message`.
- `memory`: the demo's in-process setup (`build_agent`).
- `none`: no memory at all (PRD baseline B0).
- `glossary`: no memory, the official glossary in every prompt (B1, today's AskFST approach).

Questions:
- seeded: the pilot month's team / department meanings and the official glossary, asked by a user they apply to, in
  exact wording, by alias, with a typo and with a synonym (found by Mem0);
- taught: a user teaches a term in conversation (in passing, stated outright, as a correction, or as their own
  meaning that differs from the team's), then the user or a teammate asks about it with another date range.

Every run starts from a fresh memory (demo step 2) in a temporary SQLite file; the BGL table is only read. The model
comes from the repo's .env; Mem0 uses the Databricks embedding endpoint (`--embeddings`, needs a Databricks login).

    python scripts/eval_exactness.py --mode mcp --runs 3   # writes evals/exactness_mcp_last_run.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["mcp", "memory", "none", "glossary"], default="mcp")
ap.add_argument("--runs", type=int, default=1)
ap.add_argument("--code", default=str(HERE), help="checkout whose memory_demo package is tested")
ap.add_argument("--out", default=None)
ap.add_argument("--embeddings", default="databricks-gte-large-en", help="Databricks embedding endpoint ('' = none)")
args = ap.parse_args()
OUT = Path(args.out or HERE / "evals" / f"exactness_{args.mode}_last_run.md")

DATA = HERE / "data" / "bgl.db"
os.environ.update({"BACKEND": "sqlite", "SQLITE_DATA": str(DATA), "ORG_SEED": "1",
                   "DEMO_TODAY": "2005-12-01"})
if args.embeddings:
    os.environ.update({"DATABRICKS_EMBEDDING_ENDPOINT": args.embeddings, "EMBEDDING_DIMS": "1024"})
from dotenv import load_dotenv  # noqa: E402

load_dotenv(HERE / ".env")  # the model settings; values set above win
sys.path.insert(0, args.code)

from langchain.agents import create_agent  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from memory_demo import config  # noqa: E402
from memory_demo.agent import _sql_prompt, _sql_tools, build_agent  # noqa: E402
from memory_demo.middleware import MemoryContextMiddleware, UserContext  # noqa: E402

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

SEEDED = [  # (who, question, reference condition, dates, what should apply)
    (PETRA, "How many memory errors were there in December 2005?", MEMORY_ERRORS, DEC, "team"),
    (LUKAS, "How many comms errors were there in total?", COMMS, ALL, "department"),
    (PETRA, "How many node card problems were there in November 2005?", NODE_CARD, NOV, "team relation"),
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
    (MEI, "How many kernel crashes were there in total?", "alert_label IN ('KERNRTSP', 'KERNTERM')", ALL,
     "official, by alias"),
    (PETRA, "How many memroy errors were there in November 2005?", MEMORY_ERRORS, NOV, "team, typo"),
    (LUKAS, "How many RAM problems were there in December 2005?", MEMORY_ERRORS, DEC, "team, synonym (Mem0)"),
]

TAUGHT = [  # one conversation that teaches, then questions in new conversations
    {"term": "the quiet rack", "style": "in passing", "teacher": EVA,
     "teach": ["We call rack R12 the quiet rack. How many FATAL events did the quiet rack have in November 2005?"],
     "probes": [(LUKAS, "How many events did the quiet rack have in December 2005?", "rack = 'R12'", DEC)]},
    {"term": "alignment exceptions", "style": "stated", "teacher": TOMAS,
     "teach": ["By alignment exceptions I mean only the double-hummer and the floating point alignment exception "
               "messages, not the integer ones. How many alignment exceptions were there in December 2005?"],
     "probes": [(PETRA, "How many alignment exceptions were there in total?",
                 "message LIKE '%double-hummer alignment exceptions%' OR message LIKE '%floating point alignment "
                 "exceptions%'", ALL)]},
    {"term": "login failures", "style": "correction", "teacher": JANA,
     "teach": ["How many login failures were there in total?",
               "No, by login failures I mean only the ciod LOGIN chdir messages that failed. How many were there?"],
     "probes": [(PETRA, "How many login failures were there in December 2005?",
                 "message LIKE 'ciod: LOGIN chdir%failed%'", DEC)]},
    {"term": "cache parity errors", "style": "stated", "teacher": LUKAS,
     "teach": ["For us cache parity errors are the messages saying 'instruction cache parity error corrected' or "
               "'data cache search parity error detected'. How many were there in total?"],
     "probes": [(JANA, "How many cache parity errors were there in November 2005?",
                 "message LIKE '%instruction cache parity error corrected%' OR message LIKE '%data cache search "
                 "parity error detected%'", NOV)]},
    {"term": "memory errors (own meaning)", "style": "correction of the team's", "teacher": TOMAS,
     "teach": ["How many memory errors were there in November 2005?",
               "No, for me memory errors are only the correctable DDR errors, the messages that contain 'ddr error'. "
               "How many were there?"],
     "probes": [(TOMAS, "How many memory errors were there in December 2005?", "message LIKE '%ddr error%'", DEC),
                (PETRA, "How many memory errors were there in October 2005?", MEMORY_ERRORS,
                 " AND event_date >= '2005-10-01' AND event_date < '2005-11-01'")]},
]

data = sqlite3.connect(DATA)


def reference(cond: str, dates: str) -> int:
    return data.execute(f"SELECT COUNT(*) FROM bgl_events WHERE ({cond}){dates}").fetchone()[0]


def numbers(text: str) -> set[int]:
    found = re.findall(r"\d{1,3}(?:[,   ]\d{3})+(?!\d)|\d+", text)
    return {int(re.sub(r"\D", "", n)) for n in found}


def text_of(msg) -> str:
    c = msg.content
    return c if isinstance(c, str) else " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)


GLOSSARY = "## Glossary (official definitions)\n" + "\n".join(
    f"- “{r['term']}” means: {r['meaning']}"
    for r in json.loads((Path(args.code) / "seed" / "gold_glossary.json").read_text(encoding="utf-8")))


class Setup:
    """The agent for this mode, rebuilt for every run with a fresh memory."""

    def __init__(self):
        self.server = None

    def start(self, run: int):
        os.environ["SQLITE_MEMORY"] = str(Path(tempfile.mkdtemp(prefix=f"exact-{args.mode}-{run}-")) / "memory.db")
        os.environ["MEM0_INDEX_PATH"] = str(Path(tempfile.mkdtemp(prefix="term-index-")))
        for f in (config.memory_store, config.org, config.term_index):  # a fresh store and index per run
            f.cache_clear()
        llm, db = config.llm(), config.data_db()
        if args.mode in ("none", "glossary"):
            self.agent = create_agent(llm, _sql_tools(llm, db), middleware=[_sql_prompt(db), MemoryContextMiddleware()],
                                      context_schema=UserContext)
            return
        svc = config.memory_service()
        svc.reset(step=2, start="2005-12-01")
        if args.mode == "memory":
            self.agent = build_agent(llm, db, svc)
            return
        import uvicorn

        from memory_demo.api import memory_tools
        from memory_demo.mcp_server import build_server
        from memory_demo.middleware import MemoryMiddleware
        from memory_demo.remote import MemoryClient

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(build_server(svc, require_identity=False, demo_today="2005-12-01")
                                                    .streamable_http_app(), host="127.0.0.1", port=port,
                                                    log_level="warning"))
        threading.Thread(target=self.server.run, daemon=True).start()
        while not self.server.started:
            time.sleep(0.05)
        client = MemoryClient(f"http://127.0.0.1:{port}/mcp")
        self.agent = create_agent(llm, _sql_tools(llm, db) + memory_tools(client),
                                  middleware=[_sql_prompt(db), MemoryMiddleware(client, note_in_answer=True)],
                                  context_schema=UserContext)

    def stop(self):
        if self.server is not None:
            self.server.should_exit = True
            self.server = None

    def turn(self, user: str, text: str, history: list | None = None) -> dict:
        sent = [*(history or []), HumanMessage(text)]
        state = {"messages": sent}
        if args.mode == "glossary":
            state["memory_context"] = GLOSSARY
        t0 = time.time()
        try:
            out = self.agent.invoke(state, context=UserContext(user_email=user, user_message=text))
        except Exception as e:  # one broken turn must not end the run
            return {"answer": f"ERROR {type(e).__name__}: {e}", "sql": [], "applied": [], "writes": [],
                    "messages": sent, "seconds": round(time.time() - t0, 1), "lookup_ms": None}
        new = out["messages"][len(sent):]
        trace = out.get("memory_trace") or {}
        return {"answer": text_of(out["messages"][-1]), "seconds": round(time.time() - t0, 1),
                "sql": [tc["args"].get("query", "") for m in new if isinstance(m, AIMessage) for tc in m.tool_calls
                        if tc["name"] == "sql_db_query"],
                "messages": out["messages"], "lookup_ms": trace.get("latency_ms"), "note": trace.get("note"),
                "applied": [(i.get("term"), i.get("scope"), i.get("matched_by")) for i in trace.get("items", [])],
                "writes": [(w["op"], w.get("term"), w.get("meaning"), w.get("shared_with"))
                           for w in out.get("memory_writes") or []]}


def check(setup: Setup, user: str, question: str, cond: str, dates: str) -> dict:
    want = reference(cond, dates)
    r = setup.turn(user, question)
    r.pop("messages", None)
    r.update(user=user.split("@")[0], question=question, reference=want, ok=want in numbers(r["answer"]))
    print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['user']}: {question}  want {want:,}  ({r['seconds']} s)", flush=True)
    if not r["ok"]:
        print("      answer:", r["answer"][:240].replace("\n", " "), flush=True)
    return r


setup = Setup()
runs = []
started = time.time()
for run in range(1, args.runs + 1):
    print(f"== {args.mode}, run {run} ==", flush=True)
    setup.start(run)
    result = {"seeded": [], "taught": []}
    for who, question, cond, dates, level in SEEDED:
        r = check(setup, who, question, cond, dates)
        r["level"] = level
        result["seeded"].append(r)
    for case in TAUGHT:
        history: list = []
        saved = []
        for msg in case["teach"]:
            t = setup.turn(case["teacher"], msg, history)
            history = t.pop("messages")
            saved += [w for w in t["writes"] if w[0] in ("add", "update", "team", "department", "same")]
        print(f"      {case['teacher'].split('@')[0]} taught “{case['term']}” ({case['style']}): saved {saved}",
              flush=True)
        for who, question, cond, dates in case["probes"]:
            r = check(setup, who, question, cond, dates)
            r.update(term=case["term"], style=case["style"], teacher=case["teacher"].split("@")[0], saved=saved)
            result["taught"].append(r)
    setup.stop()
    runs.append(result)

minutes = round((time.time() - started) / 60, 1)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.with_suffix(".json").write_text(json.dumps({"mode": args.mode, "runs": runs, "minutes": minutes}, indent=1,
                                               default=str), encoding="utf-8")
per_q: dict[str, list[bool]] = defaultdict(list)
for result in runs:
    for part in ("seeded", "taught"):
        for r in result[part]:
            per_q[f"{part}|{r['user']}|{r['question']}"].append(r["ok"])
seeded_ok = sum(r["ok"] for res in runs for r in res["seeded"])
taught_ok = sum(r["ok"] for res in runs for r in res["taught"])
n_seeded, n_taught = len(runs) * len(SEEDED), sum(len(res["taught"]) for res in runs)
lookups = sorted(r["lookup_ms"] for res in runs for p in ("seeded", "taught") for r in res[p] if r.get("lookup_ms"))
lines = [f"# Exactness: {args.mode}, {args.runs} run(s)", "",
         f"{minutes} min. Model from `.env`; Mem0 embeddings: `{args.embeddings or 'none'}`.", "",
         f"- Seeded meanings: **{seeded_ok} / {n_seeded}** answers exact",
         f"- Taught in conversation: **{taught_ok} / {n_taught}** answers exact",
         f"- All: **{seeded_ok + taught_ok} / {n_seeded + n_taught}**"]
if lookups:
    lines.append(f"- Memory lookup inside the turn: median {lookups[len(lookups) // 2]} ms, max {lookups[-1]} ms")
lines += ["", "| Question | Exact in runs |", "| --- | --- |"]
for key, oks in per_q.items():
    part, user, question = key.split("|", 2)
    lines.append(f"| {part}: {user}: {question} | {sum(oks)} / {len(oks)} |")
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\n{args.mode}: seeded {seeded_ok}/{n_seeded}, taught {taught_ok}/{n_taught}; wrote {OUT}")
