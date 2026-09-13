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
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.access_scope import build_scope
from app.core.deps import CurrentUserPayload, SessionDep
from app.core.ontology import REGISTRY
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
        if not perms.get(entity.perm_key, False):
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
                if perms.get(REGISTRY[ln.target].perm_key, False)
            },
        })
    return {"entities": entities}


async def _describe_field(db, entity, name, f, scope) -> dict:
    out: dict = {"kind": f.kind, "label": f.label}
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
