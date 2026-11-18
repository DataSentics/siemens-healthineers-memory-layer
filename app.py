"""Demo UI (PRD §7 flows 6 and 7). Demo-only: in AskFST this lives in the main agent's front-end (TD S8).

    streamlit run app.py
"""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import date

import pandas as pd
import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from memory_demo import bgl, config, models, resolver
from memory_demo import org as org_mod
from memory_demo.agent import build_agent, build_supervisor
from memory_demo.casebook import Casebook
from memory_demo.middleware import UserContext, message_text

st.set_page_config(page_title="Data Assistant", page_icon=":material/query_stats:", layout="wide")

SCOPE_NAMES = {"personal": "Personal", "team": "Your team", "department": "Your department",
               "gold": "Official glossary", "other": "Other departments"}
AGENT_MODES = {"multi": "Supervisor with casebook", "single": "Single SQL agent"}
AVATARS = {"user": ":material/person:", "assistant": ":material/query_stats:"}


@st.cache_resource
def services():
    memory = config.memory_service()
    day = memory.store.latest_day()
    if day:  # app restarted on an existing memory: continue where the demo clock was
        models.set_today(max(day, config.DEMO_TODAY))
    else:
        memory.reset(step=1, start=config.DEMO_TODAY)
    casebook = Casebook(memory.store, memory.org)
    llm, db = config.llm(), config.data_db()
    agents = {"multi": build_supervisor(llm, db, memory, casebook), "single": build_agent(llm, db, memory)}
    return memory, casebook, agents


try:
    memory, casebook, agents = services()
except Exception as e:  # missing env vars / credentials: say what to set instead of a stack trace
    st.error(f"Could not start the agent: {e}\n\nSee README.md, section Configuration.")
    st.stop()
org = config.org()
ss = st.session_state
ss.setdefault("chats", {})  # email -> list of messages
ss.setdefault("regen", None)  # pending regenerate: {email, n, exclude, writes}
ss.setdefault("consent", {})  # email -> {"remember", "share"} or None; read once per session, updated on change
if notice := ss.pop("notice", None):
    st.toast(notice, icon=":material/calendar_month:")


def day_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.day} {d:%b %Y}"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# Palette: petrol #009999, orange #EC6602, berry #B21C5A, black text. Confidence goes from cool (petrol, everyone
# agrees) through orange to warm (berry, one short of being replaced); relevance uses the same three colours.
# Text is the same hue, darker, so it stays readable on white.
PETROL, ORANGE, BERRY = "#009999", "#EC6602", "#B21C5A"
CONF_BANDS = [(0.5, BERRY), (0.8, ORANGE), (1.01, PETROL)]  # upper bound, colour: 33% / 67% / 100% for a team of 5
RELEVANCE = {"high": PETROL, "medium": ORANGE, "low": BERRY}


def _mix(hex_colour: str, other: tuple[int, int, int], t: float) -> str:
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (o - x) * t):02x}" for x, o in zip((r, g, b), other))


def _pill(text: str, colour: str, title: str = "", dot: bool = True) -> str:
    tint, edge, ink = _mix(colour, (255, 255, 255), 0.88), _mix(colour, (255, 255, 255), 0.65), _mix(colour, (0, 0, 0), 0.3)
    mark = (f'<span style="display:inline-block;width:7px;height:7px;border-radius:1px;background:{colour};'
            f'margin-right:6px;vertical-align:1px"></span>') if dot else ""
    return (f'<span title="{html.escape(title)}" style="background:{tint};color:{ink};border:1px solid {edge};'
            f'border-radius:3px;padding:1px 7px 1px {6 if dot else 7}px;font-size:0.78rem;font-weight:600;'
            f'white-space:nowrap{";cursor:help" if title else ""}">{mark}{text}</span>')


def confidence_tag(c: float | None, title: str = "") -> str:
    """How much the team agrees with this meaning. None: the user's own item."""
    if c is None:
        return ('<span title="Your own definition" style="background:#f2f2f2;color:#000;border:1px solid #d6d6d6;'
                'border-radius:3px;padding:1px 7px;font-size:0.78rem;font-weight:600;white-space:nowrap;'
                'cursor:help">Yours</span>')
    colour = next(col for top, col in CONF_BANDS if c < top)
    return _pill(f"{c:.0%} confidence", colour, title)


def relevance_text(level: str | None, reason: str | None) -> str:
    """How well the term fitted this question, in the assistant's own judgement (reason on hover)."""
    if not level:
        return ""
    ink = _mix(RELEVANCE[level], (0, 0, 0), 0.3)
    return (f'<span title="{html.escape(reason or "")}" style="color:{ink};font-size:0.8rem;font-weight:600;'
            f'white-space:nowrap;cursor:help">{level.capitalize()} relevance</span>')


