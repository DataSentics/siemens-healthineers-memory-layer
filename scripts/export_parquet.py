"""Export the parsed BGL table to Parquet for upload to a Unity Catalog volume.

    python scripts/export_parquet.py data/bgl.db data/bgl_events.parquet
"""

import sqlite3
import sys

import pyarrow as pa
import pyarrow.parquet as pq

src, dst = sys.argv[1], sys.argv[2]
con = sqlite3.connect(src)
cur = con.execute("SELECT * FROM bgl_events ORDER BY event_id")
names = [d[0] for d in cur.description]
writer = None
while rows := cur.fetchmany(500_000):
    table = pa.Table.from_pylist([dict(zip(names, r)) for r in rows])
    writer = writer or pq.ParquetWriter(dst, table.schema, compression="zstd")
    writer.write_table(table.cast(writer.schema))
writer.close()
print(f"wrote {pq.ParquetFile(dst).metadata.num_rows} rows -> {dst}")
