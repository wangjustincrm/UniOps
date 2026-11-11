"""How the system behaves, by area — the rules the Assistant answers from.

Questions about behaviour ("does a returned PR restart from step 1?", "how
often does NC sync?", "can I edit a PO that came from NC?") had about twenty
hand-written rules to go on, spread over the document guides. Everything else
fell through to cannot_answer, whose free text was shown as the reply — "not
in my data, ask the System Admin", once "ask the Vendor Manager", a role that
does not exist (ISS-0022 and the issues grouped under it).

This module is the one place those rules live. Each area is a file in
app/knowledge/rules/, each rule a card saying:

  answer       what the system does, in words a requester can use
  provenance   derived   — the facts in it are read from the running code or
                           configuration when the rule is served ({name}
                           placeholders, filled by app.services.rule_facts);
               pinned    — a test holds the code to the statement;
               not_built — the statement is that something does not exist,
                           and a test holds THAT (no route, no reader).
  enforced_in  where in the code it is decided
  test         the test(s) that would fail if the code stopped doing it
  said_when    (optional) patterns; a question matching one is answered with
               `says` — written here, in English and Chinese, by code — and
               the model is not asked at all (pinned_for / say below). For
               the questions on which a model-written answer kept coming out
               differently or opposite to the rule (round 2 of the review,
               2026-11-10: ISS-0018, ISS-0019, ISS-0050, ISS-0069, ISS-0022).
               A question asking for data (a list, a count, a period, who
               did it) is never answered this way (_ASKS_FOR_DATA).
  says         {en, zh}: that answer; a derived card fills its {placeholders}
               like `answer` (a `_zh` value for the Chinese text when the fact
               is words)

tests/test_rule_registry.py fails for a card without a test or with a test
that does not exist, so a rule cannot be added here on someone's say-so.

Two areas also carry rules served by approval-api (GET /workflows/rules):
the engine's general behaviour and approval delegation. Those are stated
beside the engine code there and are not copied here.
"""
from __future__ import annotations

import functools
import inspect
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

RULES_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "rules"
PROVENANCES = ("derived", "pinned", "not_built")
# Areas whose rules are partly served by approval-api's /workflows/rules.
REMOTE_SOURCE = "approval-api"


@dataclass(frozen=True)
class Area:
    key: str
    label: str
    covers: str
    remote: bool
    cards: tuple[dict, ...] = field(default_factory=tuple)


def _normalise(text: str | None) -> str:
    return " ".join((text or "").split())