def untrustworthy_tag(title: str = "Teams in several departments have replaced this official definition") -> str:
    return _pill("Untrustworthy", BERRY, title, dot=False)


# "Gold" in gold, with "Official definition" on hover. Dark gold text on a pale gold pill keeps it readable.
GOLD_BADGE = ('<span title="Official definition" style="background:#fbf1c7;color:#7a5c00;border:1px solid #e8cf7a;'
              'border-radius:3px;padding:1px 7px;font-size:0.78rem;font-weight:600;cursor:help;'
              'white-space:nowrap">Gold</span>')


def is_gold(support: str) -> bool:
    return support.startswith("gold")


def support_html(support: str) -> str:
    if is_gold(support):
        return GOLD_BADGE
    return f"<span style='color:#6b7280;font-size:0.85rem'>{html.escape(support.capitalize())}</span>"


def gray(text: str) -> str:
    return f"<span style='color:#6b7280;font-size:0.85rem'>{html.escape(text)}</span>"


def uses_it(n: int) -> str:
    return f"{plural(n, 'colleague')} in your team {'uses' if n == 1 else 'use'} this"


def agreement(overrides: int, needed: int) -> str:
    if not overrides:
        return "nobody in your team uses another meaning"
    return resolver.disagreement(overrides, needed)


# ---- sidebar: user and demo controls ----------------------------------------------------------------------
people = org.people()  # from the org directory: people and teams added by the sync appear on their own
with st.sidebar:
    st.markdown("### :blue[:material/query_stats:] Data Assistant")
    # Keyed, so the choice lives in the session state and survives the options changing when the directory does.
    if ss.get("me") not in people:  # first run, or that person is no longer in the directory
        ss.me = next(p for p in people if p.email.startswith("eva.horak"))
    me = st.selectbox("User", people, key="me",
                      format_func=lambda p: f"{p.name}, {p.team}{' (manager)' if p.is_manager else ''}")
    st.caption(me.email)
    if st.button("New conversation", icon=":material/edit_square:", width="stretch"):
        ss.chats.pop(me.email, None)

    st.divider()
    st.markdown("**Demo controls**")
    today = models.today()
    st.markdown(f"Dataset date: **{day_label(today)}**  \n"
                f":gray[Last month = {date.fromisoformat(models.months_later(today, -1)):%B %Y}]")
    next_day = models.months_later(today)
    if st.button("Advance one month", icon=":material/calendar_month:", width="stretch",
                 disabled=next_day > bgl.LAST_DAY, help="Conversations start fresh. Disabled once last month would "
                                                         "run past the end of the log."):
        models.set_today(next_day)
        ss.chats.clear()
        st.rerun()
    fuzzy = st.toggle("Find terms with Mem0", value=True, key="fuzzy",
                      help="Also recognise terms by meaning (Mem0 embeddings, e.g. RAM problems → memory errors) and "
                           "by spelling (typos). Off: only the exact wording, aliases and plurals count.")
    if fuzzy and not ss.get("index_ready"):  # build the index now, not during the first question (about 10 s)
        with st.spinner("Building the Mem0 term index"):
            try:
                memory.term_index().sync(memory.store.candidate_items(me.email, memory.teammates(me)))
                ss.index_ready = True
            except Exception as e:  # answers still work, with exact wording only
                st.caption(f"Mem0 is unavailable ({type(e).__name__}); terms are matched by exact wording.")
    mode = st.radio("Agent", list(AGENT_MODES), format_func=AGENT_MODES.get,
                    help="Both use the same memory. The supervisor also reuses earlier answers from the casebook.")
    if st.button("Reset memory", width="stretch", help=f"Official glossary only, the seed org chart, dataset date "
                                                         f"{config.DEMO_TODAY}"):
        ss.chats.clear()
        ss.consent.clear()
        with st.spinner("Resetting memory"):
            memory.reset(step=1, start=config.DEMO_TODAY)
        st.rerun()
    if st.button("Load pilot month", width="stretch", help="A month later: the teams have used the assistant "
                                                             "for a month (the seed org chart)"):
        ss.chats.clear()
        ss.consent.clear()
        with st.spinner("A month passes: loading the pilot month"):
            memory.reset(step=2, start=config.DEMO_TODAY)
        ss.notice = f"A month later: {day_label(models.today())}. The teams' first month of use is loaded."
        st.rerun()
    st.caption(f"Personal memory: {'Mem0' if memory.personal else 'memory store'} · "
               "Data: Blue Gene/L event log (Loghub)")

def my_consent() -> dict | None:
    if me.email not in ss.consent:
        ss.consent[me.email] = memory.consent(me.email)
    return ss.consent[me.email]


