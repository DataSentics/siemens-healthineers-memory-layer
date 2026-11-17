"""UI smoke test: the demo renders, answers, shows the memory drop-down and the 'added to memory' note, undo works,
a conflicting definition can be picked, an answer can be regenerated, and the demo clock moves (once)."""

import sqlite3
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from streamlit.testing.v1 import AppTest

from memory_demo import config, service
from memory_demo.extractor import MemoryOp, MemoryOps, TermRelevance

ROOT = Path(__file__).resolve().parents[1]


class FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_demo_flow(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "bgl.db")
    con.execute("CREATE TABLE bgl_events (event_id INTEGER, message TEXT)")
    con.commit()
    monkeypatch.setenv("SQLITE_DATA", str(tmp_path / "bgl.db"))
    monkeypatch.setenv("SQLITE_MEMORY", str(tmp_path / "mem.db"))
    for f in (config.llm, config.data_db, config.memory_store, config.org):
        f.cache_clear()
    answers = iter([AIMessage(content=f"Answer {i}") for i in range(10)])
    monkeypatch.setattr(config, "llm", lambda: FakeModel(messages=answers))
    ops = iter([[MemoryOp(op="add", item_type="term", term="memory errors", meaning="DDR and parity errors",
                          sql_hint="message LIKE '%ddr error%'", evidence="correction")]])
    def fake_extract(_llm, prev, msg, answer, applied, personal, **kwargs):
        rated = [TermRelevance(item_id=a["item_id"], level="medium", reason="partly") for a in applied if a.get("term")]
        return MemoryOps(ops=next(ops, []), relevance=rated)

    monkeypatch.setattr(service, "extract", fake_extract)

    def button(label, where=None):
        return next(b for b in (where or at).button if b.label == label)

    def answers_shown():
        return sum(m.name == "assistant" for m in at.chat_message)

    def shown(text, where=None):
        return any(text in m.value for m in (where or at).markdown)

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert shown("Dataset date: **1 Dec 2005**", at.sidebar)
    assert shown("**Memory for Eva**")  # never asked: the consent card shows, nothing is remembered yet
    button("Advance one month", at.sidebar).click().run()
    assert shown("Dataset date: **1 Jan 2006**", at.sidebar)
    assert button("Advance one month", at.sidebar).disabled  # February would have only 3 days of data last month
    button("Load pilot month", at.sidebar).click().run()
    assert shown("Dataset date: **1 Jan 2006**", at.sidebar)
    at.sidebar.selectbox[0].select(next(o for o in at.sidebar.selectbox[0].options if "Lukas" in o)).run()
    assert not shown("**Memory for Lukas**")  # the pilot team agreed during its month
    at.chat_input[0].set_value("memory errors on the noisy rack last month?").run()
    assert not at.exception
    assert any("Memory: 2 items applied" in e.label for e in at.expander)
    assert shown("Medium relevance") and shown("100% confidence")
    assert shown("Remembered</span>&nbsp; <b>memory errors</b>")  # above the answer, not in a drop-down
    texts = [m.value for m in at.chat_message[-1].markdown]
    first = lambda pred: next(i for i, t in enumerate(texts) if pred(t))
    assert first(lambda t: "Remembered" in t) < first(lambda t: t.startswith("Answer "))
    button("Undo").click().run()
    assert not at.exception
    assert any("Undone" in c.value for c in at.caption)

    # the team's "incidents" replaces the official one, which is marked untrustworthy; picking it redoes the
    # answer in place and keeps it for Lukas only
    at.chat_input[0].set_value("How many incidents did we have last month?").run()
    assert any("Memory: 1 item applied" in e.label for e in at.expander) and shown("Untrustworthy")
    button("Use this").click().run()
    assert not at.exception
    assert shown("Remembered</span>&nbsp; <b>incidents</b>")
    assert answers_shown() == 2

    # wrong, regenerate: answered again without that interpretation, still in place
    button("Wrong, regenerate").click().run()
    assert not at.exception and answers_shown() == 2

    # the organisation tab marks who is signed in
    assert shown("Lukas Brandt (you)")


class FailingModel(FakeModel):
    def _generate(self, *a, **k):
        raise TimeoutError("model did not answer")


def test_a_failed_turn_says_so_instead_of_hanging(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "bgl.db")
    con.execute("CREATE TABLE bgl_events (event_id INTEGER, message TEXT)")
    con.commit()
    monkeypatch.setenv("SQLITE_DATA", str(tmp_path / "bgl.db"))
    monkeypatch.setenv("SQLITE_MEMORY", str(tmp_path / "mem.db"))
    for f in (config.llm, config.data_db, config.memory_store, config.org):
        f.cache_clear()
    monkeypatch.setattr(config, "llm", lambda: FailingModel(messages=iter([])))
    import streamlit as st
    st.cache_resource.clear()  # the app caches its agents across AppTest runs in one process
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    at.chat_input[0].set_value("How many memory errors last month?").run()
    assert not at.exception
    assert any("could not be finished" in m.value and "TimeoutError" in m.value for m in at.markdown)


