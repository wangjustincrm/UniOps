"""Read-only query surface for the assistant.

Two endpoints, both scoped to the calling user's own token — the assistant
service forwards the end user's JWT rather than holding a service credential of
its own, so everything below sees exactly what that person would see in the UI.

`/assistant/schema` exists so the planner can be told what is queryable without
that list being hardcoded on the assistant side. It is filtered by the caller's
permissions: an entity the user may not view is not merely refused later, it is
never named, so the planner cannot propose a query the user would be denied.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.access_scope import build_scope
from app.core.deps import CurrentUserPayload, SessionDep
from app.core.query_registry import REGISTRY
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
                name: {"kind": f.kind, "label": f.label,
                       **({"values": list(f.values)} if f.values else {})}
                for name, f in entity.fields.items()
            },
            "metrics": {
                key: {"label": m.label} for key, m in entity.metrics.items()
            },
        })
    return {"entities": entities}


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