def save_consent(remember: bool, share: bool) -> None:
    memory.set_consent(me.email, remember, share)
    ss.consent[me.email] = {"remember": remember, "share": remember and share}


chat = ss.chats.setdefault(me.email, [])
resolver_needed = resolver.needed(org.team_size(me.team, models.today()))
tab_chat, tab_memory, tab_org, tab_admin, tab_guide = st.tabs(["Chat", "Memory", "Organisation", "Admin", "Guide"])


# ---- rendering helpers ------------------------------------------------------------------------------------
def regenerate(n: int, exclude: tuple[str, ...] = (), writes: list[dict] | None = None) -> None:
    """Redo the answer at chat[n] for the same question (FR10). A rejected answer leaves the casebook."""
    if case_id := chat[n].get("case_id"):
        casebook.withdraw(case_id)
    ss.regen = {"email": me.email, "n": n, "exclude": exclude, "writes": writes or []}
    st.rerun()


def item_title(it: dict) -> str:
    if it["item_type"] == "preference":
        return f"Preference: {it['meaning']}"
    if not it.get("term"):
        return f"Noted earlier: {it['meaning']}"
    return f"**{it['term']}** = {it['meaning']}"


def alternative_source(alt: dict) -> str:
    """Where another meaning comes from, without names."""
    if alt["scope"] == "colleagues":
        return gray(uses_it(alt["holders"]))
    if alt["scope"] == "gold":
        parts = [GOLD_BADGE] + ([untrustworthy_tag()] if alt.get("untrustworthy") else [])
    elif alt["scope"] == "other_department":
        parts = [gray(f"{alt['department']} department")]
    else:
        parts = [gray({"team": "Your team", "department": "Your department"}[alt["scope"]])]
    if alt.get("holders"):
        parts.append(gray(uses_it(alt["holders"])))
    return "&nbsp; ".join(parts)


def alternative_rows(it: dict, key: str, i: int, n: int, latest: bool) -> None:
    """Other definitions in use, each with Use this (on the latest answer)."""
    alts = it.get("alternatives") or []
    if alts:
        st.markdown("Other definitions in use")
    for j, alt in enumerate(alts):
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"{html.escape(alt['meaning'])}  \n{alternative_source(alt)}", unsafe_allow_html=True)
        remembers = (my_consent() or {}).get("remember")
        if latest and c2.button("Use this", key=f"{key}-{i}-alt{j}", type="secondary", disabled=not remembers,
                                help="Answer again with this definition and keep it" if remembers
                                else "Turn on memory (Memory tab) to keep a definition"):
            try:
                writes = memory.choose(me.email, it["item_id"], alt["item_id"])
            except ValueError as e:  # the meaning changed or went away since this answer
                st.toast(str(e))
            else:
                regenerate(n, writes=[w.to_dict() for w in writes])


def render_elsewhere(it: dict, key: str, i: int, n: int, latest: bool) -> None:
    """A term only other departments define: their meanings are shown, never applied."""
    with st.container(border=True):
        st.markdown(f"**{it['term']}**: not defined for you, your team or your department")
        st.markdown(gray("Not applied to this answer. Other departments' meanings are shown to everyone; pick one "
                         "to use it."), unsafe_allow_html=True)
        if it.get("matched_by"):
            st.caption(it["matched_by"].capitalize())
        alternative_rows(it, key, i, n, latest)


def render_trace(trace: dict, n: int, latest: bool) -> None:
    """`latest`: only the last answer can be redone (pick another meaning, or regenerate)."""
    if trace.get("note"):
        st.caption(trace["note"])
    items = trace.get("items") or []
    if not items:
        return
    key = f"{me.email}-{n}"
    warn = [it for it in items if it["level"] == "warning"]
    applied = [it for it in items if it.get("scope") != "unresolved"]
    elsewhere = [it for it in items if it.get("scope") == "unresolved"]
    if warn:
        terms = ", ".join(f"“{it['term']}”" for it in warn if it.get("term"))
        label = f":orange[:material/warning:] Memory: check the interpretation of {terms}"
    elif applied:
        label = f":blue[:material/memory:] Memory: {plural(len(applied), 'item')} applied"
    else:
        names = ", ".join(f"“{it['term']}”" for it in elsewhere)
        label = f":blue[:material/memory:] Memory: {names} is only defined in other departments"
    with st.expander(label):
        for i, it in enumerate(items):
            if it.get("scope") == "unresolved":
                render_elsewhere(it, key, i, n, latest)
                continue
            with st.container(border=True):
                st.markdown(item_title(it))
                tags = [confidence_tag(it["confidence"], it["reason"]), support_html(it["support_label"])]
                if it.get("untrustworthy"):
                    tags.append(untrustworthy_tag())
                if it.get("scope") == "personal" and it.get("months_unused"):
                    tags.append(gray(f"unused for {resolver.plural_months(it['months_unused'])}"))
                if it.get("relevance"):
                    tags.append(relevance_text(it["relevance"], it.get("relevance_reason")))
                st.markdown("&nbsp; ".join(tags), unsafe_allow_html=True)
                if it.get("matched_by"):
                    st.caption(it["matched_by"].capitalize())
                if it["level"] == "warning" or it.get("untrustworthy"):
                    st.caption(it["reason"])
                alternative_rows(it, key, i, n, latest)
                b2, b3, _ = st.columns([2, 2, 3])
                if latest and b2.button("Wrong, regenerate", key=f"{key}-{i}-down", type="tertiary",
                                        icon=":material/refresh:",
                                        help="Answer again without this. Say in the chat what it should mean."):
                    memory.feedback(me.email, it["item_id"], accept=False)
                    regenerate(n, exclude=tuple(trace.get("excluded") or ()) + (it["item_id"],))
                if it["level"] == "warning" and it.get("term") and b3.button(
                        "Don't warn me again", key=f"{key}-{i}-dismiss", type="tertiary",
                        icon=":material/notifications_off:"):
                    memory.dismiss_warning(me.email, it["term"])
                    st.toast(f"No more warnings for “{it['term']}”")
        st.caption(f"Lookup {trace.get('latency_ms', 0)} ms" + (" · Mem0 matching on" if fuzzy else ""))


