"""Create the demo schema in Databricks: upload BGL Parquet to a volume, build the Delta table with comments,
create the memory tables and the org directory table. Needs only a SQL warehouse (no cluster).

    databricks auth login --host https://<workspace>      # once, in your own terminal
    python scripts/setup_databricks.py --profile <profile> --catalog <catalog> --schema shs_memory_demo \
        --warehouse-id <id> [--app-sp <service-principal-application-id>] [--only-org]

--only-org: only create the org directory table and grant the app MODIFY on it (for an existing deployment). The
app fills it from seed/org.csv on its first start.
--only-memory: only the memory tables and the org directory table, with the grants (no demo data). For a memory
service on real data, e.g. --catalog <AskFST's catalog> --schema shs_memory. Run by someone who may create a schema
in that catalog and grant on it.
"""

import argparse
import sys
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memory_demo.bgl import COLUMNS, TABLE, TABLE_COMMENT  # noqa: E402
from memory_demo.store import DDL, ORG_DDL  # noqa: E402

CASTS = {"TIMESTAMP": "CAST({c} AS TIMESTAMP)", "DATE": "CAST({c} AS DATE)", "BOOLEAN": "CAST({c} AS BOOLEAN)"}


def sql(w: WorkspaceClient, warehouse: str, stmt: str) -> None:
    print(">", stmt.splitlines()[0][:110])
    r = w.statement_execution.execute_statement(statement=stmt, warehouse_id=warehouse, wait_timeout="50s")
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(3)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        raise SystemExit(f"failed: {r.status.error.message if r.status.error else r.status.state}")


def q(s: str) -> str:
    return "'" + s.replace("'", "\\'") + "'"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--schema", default="shs_memory_demo")
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--parquet", default=str(ROOT / "data" / "bgl_events.parquet"))
    ap.add_argument("--app-sp", help="application id of the Databricks App's service principal, to grant access")
    ap.add_argument("--only-org", action="store_true", help="only the org directory table (and its grant)")
    ap.add_argument("--only-memory", action="store_true", help="only the memory and org tables, no demo data")
    a = ap.parse_args()

    w = WorkspaceClient(profile=a.profile)
    fq = f"`{a.catalog}`.`{a.schema}`"
    org_ddl = ORG_DDL.replace("{T}", "STRING").replace("IF NOT EXISTS ", f"IF NOT EXISTS {fq}.")
    if a.only_org:
        sql(w, a.warehouse_id, org_ddl)
        if a.app_sp:
            sql(w, a.warehouse_id, f"GRANT MODIFY ON TABLE {fq}.org_people TO `{a.app_sp}`")
        print("done")
        return
    sql(w, a.warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {fq}")
    if not a.only_memory:
        load_demo_data(w, a, fq)

    for stmt in DDL:
        sql(w, a.warehouse_id, stmt.replace("{T}", "STRING").replace("IF NOT EXISTS ", f"IF NOT EXISTS {fq}."))
    sql(w, a.warehouse_id, org_ddl)

    if a.app_sp:
        sp = f"`{a.app_sp}`"
        for g in (f"GRANT USE CATALOG ON CATALOG `{a.catalog}` TO {sp}",
                  f"GRANT USE SCHEMA, SELECT ON SCHEMA {fq} TO {sp}",
                  f"GRANT MODIFY ON TABLE {fq}.memory_items TO {sp}",
                  f"GRANT MODIFY ON TABLE {fq}.memory_signals TO {sp}",
                  f"GRANT MODIFY ON TABLE {fq}.casebook TO {sp}",
                  f"GRANT MODIFY ON TABLE {fq}.org_people TO {sp}"):
            sql(w, a.warehouse_id, g)
    print("done")


def load_demo_data(w: WorkspaceClient, a, fq: str) -> None:
    """The BGL event table of the demo: upload the Parquet file to a volume and build the Delta table."""
    sql(w, a.warehouse_id, f"CREATE VOLUME IF NOT EXISTS {fq}.raw")

    vol_path = f"/Volumes/{a.catalog}/{a.schema}/raw/bgl_events.parquet"
    print(f"> upload {a.parquet} -> {vol_path}")
    with open(a.parquet, "rb") as f:
        w.files.upload(vol_path, f, overwrite=True)

    cols = ",\n  ".join(f"{n} {t} COMMENT {q(c)}" for n, t, c in COLUMNS)
    sql(w, a.warehouse_id, f"CREATE OR REPLACE TABLE {fq}.{TABLE} (\n  {cols}\n) COMMENT {q(TABLE_COMMENT)}")
    select = ", ".join(CASTS.get(t, "{c}").format(c=n) for n, t, _ in COLUMNS)
    sql(w, a.warehouse_id, f"INSERT INTO {fq}.{TABLE} SELECT {select} FROM parquet.`{vol_path}`")


if __name__ == "__main__":
    main()
