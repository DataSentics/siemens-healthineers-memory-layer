"""Read-only check for the SQL agent's queries (the idea from Michal's system: parse with sqlglot, don't trust the prompt).

The app's service principal may write to the memory tables, and the SQL agent queries through the same identity, so
the prompt rule "no DML" is not enough. Every query is parsed before it runs and must be:
- exactly one statement,
- a query (SELECT, optionally WITH / UNION), with no write, DDL or command anywhere in it,
- over the event table only. The memory tables are off limits for reading too: they hold other people's memory.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from .bgl import TABLE

DIALECTS = {"databricks": "databricks", "sqlite": "sqlite"}
_WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
           exp.Command, exp.Grant, exp.Set, exp.Use, exp.Copy, exp.LoadData)


def problem(sql: str, dialect: str = "databricks") -> str | None:
    """Why the query may not run, or None when it is a read-only query over the event table."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECTS.get(dialect, dialect)) if s is not None]
    except sqlglot.errors.ParseError as e:
        return f"the query could not be parsed ({str(e).splitlines()[0][:120]})"
    if len(statements) != 1:
        return "send exactly one statement"
    root = statements[0]
    if not isinstance(root, exp.Query):
        return f"only SELECT queries are allowed, not {root.key.upper()}"
    if any(isinstance(node, _WRITES) for node in root.walk()):
        return "the query contains a statement that changes data"
    ctes = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    tables = {t.name.lower() for t in root.find_all(exp.Table)} - ctes
    others = sorted(tables - {TABLE.lower()})
    if others:
        return f"only the table {TABLE} may be queried, not {', '.join(others)}"
    return None
