"""Tally the exactness runs (evals/**/exactness_*.json, the dated folders included) by kind of question: a team's or department's meaning, an official
term, or a term taught in conversation.

    python scripts/eval_summary.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
rows = []
for f in sorted((HERE / "evals").rglob("exactness_*.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    kinds = {"team / department": [0, 0], "official": [0, 0], "taught": [0, 0]}
    for run in d["runs"]:
        for r in run["seeded"]:
            k = "official" if r["level"].startswith("official") else "team / department"
            kinds[k][0] += r["ok"]
            kinds[k][1] += 1
        for r in run["taught"]:
            kinds["taught"][0] += r["ok"]
            kinds["taught"][1] += 1
    total = [sum(v[0] for v in kinds.values()), sum(v[1] for v in kinds.values())]
    name = f.relative_to(HERE / "evals").with_name(f.stem.removeprefix("exactness_")).as_posix()
    rows.append((name, len(d["runs"]), kinds, total))

print("| Setup | Runs | Team / department meanings | Official terms | Taught in conversation | All |")
print("| --- | --- | --- | --- | --- | --- |")
for name, runs, kinds, total in rows:
    cells = " | ".join(f"{ok} / {n}" for ok, n in kinds.values())
    print(f"| {name} | {runs} | {cells} | **{total[0]} / {total[1]}** ({total[0] / total[1]:.0%}) |")