@functools.lru_cache(maxsize=None)
def _load() -> dict[str, Area]:
    areas: dict[str, Area] = {}
    for path in sorted(RULES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        key = doc.get("area") or path.stem
        if key != path.stem:
            raise ValueError(f"{path.name}: area {key!r} must match the file name")
        areas[key] = Area(
            key=key, label=doc.get("label") or key,
            covers=_normalise(doc.get("covers")),
            remote=doc.get("remote") == REMOTE_SOURCE,
            cards=tuple(doc.get("rules") or ()),
        )
    return areas


def areas() -> dict[str, Area]:
    return _load()


def area_keys() -> tuple[str, ...]:
    return tuple(_load())


def all_cards() -> list[tuple[str, dict]]:
    """(home area, card) for every card, each once."""
    return [(a.key, c) for a in _load().values() for c in a.cards]


@functools.lru_cache(maxsize=None)
def _by_id() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for area, card in all_cards():
        if card["id"] in out:
            raise ValueError(f"rule id {card['id']!r} is defined twice")
        out[card["id"]] = card
    return out


def card(rule_id: str) -> dict:
    return _by_id()[rule_id]


def _public(card: dict) -> dict:
    out = {"id": card["id"], "answer": _normalise(card.get("answer"))}
    if card.get("topic"):
        out["topic"] = card["topic"]
    return out


def static_rules(area: str) -> list[dict]:
    """The area's own cards plus cards from other areas listed `also_in` it,
    as {id, answer[, topic]}. Derived cards still carry their {placeholders};
    resolve() fills them."""
    out = [_public(c) for c in _load()[area].cards] if area in _load() else []
    for home, c in all_cards():
        if home != area and area in (c.get("also_in") or []):
            out.append(_public(c))
    return out


async def _derived_values(name: str, db) -> dict:
    from app.services import rule_facts

    fn = getattr(rule_facts, name)
    result = fn(db)
    if inspect.isawaitable(result):
        result = await result
    return result


async def resolve(rules: list[dict], db) -> tuple[list[dict], list[str]]:
    """Fill derived cards' placeholders from the running system.

    A value that cannot be read leaves the rule out and names it in the second
    list, so the narrator says that part could not be checked rather than
    stating a placeholder or a remembered value.
    """
    out, unavailable = [], []
    cache: dict[str, dict] = {}
    for r in rules:
        src = _by_id().get(r["id"])
        if not src or src.get("provenance") != "derived":
            out.append(r)
            continue
        name = src["derive"]
        try:
            if name not in cache:
                cache[name] = await _derived_values(name, db)
            out.append({**r, "answer": r["answer"].format(**cache[name])})
        except Exception:  # noqa: BLE001 — one unreadable setting must not lose the rest
            log.exception("assistant: could not derive rule %s", r["id"])
            unavailable.append(r["id"])
    return out, unavailable


async def _remote_rules(token: str | None) -> list[dict] | None:
    from app.services import approval_client

    try:
        status, body = await approval_client.forward("GET", "/workflows/rules", token)
    except Exception:  # noqa: BLE001 — the engine being down is an answer, not a 500
        return None
    if status >= 400 or not isinstance(body, dict):
        return None
    return [r for r in body.get("rules") or [] if isinstance(r, dict) and r.get("id")]


async def build(area_list: list[str], db, token: str | None) -> dict:
    """The payload for an explain_rules answer: each asked area with its rules,
    derived facts filled, the engine's own rules fetched where the area has
    them. Unknown areas are dropped."""
    known = [a for a in dict.fromkeys(area_list) if a in _load()]
    remote = None
    if any(_load()[a].remote for a in known):
        remote = await _remote_rules(token)
    result, unavailable = [], []
    for key in known:
        area = _load()[key]
        rules, missing = await resolve(static_rules(key), db)
        unavailable += missing
        if area.remote:
            if remote is None:
                unavailable.append(f"{key}: the approval engine's rules could not be read")
            else:
                rules = [{"id": r["id"], "answer": r["answer"]}
                         for r in remote if r.get("area") == key] + rules
        result.append({"area": key, "label": area.label, "rules": rules})
    return {"areas": result, "unavailable": unavailable}


# ── Answers the code writes (said_when / says) ───────────────────────────────

_CJK_GAP = re.compile(r"(?<=[^\x00-\x7f])\s+(?=[^\x00-\x7f])")


@functools.lru_cache(maxsize=None)
def _said_when() -> tuple[tuple[re.Pattern, str], ...]:
    out = []
    for _area, c in all_cards():
        for pattern in c.get("said_when") or ():
            out.append((re.compile(pattern, re.I | re.S), c["id"]))
    return tuple(out)


# A question that asks for data — a list, a count, a period, who did it — is
# not answered with a rule card, whatever rule words it has: "上周有哪些发票被
# 取消匹配了？", "上个月录了多少笔手工贷项？", "谁改了我的请购单？" (round 2 fix,
# 2026-11-11). "收货超时几天会升级？" and "能连续催两次吗？" still ask the rule.
_ASKS_FOR_DATA = re.compile(
    r"哪些|哪几(?:张|笔|个|份|条|家)|多少(?:张|笔|个|份|条|家|单)|几(?:张|笔|份|条|家|单)|"
    r"列出|列一下|上周|本周|这周|上个?月|本月|这个月|今天|昨天|"
    r"谁(?:改|修改|编辑|动)了|"
    r"\bhow many\b|\bwhich\b|\blist\b|\bshow me\b|\blast (?:week|month)\b|"
    r"\bwho (?:changed|edited)\b", re.I)


def pinned_for(message: str) -> list[str]:
    """The ids of the cards whose `said_when` the question matches, in the
    registry's order, each once — none for a question asking for data."""
    text = " ".join((message or "").split())
    if _ASKS_FOR_DATA.search(text):
        return []
    return list(dict.fromkeys(rid for pattern, rid in _said_when() if pattern.search(text)))


async def say(rule_ids: list[str], lang: str, db) -> tuple[str, list[str]]:
    """The cards' `says` in `lang` ("zh" or "en"), joined, derived facts
    filled — and the ids of any whose facts could not be read (left out, as
    resolve() does)."""
    parts, unavailable = [], []
    cache: dict[str, dict] = {}
    for rid in rule_ids:
        src = _by_id()[rid]
        text = _normalise((src.get("says") or {}).get(lang))
        if lang == "zh":
            # A folded line break between two Chinese characters is not a space.
            text = _CJK_GAP.sub("", text)
        if src.get("provenance") == "derived":
            name = src["derive"]
            try:
                if name not in cache:
                    cache[name] = await _derived_values(name, db)
                text = text.format(**cache[name])
            except Exception:  # noqa: BLE001 — same as resolve()
                log.exception("assistant: could not derive rule %s", rid)
                unavailable.append(rid)
                continue
        parts.append(text)
    return "\n\n".join(parts), unavailable


def catalogue() -> list[dict]:
    """What each area covers — for the planner's tool and for a reply that
    has no rule to give (it names what CAN be answered instead of inventing
    someone to ask)."""
    return [{"area": a.key, "label": a.label, "covers": a.covers} for a in _load().values()]
