"""Record types and references (user 2026-11-27).

A record with a page of its own (an order, an item, a lot) is a *record type*, declared once by the service of
its list (`record_type = "item"`). A list field that names such a record declares a `Ref` (`Repository.refs`),
sent in /meta, so every client turns it into a link without code of its own.

"May read" has one source: the record type's list service `read_permission`. The list, the record's page and
every link to it decide from that same key; /meta says, per type a list refers to, whether this person may.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Ref:
    """A field that names a record: its type, and the field of the same row that holds the record's key
    (default: the field itself — e.g. `item_code`; an order is named by `order_ref` but keyed by `order_id`)."""
    type: str
    key: str | None = None

    def as_dict(self, field: str) -> dict[str, str]:
        return {"type": self.type, "key": self.key or field}


_TYPES: dict[str, Any] = {}


def register(name: str, service_cls: Any) -> None:
    """One service per type (its list): a second one is a mistake, never a silent override."""
    held = _TYPES.get(name)
    if held is not None and held.__qualname__ != service_cls.__qualname__:
        raise TypeError(f"record type {name!r} is {held.__name__}'s already, not {service_cls.__name__}'s")
    _TYPES[name] = service_cls


def record_types() -> Mapping[str, Any]:
    return dict(_TYPES)


def read_permission_of(name: str) -> str:
    try:
        return _TYPES[name].read_permission
    except KeyError:
        raise KeyError(f"no service declares record type {name!r}") from None
