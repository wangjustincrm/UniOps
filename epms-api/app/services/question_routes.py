"""Routes the code decides before the planner is asked (round 2 of the issue
review, 2026-11-10).

The planner (Haiku) was trusted to notice that a message asks several things
and call split_question, and to notice that a question is about the
conversation and call recall_conversation. On production it did neither
reliably:

  * "PO-576-2610-01 的状态、PO-535-2609-02 的金额、供应商 Raise The Standard 的
    联系人分别是什么？" asked twice, "GR-20261019-0001 是哪天几点收货的？10 月 18
    日收了哪些货？", "怎么在 Teams 里审批？…GR 确认超时会升级给谁？" — one query,
    the first part answered, the rest "not looked up" (ISS-0037, ISS-0069,
    ISS-0019, ISS-0050);
  * "这里的'上周'是哪几天？是按什么日期算的？" after a one-line answer was
    answered "你在这次对话里问的第一个问题是：…" (ISS-0067).

So both are decided here, from the text, by rules that can be read and
tested: a message is split where it plainly asks several questions (question
marks, semicolons, a numbered list, "A、B、C 分别是…", clauses that are each a
question, two documents of different kinds), and a question is about the
conversation only when it says so in so many words. The planner's
split_question stays as a supplement for what these rules do not catch; it
is no longer offered recall_conversation at all.
"""
from __future__ import annotations

import re

from app.core import doc_numbers

# ── Splitting ────────────────────────────────────────────────────────────────

# Where one question ends: a question mark (full or half width). A semicolon
# ends one only when every piece it makes is a question of its own (_asks):
# "PR-… was returned; if I resubmit it, does it start again?" is one.
_ENDS = re.compile(r"(?<=[？?])")
_SEMI = re.compile(r"(?<=[；;])")
_EN_ASKS = re.compile(
    r"^\s*(?:what|which|who|whom|whose|when|where|why|how|is|are|was|were|do|does|did|"
    r"can|could|will|would|should|has|have)\b", re.I)
# "1. … 2. …", "1、…", "(1) …", "①…" — a numbered list, at the start or after
# a space / line break / punctuation.
_NUMBERED = re.compile(
    r"(?:^|(?<=[\s，,。.；;：:]))(?:[（(]?([1-9])[.、)）]|([①②③④⑤⑥⑦⑧⑨]))\s*")
_CIRCLED = "①②③④⑤⑥⑦⑧⑨"
# Words that make a Chinese clause a question (or a request) of its own.
_ASKS = re.compile(
    r"什么|多少|怎么|怎样|如何|谁|哪|吗|呢|几|是否|能否|可否|能不能|可不可以|"
    r"有没有|会不会|是不是|为什么|为何|帮我|给我")
# "A、B、C 分别是什么" — the shared question after the list.
_EACH = re.compile(r"^(?P<items>.+?)\s*(?P<tail>(?:分别|各自)\s*(?:是|为|有)?.*)$", re.S)
# Two documents joined by "and".
_JOIN = r"\s*(?:和|与|跟|及|以及|、|,|，|&|and)\s*"
# Anything a part must contain to be a question at all.
_WORDY = re.compile(r"[A-Za-z一-鿿]")


def _clean(part: str) -> str:
    return part.strip().strip("，,、 ").strip()


def _asks(piece: str) -> bool:
    return bool(piece.rstrip("；; ").endswith(("?", "？")) or _ASKS.search(piece)
                or _EN_ASKS.search(piece))


def _by_semicolon(segment: str) -> list[str]:
    pieces = [_clean(p) for p in _SEMI.split(segment) if _clean(p)]
    if len(pieces) >= 2 and all(_asks(p) for p in pieces):
        return pieces
    return [segment]


def _by_clause(segment: str) -> list[str]:
    """"PO-079-2605-03 是什么状态，INV-2026-0123 金额多少，怎么催审批人，再帮我写一首
    诗" — split at the commas only when every clause is a question of its own;
    "NC 里刚批准的采购订单，多久能在 EPMS 里看到？" is one question."""
    clauses = [_clean(c) for c in re.split(r"[，,]", segment) if _clean(c)]
    if len(clauses) >= 2 and all(_ASKS.search(c) for c in clauses):
        return clauses
    return [segment]


def _each(segment: str) -> list[str]:
    """"A 的状态、B 的金额、C 的联系人分别是什么？" → one question per item. Not
    when an item is no more than a document number: "PO-1、PO-2 的金额分别是多
    少" is one query over both."""
    m = _EACH.match(segment)
    if not m:
        return [segment]
    items = [_clean(i) for i in re.split(r"[、]|以及|及", m["items"]) if _clean(i)]
    if len(items) < 2:
        return [segment]
    for item in items:
        bare = item
        for *_x, start, end in reversed(doc_numbers.find_all(item)):
            bare = bare[:start] + bare[end:]
        if len(_clean(bare)) < 2:
            return [segment]
    tail = re.sub(r"^(?:分别|各自)\s*", "", m["tail"])
    return [f"{item}{tail}" for item in items]


