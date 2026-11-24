"""What a list resource offers, declared once, on its service (≈ ABP's CrudAppService, user 2026-11-24):

    class LotService(ReadOnlyService[LotRow, LotRead]):
        resource = Resource(path="/lots", get=False)

`crud_router.resource_router(LotService, uow=get_uow)` mounts exactly that, and the list's /meta says it
(`capabilities`), so the page shows what the server offers instead of guessing. Three levels, field by field: the
platform's defaults (options.ResourceOptions) → a base class's `resource` → the service's own (`resolve`).

A new capability (a history, attachments…) is a new field here and its routes in resource_router: no list changes.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from typing import Any, Final, Literal

from app.platform.options import ResourceOptions, options


class _Inherit:
    def __repr__(self) -> str:
        return "INHERIT"


INHERIT: Final[Any] = _Inherit()


@dataclass(frozen=True)
class Computed:
    """A column the page makes itself (its own cell), as the export writes it: from the row as the list reads it."""
    label: str
    value: Callable[[Any], Any]


@dataclass(frozen=True)
class Resource:
    """One list resource. A field left out is the base's, else the platform's (ResourceOptions)."""
    path: str = INHERIT                      # "/lots"; without one the service has no routes
    tags: tuple[str, ...] = INHERIT          # OpenAPI; default: the path
    get: bool = INHERIT                      # GET /{id}
    detail: Any = INHERIT                    # its schema: GET /{id} answers service.detail(id); None: service.get(id)
    suggest: bool = INHERIT                  # GET /suggest (the repository's `suggestable` fields)
    export: bool = INHERIT                   # GET /export.csv
    export_permission: str | None = INHERIT  # beside read_permission; None: reading is enough
    export_headers: Literal["fields", "labels"] = INHERIT  # the full export's; "fields" is the shape an import reads back
    export_fields: tuple[str, ...] | None = INHERIT        # the full export's columns; None: the list's fields
    computed: Mapping[str, Computed] = INHERIT             # the page's own columns, by column id
    history: bool = INHERIT                  # GET /{id}/audit: what changed, when, by whom and why
    timeline: Any = INHERIT                  # its detail carries a `timeline` (its Activity): what it is read from
    attachments: Any = INHERIT               # a person's files on a record: its kind (entity_type, change, max_bytes, extensions)
    actions: Any = INHERIT                   # an ActionRegistry, or a function that gives it (import order)


@dataclass(frozen=True)
class ResolvedResource:
    path: str | None = None
    tags: tuple[str, ...] = ()
    get: bool = True
    detail: Any = None
    suggest: bool = True
    export: bool = True
    export_permission: str | None = None
    export_headers: str = "fields"
    export_fields: tuple[str, ...] | None = None
    computed: Mapping[str, Computed] = field(default_factory=dict)
    history: bool = False
    timeline: Any = None
    attachments: Any = None
    actions: Any = None

    @property
    def file_name(self) -> str:
        """The export's file: "/config/partners" → config-partners.csv."""
        return ((self.path or "").strip("/").replace("/", "-") or "export") + ".csv"

    def action_registry(self) -> Any | None:
        a = self.actions
        return a if a is None or hasattr(a, "actions") else a()


def resolve(service_cls: type) -> ResolvedResource:
    o = options(ResourceOptions)
    values: dict[str, Any] = {"get": o.get, "suggest": o.suggest, "export": o.export, "export_headers": o.export_headers,
                              "history": o.history}
    for cls in reversed(service_cls.__mro__):
        declared = cls.__dict__.get("resource")
        if declared is not None:
            values.update({f.name: v for f in fields(declared) if (v := getattr(declared, f.name)) is not INHERIT})
    return ResolvedResource(**values)
