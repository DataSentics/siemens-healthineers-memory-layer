from pathlib import Path

import pytest

from memory_demo import resolver, seeding
from memory_demo.extractor import MemoryOp
from memory_demo.org import Org
from memory_demo.service import MemoryService
from memory_demo.store import MemoryStore, SqliteExecutor

ROOT = Path(__file__).resolve().parents[1]
EMAIL = "{}@shs-demo.example".format
EVA, LUKAS, PETRA, TOMAS, JANA = (EMAIL(n) for n in ("eva.horak", "lukas.brandt", "petra.lind", "tomas.vrba",
                                                     "jana.kral"))
MEI, BEN = EMAIL("mei.chen"), EMAIL("ben.weber")
DDR_ONLY = MemoryOp(op="add", item_type="term", term="memory errors", meaning="only correctable DDR errors",
                    sql_hint="message LIKE '%ddr error%'", evidence="correction")


@pytest.fixture
def org():
    return Org.from_csv(ROOT / "seed" / "org.csv")


@pytest.fixture
def svc(org, tmp_path):
    store = MemoryStore(SqliteExecutor(str(tmp_path / "mem.db")))
    seeding.reset(store, org, step=2, start="2005-12-01")  # dataset date 1 Jan 2006: R&D / A has 5 members
    return MemoryService(store, org)


def used(svc, email, q, exclude=()):
    return {u.term or "pref:" + u.meaning: u for u in svc.retrieve(email, q, exclude)[1].items}


def pick_colleagues(svc, email, term="memory errors"):
    """`email` clicks "Use this" on the meaning colleagues use."""
    u = used(svc, email, term)[term]
    alt = next(a for a in u.alternatives if a.scope == "colleagues")
    return svc.choose(email, u.item_id, alt.item_id)


def test_threshold_is_half_the_team_at_least_two(org):
    assert [resolver.needed(n) for n in (1, 2, 4, 5, 8)] == [2, 2, 2, 3, 4]
    assert org.team_size("R&D / A", "2005-12-01") == 4 and org.team_size("R&D / A", "2006-01-01") == 5
    assert [resolver.confidence(k, 3) for k in (0, 1, 2, 3)] == [1.0, 0.67, 0.33, 0.05]


def test_newcomer_gets_team_meaning_without_eva_preferences(svc):
    u = used(svc, LUKAS, "How many memory errors were on the noisy rack last month?")
    assert u["memory errors"].scope == "team" and u["memory errors"].confidence == 1.0
    assert u["memory errors"].level == "note" and u["memory errors"].support_label == "your team"
    assert u["the noisy rack"].sql_hint == "rack = 'R37'"
    assert not [k for k in u if k.startswith("pref:")]  # preferences stay personal


def test_author_of_a_team_meaning_sees_it_as_the_team_s(svc):
    u = used(svc, EVA, "memory errors last month")
    assert u["memory errors"].scope == "team"
    assert len([k for k in u if k.startswith("pref:")]) == 2


SOMARIS = MemoryOp(op="add", item_type="term", term="SOMARIS10", meaning="the scanner software", evidence="explicit")


def test_new_term_goes_straight_to_the_team(svc):
    writes = svc.apply(TOMAS, [SOMARIS])
    team = next(w for w in writes if w.op == "team")
    assert team.shared_with == "your team" and team.note is None and team.superseded_id is None
    assert [w.item_id for w in writes if w.op == "add"] == team.folded_ids  # his own copy folded into it
    for email in (TOMAS, PETRA):
        u = used(svc, email, "any somaris10 news?")["somaris10"]
        assert u.scope == "team" and u.confidence == 1.0 and not u.alternatives
    assert [r["term"] for r in svc.view(PETRA)["team"] if r["term"] == "somaris10"] == ["somaris10"]
    assert "somaris10" not in used(svc, MEI, "any somaris10 news?")  # other teams do not get it
    svc.undo(TOMAS, team.to_dict())
    assert used(svc, TOMAS, "any somaris10 news?")["somaris10"].scope == "personal"
    assert "somaris10" not in used(svc, PETRA, "any somaris10 news?")


def test_new_term_stays_personal_without_sharing_consent(svc):
    svc.set_consent(TOMAS, remember=True, share=False)
    [w] = svc.apply(TOMAS, [SOMARIS])
    assert w.shared_with == "only you"
    assert used(svc, TOMAS, "any somaris10 news?")["somaris10"].scope == "personal"
    assert "somaris10" not in used(svc, PETRA, "any somaris10 news?")  # not even as another meaning


