"""Put together the folder the memory app is deployed from, and print the two commands that deploy it.

A Databricks App reads `app.yaml` from the root of its source folder, and the root of this project holds the demo's,
so the memory app gets a folder of its own: `deploy/mcp/app.yaml` as its `app.yaml`, the `memory_demo` package, and a
`requirements.txt` with the server's dependencies (from pyproject.toml). Nothing else: no data, no `.env`, no demo
app, no seed files.

    python scripts/stage_memory_app.py --app mcp-shs-memory --workspace-path /Workspace/Users/<you>/mcp-shs-memory

The folder is outside this repository (default: <temp>/mcp-shs-memory) because `databricks sync` skips whatever the
surrounding git repository ignores, and `build/` is ignored here: synced from inside it, nothing would be uploaded.
Fill in MEMORY_CATALOG (and, once known, MEMORY_TRUSTED_CALLERS) in deploy/mcp/app.yaml first; see INTEGRATION.md.
"""

import argparse
import re
import shutil
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGED = {"app.yaml", "requirements.txt", "memory_demo", ".databricks"}  # .databricks: the CLI's sync state


def stage(out: Path) -> list[str]:
    """Write the app's folder to `out` and return warnings (settings still empty, a folder inside a git repo)."""
    if out.exists():
        if not {p.name for p in out.iterdir()} <= STAGED:  # only ever replace an earlier staged folder
            raise SystemExit(f"{out} holds other files: name a new folder")
        shutil.rmtree(out / "memory_demo", ignore_errors=True)
    shutil.copytree(ROOT / "memory_demo", out / "memory_demo", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    app_yaml = (ROOT / "deploy" / "mcp" / "app.yaml").read_text(encoding="utf-8")
    (out / "app.yaml").write_text(app_yaml, encoding="utf-8")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = [*project["dependencies"], *project["optional-dependencies"]["server"]]
    (out / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")
    warnings = [f"{name} is empty in deploy/mcp/app.yaml" for name in ("MEMORY_CATALOG", "MEMORY_TRUSTED_CALLERS")
                if re.search(rf"name: {name}\s+value: \"\"", app_yaml)]
    if any((p / ".git").exists() for p in out.resolve().parents):
        warnings.append(f"{out} is inside a git repository: `databricks sync` skips the files it ignores")
    return warnings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "mcp-shs-memory"), help="folder to write")
    ap.add_argument("--app", default="mcp-shs-memory", help="the Databricks App's name (starts with mcp-)")
    ap.add_argument("--workspace-path", default="/Workspace/Users/<you>/mcp-shs-memory",
                    help="workspace folder the code is synced to")
    ap.add_argument("--profile", default="<profile>", help="Databricks CLI profile, for the printed commands")
    a = ap.parse_args()
    out = Path(a.out)
    warnings = stage(out)
    print(f"Staged the memory app in {out}: app.yaml, requirements.txt, memory_demo/")
    for w in warnings:
        print(f"warning: {w}")
    print("Deploy it with (in Git Bash, set MSYS_NO_PATHCONV=1 first so /Workspace paths survive):")
    print(f'  databricks sync "{out}" {a.workspace_path} --full --profile {a.profile}')
    print(f"  databricks apps deploy {a.app} --source-code-path {a.workspace_path} --profile {a.profile}")


if __name__ == "__main__":
    main()
