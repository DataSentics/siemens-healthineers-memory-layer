"""Load BGL.log into a local SQLite table for offline development.

    python scripts/ingest_sqlite.py data/BGL.log data/bgl.db   # BGL.zip from https://zenodo.org/records/8196385

On Databricks: scripts/export_parquet.py, then scripts/setup_databricks.py (the same table as Delta, with comments).
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_demo.bgl import COLUMNS, TABLE, parse_line  # noqa: E402

SQLITE_TYPES = {"BIGINT": "INTEGER", "BOOLEAN": "INTEGER", "STRING": "TEXT", "TIMESTAMP": "TEXT", "DATE": "TEXT"}


def main(src: str, dst: str) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(dst)
    con.execute(f"DROP TABLE IF EXISTS {TABLE}")
    cols = ", ".join(f"{n} {SQLITE_TYPES[t]}" for n, t, _ in COLUMNS)
    con.execute(f"CREATE TABLE {TABLE} ({cols})")
    insert = f"INSERT INTO {TABLE} VALUES ({', '.join('?' * len(COLUMNS))})"
    batch, skipped = [], 0
    with open(src, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            row = parse_line(i, line)
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) == 50_000:
                con.executemany(insert, batch)
                batch.clear()
    con.executemany(insert, batch)
    for col in ("event_date", "component", "level", "alert_label", "rack", "node_card", "event_template"):
        con.execute(f"CREATE INDEX idx_{col} ON {TABLE}({col})")
    con.commit()
    n = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    print(f"loaded {n} rows, skipped {skipped}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
