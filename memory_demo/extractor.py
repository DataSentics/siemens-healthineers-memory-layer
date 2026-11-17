"""After-turn write decision (FR1, FR2, FR3, FR17): one structured LLM call per turn.

This is the extraction phase of Mem0 (Chhikara et al. 2025, section 2.1): from the latest exchange, with the earlier
messages as context, an LLM picks out what is worth keeping. Users do not have to say "remember that": a definition
or a standing wish stated in passing is enough. Only what the user said counts, never the assistant's own reading.
Comparing it with what memory already holds (Mem0's update phase) happens next, in `consolidate`.

The data the assistant works on is described to the model in plain text (`data`): which tables and columns there
are, so it can write each term's SQL filter. The demo's default is the BGL table; another assistant (AskFST) passes a
description of its own tables (see memory_demo/config.py, DATA_DESCRIPTION_FILE)."""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError, field_validator



class MemoryOp(BaseModel):
    op: Literal["add", "update", "withdraw"]
    item_type: Literal["preference", "term", "relation", "fact"]
    term: str | None = Field(None, description="For terms and relations: the user's term, as they write it. Null for "
                                               "preferences and facts.")
    meaning: str = Field(description="What it means for this user, one short sentence in plain language.")
    sql_hint: str | None = Field(
        None, description="For terms and relations: a SQL boolean expression over the columns of the data described "
                          "in the instructions that selects exactly the rows the term refers to. Null otherwise.")
    target_item_id: str | None = Field(None, description="For update/withdraw: the id of the memory item it changes.")
    evidence: Literal["correction", "stated", "inferred"] = Field(
        description="correction: the user corrected how the assistant read something. stated: the user said it "
                    "themself, whether or not they asked to remember it. inferred: only the assistant's own reading.")
    is_joke: bool = Field(False, description="True when the definition is clearly a joke or nonsense.")
    private: bool = Field(False, description="True only for a name that only makes sense for the user ('my rack') or "
                                             "a fact about them personally. Never true for a definition just because "
                                             "the user says 'for me' or 'I mean'. Private items are never shared "
                                             "with colleagues.")
    same_as_item_id: str | None = Field(
        None, description="When the user's meaning is one already listed under 'Memory items applied this turn' (an "
                          "applied meaning or another meaning in use), only worded differently: that meaning's id. "
                          "Never for a correction that changes it.")

    @field_validator("evidence", mode="before")
    @classmethod
    def _older_names(cls, v):  # the names used before: explicit -> stated, implicit -> inferred
        return {"explicit": "stated", "implicit": "inferred"}.get(v, v)


class TermRelevance(BaseModel):
    item_id: str = Field(description="Id of a memory item applied this turn.")
    level: Literal["high", "medium", "low"]
    reason: str = Field(description="Why, in a few words.")


class MemoryOps(BaseModel):
    ops: list[MemoryOp] = Field(default_factory=list)
    relevance: list[TermRelevance] = Field(
        default_factory=list, description="One entry per applied term or relation: how well it fitted this turn.")


SYSTEM = """You maintain the long-term memory of a data assistant that answers questions over the data described below.
After each turn you decide what durable knowledge to remember about the user. Most turns need nothing.

The data:
{data}

Save what stays true beyond this one question, whenever the user states it. They do not have to ask you to remember
it: a definition or a standing wish said in passing counts ("by X I mean Y", "X is Y", "we call Y X", "no, X means
Y", "I only look at Y", "always show Z"). Infer it only from the user's own words:
- term: what a word, name or abbreviation means in their work, e.g. "memory errors" = correctable DDR and parity
  errors, "the noisy rack" = rack R37, "SOMARIS10" = the software platform their CT scanners run on.
- relation: how components or event groups relate, e.g. a node card contains compute and I/O cards.
- preference: a standing wish about the form or focus of answers, e.g. "keep it short", "I only care about rack R37".
- fact: something about the user's work that could change how a later question should be answered: what they look
  after ("racks R30 to R37"), their duty ("on call for the I/O nodes"), what they treat as noise or as important
  ("LINKCARD alerts are mostly noise"). Save a fact only if it could change a future answer.

The strongest evidence is the user correcting how the assistant interpreted something ("no, X means Y").
Do NOT save: one-off parameters of a single question (a rack, a date range, a limit) unless the user states them as
a standing rule; questions and hypotheticals; anything about other people (what a colleague or manager thinks or
wants); temporary things (plans, deadlines, appointments, how the user feels today); passwords and other secrets;
data about customers or patients; likes, dislikes and opinions that would not change an answer ("I like SOMARIS10",
"I like apples"); background the org directory already has (their team, manager, when they joined); the assistant's
own interpretation of a term that the user neither stated nor confirmed (that is evidence=inferred, and it is never
saved).

A term is a definition: it would be true for any colleague who uses the word the same way. Liking, disliking or an
opinion about something is not a definition: "I like SOMARIS10" is not a term, and it is not saved at all.
Set private=true only for a name that only makes sense for the user ("my rack" = rack R12) or a fact about them
personally: private items are never shared. A definition is NOT private because
the user says "for me", "I mean" or "in my view": what someone teaches about a term is theirs anyway, and the memory
layer itself decides whether it counts for their team. Set is_joke=true for definitions that are clearly jokes or
nonsense.

Memory items applied this turn are already remembered: never add them again just because they were used. Only return
an op for one when the user corrects or refines it; then return op=update with its target_item_id.
If the user states a meaning that is already listed under 'Memory items applied this turn' (applied, or another
meaning in use) in other words, still return it, with same_as_item_id set to that meaning's id.
If the user asks to forget something, return op=withdraw with its target_item_id.
For terms and relations write sql_hint as a boolean SQL expression over the columns of the data. When the SQL
that ran this turn implements the user's definition, copy that condition from it exactly as it ran (leave out date
filters, grouping and limits): it was checked against the data. When the term names something that is not in the
data (a product, a site, a person), sql_hint is null. `meaning` is the definition only, one sentence in the user's words,
without repeating the term. Write the term as the user writes it.
Earlier messages of the conversation are context only: save only what the latest user message states (it may refer
back to them, e.g. "it is our scanner software" after a question about SOMARIS10).

Also rate every applied term or relation (not preferences) for how well it fitted what the user asked this turn:
- high: the question is about exactly this term and the remembered meaning fits what the user meant
- medium: the term came up, but the remembered meaning fits only partly or mattered only in passing
- low: it matched by wording, but the user probably meant something else, or it played no role in the answer
Judge from the question, the answer and the SQL; this is your own judgement, not a rule check."""