def test_override_stays_personal_and_lowers_the_team_s_confidence(svc):
    [w] = svc.apply(TOMAS, [DDR_ONLY])
    assert w.op == "add" and w.shared_with == "only you"
    mine = used(svc, TOMAS, "memory errors")["memory errors"]
    assert mine.scope == "personal" and mine.confidence is None and [a.scope for a in mine.alternatives] == ["team"]
    petra = used(svc, PETRA, "memory errors")["memory errors"]
    assert petra.scope == "team" and (petra.overrides, petra.needed, petra.confidence) == (1, 3, 0.67)
    assert petra.level == "note" and "1 colleague uses another meaning; 3 are needed" in petra.reason
    [alt] = petra.alternatives
    assert (alt.scope, alt.holders, alt.meaning) == ("colleagues", 1, "only correctable DDR errors")


def test_enough_teammates_change_the_team_meaning(svc):
    svc.apply(TOMAS, [DDR_ONLY])
    pick_colleagues(svc, PETRA)
    lukas = used(svc, LUKAS, "memory errors")["memory errors"]
    assert (lukas.confidence, lukas.level) == (0.33, "warning")
    writes = pick_colleagues(svc, LUKAS)
    assert [w.op for w in writes] == ["add", "team"]
    assert writes[1].note == "3 of 5 colleagues use this meaning" and writes[1].shared_with == "your team"
    for who in (EVA, TOMAS, PETRA, LUKAS, JANA):
        u = used(svc, who, "memory errors")["memory errors"]
        assert (u.scope, u.meaning, u.confidence) == ("team", "only correctable DDR errors", 1.0)
    assert "memory errors" not in used(svc, MEI, "memory errors")  # never leaves the team


def test_undo_of_a_team_update_restores_the_old_meaning(svc):
    svc.apply(TOMAS, [DDR_ONLY])
    pick_colleagues(svc, PETRA)
    team = pick_colleagues(svc, LUKAS)[1]
    svc.undo(LUKAS, team.to_dict())
    assert used(svc, EVA, "memory errors")["memory errors"].meaning.startswith("correctable DDR memory errors")
    assert used(svc, PETRA, "memory errors")["memory errors"].scope == "personal"


def test_different_meanings_lower_confidence_but_do_not_replace(svc):
    for who, n in ((TOMAS, 1), (PETRA, 2), (LUKAS, 3)):
        svc.apply(who, [DDR_ONLY.model_copy(update={"meaning": f"variant {n}", "sql_hint": f"event_id = {n}"})])
    u = used(svc, EVA, "memory errors")["memory errors"]
    assert u.scope == "team" and u.confidence == 0.05 and len(u.alternatives) == 3


def test_going_back_to_the_team_meaning_drops_your_own(svc):
    svc.apply(TOMAS, [DDR_ONLY])
    mine = used(svc, TOMAS, "memory errors")["memory errors"]
    [w] = svc.choose(TOMAS, mine.item_id, mine.alternatives[0].item_id)
    assert w.op == "withdraw"
    assert used(svc, TOMAS, "memory errors")["memory errors"].scope == "team"
    assert used(svc, PETRA, "memory errors")["memory errors"].confidence == 1.0


def test_official_term_replaced_in_two_departments_is_untrustworthy(svc):
    mei = used(svc, MEI, "How many incidents last month?")["incidents"]
    assert mei.scope == "gold" and mei.untrustworthy and mei.confidence == 1.0  # marked, nothing else changes
    assert [(a.scope, a.department) for a in mei.alternatives] == [("other_department", "Service")]
    lukas = used(svc, LUKAS, "incidents")["incidents"]
    assert lukas.scope == "team" and not lukas.untrustworthy
    assert [(a.scope, a.untrustworthy, a.department) for a in lukas.alternatives] == [
        ("gold", True, None), ("other_department", False, "Service")]  # other departments: shown, not applied
    assert svc.untrustworthy() == {"incidents": ["R&D", "Service"]}  # R&D through a team, Service as a department


def test_department_meaning_reaches_its_other_teams_but_team_meanings_do_not(svc):
    ben = used(svc, BEN, "memory errors and comms errors")  # R&D / B
    assert "memory errors" not in ben  # R&D / A's team meaning stays in R&D / A
    assert (ben["comms errors"].scope, ben["comms errors"].support_label, ben["comms errors"].confidence) == (
        "department", "your department", 1.0)
    assert used(svc, LUKAS, "comms errors")["comms errors"].scope == "department"  # R&D / A folded into it


def test_dismissed_warning_becomes_note(svc):
    svc.apply(TOMAS, [DDR_ONLY])
    pick_colleagues(svc, PETRA)
    svc.dismiss_warning(LUKAS, "memory errors")
    u = used(svc, LUKAS, "memory errors")["memory errors"]
    assert u.level == "note" and "chose not to be warned" in u.reason


