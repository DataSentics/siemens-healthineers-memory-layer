"""Storage under load: writes are one unit of work, warehouse requests run side by side through a pool, and two
people teaching at the same moment cannot both create the team meaning."""

import threading
import time
from pathlib import Path

import pytest

from memory_demo import seeding, store as store_mod
from memory_demo.extractor import MemoryOp
from memory_demo.models import MemoryItem, Signal
from memory_demo.org import Org
from memory_demo.service import MemoryService
from memory_demo.store import DatabricksExecutor, MemoryStore, SqliteExecutor

ROOT = Path(__file__).resolve().parents[1]
EMAIL = "{}@shs-demo.example".format
TEAM = [EMAIL(n) for n in ("jana.kral", "eva.horak", "tomas.vrba", "petra.lind", "lukas.brandt")]  # R&D / A


def item(term="x", **kw):
    return MemoryItem(item_type="term", term=term, meaning="m", owner_email=None, owner_team="T",
                      owner_department="D", origin="team", **kw)


def signal(item_id):
    return Signal(item_id=item_id, user_email="a", team="T", department="D", kind="adopt", term="x")


# ---- one unit of work ------------------------------------------------------------------------------------------
class FailingSignals(SqliteExecutor):
    def run_many(self, statements):
        statements = [(sql.replace("memory_signals", "no_such_table"), p) for sql, p in statements]
        return super().run_many(statements)


def test_a_change_is_all_or_nothing(tmp_path):
    store = MemoryStore(FailingSignals(str(tmp_path / "mem.db")))
    old = item()
    store.ex.run(*store_mod._insert_items([old]))
    new = item(supersedes=old.item_id)
    with pytest.raises(Exception):
        store.commit(add=[new], status={old.item_id: "withdrawn"}, signals=[signal(new.item_id)])
    assert store.get_item(new.item_id) is None and store.get_item(old.item_id).status == "active"  # rolled back


def test_commit_writes_items_status_and_signals_together(tmp_path):
    store = MemoryStore(SqliteExecutor(str(tmp_path / "mem.db")))
    a, b = item(), item()
    store.commit(add=[a, b])
    c = item()
    store.commit(add=[c], status={a.item_id: "withdrawn", b.item_id: "withdrawn", "unknown-id": "withdrawn"},
                 signals=[signal(c.item_id)])
    assert [store.get_item(i.item_id).status for i in (a, b, c)] == ["withdrawn", "withdrawn", "active"]
    assert len(store.signals([c.item_id])) == 1
    store.commit(status={a.item_id: "active", c.item_id: "withdrawn"})  # mixed statuses in one statement
    assert [store.get_item(i.item_id).status for i in (a, b, c)] == ["active", "withdrawn", "withdrawn"]
    store.commit()  # nothing to do


def test_the_warehouse_gets_one_merge_for_an_add_with_status_changes():
    sql, params = store_mod._merge_items([item()], {"old-id": "withdrawn"})
    assert sql.startswith("MERGE INTO memory_items") and "WHEN NOT MATCHED AND s.is_new THEN INSERT" in sql
    assert params["item_id1"] == "old-id" and params["status1"] == "withdrawn" and params["is_new1"] is False
    assert params["meaning1"] is None  # a status row carries nothing else, and can never insert


# ---- the connection pool ----------------------------------------------------------------------------------------
class FakeCursor:
    description = [("n",)]

    def __init__(self, con):
        self.con = con

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        hub = self.con.hub
        with hub.lock:
            hub.running += 1
            hub.peak = max(hub.peak, hub.running)
        try:
            if "FAIL" in sql:
                raise RuntimeError("session expired")
            time.sleep(0.1)
        finally:
            with hub.lock:
                hub.running -= 1

    def fetchall(self):
        return [(1,)]


class FakeCon:
    def __init__(self, hub):
        self.hub, self.closed = hub, False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class Hub:
    def __init__(self):
        self.lock, self.running, self.peak, self.made = threading.Lock(), 0, 0, []

    def connect(self):
        con = FakeCon(self)
        self.made.append(con)
        return con


