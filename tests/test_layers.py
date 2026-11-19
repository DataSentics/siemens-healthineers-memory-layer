"""The changes after the 5 Nov demo: one meaning in other words or with typos is never stored twice (Mem0's update
phase), the department level, other departments' meanings shown to everyone, teams read from the org directory,
and what is about the user stays private."""

import json
import re
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from memory_demo import consolidate, resolver, seeding
from memory_demo.extractor import MemoryOp, parse
from memory_demo.models import MemoryItem
from memory_demo.org import Directory, Org, new_team
from memory_demo.service import MemoryService
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
    return {u.term: u for u in svc.retrieve(email, q)[1].items}


def term_op(term, meaning, **kw):
    return MemoryOp(op="add", item_type="term", term=term, meaning=meaning, evidence="stated", **kw)


def item(meaning, term="x"):
    return MemoryItem(item_type="term", term=term, meaning=meaning, owner_email=None, owner_team=None,
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
    assert consolidate.same_meaning(item("Rack R37."), item("rack R37")) and not consolidate.same_meaning(
        item("rack R37"), item("rack R38"))


def test_the_demo_duplicate_two_wordings_become_one_team_meaning(svc):
    """5 Nov: Tomas typed 'Only correctable DDR errors, ...', Eva and Petra 'only the correctable DDR errors, ...'.
    Before, those were two meanings: Tomas's stayed next to the new team meaning."""
    svc.apply(TOMAS, [term_op("memory errors", "Only correctable DDR errors, without cache parity errors or CE sym "
                                               "events.")])
    eva = term_op("memory errors", "only the correctable DDR errors, without cache parity errors or CE sym events")
    [w] = svc.apply(EVA, [eva])
    assert w.op == "add" and w.meaning.startswith("Only correctable")  # matched to the wording already in use
    writes = svc.apply(PETRA, [eva])
    assert [w.op for w in writes] == ["add", "team"] and writes[1].note == "3 of 5 colleagues use this meaning"
    team = [r for r in svc.view(LUKAS)["team"] if r["term"] == "memory errors"]
    assert len(team) == 1 and team[0]["meaning"].startswith("Only correctable")
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
    [again] = svc.apply(TOMAS, [term_op("memory errors", "Correctable DDR memory errors plus cache parity errors "
                                                          "and CE sym events: messages that contain 'ddr error' or "
                                                          "'parity error', or start with 'CE sym'.")])  # restated
    assert (again.op, again.shared_with) == ("same", "your team")


def test_a_different_meaning_is_not_merged(svc):
    [w] = svc.apply(TOMAS, [term_op("memory errors", "only uncorrectable DDR errors")])
    assert w.op == "add"
    assert used(svc, TOMAS, "memory errors")["memory errors"].scope == "personal"


class MergeModel(GenericFakeChatModel):
    """Answers Mem0's merge question in Mem0's format: NONE for the listed meaning `same`, else ADD; or a fixed raw
    `reply`. Counts the calls and keeps the last prompt."""

    same: str | None = None
    reply: str | None = None
    calls: int = 0
    prompt: str = ""

    def invoke(self, messages, *a, **k):
        self.calls += 1
        self.prompt = messages[-1][1]
        if self.reply is not None:
            return AIMessage(self.reply)
        listed = self.prompt.split("The new retrieved facts")[0]
        if self.same and self.same in listed:
            mid = re.findall(r"'id': '(\d+)'", listed[:listed.index(self.same)])[-1]
            return AIMessage(json.dumps({"memory": [{"id": mid, "text": self.same, "event": "NONE"}]}))
        return AIMessage(json.dumps({"memory": [{"id": "9", "text": "new", "event": "ADD"}]}))


# The team's "memory errors" in other words.
REWORDED = ("DDR memory errors that were corrected, and cache parity and CE sym events: messages containing "
            "'ddr error' or 'parity error', or starting with 'CE sym'")


def test_mem0_decides_when_wording_is_not_enough(svc):
    team = used(svc, TOMAS, "memory errors")["memory errors"]
    llm = MergeModel(messages=iter([]), same=team.meaning)
    svc.llm = llm
    [w] = svc.apply(TOMAS, [term_op("RAM errors", REWORDED)])
    assert (w.op, w.term, w.shared_with) == ("same", "memory errors", "your team") and llm.calls == 1
    # Mem0's update prompt template, with our rule for definitions as its custom prompt.
    assert "shared memory of a data assistant" in llm.prompt and "The new retrieved facts" in llm.prompt
    # A meaning applied this turn is compared the same way: restating it in other words is agreement.
    applied = [vars(i) for i in svc.retrieve(TOMAS, "memory errors")[1].items]
    [w] = svc.apply(TOMAS, [term_op("memory errors", REWORDED)], applied)
    assert w.op == "same" and llm.calls == 2
    llm.same = None
    [w] = svc.apply(TOMAS, [term_op("memory errors", "only DDR errors")], applied)
    assert w.op == "add" and llm.calls == 3


def test_a_merge_follows_mem0s_answer_when_it_singles_out_one_meaning(svc):
    """No exact-values check overrides Mem0: "rack 37" can be R37. Its answer must single out one meaning: an ADD,
    Mem0's default listing of every meaning as unchanged, or an answer that cannot be read keeps the user's own."""
    team = used(svc, TOMAS, "the noisy rack")["the noisy rack"]
    svc.llm = MergeModel(messages=iter([]), same=team.meaning)
    [w] = svc.apply(TOMAS, [term_op("noisy rack", "rack 37")])
    assert (w.op, w.term, w.meaning) == ("same", "the noisy rack", team.meaning)
    r37, r38 = item("rack R37", "the noisy rack"), item("rack R38", "the noisy rack")
    llm = MergeModel(messages=iter([]), reply=json.dumps({"memory": [{"id": "1", "event": "NONE"}]}))
    assert consolidate.resolve("noisy rack", "rack 38", [r37, r38], llm).same is r38
    for reply in ({"memory": [{"id": "0", "event": "NONE"}, {"id": "1", "event": "NONE"}]},
                  {"memory": [{"id": "0", "event": "NONE"}, {"id": "2", "text": "rack 37", "event": "ADD"}]},
                  {"memory": [{"id": "7", "event": "NONE"}]}, "not json"):
        llm.reply = reply if isinstance(reply, str) else json.dumps(reply)
        assert consolidate.resolve("noisy rack", "rack 37", [r37, r38], llm).same is None, reply


def test_the_wording_shortcut_never_merges_different_values():
    from memory_demo.consolidate import same_details
    assert same_details("events at level FATAL", "fatal events")
    assert same_details('messages starting "data TLB error interrupt"',
                        "messages that start with \u201cdata TLB error interrupt\u201d")
    assert not same_details("rack R37", "rack R38")
    assert not same_details("level FATAL", "level FAILURE")
    assert not same_details("the KERNDTLB events", "the KERNSTLB events")
    assert not same_details('starting "data TLB error interrupt"', 'starting "data TLB error"')
    assert not consolidate.same_meaning(item("the KERNDTLB events"), item("the KERNSTLB events"))  # not a typo


def test_a_synonym_stays_a_term_of_its_own(svc):
    """Eval 12 Nov: 'link issues = LINKCARD events' was merged into 'link card problems' by the LLM, so nothing was
    saved and Tomas's own word never matched. Only spelling variants of a term merge; the LLM is not asked."""
    llm = MergeModel(messages=iter([]))
    svc.llm = llm
    writes = svc.apply(TOMAS, [term_op("link issues", "events from the LINKCARD component")])
    assert [w.op for w in writes] == ["add", "team"] and writes[1].term == "link issues" and llm.calls == 0
    assert used(svc, TOMAS, "any link issues last month?")["link issues"].scope == "team"
    assert "link card problems" in used(svc, TOMAS, "link card problems?")  # the existing term is untouched


def test_a_colleague_s_meaning_in_other_words_counts_together(svc):
    svc.apply(TOMAS, [term_op("memory errors", "only correctable DDR errors")])
    _, trace = svc.retrieve(PETRA, "memory errors")
    applied = [vars(i) for i in trace.items]
    svc.llm = MergeModel(messages=iter([]), same="only correctable DDR errors")
    [w] = svc.apply(PETRA, [term_op("memory errors", "just the DDR ones that got corrected")], applied)
    assert w.meaning == "only correctable DDR errors"  # her copy is his meaning, so the two count together
    assert used(svc, LUKAS, "memory errors")["memory errors"].alternatives[0].holders == 2


# ---- Mem0's extraction phase (the default extractor) -----------------------------------------------------------
def test_mem0_extraction_reads_facts_as_ops():
    from memory_demo.extractor import MEM0_SYSTEM, extract

    class Facts(GenericFakeChatModel):
        prompt: str = ""

        def invoke(self, messages, *a, **k):
            self.prompt = messages[-1][1]
            return AIMessage('```json\n{"facts": ["the noisy rack: rack R38", "relation node card: contains compute '
                             'cards", "forget: link issues", "forget: nothing known", {"fact": "x"}, "no term"], '
                             '"relevance": [{"item_id": "a1", "level": "low", "reason": "not about it"}, '
                             '{"item_id": "a2", "level": "huge"}]}\n```')

    own = [{"item_id": "o1", "term": "link issues", "meaning": "LINKCARD events"}]
    llm = Facts(messages=iter([]))
    ops = extract(llm, None, "the noisy rack is R38", "Here it is.", [], own, sql=["SELECT 1"])
    assert [(o.op, o.item_type, o.term, o.meaning, o.target_item_id) for o in ops.ops] == [
        ("add", "term", "the noisy rack", "rack R38", None),
        ("add", "relation", "node card", "contains compute cards", None),
        ("withdraw", "term", "link issues", "", "o1")]
    assert [(r.item_id, r.level) for r in ops.relevance] == [("a1", "low")]  # a rating that does not fit is dropped
    assert llm.prompt.startswith("Input:\nsystem: SQL that ran") and "user: the noisy rack is R38" in llm.prompt
    assert "is_joke" not in MEM0_SYSTEM and "op=" not in MEM0_SYSTEM and "Never save anything about the user" in MEM0_SYSTEM
    # Nothing readable: nothing saved, nothing rated.
    llm_bad = GenericFakeChatModel(messages=iter([AIMessage("sorry, no json here")]))
    assert extract(llm_bad, None, "hi", "hello", [], []) == extract(llm_bad.__class__(messages=iter([AIMessage("[]")])),
                                                                     None, "hi", "hello", [], [])


# ---- departments ---------------------------------------------------------------------------------------------
def test_needed_teams():
    assert [resolver.needed_teams(n) for n in (1, 2, 3, 4, 5)] == [1, 2, 2, 2, 3]


def test_a_department_meaning_forms_when_half_the_teams_agree_and_undo_restores(svc):
    svc.set_consent(ELENA, remember=True, share=True)  # R&D / C
    assert "the noisy rack" not in used(svc, BEN, "the noisy rack")
    writes = svc.apply(ELENA, [term_op("noisy rack", "Rack R37, which produces by far the most events.")])
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
        svc.apply(who, [term_op("comms errors", "every ciod message")])
    ben = used(svc, BEN, "comms errors")["comms errors"]
    assert (ben.scope, ben.meaning) == ("team", "every ciod message")
    assert [a.scope for a in ben.alternatives] == ["department"]
    assert "replaces your department's meaning" in ben.reason
    assert used(svc, LUKAS, "comms errors")["comms errors"].scope == "department"


def test_a_one_team_department_shares_new_terms_with_the_department(svc):
    svc.set_consent(FIONA, remember=True, share=True)  # Marketing has one team
    writes = svc.apply(FIONA, [term_op("launch week", "the first week after a release")])
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
def test_only_vocabulary_is_saved_or_read(svc):
    """Memory keeps definitions colleagues can use, nothing about the user themself, so no model decides whether a
    saved item may be shared: the share switch alone does."""
    out = parse({"ops": [{"op": "add", "item_type": "preference", "meaning": "keep it short", "evidence": "stated"},
                         {"op": "add", "item_type": "fact", "meaning": "went to Egypt last summer",
                          "evidence": "stated"},
                         {"op": "add", "item_type": "term", "term": "SOMARIS10", "meaning": "the CT scanner software",
                          "evidence": "stated", "private": True}]})
    assert [(o.item_type, o.term) for o in out.ops] == [("term", "SOMARIS10")]  # no preference, no fact
    with pytest.raises(ValueError):
        MemoryOp(op="add", item_type="fact", meaning="looks after racks R30 to R37", evidence="stated")
    [w, team] = svc.apply(TOMAS, out.ops)  # a "private" flag from a model changes nothing: Tomas shares
    assert team.op == "team" and used(svc, PETRA, "SOMARIS10 problems?")["somaris10"].scope == "team"
    # Preferences and facts saved before memory kept vocabulary only stay in the table, unread.
    svc.store.add_items([MemoryItem(item_type=t, meaning=m, owner_email=TOMAS, owner_team="R&D / A",
                                    owner_department="R&D") for t, m in (("preference", "keep it short"),
                                                                         ("fact", "on call for the I/O nodes"))])
    assert set(used(svc, TOMAS, "memory errors last month")) == {"memory errors"}
    assert all(r["type"] in ("term", "relation") for rows in svc.view(TOMAS).values() for r in rows)


def test_the_extractor_answer_survives_bad_ops():
    out = parse({"ops": [{"op": "add", "item_type": "term", "term": "x", "meaning": "y", "evidence": "stated"},
                         {"op": "add", "item_type": "nonsense", "meaning": "z", "evidence": "stated"}],
                 "relevance": [{"item_id": "a", "level": "high", "reason": "r"}, {"item_id": "b"}]})
    assert [o.evidence for o in out.ops] == ["stated"] and [r.item_id for r in out.relevance] == ["a"]


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
    writes = svc.apply(nora, [term_op("hot racks", "racks R30 to R37")])
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
    svc.apply(TOMAS, [term_op("memory errors", "only correctable DDR errors")])
    assert used(svc, PETRA, "memory errors")["memory errors"].overrides == 1
    ex.run("UPDATE org_people SET team = 'R&D / B' WHERE email = :e", {"e": TOMAS})  # the nightly sync moves him
    directory.refresh()
    assert used(svc, PETRA, "memory errors")["memory errors"].overrides == 0  # no longer her teammate
    assert used(svc, TOMAS, "memory errors")["memory errors"].scope == "personal"  # still his own
