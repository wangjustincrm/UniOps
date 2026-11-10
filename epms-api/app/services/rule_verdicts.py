"""The conclusion a rule question must get — chosen and placed by code.

A rule card says how the system behaves; the narrator (Haiku) writes the
reply from it. That was enough for the body of the reply and not for its
first word: "货还没收到，发票可以先匹配吗？" opened "不可以" over a body that
said it can (ISS-0249); "我自己就是第一审批人，提交后还要自己批吗？" opened
"不需要" and then said the opposite (ISS-0026); "服务类 PO 谁来确认？" in a
new conversation was routed to the PO approval chain (ISS-0029). Each was
right in the rule and wrong in the reply, one attempt in two or three.

A verdict is the fixed answer to one shape of question. Each lives in the
area file of the rule it rests on (app/knowledge/rules/<area>.yaml,
`verdicts:`):

  rule     the card (or approval-api engine rule) it states the conclusion of
  asks     groups of words; the question matches when every group has a hit.
           Matched in code, so the same question gets the same verdict on
           every attempt — no model decides whether it applies.
  unless   words that mean a different question (none may appear)
  says     {en, zh}: the sentence itself, written here and pinned by `test`
  place    first (default): put before the reply, and an opening yes/no the
           narrator wrote is taken out — it can only repeat or contradict the
           verdict. last: added under the reply (a "must also say").
  route    rules (default): the question is answered from this area's rules
           whatever the planner would have picked. keep: the planner's route
           answers and the verdict is placed on its reply.
  derive   optional app.services.rule_facts function whose values fill the
           sentence's {placeholders}, as for a derived card.

A question naming a document number is never given a verdict: it asks about
that document, and the document's own checks answer it.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

import yaml

from app.services import rule_registry


@dataclass(frozen=True)
class Verdict:
    id: str
    area: str
    rule: str
    asks: tuple[tuple[str, ...], ...]
    unless: tuple[str, ...]
    says_en: str
    says_zh: str
    place: str = "first"
    route: str = "rules"
    derive: str | None = None
    test: tuple[str, ...] = field(default_factory=tuple)


PLACES = ("first", "last")
ROUTES = ("rules", "keep")


def _normalise(text: str | None) -> str:
    return " ".join((text or "").split())


@functools.lru_cache(maxsize=None)
def verdicts() -> tuple[Verdict, ...]:
    out = []
    for path in sorted(rule_registry.RULES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for v in doc.get("verdicts") or ():
            says = v.get("says") or {}
            out.append(Verdict(
                id=v["id"], area=path.stem, rule=v["rule"],
                asks=tuple(tuple(str(t) for t in group) for group in v.get("asks") or ()),
                unless=tuple(str(t) for t in v.get("unless") or ()),
                says_en=_normalise(says.get("en")), says_zh=_normalise(says.get("zh")),
                place=v.get("place") or "first", route=v.get("route") or "rules",
                derive=v.get("derive"), test=tuple(v.get("test") or ()),
            ))
    return tuple(out)


# ── Matching ─────────────────────────────────────────────────────────────────

# PR-2026…, PO-576-…, PA-2026…, GR-…, AGR-…, INV-… — a question about one
# document, which its own checks answer.
_DOC_NUMBER = re.compile(r"(?<![a-z0-9])(?:pr|po|pa|gr|agr|inv)-?\d", re.I)


def names_a_document(message: str) -> bool:
    return bool(_DOC_NUMBER.search(message or ""))


def _hit(term: str, lowered: str, squeezed: str) -> bool:
    term = term.lower()
    if term.isascii():
        # Whole words for Latin terms ("pa" is not in "paper"); the edge is a
        # letter or digit, so "PA吗" still matches.
        return re.search(r"(?<![a-z0-9])" + re.escape(" ".join(term.split()))
                         + r"(?![a-z0-9])", lowered) is not None
    return "".join(term.split()) in squeezed


def _matches(v: Verdict, lowered: str, squeezed: str) -> bool:
    if not v.asks:
        return False
    if any(_hit(t, lowered, squeezed) for t in v.unless):
        return False
    return all(any(_hit(t, lowered, squeezed) for t in group) for group in v.asks)


def match(message: str) -> Verdict | None:
    """The verdict for this question, or None. When several match, the one
    with the most word groups — the most specific question — wins; a tie
    goes to the first declared."""
    if not message or names_a_document(message):
        return None
    lowered = " ".join(message.lower().split())
    squeezed = "".join(lowered.split())
    best = None
    for v in verdicts():
        if _matches(v, lowered, squeezed) and (best is None or len(v.asks) > len(best.asks)):
            best = v
    return best


# ── Saying it ────────────────────────────────────────────────────────────────

async def sentences(v: Verdict, db) -> dict:
    """{en, zh, place} with the derived values filled in."""
    en, zh = v.says_en, v.says_zh
    if v.derive:
        values = await rule_registry._derived_values(v.derive, db)
        en, zh = en.format(**values), zh.format(**values)
    return {"id": v.id, "en": en, "zh": zh, "place": v.place}


# An opening yes/no the narrator wrote: the verdict states the conclusion, so
# the narrator's own one can only repeat it or contradict it (ISS-0249: "不可以"
# over a body that said it can). The first sentence is dropped when it starts
# with one of these.
_OPENING_WORD = re.compile(
    r"^\s*(?:\*\*)?(?:结论[:：]\s*)?(?:\*\*)?"
    r"(?:不可以|不能|不行|不会|不需要|不用|不必|不一定|不是|不对|没有|"
    r"可以|能够|(?:能|会|是)(?![否计])|需要|要|对(?!于)|"
    r"yes\b|no\b|not necessarily\b|it depends\b)",
    re.I)
_FIRST_SENTENCE = re.compile(r"^.*?(?:[。！？!?\n]|\.(?=\s|$))", re.S)


def without_opening_verdict(text: str) -> str:
    if not _OPENING_WORD.match(text or ""):
        return text
    m = _FIRST_SENTENCE.match(text)
    rest = text[m.end():] if m else ""
    return rest.lstrip(" \n\r\t—-–")


def place(text: str, said: str, where: str) -> str:
    """The reply with the verdict sentence in its place."""
    text = (text or "").strip()
    if where == "last":
        return "\n\n".join(p for p in (text, said) if p)
    rest = without_opening_verdict(text)
    return "\n\n".join(p for p in (said, rest) if p)
