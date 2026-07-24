"""Declarative entity registry — the single source both extract and load read.

Each Entity says: the QBO entity name, its header model, optional line model, and
how to build header/line column dicts. Adding an entity is one registry line plus
its model — no new load code.
"""
from dataclasses import dataclass
from typing import Callable

from app.models import qbo as m
from scripts.qbo_import import mappers


@dataclass(frozen=True)
class Entity:
    name: str                       # QBO entity name, e.g. "Bill"
    model: type                     # header SQLAlchemy model
    header: Callable[[dict], dict]  # raw -> header column dict
    line_model: type | None = None  # line SQLAlchemy model, if any
    line: Callable[[dict, str], dict] | None = None  # (raw_line, parent_qbo_id) -> line dict


# Populated incrementally by later tasks.
REGISTRY: list[Entity] = []

REGISTRY.append(Entity(name="Account", model=m.QboAccount, header=mappers.account_header))


def by_name(name: str) -> Entity:
    for e in REGISTRY:
        if e.name == name:
            return e
    raise KeyError(f"unknown QBO entity: {name}")
