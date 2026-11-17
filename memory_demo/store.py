"""Persistence for memory items and signals.

Same SQL on both backends: SQLite locally, Delta tables through a Databricks SQL warehouse in the app.
Both drivers accept `:name` parameters with a dict. The storage model is deliberately simple (two tables, plus the casebook);
the real choice is K2 in Unresolved Questions.
"""

from __future__ import annotations

import atexit
import json
import queue
import sqlite3
import threading
import time
from dataclasses import asdict
from typing import Any, Protocol

from .models import MemoryItem, Signal

ITEM_COLS = ["item_id", "item_type", "term", "aliases", "meaning", "sql_hint", "origin", "owner_email",
             "owner_team", "owner_department", "shareable", "status", "supersedes", "created_at"]
SIGNAL_COLS = ["item_id", "user_email", "team", "department", "kind", "term", "created_at"]


Statement = tuple[str, dict[str, Any] | None]


class Executor(Protocol):
    def run(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def run_many(self, statements: list[Statement]) -> None:
        """Run write statements as one unit of work, as far as the backend can (see each executor)."""


class SqliteExecutor:
    """Local backend: one connection behind a lock. `run_many` is a real transaction: all statements or none."""

    text_type = "TEXT"  # STRING would get NUMERIC affinity in SQLite
    merges = False

    def __init__(self, path: str):
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.lock = threading.Lock()  # one connection, used by request threads and background signal writes

    def run(self, sql, params=None):
        with self.lock:
            try:
                cur = self.con.execute(sql, params or {})
                rows = [dict(r) for r in cur.fetchall()] if cur.description else []
                self.con.commit()
                return rows
            except Exception:
                self.con.rollback()
                raise

    def run_many(self, statements):
        with self.lock:
            try:
                for sql, params in statements:
                    self.con.execute(sql, params or {})
                self.con.commit()
            except Exception:
                self.con.rollback()
                raise


# The connector's defaults retry a failing request for up to 15 minutes, which looks like a frozen app. Fail
# within about a minute instead; a stale session is then replaced (see DatabricksExecutor).
CONNECT_LIMITS = {"_socket_timeout": 120, "_retry_stop_after_attempts_count": 3,
                  "_retry_stop_after_attempts_duration": 60}
IDLE_RECONNECT_S = 600  # warehouse sessions can expire while the app sits idle
POOL_WAIT_S = 60  # how long a request waits for a free connection before it fails


class _Session:
    """One pooled warehouse connection."""

    def __init__(self, con):
        self.con, self.last_used = con, time.monotonic()


class DatabricksExecutor:
    """Runs against a SQL warehouse through a small pool of connections, so requests of different users run side by
    side instead of queueing behind one connection. Auth comes from the Databricks SDK config (app service principal,
    CLI profile or env vars), so no token is handled here.

    A warehouse has no transactions over several statements. `run_many` therefore runs its statements in order on
    one connection; `MemoryStore.commit` keeps what must not be torn apart (new items and the status changes that go
    with them) in a single MERGE, which Delta applies atomically.

    `connect`: makes one connection (tests pass a fake); default: the warehouse from the SDK config."""

    text_type = "STRING"
    merges = True

    def __init__(self, warehouse_id: str, catalog: str, schema: str, size: int = 4, connect=None):
        self.warehouse_id, self.catalog, self.schema, self.size = warehouse_id, catalog, schema, max(1, size)
        self._connect = connect or self._warehouse
        self._cfg = None
        self._idle: queue.LifoQueue[_Session] = queue.LifoQueue()
        self._made, self._lock = 0, threading.Lock()
        self._release(self._acquire())  # fail at start-up, not on the first request, when the warehouse is unreachable
        atexit.register(self.close)

    def _warehouse(self):
        from databricks import sql
        from databricks.sdk.core import Config

        self._cfg = self._cfg or Config()
        cfg = self._cfg
        return sql.connect(
            server_hostname=cfg.host.removeprefix("https://"),
            http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
            credentials_provider=lambda: cfg.authenticate,
            catalog=self.catalog,
            schema=self.schema,
            **CONNECT_LIMITS,
        )

    # ---- the pool ------------------------------------------------------------------------------------------
    def _acquire(self) -> _Session:
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            grow = self._made < self.size
            self._made += grow
        if grow:
            try:
                return _Session(self._connect())
            except Exception:
                with self._lock:
                    self._made -= 1
                raise
        try:
            return self._idle.get(timeout=POOL_WAIT_S)
        except queue.Empty:
            raise TimeoutError(f"no free warehouse connection within {POOL_WAIT_S} s (pool of {self.size})") from None

    def _release(self, session: _Session) -> None:
        self._idle.put(session)

    def _reconnect(self, session: _Session) -> None:
        try:
            session.con.close()
        except Exception:  # the old session may already be gone
            pass
        session.con, session.last_used = self._connect(), time.monotonic()

    @staticmethod
    def _execute(session: _Session, sql: str, params) -> list[dict[str, Any]]:
        with session.con.cursor() as cur:
            cur.execute(sql, params or {})
            session.last_used = time.monotonic()
            if not cur.description:
                return []
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def close(self) -> None:
        """Close the idle connections (at shutdown)."""
        while True:
            try:
                session = self._idle.get_nowait()
            except queue.Empty:
                return
            try:
                session.con.close()
            except Exception:  # already gone
                pass
            with self._lock:
                self._made -= 1

    # ---- running statements --------------------------------------------------------------------------------
    def run(self, sql, params=None):
        session = self._acquire()
        try:
            if time.monotonic() - session.last_used > IDLE_RECONNECT_S:
                self._reconnect(session)
            # Reads are retried once on a fresh connection; writes are not, so a row is never written twice.
            attempts = 2 if sql.lstrip().upper().startswith("SELECT") else 1
            for attempt in range(attempts):
                try:
                    return self._execute(session, sql, params)
                except Exception:
                    self._reconnect(session)
                    if attempt == attempts - 1:
                        raise
        finally:
            self._release(session)

    def run_many(self, statements):
        session = self._acquire()
        try:
            if time.monotonic() - session.last_used > IDLE_RECONNECT_S:
                self._reconnect(session)
            for sql, params in statements:
                try:
                    self._execute(session, sql, params)
                except Exception:
                    self._reconnect(session)
                    raise
        finally:
            self._release(session)


DDL = [
    "CREATE TABLE IF NOT EXISTS memory_items (item_id {T}, item_type {T}, term {T}, aliases {T}, "
    "meaning {T}, sql_hint {T}, origin {T}, owner_email {T}, owner_team {T}, "
    "owner_department {T}, shareable BOOLEAN, status {T}, supersedes {T}, created_at {T})",
    "CREATE TABLE IF NOT EXISTS memory_signals (item_id {T}, user_email {T}, team {T}, "
    "department {T}, kind {T}, term {T}, created_at {T})",
    "CREATE TABLE IF NOT EXISTS casebook (case_id {T}, question {T}, sql_text {T}, owner_email {T}, "
    "owner_team {T}, owner_department {T}, status {T}, created_at {T})",
]
# The org directory (org.Directory): who is in which team and department. In production an HR / Entra ID sync
# writes it; the app only reads it, except for the demo's "Add a team" and reset.
ORG_DDL = ("CREATE TABLE IF NOT EXISTS org_people (email {T}, name {T}, department {T}, team {T}, manager_email {T}, "
           "is_manager BOOLEAN, joined {T})")


class MemoryStore:
    def __init__(self, ex: Executor):
        self.ex = ex
        for stmt in DDL:
            self.ex.run(stmt.replace("{T}", getattr(ex, "text_type", "STRING")))

    # ---- writing ---------------------------------------------------------------------------------------------
    def commit(self, add: list[MemoryItem] | None = None, status: dict[str, str] | None = None,
               signals: list[Signal] | None = None) -> None:
        """One change to memory as one unit of work: new items, the status changes that go with them (the item they
        replace, the copies folded into a team meaning) and the signals about it.

        SQLite: one transaction, all or nothing. Warehouse: the items part is a single statement (a MERGE when it
        both adds and changes status), which Delta applies atomically, so readers never see the new meaning without
        the old one retired or the other way round; the signals follow in a second statement. Signals are evidence
        (who taught, adopted, undid): if that statement fails after the items one, memory itself is still consistent
        and the error is raised."""
        statements = self._item_statements(add or [], status or {})
        if signals:
            statements.append(_insert_signals(signals))
        if statements:
            self.ex.run_many(statements)

    def _item_statements(self, add: list[MemoryItem], status: dict[str, str]) -> list[Statement]:
        if add and status and getattr(self.ex, "merges", False):
            return [_merge_items(add, status)]
        out = [_insert_items(add)] if add else []
        if status:
            out.append(_update_status(status))
        return out

    def add_item(self, item: MemoryItem) -> None:
        self.commit(add=[item])

    def add_items(self, items: list[MemoryItem]) -> None:
        self.commit(add=items)

    def set_status(self, item_id: str, status: str) -> None:
        self.commit(status={item_id: status})

    def set_status_many(self, item_ids: list[str], status: str) -> None:
        self.commit(status={i: status for i in item_ids})

    def get_item(self, item_id: str) -> MemoryItem | None:
        rows = self.ex.run("SELECT * FROM memory_items WHERE item_id = :i", {"i": item_id})
        return _item(rows[0]) if rows else None

    # Last use of each item: applied in an answer (shown), taught (author), adopted by a team, or confirmed.
    _LAST_USED = ("LEFT JOIN (SELECT item_id AS used_id, MAX(created_at) AS last_used FROM memory_signals "
                  "WHERE kind IN ('shown', 'author', 'adopt', 'confirm') GROUP BY item_id) u ON u.used_id = i.item_id")

    def candidate_items(self, email: str, teammates: list[str]) -> list[MemoryItem]:
        """Everything the resolver needs for this user, in one query: their own items, gold, every team's and
        department's shared meanings (other departments' are shown as alternatives; other teams' only count towards
        departments and 'untrustworthy') and teammates' own terms (counted, never shown with names). Teammates come
        from the org directory by email, so someone who moved team counts for their new team. What the user actually
        sees is decided by the resolver."""
        names = _names("m", teammates)
        rows = self.ex.run(
            f"SELECT i.*, u.last_used FROM memory_items i {self._LAST_USED} WHERE i.status = 'active' AND "
            "(i.owner_email = :e OR i.origin IN ('gold', 'team', 'department') "
            f"OR (i.origin = 'user' AND i.owner_email IN ({_in(names)}) AND i.shareable = TRUE AND i.term IS NOT NULL))",
            {"e": email, **names},
        )
        return [_item(r) for r in rows]

    def own_items(self, email: str) -> list[MemoryItem]:
        """One person's active items of every type."""
        rows = self.ex.run(
            f"SELECT i.*, u.last_used FROM memory_items i {self._LAST_USED} WHERE i.status = 'active' AND "
            "i.origin = 'user' AND i.owner_email = :e", {"e": email})
        return [_item(r) for r in rows]

    def term_items(self, term: str, teammates: list[str]) -> list[MemoryItem]:
        """Active items of one term: every shared meaning (all teams and departments, gold) and teammates' own."""
        names = _names("m", teammates)
        rows = self.ex.run(
            f"SELECT i.*, u.last_used FROM memory_items i {self._LAST_USED} WHERE i.status = 'active' AND "
            f"i.term = :term AND (i.origin IN ('gold', 'team', 'department') OR i.owner_email IN ({_in(names)}))",
            {"term": term, **names},
        )
        return [_item(r) for r in rows]

    def shared_counts(self) -> dict[str, dict[str, int]]:
        """Active shared meanings per team and per department (org view): {"team": {...}, "department": {...}}.
        Two maps, because a one-team department (Marketing) has the same name as its team."""
        rows = self.ex.run(
            "SELECT origin, owner_team, owner_department, COUNT(*) AS n FROM memory_items WHERE status = 'active' "
            "AND origin IN ('team', 'department') GROUP BY origin, owner_team, owner_department"
        )
        out: dict[str, dict[str, int]] = {"team": {}, "department": {}}
        for r in rows:
            out[r["origin"]][r["owner_team"] if r["origin"] == "team" else r["owner_department"]] = int(r["n"])
        return out

    def replaced_gold(self) -> list[MemoryItem]:
        """Gold terms and every team or department meaning of the same terms (for 'untrustworthy')."""
        rows = self.ex.run(
            "SELECT * FROM memory_items WHERE status = 'active' AND (origin = 'gold' OR (origin IN ('team', "
            "'department') AND term IN (SELECT term FROM memory_items WHERE origin = 'gold' AND status = 'active')))"
        )
        return [_item(r) for r in rows]

    def latest_day(self) -> str | None:
        rows = self.ex.run("SELECT MAX(created_at) AS t FROM memory_items")
        return rows[0]["t"][:10] if rows and rows[0]["t"] else None

    # signals
    def add_signals(self, signals: list[Signal]) -> None:
        self.commit(signals=signals)

    def signals(self, item_ids: list[str]) -> list[Signal]:
        if not item_ids:
            return []
        names = {f"i{n}": i for n, i in enumerate(item_ids)}
        rows = self.ex.run(
            f"SELECT * FROM memory_signals WHERE item_id IN ({', '.join(':' + k for k in names)})", names
        )
        return [Signal(**{c: r[c] for c in SIGNAL_COLS}) for r in rows]

    def user_signals(self, email: str, teammates: list[str]) -> list[dict[str, Any]]:
        """In one query: this user's dismissed warnings and the consent choices of everyone in their team."""
        names = _names("m", [email, *teammates])
        return self.ex.run(
            "SELECT user_email, kind, term, created_at FROM memory_signals WHERE (kind = 'dismiss' AND user_email = :e) "
            f"OR (kind = 'consent' AND user_email IN ({_in(names)})) ORDER BY created_at",
            {"e": email, **names},
        )

    def dismissed_terms(self, email: str) -> set[str]:
        rows = self.ex.run(
            "SELECT DISTINCT term FROM memory_signals WHERE user_email = :e AND kind = 'dismiss'", {"e": email}
        )
        return {r["term"] for r in rows if r["term"]}

    def signal_summary(self) -> list[dict[str, Any]]:
        """Background acceptance evidence for the internal page (FR11): counts per item, team and kind."""
        return self.ex.run(
            "SELECT s.item_id, i.term, i.origin, s.team, s.kind, COUNT(*) AS n FROM memory_signals s "
            "JOIN memory_items i ON i.item_id = s.item_id GROUP BY s.item_id, i.term, i.origin, s.team, s.kind "
            "ORDER BY i.term, s.team, s.kind"
        )

    def withdraw_cases_using(self, team: str, sql_filter: str) -> None:
        """Withdraw a team's casebook cases whose SQL contains this filter (instr works on SQLite and Databricks)."""
        self.ex.run("UPDATE casebook SET status = 'withdrawn' WHERE owner_team = :t AND status = 'active' "
                    "AND instr(sql_text, :f) > 0", {"t": team, "f": sql_filter})

    def clear(self) -> None:
        self.ex.run("DELETE FROM memory_signals")
        self.ex.run("DELETE FROM memory_items")
        self.ex.run("DELETE FROM casebook")


def _item_row(item: MemoryItem) -> dict[str, Any]:
    row = asdict(item)
    row["aliases"] = json.dumps(row["aliases"])
    return {c: row[c] for c in ITEM_COLS}


def _insert_items(items: list[MemoryItem]) -> Statement:
    """One multi-row INSERT: on a SQL warehouse every statement costs a round trip."""
    values, params = [], {}
    for n, item in enumerate(items):
        values.append("(" + ", ".join(f":{c}{n}" for c in ITEM_COLS) + ")")
        params.update({f"{c}{n}": v for c, v in _item_row(item).items()})
    return f"INSERT INTO memory_items ({', '.join(ITEM_COLS)}) VALUES {', '.join(values)}", params


def _update_status(status: dict[str, str]) -> Statement:
    """One UPDATE for any mix of new statuses."""
    params: dict[str, Any] = {}
    whens = []
    for n, (item_id, new) in enumerate(status.items()):
        params[f"i{n}"], params[f"s{n}"] = item_id, new
        whens.append(f"WHEN :i{n} THEN :s{n}")
    ids = ", ".join(f":i{n}" for n in range(len(status)))
    return f"UPDATE memory_items SET status = CASE item_id {' '.join(whens)} END WHERE item_id IN ({ids})", params


def _merge_items(add: list[MemoryItem], status: dict[str, str]) -> Statement:
    """New items and status changes in one atomic statement (Delta MERGE). A status row only names an id and its new
    status; it never inserts (the `is_new` guard), so an unknown id changes nothing."""
    rows = [{**_item_row(i), "is_new": True} for i in add]
    rows += [{**dict.fromkeys(ITEM_COLS), "item_id": item_id, "status": new, "is_new": False}
             for item_id, new in status.items()]
    cols = [*ITEM_COLS, "is_new"]
    values, params = [], {}
    for n, row in enumerate(rows):
        values.append("(" + ", ".join(f":{c}{n}" for c in cols) + ")")
        params.update({f"{c}{n}": row[c] for c in cols})
    typed = ", ".join(f"CAST({c} AS {'BOOLEAN' if c in ('shareable', 'is_new') else 'STRING'}) AS {c}" for c in cols)
    return (f"MERGE INTO memory_items t USING (SELECT {typed} FROM VALUES {', '.join(values)} AS v({', '.join(cols)})) s "
            "ON t.item_id = s.item_id "
            "WHEN MATCHED AND NOT s.is_new THEN UPDATE SET t.status = s.status "
            f"WHEN NOT MATCHED AND s.is_new THEN INSERT ({', '.join(ITEM_COLS)}) "
            f"VALUES ({', '.join('s.' + c for c in ITEM_COLS)})"), params


def _insert_signals(signals: list[Signal]) -> Statement:
    values, params = [], {}
    for n, s in enumerate(signals):
        values.append("(" + ", ".join(f":{c}{n}" for c in SIGNAL_COLS) + ")")
        params.update({f"{c}{n}": v for c, v in asdict(s).items()})
    return f"INSERT INTO memory_signals ({', '.join(SIGNAL_COLS)}) VALUES {', '.join(values)}", params


def _names(prefix: str, values: list[str]) -> dict[str, str]:
    """Named parameters for an IN list; never empty (an empty IN () is a syntax error)."""
    return {f"{prefix}{n}": v for n, v in enumerate(values or ["-"])}


def _in(names: dict[str, str]) -> str:
    return ", ".join(":" + k for k in names)


def _item(r: dict[str, Any]) -> MemoryItem:
    d = {c: r[c] for c in ITEM_COLS}
    d["aliases"] = json.loads(d["aliases"] or "[]")
    d["shareable"] = bool(d["shareable"])
    d["last_used"] = r.get("last_used")
    return MemoryItem(**d)
