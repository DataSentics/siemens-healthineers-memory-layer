"""Pilot set-up scripts: loading people into the org directory, switching consent on, staging the memory app."""

import importlib.util
from pathlib import Path

import pytest

from memory_demo.org import ORG_COLS

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


admin, staging = script("pilot_admin"), script("stage_memory_app")


def write_csv(path, rows, header=ORG_COLS, bom=False):
    lines = [",".join(header)] + [",".join(r) for r in rows]
    path.write_text(("﻿" if bom else "") + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def person(n, team="Ops / A", department="Ops", joined="2026-11-01"):
    return [f"p{n}@corp.example", f"Person {n}", department, team, "p0@corp.example", "true" if n == 0 else "false",
            joined]


def run(db, *args, capsys):
    admin.main(["--sqlite", str(db), *args])
    return capsys.readouterr().out


def test_people_are_shown_first_then_written_and_replace_the_directory(tmp_path, capsys):
    db = tmp_path / "memory.db"
    first = write_csv(tmp_path / "first.csv", [person(n) for n in range(45)])  # more than one INSERT
    out = run(db, "people", str(first), capsys=capsys)
    assert "45 people in 1 teams and 1 departments: 45 new" in out and "Nothing written" in out
    assert admin.Directory(admin.SqliteExecutor(str(db)), None).people() == []
    out = run(db, "people", str(first), "--yes", capsys=capsys)
    assert "45 new" in out and "Written" in out
    second = write_csv(tmp_path / "second.csv", [person(n) for n in range(40, 50)], bom=True)
    out = run(db, "people", str(second), "--yes", capsys=capsys)
    assert "5 new, 5 already listed, 40 no longer listed" in out
    directory = admin.Directory(admin.SqliteExecutor(str(db)), None)
    assert sorted(p.email for p in directory.people()) == sorted(f"p{n}@corp.example" for n in range(40, 50))


@pytest.mark.parametrize("rows, header, problem", [
    ([person(0)], ORG_COLS[:-1], "missing columns: joined"),
    ([person(0), person(0)], ORG_COLS, "p0@corp.example is listed 2 times"),
    ([person(0), person(1, department="R&D")], ORG_COLS, "team 'Ops / A' is in several departments"),
    ([person(0, joined="1.11.2026")], ORG_COLS, "is not a date"),
    ([["", "Nobody", "Ops", "Ops / A", "", "false", ""]], ORG_COLS, "line 2: no email"),
    ([], ORG_COLS, "lists nobody"),
])
def test_a_bad_csv_writes_nothing(tmp_path, capsys, rows, header, problem):
    db = tmp_path / "memory.db"
    with pytest.raises(SystemExit, match=problem):
        run(db, "people", str(write_csv(tmp_path / "bad.csv", rows, header)), "--yes", capsys=capsys)
    assert admin.Directory(admin.SqliteExecutor(str(db)), None).people() == []


def test_spaces_around_values_are_ignored(tmp_path):
    path = write_csv(tmp_path / "p.csv", [[" p0@corp.example ", " Ann ", "Ops", "Ops / A", "", " TRUE ", ""]])
    [p], problems = admin.check_people(path)
    assert problems == [] and p.email == "p0@corp.example" and p.is_manager and p.joined == ""


def test_consent_is_switched_for_known_people_only(tmp_path, capsys):
    db = tmp_path / "memory.db"
    run(db, "people", str(write_csv(tmp_path / "p.csv", [person(n) for n in range(3)])), "--yes", capsys=capsys)
    with pytest.raises(SystemExit, match="stranger@corp.example"):
        run(db, "consent", "p1@corp.example", "stranger@corp.example", capsys=capsys)
    ex = admin.SqliteExecutor(str(db))
    service = admin.MemoryService(admin.MemoryStore(ex), admin.Directory(ex, None))
    assert service.consent("p1@corp.example") is None  # nothing changed

    out = run(db, "consent", "P1@corp.example", "p2@corp.example", "--share", capsys=capsys)
    assert "{'remember': True, 'share': True}" in out
    assert service.consent("p2@corp.example") == {"remember": True, "share": True}
    run(db, "consent", "p2@corp.example", capsys=capsys)
    assert service.consent("p2@corp.example") == {"remember": True, "share": False}
    run(db, "consent", "--off", "p2@corp.example", capsys=capsys)
    assert service.consent("p2@corp.example") == {"remember": False, "share": False}
    assert service.consent("p0@corp.example") is None


def test_the_memory_app_folder_holds_only_what_the_server_needs(tmp_path):
    out = tmp_path / "mcp-app"
    staging.stage(out)
    assert {p.name for p in out.iterdir()} == {"app.yaml", "requirements.txt", "memory_demo"}
    assert (out / "app.yaml").read_text(encoding="utf-8").count("memory_demo.mcp_server") == 1
    requirements = (out / "requirements.txt").read_text(encoding="utf-8")
    assert "mcp>=" in requirements and "mem0ai" in requirements and "streamlit" not in requirements
    assert not list(out.rglob("__pycache__"))
    staging.stage(out)  # an earlier staged folder is replaced
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "notes.txt").write_text("keep me")
    with pytest.raises(SystemExit, match="holds other files"):
        staging.stage(tmp_path / "other")
    assert (tmp_path / "other" / "notes.txt").exists()
