"""The changes after the 5 Nov demo: one meaning in other words or with typos is never stored twice (Mem0's update
phase), the department level, other departments' meanings shown to everyone, teams read from the org directory,
and what is about the user stays private."""

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from memory_demo import consolidate, resolver, seeding
from memory_demo.extractor import MemoryOp, parse
from memory_demo.models import MemoryItem
from memory_demo.org import Directory, Org, new_team
from memory_demo.service import MemoryService, relevant_facts
from memory_demo.store import MemoryStore, SqliteExecutor

ROOT = Path(__file__).resolve().parents[1]
EMAIL = "{}@shs-demo.example".format
EVA, LUKAS, PETRA, TOMAS, JANA = (EMAIL(n) for n in ("eva.horak", "lukas.brandt", "petra.lind", "tomas.vrba",
                                                     "jana.kral"))
BEN, ELENA, MEI, FIONA = EMAIL("ben.weber"), EMAIL("elena.dvorak"), EMAIL("mei.chen"), EMAIL("fiona.krueger")


@pytest.fixture
def svc(tmp_path):
    org = Org.from_csv(ROOT / "seed" / "org.csv")
    store = MemoryStore(SqliteExecutor(str(tmp_path / "mem.db")))
    seeding.reset(store, org, step=2, start="2005-12-01")  # dataset date 1 Jan 2006
    return MemoryService(store, org)


def used(svc, email, q):
    return {u.term or "pref:" + u.meaning: u for u in svc.retrieve(email, q)[1].items}


def term_op(term, meaning, sql=None, **kw):
    return MemoryOp(op="add", item_type="term", term=term, meaning=meaning, sql_hint=sql, evidence="stated", **kw)


def item(meaning, sql=None, term="x"):
    return MemoryItem(item_type="term", term=term, meaning=meaning, sql_hint=sql, owner_email=None, owner_team=None,
                      owner_department=None)


# ---- the same thing in other words -----------------------------------------------------------------------
def test_wording_checks_merge_typos_but_not_different_things():
    assert consolidate.same_term("SOMARIS10", "Somaris 10") and consolidate.same_term("somaris-10", "somaris10")
    assert consolidate.same_term("memroy errors", "memory errors") and consolidate.same_term("memory error", "memory errors")
    assert consolidate.same_term("i/o nodes", "io nodes")
    assert not consolidate.same_term("R37 errors", "R38 errors") and not consolidate.same_term("tlb errors", "dlb errors")
    a = "Only correctable DDR errors, without cache parity errors or CE sym events."
    assert consolidate.same_words(a, "only the correctable DDR errors, without cache parity errors or CE sym events", 2)
    assert consolidate.same_words(a, "only corectable DDR errors without cache parity erors or CE sym events", 2)
    assert not consolidate.same_words(a, "Only uncorrectable DDR errors, without cache parity errors or CE sym events.", 2)
    assert not consolidate.same_words("errors on rack R37", "errors on rack R38", 2)
    assert consolidate.same_meaning(item("one wording", "(message LIKE '%ddr%')"), item("other", "message  like '%ddr%'"))
    assert consolidate.sql_key("(1=0)") is None and consolidate.sql_key(" ( rack = 'R37' ) ") == "rack='r37'"


def test_the_demo_duplicate_two_wordings_and_two_filters_become_one_team_meaning(svc):
    """5 Nov: Tomas typed 'Only correctable DDR errors, ...', Eva and Petra 'only the correctable DDR errors, ...',
    each with its own SQL. Before, those were two meanings: Tomas's stayed next to the new team meaning."""
    svc.apply(TOMAS, [term_op("memory errors", "Only correctable DDR errors, without cache parity errors or CE sym "
                                               "events.", "message LIKE '%ddr error%'")])
    eva = term_op("memory errors", "only the correctable DDR errors, without cache parity errors or CE sym events",
                  "LOWER(message) LIKE '%ddr%' AND LOWER(message) NOT LIKE '%parity%'")
    [w] = svc.apply(EVA, [eva])
    assert w.op == "add" and w.meaning.startswith("Only correctable")  # matched to the wording already in use
    writes = svc.apply(PETRA, [eva])
    assert [w.op for w in writes] == ["add", "team"] and writes[1].note == "3 of 5 colleagues use this meaning"
    team = [r for r in svc.view(LUKAS)["team"] if r["term"] == "memory errors"]
    assert len(team) == 1 and team[0]["sql_hint"] == "(message LIKE '%ddr error%')"
    for who in (TOMAS, EVA, PETRA):
        assert not [r for r in svc.view(who)["personal"] if r["term"] == "memory errors"]  # all folded in
    u = used(svc, LUKAS, "memory errors")["memory errors"]
    assert u.scope == "team" and u.confidence == 1.0 and u.alternatives == []


