"""What gets remembered from one chat message: our extractor + memory rules vs Mem0's own extraction prompt.

Each case in evals/capture_cases.json is one message from Tomas Vrba (R&D / A, pilot month, memory and sharing on),
run against a fresh memory. Ours: the real extractor and MemoryService.apply, so the outcome is what would be stored
(shared with the team, kept for the user only, or nothing). Memory keeps shared vocabulary only, so anything about the
user themself (preferences, what they look after, likes, a name only they use) is expected to leave nothing.
Mem0: its default fact extraction prompt (mem0 1.x, USER_MEMORY_EXTRACTION_PROMPT) on the same exchange; it only says
which facts it would store, not who sees them.

    LLM_PROVIDER=databricks DATABRICKS_LLM_ENDPOINT=databricks-claude-sonnet-5-5 DATABRICKS_CONFIG_PROFILE=<profile> \\
        python scripts/eval_capture.py [--runs 2] [--out evals/capture_last_run.md]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEM0_TELEMETRY", "False")

from memory_demo import config, seeding  # noqa: E402
from memory_demo.extractor import extract, extract_structured  # noqa: E402
from memory_demo.middleware import message_text  # noqa: E402
from memory_demo.org import Org  # noqa: E402
from memory_demo.service import MemoryService  # noqa: E402
from memory_demo.store import MemoryStore, SqliteExecutor  # noqa: E402

USER = "tomas.vrba@shs-demo.example"
ANSWER = "Here are the results for your question."


def ours(case: dict, llm, org: Org, workdir: str, extraction: str = "mem0") -> dict:
    # A fresh memory per run. Not named by time: on Windows time_ns() changes only every few milliseconds, so runs
    # started together shared one memory and saw each other's definitions.
    store = MemoryStore(SqliteExecutor(os.path.join(workdir, f"{case['id']}-{uuid.uuid4().hex}.db")))
    seeding.reset(store, org, step=2, start="2005-12-01")
    svc = MemoryService(store, org, llm)
    _, trace = svc.retrieve(USER, case["msg"])
    applied = trace.to_dict()["items"]
    own = [vars(i) for i in store.own_items(USER)]
    decide = extract_structured if extraction == "structured" else extract
    result = decide(llm, case.get("prev"), case["msg"], ANSWER, applied, own, sql=[], recent=[])
    writes = svc.apply(USER, result.ops, applied)
    saved = []
    folded = {i for w in writes for i in w.folded_ids}  # a personal copy that became the team's is one item
    for w in writes:
        if w.op in ("add", "update", "team", "department") and w.item_id not in folded:
            item = store.get_item(w.item_id)
            shared = w.op in ("team", "department") or bool(item and item.term and item.shareable)
            saved.append({"op": w.op, "type": w.item_type, "term": w.term, "meaning": w.meaning, "shared": shared})
    dropped = [f"{o.evidence}: {o.term or o.item_type}" for o in result.ops if o.evidence == "inferred"]
    if any(s["shared"] for s in saved):
        outcome = "shared"
    elif saved:
        outcome = "personal"
    elif any(w.op == "same" for w in writes):
        outcome = "same"
    else:
        outcome = "none"
    return {"outcome": outcome, "saved": saved, "dropped": dropped,
            "ops": [o.model_dump(include={"op", "item_type", "term", "meaning", "evidence", "is_joke"})
                    for o in result.ops]}


def mem0_facts(case: dict, llm) -> list[str]:
    from mem0.memory.utils import get_fact_retrieval_messages, parse_messages

    messages = ([{"role": "assistant", "content": case["prev"]}] if case.get("prev") else []) + [
        {"role": "user", "content": case["msg"]}, {"role": "assistant", "content": ANSWER}]
    system, user = get_fact_retrieval_messages(parse_messages(messages))
    msg = llm.invoke([("system", system + "\nAnswer with the JSON object only."), ("human", user)])
    text = message_text(msg)  # Claude on Databricks can wrap the text in reasoning blocks
    text = text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(text).get("facts", [])
    except ValueError:
        return [f"(unparsed: {text[:80]})"]


def correct(expect: str, outcome: str) -> bool:
    return {"shared": outcome == "shared", "none": outcome in ("none", "same")}[expect]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "evals" / "capture_last_run.md"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--extraction", choices=("mem0", "structured"), default="mem0",
                    help="structured: the same rules as one structured tool call (extractor.extract_structured)")
    a = ap.parse_args()
    cases = json.loads((ROOT / "evals" / "capture_cases.json").read_text(encoding="utf-8"))["cases"]
    llm, org = config.llm(), Org.from_csv(ROOT / "seed" / "org.csv")
    workdir = tempfile.mkdtemp(prefix="eval_capture_")
    jobs = [(c, r) for r in range(a.runs) for c in cases]
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as pool:
        our_runs = list(pool.map(lambda j: ours(j[0], llm, org, workdir, a.extraction), jobs))
        facts = list(pool.map(lambda c: mem0_facts(c, llm), cases))
    by_case: dict[str, list[dict]] = {}
    for (c, _), res in zip(jobs, our_runs):
        by_case.setdefault(c["id"], []).append(res)

    lines = [f"# What gets remembered: {len(cases)} messages, {a.runs} runs of ours, 1 of Mem0 "
             f"({time.time() - t0:.0f} s)", "", f"Extraction: {a.extraction}.", "",
             "| Case | Kind | Expected | Ours (each run) | What we saved (run 1) | Mem0 would store |",
             "| --- | --- | --- | --- | --- | --- |"]
    stats = {k: [0, 0] for k in ("shared", "none")}
    leaks, mem0_fp, mem0_hits = [], 0, 0
    for c, f in zip(cases, facts):
        runs = by_case[c["id"]]
        for r in runs:
            stats[c["expect"]][0] += correct(c["expect"], r["outcome"])
            stats[c["expect"]][1] += 1
            if r["outcome"] == "shared" and c["expect"] != "shared":
                leaks.append(c["id"])
        if c["expect"] == "none" and f:
            mem0_fp += 1
        if c["expect"] == "shared" and f:
            mem0_hits += 1
        mark = " ".join(("✓" if correct(c["expect"], r["outcome"]) else "✗") + r["outcome"] for r in runs)
        saved = "; ".join(f"{s['type']} {s['term'] or ''}: {s['meaning'][:60]}{' (team)' if s['shared'] else ''}"
                          for s in runs[0]["saved"]) or "–"
        lines.append(f"| {c['id']} | {c['kind']} | {c['expect']} | {mark} | {saved} | "
                     f"{'; '.join(x[:60] for x in f) or '–'} |")
    n_none = sum(c["expect"] == "none" for c in cases)
    n_pos = sum(c["expect"] == "shared" for c in cases)
    lines += ["", "## Summary", ""]
    for k, (ok, n) in stats.items():
        lines.append(f"- Ours, expected {k}: {ok} of {n} runs right")
    lines.append(f"- Ours, something shared with the team that should not be: {len(leaks)} ({', '.join(leaks) or 'none'})")
    lines.append(f"- Mem0, would store something for a message that needs nothing: {mem0_fp} of {n_none}")
    lines.append(f"- Mem0, would store something for a message that needs it: {mem0_hits} of {n_pos}")
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (Path(a.out).with_suffix(".json")).write_text(json.dumps({"ours": by_case, "mem0": dict(zip(
        [c["id"] for c in cases], facts))}, indent=1, ensure_ascii=False), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console is cp1252
    print("\n".join(lines))


if __name__ == "__main__":
    main()
