"""Build seed/ontology.json from the local SQLite copy of BGL (see memory_demo/ontology.py).

    python scripts/build_ontology.py data/bgl.db
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_demo import ontology  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "data/bgl.db")
onto = ontology.build(lambda sql: con.execute(sql).fetchall())
ontology.PATH.write_text(json.dumps(onto, indent=1, sort_keys=True), encoding="utf-8")
print(f"{ontology.PATH}: {len(onto['racks'])} racks, {len(onto['alert_labels'])} alert labels")
print(ontology.summary(onto))