def bgl_data() -> str:
    """The demo's data: the BGL event table and its columns."""
    from .bgl import COLUMNS, TABLE

    cols = "\n".join(f"- {n} ({t}): {c}" for n, t, c in COLUMNS)
    return f"Table {TABLE}, one row per log event. Columns:\n{cols}"


def system_prompt(data: str | None = None) -> str:
    return SYSTEM.replace("{data}", (data or bgl_data()).strip())


MAX_MESSAGE, MAX_ANSWER, MAX_SQL = 4000, 2000, 2000  # characters of each part that reach the model
RECENT = 6  # earlier user messages passed as context (Mem0 uses the last m=10 messages of both sides)
ANSWER_WITH_TOOL = "\n\nAlways answer by calling the MemoryOps tool, with an empty ops list when there is nothing to remember."


def _applied_text(applied: list[dict]) -> str:
    """Applied items with the other meanings in use, so the model can tell a new wording from a new meaning."""
    lines = []
    for a in applied:
        if a.get("scope") == "unresolved":
            lines.append(f"- {a['term']}: not defined for this user (only other departments define it)")
        else:
            whose = {"personal": "theirs", "team": "their team's", "department": "their department's",
                     "gold": "official"}.get(a.get("scope"), "")
            lines.append(f"- [{a['item_id']}] {a.get('term') or a.get('item_type', 'preference')} ({whose}): "
                         f"{a['meaning']}")
        for alt in a.get("alternatives") or []:
            where = f"{alt['department']} department" if alt.get("department") else alt["scope"]
            lines.append(f"    another meaning in use [{alt['item_id']}] ({where}): {alt['meaning']}")
    return "\n".join(lines) or "none"


def extract(
    llm: BaseChatModel,
    previous_answer: str | None,
    user_message: str,
    answer: str,
    applied: list[dict],
    personal: list[dict],
    sql: list[str] | None = None,
    recent: list[str] | None = None,
    data: str | None = None,
) -> MemoryOps:
    """`recent`: the user's earlier messages in this conversation, oldest first (context only).
    `data`: the tables and columns the assistant answers from (default: the demo's BGL table)."""
    personal_txt = "\n".join(f"- [{p['item_id']}] {p.get('term') or p.get('item_type', 'preference')}: {p['meaning']}"
                             for p in personal) or "none"
    earlier = "\n".join(f"- {m[:300]}" for m in (recent or [])[-RECENT:]) or "none"
    human = (
        f"Earlier user messages in this conversation (context only):\n{earlier}\n\n"
        f"Previous assistant answer:\n{(previous_answer or 'none')[:MAX_ANSWER]}\n\n"
        f"User message:\n{user_message[:MAX_MESSAGE]}\n\n"
        f"Assistant answer to it:\n{answer[:MAX_ANSWER]}\n\n"
        f"SQL that ran to answer it (last one is final):\n"
        f"{chr(10).join(q[:MAX_SQL] for q in (sql or [])[-3:]) or 'none'}\n\n"
        f"Memory items applied this turn:\n{_applied_text(applied)}\n\n"
        f"The user's existing personal memory:\n{personal_txt}"
    )
    # Offered as a normal tool, not forced: Claude on Databricks rejects a forced tool_choice.
    msg = llm.bind_tools([MemoryOps]).invoke([("system", system_prompt(data) + ANSWER_WITH_TOOL), ("human", human)])
    call = next((tc for tc in getattr(msg, "tool_calls", []) or [] if tc["name"] == "MemoryOps"), None)
    return parse(call["args"]) if call else MemoryOps()


def parse(args: dict) -> MemoryOps:
    """The model's answer; an op or rating that does not fit the schema is dropped instead of failing the turn.
    Some models send a list as a JSON string; that is read too."""
    args = {k: _listed(v) for k, v in args.items()}
    try:
        return MemoryOps(**args)
    except ValidationError:
        ops, rated = [], []
        for raw in args.get("ops") or []:
            try:
                ops.append(MemoryOp(**raw))
            except (ValidationError, TypeError):
                pass
        for raw in args.get("relevance") or []:
            try:
                rated.append(TermRelevance(**raw))
            except (ValidationError, TypeError):
                pass
        return MemoryOps(ops=ops, relevance=rated)


def _listed(value):
    """A list that arrived as its JSON text; anything else as it is (an unreadable string becomes no entries)."""
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []
