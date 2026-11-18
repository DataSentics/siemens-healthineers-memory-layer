"""Pilot set-up of the memory app's tables, run by an admin from their own machine: who is in the org directory, and
whose memory is switched on. Until AskFST's HR / Entra ID sync fills `org_people`, this is how people get in; the
memory app refuses users it cannot find there.

    databricks auth login --host https://<workspace> --profile <profile>        # once, in your own terminal
    python scripts/pilot_admin.py --profile <profile> --warehouse-id <id> --catalog <catalog> --schema shs_memory \
        people pilot_people.csv                     # shows what would change; add --yes to write it
    python scripts/pilot_admin.py <same options> consent eva@example.com petr@example.com [--share]
    python scripts/pilot_admin.py <same options> consent --off eva@example.com

people: the CSV becomes the whole directory. Columns as in seed/org.csv: email, name, department, team,
manager_email, is_manager (true/false), joined (an ISO date, or empty). A team name must be unique across
departments (e.g. "R&D / A"). Keep the file out of this repository: it lists real people.
consent: nothing is remembered for a user before remembering is on; --share also lets what they teach count towards
their team's meaning. Users can change it later themselves (`set_consent`).

The connection options go before `people` / `consent`. Needs MODIFY on org_people and memory_signals (the schema's
owner has it). `--sqlite <file>` works on a local memory database instead (the demo's data/memory.db), to try it out.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memory_demo.org import ORG_COLS, Directory, Person, read_csv  # noqa: E402
from memory_demo.service import MemoryService  # noqa: E402
from memory_demo.store import DatabricksExecutor, MemoryStore, SqliteExecutor  # noqa: E402


def check_people(path: str | Path) -> tuple[list[Person], list[str]]:
    """The people in the CSV, and what is wrong with it (nothing is written while anything is)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        header = csv.DictReader(f).fieldnames or []
    missing = [c for c in ORG_COLS if c not in header]
    if missing:
        return [], [f"missing columns: {', '.join(missing)} (expected {', '.join(ORG_COLS)})"]
    people, problems = read_csv(path), []
    for line, p in enumerate(people, start=2):
        problems += [f"line {line}: no {c}" for c in ("email", "department", "team") if not getattr(p, c)]
        if p.email and "@" not in p.email:
            problems.append(f"line {line}: {p.email!r} is not an email")
        if p.joined and not _is_date(p.joined):
            problems.append(f"line {line}: joined {p.joined!r} is not a date like 2026-11-18")
    counts = Counter(p.email.lower() for p in people if p.email)
    problems += [f"{e} is listed {n} times" for e, n in counts.items() if n > 1]
    departments: dict[str, set[str]] = defaultdict(set)
    for p in people:
        departments[p.team].add(p.department)
    problems += [f"team {t!r} is in several departments ({', '.join(sorted(d))}); give each its own name"
                 for t, d in departments.items() if t and len(d) > 1]
    if not people:
        problems.append("the file lists nobody")
    return people, problems


def load_people(directory: Directory, path: str | Path, write: bool) -> str:
    people, problems = check_people(path)
    if problems:
        raise SystemExit("Nothing written:\n  " + "\n  ".join(problems[:20]))
    before = {p.email.lower() for p in directory.people()}
    after = {p.email.lower() for p in people}
    summary = (f"{len(people)} people in {len({p.team for p in people})} teams and "
               f"{len({p.department for p in people})} departments: {len(after - before)} new, "
               f"{len(after & before)} already listed, {len(before - after)} no longer listed")
    if not write:
        return f"{summary}.\nNothing written: run again with --yes to make this the directory."
    directory.replace(people)
    return (f"{summary}.\nWritten. The memory app sees it within 5 minutes (a new person within 30 seconds). "
            "Memories of people no longer listed stay in the tables.")


def switch_consent(service: MemoryService, emails: list[str], remember: bool, share: bool) -> list[str]:
    unknown = [e for e in emails if not service.org.known(e)]
    if unknown:
        raise SystemExit(f"Nothing changed: not in the org directory (load them with `people` first): "
                         f"{', '.join(unknown)}")
    lines = []
    for email in emails:
        service.set_consent(email, remember, share)
        lines.append(f"{email}: {service.consent(email)}")
    return lines


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def executor(a: argparse.Namespace):
    if a.sqlite:
        return SqliteExecutor(a.sqlite)
    if not (a.warehouse_id and a.catalog):
        raise SystemExit("name the tables: --warehouse-id and --catalog (and --schema), or --sqlite <file>")
    if a.profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = a.profile
    return DatabricksExecutor(a.warehouse_id, a.catalog, a.schema, size=1)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", help="Databricks CLI profile")
    ap.add_argument("--warehouse-id")
    ap.add_argument("--catalog")
    ap.add_argument("--schema", default="shs_memory")
    ap.add_argument("--sqlite", help="a local memory database instead of Databricks")
    sub = ap.add_subparsers(dest="command", required=True)
    people = sub.add_parser("people", help="make a CSV the org directory")
    people.add_argument("csv")
    people.add_argument("--yes", action="store_true", help="write it (without: only show what would change)")
    consent = sub.add_parser("consent", help="switch memory on (or off) for these users")
    consent.add_argument("emails", nargs="+")
    choice = consent.add_mutually_exclusive_group()
    choice.add_argument("--share", action="store_true", help="also let what they teach count for their team")
    choice.add_argument("--off", action="store_true", help="switch remembering off")
    a = ap.parse_args(argv)

    ex = executor(a)
    directory = Directory(ex, None)
    if a.command == "people":
        print(load_people(directory, a.csv, a.yes))
    else:
        service = MemoryService(MemoryStore(ex), directory)
        print("\n".join(switch_consent(service, a.emails, remember=not a.off, share=a.share)))


if __name__ == "__main__":
    main()
