import pytest

from memory_demo import sqlguard

# Queries the agents actually wrote in today's real-model runs, plus the usual shapes.
ALLOWED = [
    "SELECT COUNT(*) AS memory_errors FROM bgl_events WHERE (message LIKE '%ddr error%' OR message LIKE "
    "'%parity error%' OR message LIKE 'CE sym%') AND event_date >= '2005-12-01' AND event_date < '2006-01-01'",
    "SELECT COUNT(*) AS memory_errors FROM bgl_events WHERE (message LIKE '%ddr error%') AND event_date >= "
    "DATE '2005-12-01' AND event_date < DATE '2006-01-01'",
    "SELECT SUM(CASE WHEN alert_label IS NOT NULL THEN 1 ELSE 0 END) AS incidents, SUM(CASE WHEN LOWER(message) "
    "LIKE '%ddr error%' THEN 1 ELSE 0 END) AS memory_errors FROM bgl_events WHERE event_date >= '2005-12-01'",
    "SELECT event_template, level, COUNT(*) c FROM bgl_events GROUP BY event_template, level ORDER BY c DESC LIMIT 20",
    "WITH nc AS (SELECT node_card, COUNT(*) n FROM bgl_events WHERE midplane = 'R37-M1' GROUP BY node_card) "
    "SELECT * FROM nc ORDER BY n DESC LIMIT 5",
    "SELECT rack FROM bgl_events WHERE level = 'FATAL' UNION SELECT rack FROM bgl_events WHERE level = 'FAILURE'",
    "SELECT COUNT(*) FROM askar.shs_memory_demo.bgl_events",
    "SELECT COUNT(*) FROM bgl_events WHERE event_id IN (SELECT event_id FROM bgl_events WHERE rack = 'R37');",
]

REFUSED = [
    ("DELETE FROM memory_items", "SELECT"),
    ("UPDATE memory_items SET status = 'withdrawn'", "SELECT"),
    ("INSERT INTO casebook SELECT * FROM casebook", "SELECT"),
    ("DROP TABLE bgl_events", "SELECT"),
    ("CREATE TABLE x AS SELECT * FROM bgl_events", "SELECT"),
    ("MERGE INTO memory_items USING bgl_events ON 1=1 WHEN MATCHED THEN DELETE", "SELECT"),
    ("SELECT 1; DELETE FROM memory_items", "one statement"),
    ("SELECT * FROM memory_items", "memory_items"),
    ("SELECT term, meaning FROM memory_signals s JOIN bgl_events b ON 1=1", "memory_signals"),
    ("SELECT * FROM askar.shs_memory_demo.casebook", "casebook"),
    ("SELEKT * FROM bgl_events WHERE", "parsed"),
]


@pytest.mark.parametrize("sql", ALLOWED)
@pytest.mark.parametrize("dialect", ["databricks", "sqlite"])
def test_read_only_queries_over_the_event_table_run(sql, dialect):
    assert sqlguard.problem(sql, dialect) is None


@pytest.mark.parametrize("sql,why", REFUSED)
def test_writes_other_tables_and_several_statements_are_refused(sql, why):
    assert why in (sqlguard.problem(sql, "databricks") or "")


def test_the_sql_agent_uses_the_checked_tool(tmp_path):
    import sqlite3

    from langchain_community.utilities import SQLDatabase
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    from memory_demo.agent import ReadOnlyQueryTool, _sql_tools

    con = sqlite3.connect(tmp_path / "bgl.db")
    con.execute("CREATE TABLE bgl_events (event_id INTEGER, message TEXT)")
    con.execute("CREATE TABLE memory_items (item_id TEXT)")
    con.execute("INSERT INTO bgl_events VALUES (1, 'x')")
    con.commit()
    db = SQLDatabase.from_uri(f"sqlite:///{tmp_path / 'bgl.db'}")
    [query] = [t for t in _sql_tools(GenericFakeChatModel(messages=iter([])), db) if t.name == "sql_db_query"]
    assert isinstance(query, ReadOnlyQueryTool)
    assert query.invoke("SELECT COUNT(*) FROM bgl_events") == "[(1,)]"
    assert query.invoke("DELETE FROM memory_items").startswith("Error: query refused")
    assert sqlite3.connect(tmp_path / "bgl.db").execute("SELECT COUNT(*) FROM bgl_events").fetchone() == (1,)