def test_joke_stays_private_and_does_not_count(svc):
    svc.apply(PETRA, [DDR_ONLY.model_copy(update={"meaning": "gremlins", "sql_hint": "1=1", "is_joke": True})])
    assert used(svc, PETRA, "memory errors")["memory errors"].scope == "personal"
    eva = used(svc, EVA, "memory errors")["memory errors"]
    assert eva.confidence == 1.0 and eva.alternatives == []


def test_plural_and_alias_matching():
    from memory_demo.models import MemoryItem
    it = MemoryItem(item_type="term", term="memory errors", aliases=["ram errors"], meaning="x",
                    owner_email=None, owner_team=None, owner_department=None)
    assert resolver.mentions(it, "any Memory Error on R37?")
    assert resolver.mentions(it, "RAM errors please")
    assert not resolver.mentions(it, "memory usage")
    quiet = MemoryItem(item_type="term", term="the quiet rack", meaning="x", owner_email=None, owner_team=None,
                       owner_department=None)
    assert resolver.mentions(quiet, "events on quiet rack?") and resolver.mentions(quiet, "The quiet rack")
    assert not resolver.mentions(quiet, "quietness")


def test_same_filter_counts_as_same_meaning():
    from memory_demo.models import MemoryItem
    a = MemoryItem(item_type="term", term="x", meaning="one wording", sql_hint="(message LIKE '%ddr%')",
                   owner_email=None, owner_team=None, owner_department=None)
    b = MemoryItem(item_type="term", term="x", meaning="another wording", sql_hint="message  like '%ddr%'",
                   owner_email=None, owner_team=None, owner_department=None)
    assert resolver.meaning_key(a) == resolver.meaning_key(b)


def test_forgetting_twice_is_recorded_once(svc):
    """Runtime test 16 Nov: the model's forget tool removed Tomas's item, then the after-turn step read the same
    request as a withdraw of the same item, so the removal was logged and shown twice."""
    [w] = svc.apply(TOMAS, [DDR_ONLY])
    assert svc.forget(TOMAS, w.item_id).op == "withdraw"
    again = MemoryOp(op="withdraw", item_type="term", term="memory errors", meaning="only correctable DDR errors",
                     target_item_id=w.item_id, evidence="stated")
    assert svc.apply(TOMAS, [again]) == []  # nothing to show
    assert svc.forget(TOMAS, w.item_id) is None
    kinds = [r["kind"] for r in svc.store.ex.run("SELECT kind FROM memory_signals WHERE item_id = :i", {"i": w.item_id})]
    assert kinds.count("withdraw") == 1
    assert used(svc, TOMAS, "memory errors")["memory errors"].scope == "team"


def test_update_own_item_and_undo(svc):
    [first] = svc.apply(TOMAS, [DDR_ONLY])
    [second] = svc.apply(TOMAS, [DDR_ONLY.model_copy(update={"meaning": "all DDR errors", "sql_hint": "message LIKE '%ddr%'"})])
    assert second.op == "update" and second.superseded_id == first.item_id
    assert used(svc, TOMAS, "memory errors")["memory errors"].meaning == "all DDR errors"
    svc.undo(TOMAS, second.to_dict())
    assert used(svc, TOMAS, "memory errors")["memory errors"].item_id == first.item_id


def test_viewer_is_anonymised(svc):
    view = svc.view(LUKAS)
    assert set(view) == {"personal", "team", "department", "gold", "other"}
    assert [r["term"] for r in view["department"]] == ["comms errors"]
    assert {(r["department"], r["term"]) for r in view["other"]} == {("Service", "incidents"),
                                                                    ("Customer Support", "job failures")}
    assert {r["term"] for r in view["team"]} >= {"memory errors", "incidents", "the noisy rack"}
    assert all("eva" not in str(r).lower() for r in view["team"])
    assert view["personal"] == []
    gold = {r["term"]: r for r in view["gold"]}
    assert gold["incidents"]["untrustworthy"] and gold["incidents"]["replaced"]


def test_regenerate_without_rejected_item_falls_back_to_gold(svc):
    inc = used(svc, LUKAS, "incidents last month")["incidents"]
    assert used(svc, LUKAS, "incidents last month", exclude=(inc.item_id,))["incidents"].scope == "gold"


def test_demo_clock():
    from memory_demo import models
    models.set_today("2005-12-01")
    assert models.now_iso().startswith("2005-12-01T") and models.months_later("2005-12-01") == "2006-01-01"
    assert models.months_later("2006-01-01", -1) == "2005-12-01"


def test_saved_filters_are_bracketed_so_dates_still_apply():
    from memory_demo.service import _enclosed
    assert _enclosed("message LIKE '%a%' OR message LIKE '%b%'") == "(message LIKE '%a%' OR message LIKE '%b%')"
    assert _enclosed("(a) OR (b)") == "((a) OR (b))"
    assert _enclosed("(a OR b)") == "(a OR b)" and _enclosed("  ") is None