def test_saying_it_again_with_a_typo_is_already_in_memory(svc):
    svc.apply(TOMAS, [term_op("SOMARIS10", "the software platform our CT scanners run on")])
    [w] = svc.apply(PETRA, [term_op("Somaris 10", "The software platfrom our CT scanners run on.")])
    assert (w.op, w.term, w.shared_with) == ("same", "somaris10", "your team")
    assert [r["term"] for r in svc.view(PETRA)["team"]].count("somaris10") == 1
    assert used(svc, PETRA, "any news on somaris 10?")["somaris10"].scope == "team"  # other spacing still matches
    svc.undo(PETRA, w.to_dict())  # nothing to undo
    assert used(svc, PETRA, "somaris10")["somaris10"].scope == "team"
    [again] = svc.apply(TOMAS, [term_op("memory errors", "correctable DDR memory errors plus cache parity errors "
                                                          "and CE sym events")])  # the team's meaning, restated
    assert (again.op, again.shared_with) == ("same", "your team")


def test_a_different_meaning_is_not_merged(svc):
    [w] = svc.apply(TOMAS, [term_op("memory errors", "only uncorrectable DDR errors", "message LIKE '%uncorrectable%'")])
    assert w.op == "add"
    assert used(svc, TOMAS, "memory errors")["memory errors"].scope == "personal"


class SameAsModel(GenericFakeChatModel):
    """Answers the consolidation question with a fixed SameAs call and counts the calls."""

    answer: dict = {}
    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, *a, **k):
        self.calls += 1
        return AIMessage("", tool_calls=[{"name": "SameAs", "args": self.answer, "id": "1"}])


def test_the_llm_decides_when_wording_is_not_enough(svc):
    team_id = used(svc, TOMAS, "memory errors")["memory errors"].item_id
    llm = SameAsModel(messages=iter([]), answer={"same_meaning_id": team_id})
    svc.llm = llm
    [w] = svc.apply(TOMAS, [term_op("RAM errors", "DDR memory errors together with parity errors and CE sym",
                                    "message LIKE '%ddr%' OR message LIKE '%parity%'")])
    assert (w.op, w.term, w.shared_with) == ("same", "memory errors", "your team") and llm.calls == 1
    # A term applied this turn was already compared by the extractor: no second LLM call.
    llm.answer = {"same_meaning_id": None}
    applied = [vars(i) for i in svc.retrieve(TOMAS, "memory errors")[1].items]
    [w] = svc.apply(TOMAS, [term_op("memory errors", "only DDR errors", "message LIKE '%ddr error%'")], applied)
    assert w.op == "add" and llm.calls == 1


def test_a_synonym_stays_a_term_of_its_own(svc):
    """Eval 12 Nov: 'link issues = LINKCARD events' was merged into 'link card problems' by the LLM, so nothing was
    saved and Tomas's own word never matched. Only spelling variants of a term merge; the LLM is not asked."""
    llm = SameAsModel(messages=iter([]), answer={"same_meaning_id": None})
    svc.llm = llm
    writes = svc.apply(TOMAS, [term_op("link issues", "events from the LINKCARD component", "component = 'LINKCARD'")])
    assert [w.op for w in writes] == ["add", "team"] and writes[1].term == "link issues" and llm.calls == 0
    assert used(svc, TOMAS, "any link issues last month?")["link issues"].scope == "team"
    assert "link card problems" in used(svc, TOMAS, "link card problems?")  # the existing term is untouched


def test_the_extractor_can_say_it_is_a_colleague_s_meaning(svc):
    svc.apply(TOMAS, [term_op("memory errors", "only correctable DDR errors", "message LIKE '%ddr error%'")])
    _, trace = svc.retrieve(PETRA, "memory errors")
    applied = [vars(i) for i in trace.items]
    tomas_id = trace.items[0].alternatives[0].item_id
    op = term_op("memory errors", "just the DDR ones that got corrected", "message LIKE '%DDR%'",
                 same_as_item_id=tomas_id)
    [w] = svc.apply(PETRA, [op], applied)
    assert w.meaning == "only correctable DDR errors"  # her copy is his meaning, so the two count together
    assert used(svc, LUKAS, "memory errors")["memory errors"].alternatives[0].holders == 2


# ---- departments ---------------------------------------------------------------------------------------------
def test_needed_teams():
    assert [resolver.needed_teams(n) for n in (1, 2, 3, 4, 5)] == [1, 2, 2, 2, 3]