def shown_writes(writes: list[dict]) -> list[dict]:
    """A user's own copy folded into a team update, or a team meaning folded into a department update, is part of
    that update, not a change of its own."""
    folded = {i for w in writes if w["op"] in ("team", "department") and not w.get("undone")
              for i in w.get("folded_ids", [])}
    return [w for w in writes if w["item_id"] not in folded]


def remembered_line(w: dict) -> str:
    """One quiet line: what was remembered and who sees it. Petrol edge, small text, full meaning on hover."""
    verb = {"add": "Remembered", "update": "Updated your memory", "withdraw": "Removed from your memory",
            "team": "Team meaning updated" if w.get("note") else "Remembered",  # no note: a new term
            "department": "Department meaning updated" if w.get("superseded_id") else "Now your department's",
            "same": "Already in memory"}[w["op"]]
    meaning = w["meaning"] if len(w["meaning"]) <= 110 else w["meaning"][:107].rstrip() + "…"
    what = (f"<b>{html.escape(w['term'])}</b>: " if w["term"] else "") + html.escape(meaning)
    who = w["shared_with"] + (f" · {w['note']}" if w.get("note") else "")
    return (f'<div title="{html.escape(w["meaning"])}" style="font-size:0.85rem;line-height:1.35;background:#f3f8f8;'
            f'border-left:3px solid {PETROL};border-radius:4px;padding:5px 10px">'
            f'<span style="color:{_mix(PETROL, (0, 0, 0), 0.3)};font-weight:600">{verb}</span>&nbsp; {what} '
            f'<span style="color:#6b7280">· {html.escape(who)}</span></div>')


def render_writes(writes: list[dict], n: int) -> None:
    """What this turn saved, shown above the answer (FR13: visible, not buried), each with Undo."""
    for i, w in enumerate(shown_writes(writes)):
        if w.get("undone"):
            st.caption(f"Undone: {w['term'] or w['meaning']}")
            continue
        c1, c2 = st.columns([12, 1], vertical_alignment="center")
        c1.markdown(remembered_line(w), unsafe_allow_html=True)
        if w["op"] != "same" and c2.button("Undo", key=f"{me.email}-{n}-w{i}", type="tertiary"):
            try:
                memory.undo(me.email, w)
            except ValueError as e:  # already undone, or changed since
                st.toast(str(e))
            else:
                w["undone"] = True
                st.rerun()


def render_cases(cases: list[dict]) -> None:
    if cases:
        with st.expander(f":blue[:material/library_books:] Casebook: {plural(len(cases), 'similar question')}"):
            for c in cases:
                st.markdown(f"“{c['question']}”  \n:gray[Asked by {c['source']} on {day_label(c['asked_on'])}]")
                st.code(c["sql"], language="sql")


def render_sql(sql: list[str]) -> None:
    if sql:
        with st.expander(":gray[:material/code:] SQL"):
            for q in sql:
                st.code(q, language="sql")


