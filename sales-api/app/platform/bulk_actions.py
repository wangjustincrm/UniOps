"""Record actions on one record or many (user 2026-11-23: an action is defined once, and some apply to a group of
records). The page (platform-ui/actions) defines the label, the form and where it shows; the server defines the rule
and the work, here, once per action:

    ORDER_ACTIONS = ActionRegistry(OrderRepository, [
        BulkAction("settle", permission=SETTLE, input=SettleIn, check=settle_check, run=settle_run)])

POST /<list>/actions/<id> (`action_router`) takes the records as {id: version} (what the person saw), or "all matching"
as the list's own query string with the count the person saw (at most MAX_RECORDS). The server decides:

- the permission once (403 for the whole request; hiding in the UI is not security);
- each record in a savepoint of its own: missing → failed; `check` gives a reason → skipped (the rule first, so a
  second run of a done action is skipped whatever the version, never a second write); a version that moved → failed
  ("changed since you selected it"); `run` raises a BusinessError → failed; else done;
- dry_run: the same, without `run` and without the input (the confirm step's preview: "10 will …, 2 skipped");
- a record's domain events go with its writes: every rollback (skipped, failed, stale, dry run) also drops the events
  it raised, so commit dispatches only those of the records done.

Each record's audit reason and log entry are its own (`run` sets them). The single-record endpoints keep their routes
and call the same `run`.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import QueryParams

from app.platform.authorization import authorize
from app.platform.commands import Stale, check_version
from app.platform.errors import BusinessError, InvalidInput, NotFound
from app.platform.query import QuerySpec, query_spec

MAX_RECORDS = 500
Check = Callable[[Any, Any], Awaitable[str | None]]          # (uow, record) → None, or why it is skipped
Run = Callable[[Any, Any, Any], Awaitable[None]]             # (uow, record, input)


@dataclass(frozen=True)
class BulkAction:
    id: str
    permission: str
    run: Run
    check: Check | None = None
    input: type[BaseModel] | None = None
    # Checks of the input alone (not of a record), e.g. a date not in the future: once, before any record (a 422).
    validate: Callable[[Any], None] | None = None


class ActionRegistry:
    def __init__(self, repo_cls: type, actions: Sequence[BulkAction]) -> None:
        self.repo_cls = repo_cls
        self.actions = {a.id: a for a in actions}

    def __getitem__(self, action_id: str) -> BulkAction:
        if action_id not in self.actions:
            raise NotFound("action", action_id)
        return self.actions[action_id]


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: dict[int, int] | None = None              # id → the version the person saw
    query: Annotated[str, Field(max_length=8000)] | None = None  # "all matching": the list's own query string
    expected: int | None = None                    # with `query`: how many the person was told match
    input: dict[str, Any] | None = None
    dry_run: bool = False


class ActionResult(BaseModel):
    id: int
    status: Literal["done", "skipped", "failed"]
    reason: str | None = None
    version: int | None = None


class ActionOut(BaseModel):
    action: str
    dry_run: bool
    results: list[ActionResult]
    counts: dict[str, int]


def _spec(query: str) -> QuerySpec:
    p = QueryParams(query)
    return query_spec(page=1, page_size=1, sort=p.get("sort"), search=p.get("search"), filter=p.getlist("filter") or None,
                      group=None)


async def _targets(uow: Any, registry: ActionRegistry, body: ActionIn) -> dict[int, int | None]:
    if (body.ids is None) == (body.query is None):
        raise InvalidInput("action.targets", "give the records (ids) or the list's query, one of them")
    if body.ids is not None:
        if not 1 <= len(body.ids) <= MAX_RECORDS:
            raise InvalidInput("action.too_many", f"1 to {MAX_RECORDS} records at a time", {"max": MAX_RECORDS})
        return dict(body.ids)
    repo = registry.repo_cls(uow)
    ids = await repo.matching_ids(_spec(body.query or ""), MAX_RECORDS + 1)
    if len(ids) > MAX_RECORDS:
        raise InvalidInput("action.too_many", f"more than {MAX_RECORDS} match: narrow the list first", {"max": MAX_RECORDS})
    if body.expected is not None and len(ids) != body.expected:
        raise BusinessError("action.list_changed", f"the list changed: {len(ids)} now match, not {body.expected}; look again",
                            {"now": len(ids), "expected": body.expected})
    return {i: None for i in ids}  # "all matching": as they are now


async def run_action(uow: Any, registry: ActionRegistry, action_id: str, body: ActionIn) -> ActionOut:
    action = registry[action_id]
    await authorize(uow, action.permission)
    from app.platform.auditing.scope import current_scope
    if (scope := current_scope()) is not None:  # ≈ AuditLogAction: the record action that runs
        scope.add_action(f"{registry.repo_cls.model.__name__}Actions", action_id)
    data = None
    if action.input is not None and not body.dry_run:
        try:
            data = action.input.model_validate(body.input or {})
        except ValidationError as e:
            raise InvalidInput("request.invalid", "the action's input is not valid",
                               {"errors": [{"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
                                           for err in e.errors()]}) from None
        if action.validate:
            action.validate(data)
    s = uow.session
    model = registry.repo_cls.model
    out: list[ActionResult] = []
    events = uow.events
    for rid, version in (await _targets(uow, registry, body)).items():
        sp = await s.begin_nested()
        mark = events.mark()  # a record's events go with its writes: dropped on every rollback below

        async def undo() -> None:
            await sp.rollback()
            events.truncate(mark)  # noqa: B023 — called in this iteration only
        try:
            record = await s.get(model, rid, with_for_update=True, populate_existing=True)
            if record is None:
                raise NotFound(model.__name__, rid)
            # The rule first: what is already done (a double click, someone else) is skipped whatever its version.
            why = await action.check(uow, record) if action.check else None
            if why:
                await undo()
                out.append(ActionResult(id=rid, status="skipped", reason=why, version=record.version))
                continue
            if version is not None:
                check_version(record, version, "this record")
            if not body.dry_run:
                await action.run(uow, record, data)
                await s.flush()
            out.append(ActionResult(id=rid, status="done", version=record.version))
            if body.dry_run:
                await undo()
            else:
                await sp.commit()
        except Stale:
            await undo()
            out.append(ActionResult(id=rid, status="failed", reason="changed since you selected it: reload and look again"))
        except BusinessError as e:
            await undo()
            out.append(ActionResult(id=rid, status="failed", reason=e.message))
    counts = {k: sum(1 for r in out if r.status == k) for k in ("done", "skipped", "failed")}
    return ActionOut(action=action_id, dry_run=body.dry_run, results=out, counts=counts)


def action_router(registry: ActionRegistry, get_uow: Callable[..., Any]) -> APIRouter:
    """POST /actions/{action_id}: include it in the list's router BEFORE its /{id}/… routes."""
    router = APIRouter()

    @router.post("/actions/{action_id}", response_model=ActionOut)
    async def run(action_id: str, body: ActionIn, u: Annotated[Any, Depends(get_uow)]) -> ActionOut:
        out = await run_action(u, registry, action_id, body)
        if not body.dry_run:
            await u.commit()
        return out

    return router
