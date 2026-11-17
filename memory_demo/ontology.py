"""A small ontology of the BGL machine, built from the data with no human review (PRD A9).

- Component hierarchy from the location column: rack → midplane → node card → card (compute / I/O / link)
- Alert families: every alert label belongs to exactly one logging component in the data, so the component is the family
- Example message templates per alert label

`scripts/build_ontology.py` writes it to seed/ontology.json once; both backends read that file. The SQL agent gets
a short summary in its prompt and the `describe_bgl_entity` tool for lookups.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from .bgl import TABLE

PATH = Path(__file__).resolve().parents[1] / "seed" / "ontology.json"
TOP_TEMPLATES = 60
Run = Callable[[str], list[tuple]]


def build(run: Run) -> dict[str, Any]:
    """`run(sql)` returns rows as tuples. Takes a few seconds on the full table."""
    racks: dict[str, Any] = defaultdict(lambda: {"events": 0, "midplanes": defaultdict(lambda: {"events": 0, "node_cards": {}})})
    for rack, mid, nc, n in run(f"SELECT rack, midplane, node_card, COUNT(*) FROM {TABLE} WHERE rack IS NOT NULL "
                                "GROUP BY rack, midplane, node_card"):
        racks[rack]["events"] += n
        if mid:
            racks[rack]["midplanes"][mid]["events"] += n
            if nc:
                racks[rack]["midplanes"][mid]["node_cards"][nc] = n
    cards = {t: n for t, n in run(f"SELECT card_type, COUNT(*) FROM {TABLE} GROUP BY card_type")}
    components: dict[str, Any] = defaultdict(lambda: {"events": 0, "alert_labels": {}})
    for comp, n in run(f"SELECT component, COUNT(*) FROM {TABLE} GROUP BY component"):
        components[comp]["events"] = n
    labels: dict[str, Any] = {}
    for label, comp, n in run(f"SELECT alert_label, component, COUNT(*) FROM {TABLE} WHERE alert_label IS NOT NULL "
                              "GROUP BY alert_label, component ORDER BY alert_label, COUNT(*) DESC"):
        if label not in labels:  # the dominant component is the family
            labels[label] = {"component": comp, "events": n, "examples": []}
            components[comp]["alert_labels"][label] = n
    for label, tpl, n in run(f"SELECT alert_label, event_template, COUNT(*) FROM {TABLE} WHERE alert_label IS NOT NULL "
                             "GROUP BY alert_label, event_template ORDER BY alert_label, COUNT(*) DESC"):
        if len(labels[label]["examples"]) < 2:
            labels[label]["examples"].append(tpl)
    # The most common message wordings, so filters for user terms match real messages (they cover ~80% of events)
    templates = [{"template": tpl[:160], "level": lvl, "events": n} for tpl, lvl, n in run(
        f"SELECT event_template, level, COUNT(*) FROM {TABLE} GROUP BY event_template, level "
        f"ORDER BY COUNT(*) DESC LIMIT {TOP_TEMPLATES}")]
    return {"racks": racks, "card_types": cards, "components": components, "alert_labels": labels,
            "templates": templates}


@lru_cache
def load() -> dict[str, Any]:
    return json.loads(PATH.read_text(encoding="utf-8"))


def summary(onto: dict[str, Any] | None = None) -> str:
    """Prompt section for the SQL agent."""
    o = onto or load()
    racks = sorted(o["racks"])
    families = []
    for comp, c in sorted(o["components"].items(), key=lambda kv: -kv[1]["events"]):
        if c["alert_labels"]:
            top = sorted(c["alert_labels"], key=lambda k: -c["alert_labels"][k])[:6]
            more = len(c["alert_labels"]) - len(top)
            families.append(f"{comp}: {', '.join(top)}{f' (+{more} more)' if more > 0 else ''}")
    return "\n".join([
        "## Ontology of the machine (built from the data)",
        f"- Hierarchy: rack ({len(racks)}, {racks[0]}..{racks[-1]}, column rack) → midplane (2 per rack, column midplane, "
        "values like 'R37-M1') → node card (16 per midplane, column node_card, values like 'R37-M1-N4') → card "
        "(column card_type: COMPUTE, IO, LINK; NODE_CARD = the node card itself). Location: R37-M1-N4-C:J12-U01.",
        "- A problem of a part includes the problems of everything inside it (e.g. node card problems include its cards).",
        "- Alert families (alert_label by logging component): " + "; ".join(families),
        "- Use the describe_bgl_entity tool to look up a rack, midplane, node card, alert label or component.",
    ])


def templates(onto: dict[str, Any] | None = None) -> str:
    """Prompt section: the most common message wordings. Filter on these, not on guessed words."""
    o = onto or load()
    lines = [f"- [{t['level']}] {t['template']} ({t['events']:,})" for t in o.get("templates", [])]
    return ("## Most common messages (event_template, numbers replaced by <N>; level; events)\n"
            "Build term filters from these wordings, e.g. message LIKE '%ddr error%', not from words the user used.\n"
            + "\n".join(lines))


def describe(entity: str, onto: dict[str, Any] | None = None) -> str:
    o = onto or load()
    e = entity.strip().upper().replace(" ", "")
    if m := re.fullmatch(r"(R[0-9A-F]{2})(?:-(M\d))?(?:-(N[0-9A-F]))?", e):
        rack, mid, nc = m.groups()
        r = o["racks"].get(rack)
        if not r:
            return f"No rack {rack} in the data."
        if not mid:
            mids = ", ".join(f"{k} ({v['events']:,} events)" for k, v in sorted(r["midplanes"].items()))
            return f"Rack {rack}: {r['events']:,} events. Contains midplanes {mids}. Filter: rack = '{rack}'."
        mid, nc = f"{rack}-{mid}", nc and f"{rack}-{mid}-{nc}"  # stored as full paths
        mp = r["midplanes"].get(mid)
        if not mp:
            return f"No midplane {mid} in the data."
        if not nc:
            top = sorted(mp["node_cards"].items(), key=lambda kv: -kv[1])[:5]
            return (f"Midplane {mid}: {mp['events']:,} events, part of rack {rack}. Contains 16 node cards; "
                    f"busiest: {', '.join(f'{k} ({v:,})' for k, v in top)}. Filter: midplane = '{mid}'.")
        n = mp["node_cards"].get(nc)
        return (f"Node card {nc}: {n or 0:,} events, part of midplane {mid}. Contains compute (C) and I/O (I) cards. "
                f"Filter: node_card = '{nc}'.")
    if e in o["alert_labels"]:
        a = o["alert_labels"][e]
        ex = "; ".join(a["examples"])
        return (f"Alert label {e}: {a['events']:,} events, family {a['component']}. Typical messages: {ex}. "
                f"Filter: alert_label = '{e}'.")
    if e in o["components"]:
        c = o["components"][e]
        labels = ", ".join(sorted(c["alert_labels"], key=lambda k: -c["alert_labels"][k])) or "none"
        return f"Component {e}: {c['events']:,} events. Alert labels in this family: {labels}. Filter: component = '{e}'."
    levels = {"RACK": "rack", "RACKS": "rack", "MIDPLANE": "midplane", "MIDPLANES": "midplane", "NODECARD": "node card",
              "NODECARDS": "node card", "CARD": "card", "CARDS": "card", "IONODE": "card", "IONODES": "card"}
    if e in levels:
        return summary(o).splitlines()[1].removeprefix("- ")
    return (f"“{entity}” is not a rack, midplane, node card, alert label or component in the data. "
            "Examples: R37, R37-M1, R37-M1-N4, KERNDTLB, KERNEL, node card.")