def run_turn(exclude: tuple[str, ...] = (), writes: list[dict] | None = None) -> dict:
    """Answer the last user message in `chat`, streaming progress; returns the assistant message."""
    history = [HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"]) for m in chat]
    reply = {"role": "assistant", "content": "", "trace": {}, "writes": list(writes or []), "sql": [], "cases": []}
    labels = {"search_casebook": "Searching the casebook", "ask_sql_agent": "Querying the data",
              "sql_db_query": "Querying the data"}
    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        status = st.status("Checking memory", expanded=False)
        answer_slot = st.empty()
        try:
            _stream_turn(history, exclude, reply, status, answer_slot, labels)
        except Exception as e:  # timeouts and lost connections: say so instead of hanging or showing a trace
            status.update(label="Could not finish", state="error")
            answer_slot.empty()
            reply["content"] = ("Sorry, this answer could not be finished: the model or the data warehouse did not "
                                f"respond in time ({type(e).__name__}). Please ask again.")
            return reply
        status.update(label="Done", state="complete")
        answer_slot.empty()
    return reply


def _stream_turn(history, exclude, reply, status, answer_slot, labels) -> None:
    """Run the agent and fill `reply` from its updates as they arrive."""
    for chunk in agents[mode].stream({"messages": history},
                                     context=UserContext(user_email=me.email, exclude=exclude, fuzzy=fuzzy),
                                     stream_mode="updates"):
        for node, upd in chunk.items():
            if not upd:
                continue
            if node.endswith("before_agent"):
                reply["trace"] = {**(upd.get("memory_trace") or {}), "excluded": list(exclude)}
                status.update(label="Thinking")
            elif node == "model":
                msg = upd["messages"][-1]
                for tc in getattr(msg, "tool_calls", []) or []:
                    if tc["name"] == "sql_db_query":
                        reply["sql"].append(tc["args"].get("query", ""))
                    if tc["name"] in labels:
                        status.update(label=labels[tc["name"]])
                if not getattr(msg, "tool_calls", None):
                    reply["content"] = message_text(msg)
                    answer_slot.markdown(reply["content"])
                    status.update(label="Updating memory")
            elif node == "tools":  # supervisor tools carry their results as artifacts
                for tm in upd.get("messages", []):
                    art = getattr(tm, "artifact", None) or {}
                    reply["sql"] += art.get("sql", [])
                    reply["cases"] += art.get("cases", [])
            elif node.endswith("after_agent"):
                for it in reply["trace"].get("items", []):
                    if rated := (upd.get("memory_relevance") or {}).get(it["item_id"]):
                        it["relevance"], it["relevance_reason"] = rated["level"], rated["reason"]
                if upd.get("casebook_saved"):
                    reply["case_id"] = upd["casebook_saved"]
                reply["writes"] += upd.get("memory_writes") or []


# ---- chat -------------------------------------------------------------------------------------------------
with tab_chat:
    regen = ss.regen if ss.regen and ss.regen["email"] == me.email else None
    if regen:
        del chat[regen["n"]:]  # drop the answer being redone (and anything after it)
    if my_consent() is None:
        with st.container(border=True):
            st.markdown(f"**Memory for {me.name.split()[0]}** :gray[· nothing is remembered until you choose]")
            remember = st.checkbox("Remember what I teach you: definitions and preferences", value=True,
                                   key=f"consent-remember-{me.email}")
            share = st.checkbox("Let my team see my own definitions, without my name, and count them when the team "
                                "decides on a meaning", value=True, key=f"consent-share-{me.email}",
                                disabled=not remember)
            if st.button("Save", key=f"consent-save-{me.email}", type="secondary"):
                save_consent(remember, share)
                st.rerun()
            st.caption("You can change this any time in the Memory tab.")
    if not chat:
        st.caption("Ask about the Blue Gene/L event log, for example: How many memory errors did we have last "
                   "month, by rack?")
    for n, m in enumerate(chat):
        with st.chat_message(m["role"], avatar=AVATARS[m["role"]]):
            if m["role"] == "assistant":
                render_writes(m.get("writes") or [], n)
            st.markdown(m["content"])
            if m["role"] == "assistant":
                render_trace(m.get("trace") or {}, n, latest=n == len(chat) - 1)
                render_cases(m.get("cases") or [])
                render_sql(m.get("sql") or [])

    if regen:
        ss.regen = None
        chat.append(run_turn(regen["exclude"], regen["writes"]))
        st.rerun()
    if prompt := st.chat_input("Ask a question about the data"):
        chat.append({"role": "user", "content": prompt})
        with st.chat_message("user", avatar=AVATARS["user"]):
            st.markdown(prompt)
        chat.append(run_turn())
        st.rerun()


# ---- memory viewer (FR21) ---------------------------------------------------------------------------------
with tab_memory:
    view = memory.view(me.email)
    st.caption("What the assistant uses when it answers you. Shared definitions never show who added them. "
               "Hover over a tag for details; the Guide tab explains them.")
    current = my_consent() or {"remember": False, "share": False}
    # The keys carry the stored choice: when it changes elsewhere (the consent card in the chat), these toggles are
    # new widgets showing it, instead of keeping their old state and writing it back over the user's choice.
    version = f"{me.email}-{current['remember']}-{current['share']}"
    s1, s2 = st.columns(2)
    remember = s1.toggle("Remember what I teach you", value=current["remember"], key=f"set-remember-{version}")
    share = s2.toggle("Share my own definitions with my team (no name)", value=current["share"],
                      disabled=not remember, key=f"set-share-{version}")
    if (remember, share and remember) != (current["remember"], current["share"]):
        save_consent(remember, share)
        st.rerun()
    def memory_row(r: dict, scope: str) -> None:
        c1, c2 = st.columns([8, 1], vertical_alignment="center")
        head = f"**{html.escape(r['term'])}**" if r["term"] else f"**{r['type'].capitalize()}**"
        tags, meta = [], []
        if scope == "gold":
            tags.append(GOLD_BADGE)
        if r["untrustworthy"]:
            tags.append(untrustworthy_tag())
        if r["confidence"] is not None:
            tip = agreement(r["overrides"], resolver_needed).capitalize()
            if r["months_unused"]:
                tip += f"; not used for {resolver.plural_months(r['months_unused'])}"
            tags.append(confidence_tag(r["confidence"], tip))
        if r["months_unused"]:
            meta.append(f"unused for {resolver.plural_months(r['months_unused'])}")
        if r["replaced"]:
            meta.append("a closer level (your team or department) uses its own meaning instead")
        if scope == "personal" and r["shown_to_team"]:
            meta.append("colleagues see it as another meaning, without your name")
        if r.get("where") == "mem0":
            meta.append("Mem0")
        meta.append(day_label(r["created_at"][:10]))
        line = "&nbsp; ".join(tags + [gray(" · ".join(meta))])
        c1.markdown(f"{head}: {html.escape(r['meaning'])}  \n{line}", unsafe_allow_html=True)
        if scope == "personal" and c2.button("Forget", key=f"forget-{r['item_id']}", type="tertiary"):
            memory.forget(me.email, r["item_id"])
            st.rerun()

    def by_term(rows: list[dict]) -> list[dict]:
        return sorted(rows, key=lambda r: (r["type"] != "preference", r["term"] or ""))

    for scope in ("personal", "team", "department", "gold"):
        rows = view[scope]
        title = f"Your department, {me.department}" if scope == "department" else SCOPE_NAMES[scope]
        st.markdown(f"#### {title} :gray[{len(rows)}]")
        if not rows:
            st.caption("Nothing yet.")
        for r in by_term(rows):
            memory_row(r, scope)
    others = defaultdict(list)
    for r in view["other"]:
        others[r["department"]].append(r)
    st.markdown(f"#### {SCOPE_NAMES['other']} :gray[{len(view['other'])}]")
    st.caption("Every department's meanings are visible to everyone. They are never applied to your answers; when "
               "an answer uses one of these terms, they are listed there and you can pick one.")
    if not others:
        st.caption("Nothing yet.")
    for dept in sorted(others):
        st.markdown(f"**{dept}**")
        for r in by_term(others[dept]):
            memory_row(r, "other")


# ---- organisation: who sits where -------------------------------------------------------------------------
with tab_org:
    st.caption("From the org directory (email to department and team), re-read every few minutes: a team that the "
               "HR sync adds overnight shows up here on its own, starts with an empty team memory and gets its "
               "department's meanings. What one person teaches about an existing term stays theirs until half the "
               "team uses the same meaning; a brand-new term goes to the team straight away; a team meaning becomes "
               "the department's once half of its teams (at least two) use it.")
    shared = memory.store.shared_counts()
    depts: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for p in people:
        depts[p.department][p.team].append(p)
    for col, (dept, teams) in zip(st.columns(len(depts)), sorted(depts.items())):
        with col:
            n = shared["department"].get(dept, 0)
            st.markdown(f"#### {dept}")
            st.caption(plural(n, "department meaning") if n else "No department meanings yet")
            for team, members in teams.items():
                n = shared["team"].get(team, 0)
                st.markdown(f"**{team}**" + (f" :gray[· {plural(n, 'team meaning')}]" if n else ""))
                st.markdown("\n".join(
                    f"- {'**' if p.email == me.email else ''}{p.name}{' (you)' if p.email == me.email else ''}"
                    f"{'**' if p.email == me.email else ''}{', manager' if p.is_manager else ''}"
                    f" :gray[· joined {day_label(p.joined)}]" for p in members))


# ---- admin: background signals (FR11, A12) and casebook ---------------------------------------------------
with tab_admin:
    st.markdown("#### Org directory")
    st.caption(f"Teams and departments come from the directory table, which the HR or Entra ID sync keeps up to "
               f"date; the app re-reads it every {max(1, round(org.ttl / 60))} minutes (last read "
               f"{org.read_at_wall:%H:%M:%S} UTC). Nothing is set up per team in the memory layer. To try it, add a "
               f"team here the way the sync would overnight; Reset memory removes it again.")
    if done := ss.pop("admin_notice", None):
        st.success(done)
    with st.form("add-team", clear_on_submit=True):
        c1, c2 = st.columns(2)
        dept_pick = c1.selectbox("Department", org.department_names() + ["New department"])
        new_dept = c1.text_input("Name of the new department", placeholder="Only for a new department")
        team_name = c2.text_input("Team name", placeholder="e.g. R&D / D")
        names = c2.text_area("People, one per line (the first one is the manager)",
                             placeholder="Nora Lange\nMilan Zeman\nJulia Werner")
        if st.form_submit_button("Add team"):
            dept = new_dept.strip() if dept_pick == "New department" else dept_pick
            try:
                added = org_mod.new_team(org.snapshot(), dept, team_name, names.splitlines(), joined=models.today())
            except ValueError as e:
                st.error(str(e))
            else:
                org.add_people(added)
                who = "1 person" if len(added) == 1 else f"{len(added)} people"
                ss.admin_notice = (f"Added {team_name.strip()} to {dept} with {who}. "
                                   "Pick one of them in the sidebar.")
                st.rerun()
    if st.button("Re-read the directory now", type="tertiary", icon=":material/refresh:"):
        org.refresh()
        st.rerun()

    st.markdown("#### Acceptance signals")
    st.caption("How definitions are shown, confirmed, corrected and dismissed, per team. Not shown to users.")
    rows = memory.store.signal_summary()
    if rows:
        df = pd.DataFrame(rows).pivot_table(index=["term", "origin", "team"], columns="kind", values="n",
                                            aggfunc="sum", fill_value=0)
        st.dataframe(df, width="stretch")
    else:
        st.caption("No signals yet.")
    st.markdown("#### Untrustworthy official terms")
    st.caption("Official terms that teams or departments in at least half of the departments have replaced with "
               "their own meaning. Nothing changes automatically; the glossary owners should look at them.")
    flagged = memory.untrustworthy()
    if flagged:
        st.dataframe(pd.DataFrame([{"term": t, "replaced in": ", ".join(d)} for t, d in flagged.items()]),
                     width="stretch", hide_index=True)
    else:
        st.caption("None.")
    st.markdown("#### Casebook")
    st.caption("Saved after each answer that ran SQL, unless it used someone's own meaning. Withdrawn when the "
               "answer is corrected or rejected.")
    cases = casebook.all()
    if cases:
        st.dataframe(pd.DataFrame(cases), width="stretch", hide_index=True)
    else:
        st.caption("No cases yet.")


# ---- guide: how the memory works ------------------------------------------------------------------------
with tab_guide:
    team_size = org.team_size(me.team, models.today())
    dept_teams = org.teams_in(me.department, models.today())
    st.markdown("#### Where a meaning comes from")
    st.markdown(
        "When a question uses a term, the closest definition wins:\n\n"
        "1. **Personal**: what you taught the assistant. Only you see it.\n"
        "2. **Your team**: the meaning your team agreed on. Shown without names.\n"
        "3. **Your department**: the meaning most of your department's teams agreed on.\n"
        "4. **Official glossary** " + GOLD_BADGE + ": the company definition, for everyone.\n\n"
        "**Other departments'** meanings are visible to everyone (Memory tab, and in an answer's drop-down under "
        "*Other definitions in use*), but they are never applied to your answers unless you pick one with **Use "
        "this**. Teams' meanings stay inside their team.", unsafe_allow_html=True)

    st.markdown("#### How a team meaning changes")
    st.markdown(
        f"A **new term** (no team, department or official meaning yet) goes to your team straight away, if you share. "
        f"A different meaning for an existing term stays yours. Colleagues see it as another meaning in use, "
        f"without your name, and can pick it with **Use this**. Once half the team (at least two) uses the same other "
        f"meaning, it becomes the team's. Your team, {me.team}, has {team_size} people, so "
        f"**{resolver_needed}** are needed.")

    st.markdown("#### How a department meaning forms")
    st.markdown(
        f"Once half of a department's teams (at least two, or all of them in a department with one team) have the "
        f"same team meaning, it becomes the department's, and those teams' copies fold into it. Teams with another "
        f"meaning keep theirs, since the team level comes first. {me.department} has {len(dept_teams)} "
        f"{'team' if len(dept_teams) == 1 else 'teams'}, so **{resolver.needed_teams(len(dept_teams))}** are needed. "
        f"Teams come from the org directory: a team added overnight works at once, with no setup.")

    st.markdown("#### What gets remembered")
    st.markdown(
        "You don't have to say *remember that*. When you state what a term means (\"by memory errors I mean only "
        "DDR errors\", \"we call R37 the noisy rack\"), correct an answer, or state a standing wish (\"keep it "
        "short\"), the assistant notices and saves it, and the line above the answer shows what was saved, with "
        "**Undo**. One-off details of a single question are not saved, and neither is the assistant's own guess.\n\n"
        "Only **definitions** can be shared. Something about your work that changes how you want answers (\"I look "
        "after racks R30 to R37\", \"LINKCARD alerts are mostly noise\") is saved for you only. Likes and opinions "
        "that would not change an answer (\"I like SOMARIS10\") are not saved at all, and neither is anything about "
        "other people, plans and deadlines, passwords, or customer and patient data.")

    st.markdown("#### The same meaning in other words")
    st.markdown(
        "Before anything is saved it is compared with what memory already holds, as Mem0 does: a different spelling "
        "of the term (\"SOMARIS 10\", \"memroy errors\"), different capitals, a typo, or the same meaning in other "
        "words counts as the same, so it is never stored twice. The line above the answer then says **Already in "
        "memory**. Your wording is matched to the existing one, so colleagues who mean the same thing are counted "
        "together. A different word for the same thing (\"link issues\" next to \"link card problems\") is a word of "
        "its own, so your word keeps working.")

    st.markdown("#### Confidence: how much your team agrees, and how recently the meaning was used")
    st.markdown("Two things lower it: colleagues who use another meaning, and months in which nobody used it.")
    rows = [(confidence_tag(1.0), "Nobody in your team uses another meaning, and it was used this month."),
            (confidence_tag(round(1 - 1 / 3, 2)), "Some colleagues use another meaning, or it has not been used for "
                                                  "about two months."),
            (confidence_tag(round(1 - 2 / 3, 2)), "Close to being replaced, or unused for about four months. Shown "
                                                  "as a warning, with the other meanings to pick from."),
            (confidence_tag(None), "Your own definition: nobody else's agreement is involved. It still fades.")]
    st.markdown("\n".join(f"- {tag}&nbsp; {html.escape(text)}" for tag, text in rows), unsafe_allow_html=True)
    st.caption(f"Confidence = (1 − colleagues using another meaning ÷ colleagues needed) × (1 − unused months ÷ "
               f"{resolver.DECAY_MONTHS}). Your team needs {resolver_needed}; the examples are for a team that needs 3. "
               "Official definitions only have the first part: they don't fade.")

    st.markdown("#### Finding terms: exact wording, or Mem0")
    st.markdown(
        "By default a term counts only when the question uses its exact wording, an alias or a plural. With **Find "
        "terms with Mem0** switched on (sidebar), it is also found **by meaning**, with Mem0 embeddings (\"RAM "
        "problems\" → memory errors), and **by spelling** (\"memroy errors\"). The drop-down then says how it was "
        "found. Mem0 only finds the terms; which meaning applies is decided by the same rules as always.")

    st.markdown("#### Fading: unused meanings lose confidence")
    st.markdown(
        f"Every full month a team meaning is not used, its confidence drops by a sixth (100% → 83% → 67% → 50% → "
        f"33% → 17%). Your own definitions show how long they have been unused instead. After "
        f"**{resolver.DECAY_MONTHS} months** without use a meaning is no longer applied, and the official definition "
        f"(if any) takes over again. Using a meaning resets the clock. Official definitions never fade; the glossary "
        f"owners keep them up to date.")

    st.markdown("#### Your consent")
    st.markdown(
        "Nothing is remembered until you agree. You choose two things, and can change them in the Memory tab: "
        "whether the assistant **remembers what you teach it**, and whether your own definitions are **shared with "
        "your team** (shown without your name and counted when the team decides on a meaning). Reading what your "
        "team and the official glossary say works either way.")

    st.markdown("#### Relevance: how well a term fitted your question")
    rel = [("high", "The question was about exactly this term, and the meaning fits."),
           ("medium", "The term came up, but the meaning fits only partly or mattered in passing."),
           ("low", "It matched by wording, but you probably meant something else.")]
    st.markdown("\n".join(f"- {relevance_text(level, '')}&nbsp; {html.escape(text)}" for level, text in rel),
                unsafe_allow_html=True)
    st.caption("The assistant's own judgement after each answer. Hover over the tag in an answer for its reason.")

    st.markdown("#### Other tags")
    st.markdown(
        f"- {GOLD_BADGE}&nbsp; Official definition from the company glossary.\n"
        f"- {untrustworthy_tag()}&nbsp; An official definition that teams or departments in at least half of the "
        f"departments have replaced with their own. Nothing changes automatically; the glossary owners should look "
        f"at it.",
        unsafe_allow_html=True)
