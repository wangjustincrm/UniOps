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


def catalogue() -> list[dict]:
    """What each area covers — for the planner's tool and for a reply that
    has no rule to give (it names what CAN be answered instead of inventing
    someone to ask)."""
    return [{"area": a.key, "label": a.label, "covers": a.covers} for a in _load().values()]