def _by_document(segment: str) -> list[str]:
    """"PR-20261015-0006 和 PA-20260923-0017 现在是什么状态？" — one query reaches one
    kind of document, so documents of different kinds are asked one by one.
    The same kind stays together (one query with `in`)."""
    found = doc_numbers.find_all(segment)
    if len({prefix for prefix, *_ in found}) < 2:
        return [segment]
    numbers = "|".join(re.escape(segment[s:e]) for _p, _n, s, e in found)
    run = re.search(rf"(?:{numbers})(?:{_JOIN}(?:{numbers}))+", segment, re.I)
    if not run:
        return [segment]
    inside = [(p, segment[s:e]) for p, _n, s, e in found
              if run.start() <= s and e <= run.end()]
    if len({p for p, _ in inside}) < 2:
        return [segment]
    return [segment[:run.start()] + typed + segment[run.end():] for _p, typed in inside]


def _numbered(text: str) -> list[str] | None:
    """The items of "1. … 2. …" / "①…②…" — only when they count 1, 2, 3… in
    order, so "版本 1.5" or a stray "3)" is not a list."""
    marks = list(_NUMBERED.finditer(text))
    numbers = [int(m.group(1)) if m.group(1) else _CIRCLED.index(m.group(2)) + 1
               for m in marks]
    if len(marks) < 2 or numbers != list(range(1, len(marks) + 1)):
        return None
    head = text[:marks[0].start()]
    items = [text[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(text))]
             for i, m in enumerate(marks)]
    return [p for p in (_clean(head), *(_clean(i) for i in items)) if p]


def split(message: str) -> list[str]:
    """The separate questions in `message`, in order — a list of one when it
    asks one thing."""
    text = (message or "").strip()
    if not text:
        return []
    segments = _numbered(text) or [text]
    out: list[str] = []
    for segment in segments:
        for asked in (_clean(p) for p in _ENDS.split(segment)):
            for piece in (_by_semicolon(asked) if asked else []):
                for clause in _by_clause(piece):
                    for item in _each(clause):
                        out.extend(_by_document(item))
    out = [p for p in (_clean(p) for p in out) if _WORDY.search(p)]
    return out or [text]


# ── A question about the conversation itself ─────────────────────────────────

_ZH_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9, "十": 10}
_EN_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}

# Each pattern names the position AND asks what it was: "这是我第一个问题，PO 状态
#是什么" is not a question about the conversation, "我问的第一个问题是什么" is.
_WHAT_ZH = r"\s*(?:是|叫)?\s*(?:什么|啥|哪(?:个|一个|句|条)?)"
_ANSWERED_ZH = r".{0,12}?(?:你|您)\s*(?:当时|那时|是)?\s*(?:怎么|如何)?\s*(?:回答|答)"
_ZH_NTH = re.compile(
    r"第\s*([一二两三四五六七八九十\d]+)\s*个?\s*(?:问题|提问)(?=" + _WHAT_ZH + "|"
    + _ANSWERED_ZH + ")")
_ZH_PREVIOUS = re.compile(
    r"(?:上一个|上个|前一个|刚才那个|刚才的|最后一个)\s*(?:问题|提问)(?=" + _WHAT_ZH + "|"
    + _ANSWERED_ZH + ")|我\s*刚才\s*问(?:了|的)?\s*(?:什么|啥)")
_ZH_ABOUT_ME = re.compile(r"我|咱|你.{0,6}(?:回答|答)")
_EN_ORD = (r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
           r"\d+(?:st|nd|rd|th))")
_EN_NTH = re.compile(r"\bwhat\s+(?:was|is)\s+my\s+" + _EN_ORD + r"\s+question\b", re.I)
_EN_PREVIOUS = re.compile(
    r"\bwhat\s+(?:was|is)\s+my\s+(?:previous|last|earlier)\s+question\b|"
    r"\bwhat\s+did\s+i\s+(?:just\s+)?ask\b", re.I)


def _zh_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if text in _ZH_DIGITS:
        return _ZH_DIGITS[text]
    if text.startswith("十"):                       # 十一 … 十九
        return 10 + _ZH_DIGITS.get(text[1:], 0)
    if len(text) >= 2 and text[1] == "十":          # 二十, 二十三
        return _ZH_DIGITS.get(text[0], 0) * 10 + _ZH_DIGITS.get(text[2:], 0)
    return None


def recall_position(message: str) -> int | None:
    """Which earlier question the message asks about — 1 = the first, -1 = the
    one just before — or None when it is not a question about the
    conversation. Only explicit wording counts: "上面这些是按什么日期算的？" is a
    question about the data shown, not about the conversation."""
    text = (message or "").strip()
    m = _ZH_NTH.search(text)
    if m and _ZH_ABOUT_ME.search(text):
        n = _zh_number(m.group(1))
        if n:
            return n
    if _ZH_PREVIOUS.search(text) and _ZH_ABOUT_ME.search(text):
        return -1
    m = _EN_NTH.search(text)
    if m:
        word = m.group(1).lower()
        return _EN_ORDINALS.get(word) or int(re.sub(r"\D", "", word))
    if _EN_PREVIOUS.search(text):
        return -1
    return None
