"""Read-only query surface for the assistant.

Two endpoints, both scoped to the calling user's own token — the assistant
service forwards the end user's JWT rather than holding a service credential of
its own, so everything below sees exactly what that person would see in the UI.

`/assistant/schema` exists so the planner can be told what is queryable without
that list being hardcoded on the assistant side. It is filtered by the caller's
permissions: an entity the user may not view is not merely refused later, it is
never named, so the planner cannot propose a query the user would be denied.
"""
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.core.access_scope import build_scope
from app.core.deps import CurrentUserPayload, SessionDep
from app.core.ontology import REGISTRY, get_entity, may_view
from app.services import assistant_export
from app.services import controlled_query as cq

router = APIRouter(prefix="/assistant", tags=["assistant"])


class WhereClause(BaseModel):
    field: str
    op: str = "eq"
    value: object | None = None


class Period(BaseModel):
    field: str | None = None
    last_n_months: int | None = None
    last_n_days: int | None = None
    # `from` is a Python keyword, so the wire name is set via alias.
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None

    model_config = {"populate_by_name": True}


class OrderBy(BaseModel):
    field: str
    desc: bool = False


class QueryRequest(BaseModel):
    entity: str
    # Only used by /export, to head the sheet with what was asked.
    question: str | None = None
    select: list[str] | None = None
    where: list[WhereClause] | None = None
    group_by: list[str] | None = None
    metrics: list[str] | None = None
    period: Period | None = None
    order_by: OrderBy | None = None
    limit: int | None = None


# A field marked `enumerate` is listed with its real values, but only if there
# are few enough to be a vocabulary rather than a data dump. Past this, the
# planner is better off filtering with `like`.
_MAX_ENUMERATED = 60


async def _distinct_values(db, entity, field_name: str, scope: dict) -> list[str] | None:
    """The values this caller could actually encounter in a column.

    Scoped like any other read: the list of departments someone can see should
    not be wider than the documents they can see. Returns None when there are
    too many to be useful as a vocabulary.
    """
    col = getattr(entity.model, field_name)
    stmt = sa.select(col).where(col.is_not(None)).distinct().limit(_MAX_ENUMERATED + 1)
    stmt = await entity.apply_scope(stmt, scope, db)
    rows = (await db.execute(stmt)).scalars().all()
    if len(rows) > _MAX_ENUMERATED:
        return None
    return sorted(str(r) for r in rows if str(r).strip())


@router.get("/schema")
async def assistant_schema(db: SessionDep, user: CurrentUserPayload):
    """Describe the queryable entities this caller may actually reach."""
    scope = await build_scope(db, user)
    perms = scope.get("perms", {})
    entities = []
    for entity in REGISTRY.values():
        if not may_view(entity, perms):
            continue
        entities.append({
            "name": entity.name,
            "label": entity.label,
            "date_field": entity.date_field,
            "fields": {
                name: await _describe_field(db, entity, name, f, scope)
                for name, f in entity.fields.items()
            },
            "metrics": {
                key: {"label": m.label} for key, m in entity.metrics.items()
            },
            # Declared relationships, and they can be walked: a field on the far
            # side is addressed as "<link>.<field>", up to three hops. This is
            # the only way to reach facts the entity does not carry itself —
            # purchase_orders has no department column, so grouping orders by
            # department means going through originating_pr.
            "links": {
                key: {"target": ln.target, "cardinality": ln.cardinality,
                      "label": ln.label, "traversable": True,
                      "example": f"{key}.<field of {ln.target}>"}
                for key, ln in entity.links.items()
                # Do not advertise a hop into something this caller cannot see.
                if may_view(REGISTRY[ln.target], perms)
            },
        })
    return {"entities": entities}


async def _describe_field(db, entity, name, f, scope) -> dict:
    out: dict = {"kind": f.kind, "label": f.label}
    if f.value_labels:
        # Both directions: the planner filters on the code, and whatever reports
        # the result needs to say what the code means.
        out["value_labels"] = {k: v for k, v in f.value_labels}
    if f.values:
        out["values"] = list(f.values)
    elif f.enumerate_values:
        values = await _distinct_values(db, entity, name, scope)
        if values:
            # Named explicitly so the planner filters on a value that exists
            # rather than on the words the question happened to use.
            out["values"] = values
            out["values_are_complete"] = True
    return out


@router.post("/query")
async def assistant_query(body: QueryRequest, db: SessionDep, user: CurrentUserPayload):
    scope = await build_scope(db, user)
    request = body.model_dump(by_alias=True, exclude_none=True)
    try:
        return await cq.execute(db, request, scope)
    except cq.QueryRejected as exc:
        # 422 with the reason intact: the planner reads this and retries, so a
        # generic "bad request" would cost a round trip and teach it nothing.
        raise HTTPException(status_code=422, detail=str(exc))


def _column_headings(entity, labels: list[str]) -> dict[str, str]:
    """Field key -> the label a person would recognise, following links.

    A heading of "order.originating_pr.department_name" is accurate and
    unreadable; the ontology already carries a human label for every field, and
    for a hop the useful heading names both ends.
    """
    out: dict[str, str] = {}
    for key in labels:
        parts = key.split(".")
        current = entity
        try:
            for step in parts[:-1]:
                current = REGISTRY[current.links[step].target]
            field = current.fields.get(parts[-1])
        except (KeyError, AttributeError):
            field = None
        if field is None:
            # A metric, or something the registry does not describe. The key
            # itself beats a wrong guess.
            metric = entity.metrics.get(key)
            out[key] = metric.label if metric else key
            continue
        out[key] = field.label if current is entity else f"{current.label.split('—')[0].strip()}: {field.label}"
    return out


@router.post("/export")
async def assistant_export_xlsx(body: QueryRequest, db: SessionDep,
                                user: CurrentUserPayload) -> Response:
    """Run a query again, in full, and return it as a workbook.

    Deliberately not a tool the model can call. The person saw the answer,
    decided it was right, and asked for it as a file — so nothing gets written
    that was not already checked on screen.

    Re-run rather than serialise what the chat returned: that was capped for a
    reply. And re-run under THIS caller's scope, not the scope of whoever
    produced the answer — a spreadsheet is the easiest thing in the world to
    forward, and the rows in it have to be the rows the person downloading is
    allowed to see.
    """
    entity = get_entity(body.entity)
    if entity is None:
        raise HTTPException(status_code=422, detail=f"Unknown entity '{body.entity}'")

    scope = await build_scope(db, user)
    request = body.model_dump(by_alias=True, exclude_none=True)
    try:
        result = await cq.execute(db, request, scope, for_export=True)
    except cq.QueryRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if result.get("denied"):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to view that kind of document.")

    labels = result.get("labels") or []
    content = assistant_export.build_workbook(
        question=body.question or "",
        entity_label=entity.label.split("—")[0].strip(),
        query={k: v for k, v in request.items() if k != "question"},
        rows=result["rows"],
        labels=labels,
        headers=_column_headings(entity, labels),
        totals=result.get("totals"),
        truncated=result.get("truncated", False),
        row_cap=result.get("row_cap", 0),
        generated_for=user.get("email") or None,
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition":
                f'attachment; filename="{assistant_export.filename_for(entity.name)}"',
            # Without this a cross-origin download cannot read the name above.
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