def test_terms_only_used_this_turn_are_not_saved_again(svc):
    _, trace = svc.retrieve(LUKAS, "memory errors last month")
    applied = [vars(i) for i in trace.items]
    op = DDR_ONLY.model_copy(update={"evidence": "inferred"})
    assert svc.apply(LUKAS, [op], applied) == []
    corrected = op.model_copy(update={"evidence": "correction"})
    assert svc.apply(LUKAS, [corrected], applied)[0].op == "add"  # a correction still counts


def test_team_update_withdraws_cases_with_the_old_filter(svc):
    from memory_demo.casebook import Casebook
    cases = Casebook(svc.store, svc.org)
    old = used(svc, EVA, "memory errors")["memory errors"].sql_hint
    stale = cases.add(EVA, "memory errors last month?", f"SELECT COUNT(*) FROM bgl_events WHERE {old}")
    other = cases.add(EVA, "fatal events?", "SELECT COUNT(*) FROM bgl_events WHERE level = 'FATAL'")
    svc.apply(TOMAS, [DDR_ONLY])
    pick_colleagues(svc, PETRA)
    pick_colleagues(svc, LUKAS)
    status = {r["case_id"]: r["status"] for r in svc.store.ex.run("SELECT case_id, status FROM casebook")}
    assert status[stale.case_id] == "withdrawn" and status[other.case_id] == "active"


def test_meanings_fade_each_unused_month_and_expire_after_six():
    from memory_demo.models import MemoryItem
    it = MemoryItem(item_type="term", term="x", meaning="y", owner_email=None, owner_team="T", owner_department="D",
                    origin="team", created_at="2005-08-20T10:00:00+00:00")
    assert resolver.months_unused(it, "2006-01-01") == 4 and resolver.freshness(it, "2006-01-01") == pytest.approx(1 / 3)
    it.last_used = "2005-12-31T09:00:00+00:00"  # used yesterday
    assert resolver.months_unused(it, "2006-01-01") == 0 and not resolver.expired(it, "2006-01-01")
    assert resolver.expired(it, "2006-07-01") and resolver.freshness(it, "2006-07-01") == 0
    gold = MemoryItem(item_type="term", term="x", meaning="y", owner_email=None, owner_team=None,
                      owner_department=None, origin="gold", created_at="2000-01-01T00:00:00+00:00")
    assert not resolver.expired(gold, "2006-01-01") and resolver.freshness(gold, "2006-01-01") == 1.0


def test_an_unused_team_meaning_has_faded_and_using_it_refreshes_it(svc):
    import time
    u = used(svc, PETRA, "How many link card problems did we have last month?")["link card problems"]
    assert u.months_unused == 4 and u.confidence == 0.33 and u.level == "warning"
    assert "Not used for 4 months" in u.reason
    time.sleep(0.3)  # the "shown" signal is written in the background
    again = used(svc, LUKAS, "link card problems last month?")["link card problems"]
    assert again.months_unused == 0 and again.confidence == 1.0


def test_an_expired_meaning_is_no_longer_applied(svc):
    from memory_demo import models
    models.set_today("2006-03-01")  # link card problems was last used in August 2005: 6 months
    try:
        assert "link card problems" not in used(svc, PETRA, "link card problems last month?")
        assert "link card problems" not in {r["term"] for r in svc.view(PETRA)["team"]}
    finally:
        models.set_today("2006-01-01")


def test_nothing_is_remembered_without_consent(svc):
    clara = EMAIL("elena.dvorak")  # R&D / C: not part of the pilot, never asked
    assert svc.consent(clara) is None
    op = DDR_ONLY.model_copy(update={"term": "the bad rack", "meaning": "rack R00", "sql_hint": "rack = 'R00'"})
    assert svc.apply(clara, [op]) == []
    svc.set_consent(clara, remember=True, share=False)
    assert svc.consent(clara) == {"remember": True, "share": False}
    assert svc.apply(clara, [op])[0].op == "add"


def test_a_colleague_who_does_not_share_does_not_count(svc):
    svc.set_consent(TOMAS, remember=True, share=False)
    svc.apply(TOMAS, [DDR_ONLY])
    assert used(svc, TOMAS, "memory errors")["memory errors"].scope == "personal"  # still his
    petra = used(svc, PETRA, "memory errors")["memory errors"]
    assert petra.confidence == 1.0 and petra.alternatives == []  # not counted, not shown


def test_the_pilot_teams_have_agreed_to_remember_and_share(svc):
    assert svc.consent(LUKAS) == {"remember": True, "share": True}
    assert svc.consent(EMAIL("uwe.marek")) == {"remember": True, "share": True}  # Service / A
    assert svc.consent(MEI) is None
