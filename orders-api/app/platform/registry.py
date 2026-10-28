"""Strategy registries (design §13.8): a key chooses the implementation —
a document kind its parser, a channel its builder, a method its sender."""
from __future__ import annotations

from collections.abc import Hashable, Iterator
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class Registry(Generic[K, V]):
    def __init__(self, name: str) -> None:
        self.name = name
        self._items: dict[K, V] = {}

    def register(self, key: K, value: V) -> V:
        if key in self._items:
            raise ValueError(f"{self.name}: {key!r} is already registered")
        self._items[key] = value
        return value

    def get(self, key: K) -> V:
        try:
            return self._items[key]
        except KeyError:
            raise LookupError(f"{self.name}: nothing registered for {key!r}") from None

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __iter__(self) -> Iterator[K]:
        return iter(self._items)

    def keys(self) -> list[K]:
        return list(self._items)
