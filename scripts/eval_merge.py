"""Merging two meanings of one term: the memory layer vs Mem0's own update phase, on hand-labelled pairs.

Each case in evals/merge_cases.json is a meaning memory already has for a term (some cases list the term's other
meanings too) and a meaning a user just stated for it; the label says whether the two select the same events.

- Memory layer: `consolidate.resolve` as it runs: the wording check, else Mem0's update phase (its prompt template and
  answer format) with our rule for definitions (`consolidate.MERGE_PROMPT`). Right for "same" when it matches the new
  meaning to that existing one (not to another meaning of the term); right for "different" when it matches none.
- Mem0 default: the same update phase with Mem0's own prompt, on the pair cases (with several meanings its answer
  lists them all as unchanged, so it cannot say which one holds the fact). NONE or UPDATE keep one memory, ADD keeps
  both, DELETE removes the old one.

Both compare the meanings as written down (Mem0 compares facts, not the user's message). Mem0 gets the meanings of
this term only; in its own add() it gets the 5 memories most similar to the fact.
An earlier run (18 Nov, evals/REPORT.md) compared these with the strict check this replaced.

    LLM_PROVIDER=databricks DATABRICKS_LLM_ENDPOINT=databricks-claude-sonnet-5-5 DATABRICKS_CONFIG_PROFILE=<profile> \\
        python scripts/eval_merge.py [--runs 2] [--out evals/merge_last_run.md]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEM0_TELEMETRY", "False")

from memory_demo import config, consolidate  # noqa: E402
from memory_demo.middleware import message_text  # noqa: E402
from memory_demo.models import MemoryItem  # noqa: E402

ARMS = ("layer", "mem0")
TITLES = {"layer": "Memory layer (Mem0 update phase, our prompt)", "mem0": "Mem0 default"}


def term_item(term: str, meaning: str) -> MemoryItem:
    return MemoryItem(item_type="term", term=term, meaning=meaning, owner_email=None, owner_team="R&D / A",
                      owner_department="R&D")


def layer(case: dict, llm) -> dict:
    old = term_item(case["term"], case["old"])
    known = [old, *(term_item(case["term"], m) for m in case.get("others", []))]
    res = consolidate.resolve(case["term"], case["new"], known, llm)
    found = "two" if res.same is None else ("one" if res.same is old else "other")
    return {"found": found, "how": res.how, "matched": res.same.meaning if res.same else None}


def mem0(case: dict, llm) -> dict:
    from mem0.configs.prompts import get_update_memory_messages

    text = get_update_memory_messages([{"id": "0", "text": f"{case['term']}: {case['old']}"}],
                                      [f"{case['term']}: {case['new']}"])
    raw = message_text(llm.invoke([("human", text)]))
    body = raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        entries = json.loads(body).get("memory", [])
    except ValueError:
        return {"event": "unparsed", "raw": raw[:300]}
    old = next((e for e in entries if str(e.get("id")) == "0"), {})
    e0 = (old.get("event") or "NONE").upper()
    added = any((e.get("event") or "").upper() == "ADD" for e in entries)
    if e0 == "DELETE":
        event = "DELETE+ADD" if added else "DELETE"
    elif e0 == "UPDATE":
        event = "UPDATE"
    else:
        event = "ADD" if added else "NONE"
    return {"event": event, "text": old.get("text") if e0 == "UPDATE" else None}


def run(case: dict, llm) -> dict:
    out = {}
    for name, fn in (("layer", layer), ("mem0", mem0)):
        if name == "mem0" and case.get("others"):
            continue
        try:
            out[name] = fn(case, llm)
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return out


def right(expect: str, arm: str, r: dict) -> bool:
    if arm == "mem0":
        ev = r.get("event")
        return ev in ("NONE", "UPDATE") if expect == "same" else ev == "ADD"
    return r.get("found") == ("one" if expect == "same" else "two")


def word(arm: str, r: dict) -> str:
    return r.get("event" if arm == "mem0" else "found") or "error"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "evals" / "merge_last_run.md"))
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    cases = json.loads((ROOT / "evals" / "merge_cases.json").read_text(encoding="utf-8"))["cases"]
    llm = config.llm()
    jobs = [(c, i) for i in range(a.runs) for c in cases]
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as pool:
        results = list(pool.map(lambda j: run(j[0], llm), jobs))
    by_case: dict[str, list[dict]] = {}
    for (c, _), r in zip(jobs, results):
        by_case.setdefault(c["id"], []).append(r)

    lines = [f"# Merging: {len(cases)} cases, {a.runs} runs ({time.time() - t0:.0f} s)", "",
             "Each cell: one mark per run. Memory layer: one (matched to the expected meaning), other (matched to "
             "another meaning of the term) or two (kept apart). Mem0 default: its event for the pair (NONE or UPDATE "
             "keep one memory, ADD keeps both, DELETE removes the old one); not run on cases with several meanings.",
             "", "| Case | Kind | Expected | " + " | ".join(TITLES[x] for x in ARMS) + " |",
             "| --- | --- | --- | " + " | ".join("---" for _ in ARMS) + " |"]
    stats = {arm: {"same": [0, 0], "different": [0, 0]} for arm in ARMS}
    events: dict[str, dict[str, int]] = {}
    for c in cases:
        cells = []
        for arm in ARMS:
            marks = []
            for r in by_case[c["id"]]:
                if arm not in r:
                    continue
                ok = right(c["expect"], arm, r[arm])
                stats[arm][c["expect"]][0] += ok
                stats[arm][c["expect"]][1] += 1
                marks.append(("✓" if ok else "✗") + word(arm, r[arm]))
                if arm == "mem0":
                    counts = events.setdefault(c["expect"], {})
                    counts[word(arm, r[arm])] = counts.get(word(arm, r[arm]), 0) + 1
            cells.append(" ".join(marks) or "–")
        lines.append(f"| {c['id']} | {c['kind']} | {c['expect']} | " + " | ".join(cells) + " |")
    lines += ["", "## Summary", "", "| | Same meaning merged | Different meanings kept apart |", "| --- | --- | --- |"]
    for arm in ARMS:
        s, d = stats[arm]["same"], stats[arm]["different"]
        lines.append(f"| {TITLES[arm]} | {s[0]} / {s[1]} | {d[0]} / {d[1]} |")
    lines.append("")
    for expect, counts in events.items():
        lines.append(f"- Mem0 default, expected {expect}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path(a.out).with_suffix(".json").write_text(json.dumps(by_case, indent=1, ensure_ascii=False), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console is cp1252
    print("\n".join(lines))


if __name__ == "__main__":
    main()