def test_consent_card_choice_is_kept(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "bgl.db")
    con.execute("CREATE TABLE bgl_events (event_id INTEGER, message TEXT)")
    con.commit()
    monkeypatch.setenv("SQLITE_DATA", str(tmp_path / "bgl.db"))
    monkeypatch.setenv("SQLITE_MEMORY", str(tmp_path / "mem.db"))
    for f in (config.llm, config.data_db, config.memory_store, config.org):
        f.cache_clear()
    monkeypatch.setattr(config, "llm", lambda: FakeModel(messages=iter([])))
    import streamlit as st
    st.cache_resource.clear()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert any("Memory for Eva" in m.value for m in at.markdown)
    next(b for b in at.button if b.label == "Save").click().run()
    at.run()  # once more: the Memory tab's toggles must not write their old state back
    assert not at.exception
    assert config.memory_service(with_llm=False).consent("eva.horak@shs-demo.example") == {"remember": True, "share": True}
    assert not any("Memory for Eva" in m.value for m in at.markdown)


def test_departments_new_team_and_already_in_memory(tmp_path, monkeypatch):
    """Department level and other departments in the Memory tab, a team added in the Admin tab works at once, a term
    only another department defines can be picked, and saying what memory holds shows 'Already in memory'."""
    con = sqlite3.connect(tmp_path / "bgl.db")
    con.execute("CREATE TABLE bgl_events (event_id INTEGER, message TEXT)")
    con.commit()
    monkeypatch.setenv("SQLITE_DATA", str(tmp_path / "bgl.db"))
    monkeypatch.setenv("SQLITE_MEMORY", str(tmp_path / "mem.db"))
    for f in (config.llm, config.data_db, config.memory_store, config.org):
        f.cache_clear()
    answers = iter([AIMessage(content=f"Answer {i}") for i in range(10)])
    monkeypatch.setattr(config, "llm", lambda: FakeModel(messages=answers))
    ops = iter([[], [], [MemoryOp(op="add", item_type="term", term="Comms-errors", evidence="stated",
                                  meaning="ciod socket errors between compute and I/O nodes.")]])
    monkeypatch.setattr(service, "extract", lambda *a, **k: MemoryOps(ops=next(ops, [])))
    import streamlit as st
    st.cache_resource.clear()

    def button(label):
        return next(b for b in at.button if b.label == label)

    def shown(text):
        return any(text in m.value for m in at.markdown)

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    button("Load pilot month").click().run()
    assert shown("#### Your department, R&D") and shown("#### Other departments") and shown("**Customer Support**")

    # Admin: a team appears the way the overnight sync would add it
    form_dept = next(s for s in at.selectbox if s.label == "Department")
    form_dept.select("R&D")
    next(t for t in at.text_input if t.label == "Team name").input("R&D / D")
    next(t for t in at.text_area if t.label.startswith("People")).input("Nora Lange\nMilan Zeman")
    button("Add team").click().run()
    assert not at.exception, at.exception
    assert any("Added R&D / D to R&D with 2 people" in s.value for s in at.success)
    user = at.sidebar.selectbox[0]
    nora = next(o for o in user.options if o.startswith("Nora Lange, R&D / D"))
    user.select(nora).run()
    assert shown("**Memory for Nora**")  # a new person: asked for consent first
    button("Save").click().run()
    at.chat_input[0].set_value("How many comms errors last month?").run()
    assert not at.exception
    assert any("Memory: 1 item applied" in e.label for e in at.expander) and shown("Your department")

    # Lukas: a term only Customer Support defines is shown, not applied, and can be picked
    user = at.sidebar.selectbox[0]
    user.select(next(o for o in user.options if "Lukas" in o)).run()
    at.chat_input[0].set_value("How many job failures last month?").run()
    assert any("only defined in other departments" in e.label for e in at.expander)
    assert shown("Customer Support department")
    button("Use this").click().run()
    assert not at.exception and shown("Remembered</span>&nbsp; <b>job failures</b>")

    # Saying what the department already holds, with other spelling: nothing is stored twice
    at.chat_input[0].set_value("comms errors means ciod socket errors between compute and I/O nodes").run()
    assert shown("Already in memory</span>&nbsp; <b>comms errors</b>") and shown("your department")
    assert not any(b.label == "Undo" for b in at.chat_message[-1].button)


def test_switching_users_twice_in_a_row_keeps_each_choice(tmp_path, monkeypatch):
    """Live bug on 11 Nov: picking a second user right after the first one fell back to the first."""
    con = sqlite3.connect(tmp_path / "bgl.db")
    con.execute("CREATE TABLE bgl_events (event_id INTEGER, message TEXT)")
    con.commit()
    monkeypatch.setenv("SQLITE_DATA", str(tmp_path / "bgl.db"))
    monkeypatch.setenv("SQLITE_MEMORY", str(tmp_path / "mem.db"))
    for f in (config.llm, config.data_db, config.memory_store, config.org):
        f.cache_clear()
    monkeypatch.setattr(config, "llm", lambda: FakeModel(messages=iter([])))
    import streamlit as st
    st.cache_resource.clear()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    for name in ("Lukas", "Tomas", "Petra", "Eva"):
        user = at.sidebar.selectbox[0]
        user.select(next(o for o in user.options if o.startswith(name))).run()
        assert not at.exception
        assert at.sidebar.caption[0].value.startswith(name.lower()), (name, at.sidebar.caption[0].value)
