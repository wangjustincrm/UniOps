"""VMS entity registry: schema metadata + cascade handlers.

Each entity exposes:
- a render/edit schema (EntitySchema)
- cascade_preview(db, row): counts only, NO mutation
- cascade_delete(db, row): deletes the row (+ children) and purges shared
  `tasks` refs by document_id. The CALLER commits.

Cascade facts (verified against the ORM models):
- vms_health_declarations.visit_id and vms_badge_prints.visit_id are
  ondelete=CASCADE -> deleting a Visit row auto-removes them.
- vms_visits.visitor_id -> vms_visitors is ondelete=RESTRICT -> a Visitor
  cannot be deleted while it has visits; its visits are deleted first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.cascade import purge_shared_refs
from app.admin.fields import EntitySchema, FieldSpec
from app.models.health_declaration import HealthDeclaration
from app.models.visit import Visit
from app.models.visitor import Visitor

CascadeFn = Callable[[AsyncSession, object], Awaitable[dict[str, int]]]


@dataclass
class EntitySpec:
    schema: EntitySchema
    model: type
    system: str
    cascade_preview: CascadeFn
    cascade_delete: CascadeFn


# ── helpers ──────────────────────────────────────────────────────────────────

def _merge(into: dict, add: dict) -> dict:
    for k, v in add.items():
        into[k] = into.get(k, 0) + v
    return into


# ── Visit ────────────────────────────────────────────────────────────────────
# CASCADE removes vms_health_declarations + vms_badge_prints automatically.

async def _visit_preview(db: AsyncSession, visit) -> dict[str, int]:
    return {"vms_visits": 1}


async def _visit_delete(db: AsyncSession, visit) -> dict[str, int]:
    refs = await purge_shared_refs(db, visit.id)
    await db.delete(visit)  # vms_health_declarations + vms_badge_prints cascade via FK
    return _merge({"vms_visits": 1}, refs)


# ── Visitor ──────────────────────────────────────────────────────────────────
# vms_visits.visitor_id is ondelete=RESTRICT, so delete the visitor's visits
# (and their children + tasks) first, then the visitor itself.

async def _visitor_preview(db: AsyncSession, visitor) -> dict[str, int]:
    visits = (await db.execute(
        select(Visit).where(Visit.visitor_id == visitor.id)
    )).scalars().all()
    return {"vms_visitors": 1, "vms_visits": len(visits)}


async def _visitor_delete(db: AsyncSession, visitor) -> dict[str, int]:
    summary: dict[str, int] = {"vms_visitors": 1}
    visits = (await db.execute(
        select(Visit).where(Visit.visitor_id == visitor.id)
    )).scalars().all()
    for visit in visits:
        _merge(summary, await _visit_delete(db, visit))
    await db.flush()
    refs = await purge_shared_refs(db, visitor.id)
    _merge(summary, refs)
    await db.delete(visitor)
    return summary


# ── HealthDeclaration ────────────────────────────────────────────────────────

async def _health_declaration_preview(db: AsyncSession, hd) -> dict[str, int]:
    return {"vms_health_declarations": 1}


async def _health_declaration_delete(db: AsyncSession, hd) -> dict[str, int]:
    refs = await purge_shared_refs(db, hd.id)
    await db.delete(hd)
    return _merge({"vms_health_declarations": 1}, refs)


# ── Schemas ──────────────────────────────────────────────────────────────────
# Field names verified against the ORM models. Identity/created_at are
# read-only; status, names, notes, dates are editable.

_VISIT_SCHEMA = EntitySchema(
    key="visit", label="Visit", number_field="visit_title",
    list_columns=["visit_title", "visit_purpose", "status", "visit_date", "created_at"],
    search_fields=["visit_title", "notes"], order_by="created_at desc",
    fields=[
        FieldSpec("visit_title", "string", True),
        FieldSpec("visit_purpose", "string", True),
        FieldSpec("access_area", "string", True),
        FieldSpec("status", "string", True),
        FieldSpec("notes", "string", True),
        FieldSpec("visit_date", "date", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_VISITOR_SCHEMA = EntitySchema(
    key="visitor", label="Visitor", number_field="last_name",
    list_columns=["first_name", "last_name", "company_name", "visitor_type", "email", "created_at"],
    search_fields=["first_name", "last_name", "company_name", "email"], order_by="created_at desc",
    fields=[
        FieldSpec("first_name", "string", True),
        FieldSpec("last_name", "string", True),
        FieldSpec("company_name", "string", True),
        FieldSpec("job_title", "string", True),
        FieldSpec("phone", "string", True),
        FieldSpec("email", "string", True),
        FieldSpec("visitor_type", "string", True),
        FieldSpec("id_verified", "bool", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_HEALTH_DECLARATION_SCHEMA = EntitySchema(
    key="health_declaration", label="Health Declaration", number_field="result",
    list_columns=["result", "created_at"],
    search_fields=[], order_by="created_at desc",
    fields=[
        FieldSpec("result", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

REGISTRY: dict[str, EntitySpec] = {
    "visit": EntitySpec(_VISIT_SCHEMA, Visit, "vms", _visit_preview, _visit_delete),
    "visitor": EntitySpec(_VISITOR_SCHEMA, Visitor, "vms", _visitor_preview, _visitor_delete),
    "health_declaration": EntitySpec(
        _HEALTH_DECLARATION_SCHEMA, HealthDeclaration, "vms",
        _health_declaration_preview, _health_declaration_delete,
    ),
}
