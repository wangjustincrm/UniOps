""""Which questions have I marked?" — answered from the Marked list, in code.

Asked "你可以查询到我mark问题有哪些吗？" three times, the planner chose the task
inbox each time and the reply was 406 open tasks (ISS-0102). The marked
questions are a list this service keeps (api/v1/assistant_saved.py, PRD
AST-007), so the question is recognised here and answered from that list,
whatever tool the planner picked. The questions are the person's own words and
are quoted as saved, never passed through a model.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.models.assistant_saved_question import (
    MAX_SAVED_QUESTIONS, AssistantSavedQuestion,
)

# "What's in my Marked list?" names the list rather than the questions.
_THE_LIST = re.compile(r"(?<![a-z])marked\s*(?:list|\(\d+\))|收藏(?:夹|列表)", re.I)

# Or the person's own questions, with a word for marking, in the forms people
# type it, next to "question": "mark the PO as received", "发票被标记为有问题怎么办？"
# and "How do I save a draft PR? I have a question." are not about this list
# (the last two: round 2 fix, 2026-11-11). Latin words are bounded by
# non-letters only, so "我mark问题" (no spaces in Chinese) still reads as "mark".
_MINE = re.compile(r"我|(?<![a-z])(?:my|i|me)(?![a-z])", re.I)
_MARKED_QUESTIONS = re.compile(
    r"(?:收藏|标记|星标|加星|保存|存下|(?<![a-z])(?:mark|marked|bookmark|bookmarked|save|saved|"
    r"star|starred))\s*(?:了|过|的|过的|了的)?\s*(?:哪些|哪几个|什么|几个|多少)?\s*(?:个)?\s*的?\s*"
    r"(?:问题|提问|questions?(?![a-z]))"
    r"|(?<![a-z])questions?\s+(?:that\s+)?(?:have\s+|did\s+)?i(?:'ve|\s+have)?\s+"
    r"(?:marked|saved|starred|bookmarked)(?![a-z])",
    re.I)


def asks_for_marked(message: str) -> bool:
    text = message or ""
    return bool(_THE_LIST.search(text)
                or (_MINE.search(text) and _MARKED_QUESTIONS.search(text)))


async def my_marked(db, user_id) -> list[str]:
    """The person's marked questions, newest first — the Marked list's order
    (assistant_saved._list)."""
    rows = (await db.execute(
        select(AssistantSavedQuestion.question)
        .where(AssistantSavedQuestion.user_id == user_id)
        .order_by(AssistantSavedQuestion.created_at.desc())
    )).scalars().all()
    return list(rows)


_ZH = {
    "some": "您收藏了 {n} 个问题（每人最多 {limit} 个），最近收藏的在前：",
    "none": "您还没有收藏任何问题（每人最多可收藏 {limit} 个）。",
    "how": ("在您提的问题旁点星标即可收藏或取消收藏；点面板右上角的「Marked」"
            "可以一键再问其中任何一个。"),
}
_EN = {
    "some": "You have marked {n} question(s) (up to {limit} each), newest first:",
    "none": "You have not marked any questions yet (you can keep up to {limit}).",
    "how": ("Click the star next to a question you asked to mark or unmark it; "
            "click \"Marked\" at the top right of this panel to ask any of them "
            "again with one click."),
}


def sentences(language: str | None) -> dict | None:
    """The fixed sentences for `language`, or None when they must be rendered
    into it (anything but Chinese and English)."""
    if language == "Chinese (Simplified)":
        return _ZH
    if language in (None, "English"):
        return _EN
    return None


def as_text(questions: list[str], said: dict) -> str:
    n = len(questions)
    lead = (said["some"] if n else said["none"]).format(n=n, limit=MAX_SAVED_QUESTIONS)
    listed = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    return "\n\n".join(p for p in (lead, listed, said["how"]) if p)