def test_a_department_meaning_forms_when_half_the_teams_agree_and_undo_restores(svc):
    svc.set_consent(ELENA, remember=True, share=True)  # R&D / C
    assert "the noisy rack" not in used(svc, BEN, "the noisy rack")
    writes = svc.apply(ELENA, [term_op("noisy rack", "R37, the rack with the most events", "rack='R37'")])
    assert [w.op for w in writes] == ["add", "team", "department"]
    dept = writes[2]
    assert dept.note == "2 of 3 teams in R&D use this meaning" and dept.shared_with == "your department"
    assert dept.term == "the noisy rack" and dept.meaning == "rack R37, which produces by far the most events"
    for who in (BEN, LUKAS, ELENA):  # R&D / B gets it too; R&D / A's and R&D / C's copies folded in
        u = used(svc, who, "errors on the noisy rack")["the noisy rack"]
        assert (u.scope, u.confidence) == ("department", 1.0)
    assert used(svc, MEI, "the noisy rack")["the noisy rack"].scope == "unresolved"  # shown in Service, not applied
    assert sorted(r["term"] for r in svc.view(MEI)["other"] if r["department"] == "R&D") == ["comms errors",
                                                                                          "the noisy rack"]
    svc.undo(ELENA, dept.to_dict())
    assert used(svc, LUKAS, "the noisy rack")["the noisy rack"].scope == "team"
    assert used(svc, ELENA, "the noisy rack")["the noisy rack"].scope == "team"
    assert "the noisy rack" not in used(svc, BEN, "the noisy rack")


def test_a_team_can_keep_its_own_meaning_under_a_department_meaning(svc):
    for who in (BEN, EMAIL("clara.svoboda")):  # R&D / B: 4 people, 2 needed
        svc.apply(who, [term_op("comms errors", "every ciod message", "message LIKE 'ciod%'")])
    ben = used(svc, BEN, "comms errors")["comms errors"]
    assert (ben.scope, ben.meaning) == ("team", "every ciod message")
    assert [a.scope for a in ben.alternatives] == ["department"]
    assert "replaces your department's meaning" in ben.reason
    assert used(svc, LUKAS, "comms errors")["comms errors"].scope == "department"


def test_a_one_team_department_shares_new_terms_with_the_department(svc):
    svc.set_consent(FIONA, remember=True, share=True)  # Marketing has one team
    writes = svc.apply(FIONA, [term_op("launch week", "the first week after a release", "event_date >= '2005-06-01'")])
    assert [w.op for w in writes] == ["add", "team", "department"]
    u = used(svc, LUKAS, "anything in launch week?")["launch week"]
    assert u.scope == "unresolved" and u.confidence is None
    assert [(a.scope, a.department) for a in u.alternatives] == [("other_department", "Marketing")]
    context, _ = svc.retrieve(LUKAS, "anything in launch week?")
    assert "launch week" not in context  # shown, never applied


def test_picking_another_department_s_meaning_makes_it_your_team_s(svc):
    u = used(svc, LUKAS, "How many job failures last month?")["job failures"]
    assert u.scope == "unresolved" and u.alternatives[0].department == "Customer Support"
    writes = svc.choose(LUKAS, u.item_id, u.alternatives[0].item_id)
    assert [w.op for w in writes] == ["add", "team"]  # a new term for R&D / A
    now = used(svc, LUKAS, "job failures")["job failures"]
    assert now.scope == "team" and now.meaning == u.alternatives[0].meaning


# ---- what is about the user stays private -----------------------------------------------------------------
def test_facts_never_become_terms_and_private_terms_are_never_shared(svc):
    """Whether a fact is worth saving is the extractor's call (likes are not, since 12 Nov: see evals/). Whatever it
    saves about a person stays theirs and never changes what a term means."""
    svc.apply(TOMAS, [term_op("SOMARIS10", "the software platform our CT scanners run on")])
    fact = MemoryOp(op="add", item_type="fact", term="SOMARIS10", meaning="treats SOMARIS10 warnings as noise",
                    evidence="stated", private=True)
    [w] = svc.apply(PETRA, [fact])
    assert w.term is None and w.shared_with == "only you"  # stored as a fact, not as what SOMARIS10 means
    petra = used(svc, PETRA, "any SOMARIS10 problems?")
    key = "pref:treats SOMARIS10 warnings as noise"
    assert petra["somaris10"].scope == "team" and key in petra  # applied as a fact, by topic
    assert key not in used(svc, PETRA, "memory errors last month")
    assert key not in used(svc, TOMAS, "any SOMARIS10 problems?")
    [mine] = svc.apply(PETRA, [term_op("my rack", "rack R12", "rack = 'R12'", private=True)])
    assert mine.shared_with == "only you"
    assert used(svc, PETRA, "my rack")["my rack"].scope == "personal"
    assert "my rack" not in used(svc, TOMAS, "my rack")  # not shared, not even as another meaning


def test_relevant_facts_share_a_word_with_the_question():
    facts = [MemoryItem(item_type="fact", meaning=m, owner_email="a", owner_team="t", owner_department="d")
             for m in ("likes apples", "looks after racks R30 to R37")]
    assert [f.meaning for f in relevant_facts(facts, "errors on my racks?")] == ["looks after racks R30 to R37"]
    assert relevant_facts(facts, "memory errors") == []


def test_the_extractor_answer_survives_old_names_and_bad_ops():
    out = parse({"ops": [{"op": "add", "item_type": "term", "term": "x", "meaning": "y", "evidence": "explicit"},
                         {"op": "add", "item_type": "nonsense", "meaning": "z", "evidence": "stated"}],
                 "relevance": [{"item_id": "a", "level": "high", "reason": "r"}, {"item_id": "b"}]})
    assert [o.evidence for o in out.ops] == ["stated"] and [r.item_id for r in out.relevance] == ["a"]


def test_constant_filters_are_not_saved(svc):
    [team] = [w for w in svc.apply(TOMAS, [term_op("SOMARIS10", "the scanner software", "(1=0)")]) if w.op == "team"]
    assert svc.store.get_item(team.item_id).sql_hint is None


# ---- teams come from the org directory ------------------------------------------------------------------
def test_a_new_team_works_without_any_setup(tmp_path):
    ex = SqliteExecutor(str(tmp_path / "mem.db"))
    store = MemoryStore(ex)
    directory = Directory(ex, ROOT / "seed" / "org.csv", ttl=300)
    svc = MemoryService(store, directory)
    svc.reset(step=2, start="2005-12-01")
    people = new_team(directory.snapshot(), "R&D", "R&D / D", ["Nora Lange", "Milan Zeman", "Nora Lange"],
                      joined="2006-01-01")
    assert [p.email for p in people] == [EMAIL("nora.lange"), EMAIL("milan.zeman"), EMAIL("nora.lange2")]
    assert people[0].is_manager and {p.manager_email for p in people} == {EMAIL("nora.lange")}
    other_replica = Directory(ex, ROOT / "seed" / "org.csv", ttl=0, miss_ttl=0)  # another app instance, re-reading
    directory.add_people(people)
    nora = EMAIL("nora.lange")
    assert directory.get(nora).team == "R&D / D" and other_replica.get(nora).team == "R&D / D"
    assert directory.teams_in("R&D", "2006-01-01") == ["R&D / A", "R&D / B", "R&D / C", "R&D / D"]
    assert used(svc, nora, "comms errors")["comms errors"].scope == "department"  # the department's, at once
    svc.set_consent(nora, remember=True, share=True)
    writes = svc.apply(nora, [term_op("hot racks", "racks R30 to R37", "rack BETWEEN 'R30' AND 'R37'")])
    assert [w.op for w in writes] == ["add", "team"]
    assert used(svc, EMAIL("milan.zeman"), "hot racks")["hot racks"].scope == "team"
    with pytest.raises(ValueError, match="already exists"):
        new_team(directory.snapshot(), "R&D", "R&D / D", ["X Y"], joined="2006-01-01")
    svc.reset(step=1, start="2005-12-01")  # back to the seed org chart
    assert not directory.known(nora)


def test_someone_who_moved_team_counts_for_the_new_team(tmp_path):
    ex = SqliteExecutor(str(tmp_path / "mem.db"))
    store = MemoryStore(ex)
    directory = Directory(ex, ROOT / "seed" / "org.csv", ttl=300)
    svc = MemoryService(store, directory)
    svc.reset(step=2, start="2005-12-01")
    svc.apply(TOMAS, [term_op("memory errors", "only correctable DDR errors", "message LIKE '%ddr error%'")])
    assert used(svc, PETRA, "memory errors")["memory errors"].overrides == 1
    ex.run("UPDATE org_people SET team = 'R&D / B' WHERE email = :e", {"e": TOMAS})  # the nightly sync moves him
    directory.refresh()
    assert used(svc, PETRA, "memory errors")["memory errors"].overrides == 0  # no longer her teammate
    assert used(svc, TOMAS, "memory errors")["memory errors"].scope == "personal"  # still his own