def test_requests_run_side_by_side_up_to_the_pool_size():
    hub = Hub()
    ex = DatabricksExecutor("wh", "c", "s", size=4, connect=hub.connect)
    t0 = time.perf_counter()
    threads = [threading.Thread(target=ex.run, args=("SELECT 1",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    took = time.perf_counter() - t0
    assert hub.peak == 4 and len(hub.made) == 4  # four at a time, four connections in all
    assert took < 0.6  # two rounds of 0.1 s; one connection would take 0.8 s
    assert ex.run("SELECT 1") == [{"n": 1}]


def test_a_broken_connection_is_replaced_and_a_read_retried():
    hub = Hub()
    ex = DatabricksExecutor("wh", "c", "s", size=1, connect=hub.connect)
    with pytest.raises(RuntimeError):
        ex.run("SELECT FAIL")  # failed twice: the original and the one retry
    assert len(hub.made) == 3 and hub.made[0].closed and hub.made[1].closed
    with pytest.raises(RuntimeError):
        ex.run("UPDATE FAIL")  # a write is never retried
    assert len(hub.made) == 4
    assert ex.run("SELECT 1") == [{"n": 1}]  # the pool still works


def test_waiting_for_a_connection_gives_up_with_a_clear_error(monkeypatch):
    monkeypatch.setattr(store_mod, "POOL_WAIT_S", 0.2)
    hub = Hub()
    ex = DatabricksExecutor("wh", "c", "s", size=1, connect=hub.connect)
    held = ex._acquire()
    with pytest.raises(TimeoutError, match="no free warehouse connection"):
        ex.run("SELECT 1")
    ex._release(held)
    assert ex.run("SELECT 1") == [{"n": 1}]


# ---- two writers at once ----------------------------------------------------------------------------------------
@pytest.fixture
def svc(tmp_path):
    org = Org.from_csv(ROOT / "seed" / "org.csv")
    store = MemoryStore(SqliteExecutor(str(tmp_path / "mem.db")))
    seeding.reset(store, org, step=2, start="2005-12-01")
    return MemoryService(store, org)


def slow_state(svc, monkeypatch):
    """Widen the gap between reading a term's state and writing, so writers overlap every time."""
    real = svc._state

    def state(*a, **k):
        st = real(*a, **k)
        time.sleep(0.05)
        return st

    monkeypatch.setattr(svc, "_state", state)


def together(calls):
    barrier, errors = threading.Barrier(len(calls)), []

    def run(fn):
        barrier.wait()
        try:
            fn()
        except Exception as e:  # pragma: no cover - shown by the assertion below
            errors.append(e)

    threads = [threading.Thread(target=run, args=(fn,)) for fn in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def active(svc, origin, term):
    return svc.store.ex.run("SELECT item_id, owner_email, meaning FROM memory_items WHERE status = 'active' AND "
                            "origin = :o AND term = :t", {"o": origin, "t": term})


def test_five_teammates_teaching_a_new_term_at_once_make_one_team_meaning(svc, monkeypatch):
    slow_state(svc, monkeypatch)
    ops = [MemoryOp(op="add", item_type="term", term="zeta runs", meaning=f"meaning number {n}",
                    evidence="stated") for n in range(len(TEAM))]
    together([lambda e=e, op=op: svc.apply(e, [op]) for e, op in zip(TEAM, ops)])
    assert len(active(svc, "team", "zeta runs")) == 1  # the first one; the others keep theirs as their own
    assert len(active(svc, "user", "zeta runs")) == len(TEAM) - 1


def test_the_same_new_meaning_from_everyone_at_once_is_stored_once(svc, monkeypatch):
    slow_state(svc, monkeypatch)
    op = MemoryOp(op="add", item_type="term", term="zeta runs", meaning="nightly test runs", evidence="stated")
    together([lambda e=e: svc.apply(e, [op]) for e in TEAM])
    assert len(active(svc, "team", "zeta runs")) == 1 and active(svc, "user", "zeta runs") == []


def test_two_teams_agreeing_at_once_make_one_department_meaning(svc, monkeypatch):
    slow_state(svc, monkeypatch)
    for e in (EMAIL("anna.novak"), EMAIL("elena.dvorak")):  # R&D / B and R&D / C
        svc.set_consent(e, remember=True, share=True)
    op = MemoryOp(op="add", item_type="term", term="noisy rack",
                  meaning="Rack R37, which produces by far the most events.", evidence="stated")
    together([lambda e=e: svc.apply(e, [op]) for e in (EMAIL("anna.novak"), EMAIL("elena.dvorak"))])
    assert len(active(svc, "department", "the noisy rack")) == 1
    assert active(svc, "team", "the noisy rack") == []  # all three teams' copies folded into it
