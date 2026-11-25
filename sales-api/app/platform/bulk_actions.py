"""Record actions on one record or many (user 2026-11-23: an action is defined once, and some apply to a group of
records). The page (platform-ui/actions) defines the label, the form and where it shows; the server defines the rule
and the work, here, once per action:

    ORDER_ACTIONS = ActionRegistry(OrderRepository, [
        Action("settle", label="Mark settled", permission=SETTLE, input=SettleIn, check=settle_check, run=settle_run)])

The service's resource declares the registry (Resource.actions): the route comes from it, and /meta says each action
(its words, whether this person may use it, whether it fits many records, whether it needs a reason, what it sends).

POST /<list>/actions/<id> (`action_router`) takes the records as {id: version} (what the person saw), or "all matching"
as the list's own query string with the count the person saw (at most MAX_RECORDS). The server decides:

- the permission once (403 for the whole request; hiding in the UI is not security);
- each record in a savepoint of its own: missing → failed; `check` gives a reason → skipped (the rule first, so a
  second run of a done action is skipped whatever the version, never a second write); a version that moved → failed
  ("changed since you selected it"); `run` raises a BusinessError → failed; else done;
- dry_run: the same, without `run` and without the input (the confirm step's preview: "10 will …, 2 skipped");
- a record's domain events go with its writes: every rollback (skipped, failed, stale, dry run) also drops the events
  it raised, so commit dispatches only those of the records done.

Each record's audit reason and log entry are its own (`run` sets them). One record and many are this one route (the
single-record endpoints went with stage 3 l); one record that was left out or failed is said as an error by the page, and
is kept on the request's audit log as one.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm.exc import StaleDataError
from starlette.datastructures import QueryParams

from app.platform.authorization import authorize
from app.platform.commands import Stale, check_version, require_reason
from app.platform.errors import BusinessError, InvalidInput, NotFound
from app.platform.query import QuerySpec, query_spec
from app.platform.uow import Savepoint
from app.platform.routes import Router, own_commit

MAX_RECORDS = 500
# (uow, record) → None, or why it is skipped: the words, or the rule's own BusinessError (its words and its code)
Check = Callable[..., Awaitable["str | BusinessError | None"]]
Run = Callable[..., Awaitable[None]]                         # (uow, record, input) — or with a context: (uow, record, input, ctx)


@dataclass
class ActionContext:
    """What a run needs beside its record: the module's long-lived objects, why, and what was read before any lock."""
    runtime: Any = None
    reason: str | None = None
    prepared: Any = None
    note: str | None = None                # what to say of this record beside "done" (set by the run)
    problem: str | None = None             # done, and yet not what was asked for (a file that failed its checks): said as an error


@dataclass(frozen=True)
class Action:
    id: str
    permission: str
    run: Run
    check: Check | None = None
    input: type[BaseModel] | None = None
    # Checks of the input alone (not of a record), e.g. a date not in the future: once, before any record (a 422).
    validate: Callable[[Any], None] | None = None
    label: str = ""
    bulk: bool = True                      # many records at once; False: one at a time (refused otherwise)
    reason: Literal["none", "optional", "required"] = "none"  # the request's own `reason`, in the record's audit
    versioned: bool = True                 # False: the record has no version (its status is the guard)
    confirm: str | None = None             # what the confirm step says
    sends: str | None = None               # what leaves the building, if anything (a file, an email)
    context: bool = False                  # run and prepare take an ActionContext
    check_context: bool = False            # the check too (a rule that needs the runtime)
    # More permissions this input needs (e.g. sending too): checked once, before any record.
    also: Callable[[Any], Sequence[str]] | None = None
    # (uow, ids, ctx) → what the runs need from outside, read before any record is locked.
    prepare: Callable[..., Awaitable[Any]] | None = None
    lock: bool = True                      # False: the record is only read (its owner is what changes, locked by the run)


BulkAction = Action  # the name it had


async def refusal(rule: Awaitable[Any]) -> BusinessError | None:
    """A service's rule (it raises what refuses) as a `check` says it: why (its words and its code), or None."""
    try:
        await rule
    except BusinessError as e:
        return e
    return None


class ActionRegistry:
    def __init__(self, repo_cls: type, actions: Sequence[Action], *, lock_first: Callable[[Any, int], Awaitable[None]] | None = None) -> None:
        """`lock_first(uow, id)`: what is locked before the record itself, where every path takes another row first (one
        order of locks, so no two of them wait for each other)."""
        self.repo_cls = repo_cls
        self.lock_first = lock_first
        self.actions = {a.id: a for a in actions}

    def __getitem__(self, action_id: str) -> Action:
        if action_id not in self.actions:
            raise NotFound("action", action_id)
        return self.actions[action_id]


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: dict[int, int | None] | None = None       # id → the version the person saw (null: the record has none)
    query: Annotated[str, Field(max_length=8000)] | None = None  # "all matching": the list's own query string
    expected: int | None = None                    # with `query`: how many the person was told match
    input: dict[str, Any] | None = None
    reason: Annotated[str, Field(max_length=2000)] | None = None  # why, where the action asks for it
    dry_run: bool = False


class ActionResult(BaseModel):
    id: int
    status: Literal["done", "skipped", "failed"]
    reason: str | None = None
    version: int | None = None
    code: str | None = None      # why it was not done, for the page's words: entity.stale, entity.not_found, the rule's own
    note: str | None = None      # what to say of a done one
    problem: str | None = None   # done, and yet not what was asked for: never said as a success


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


async def run_action(uow: Any, registry: ActionRegistry, action_id: str, body: ActionIn, runtime: Any = None) -> ActionOut:
    action = registry[action_id]
    await authorize(uow, action.permission)
    from app.platform.auditing.scope import current_scope
    scope = current_scope()
    if scope is not None:  # ≈ AuditLogAction: the record action that runs
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
    for key in action.also(data) if action.also and not body.dry_run else ():
        await authorize(uow, key)
    ctx = ActionContext(runtime=runtime)
    if action.reason == "required" and not body.dry_run:
        ctx.reason = require_reason(body.reason)
    elif action.reason == "optional":
        ctx.reason = (body.reason or "").strip() or None
    if not action.bulk and (body.query is not None or len(body.ids or {}) > 1):
        raise InvalidInput("action.single", "one record at a time")
    if action.versioned and body.ids is not None and None in body.ids.values():
        raise InvalidInput("entity.version_required", "send the version you edited", {"field": "ids"})
    s = uow.session
    model = registry.repo_cls.model
    out: list[ActionResult] = []
    targets = await _targets(uow, registry, body)
    if action.prepare is not None and not body.dry_run:
        ctx.prepared = await action.prepare(uow, list(targets), ctx)
    for rid, version in targets.items():
        ctx.note = ctx.problem = None
        sp = await Savepoint.begin(uow)  # a record's events go with its writes: dropped on every undo below
        try:
            if registry.lock_first is not None and action.lock:
                await registry.lock_first(uow, rid)
            record = await s.get(model, rid, with_for_update=action.lock, populate_existing=True)
            if record is None:
                raise NotFound(model.__name__, rid)
            # The rule first: what is already done (a double click, someone else) is skipped whatever its version.
            why = await action.check(uow, record, *([ctx] if action.check_context else [])) if action.check else None
            if why:
                await sp.undo()
                code, words = (why.code, why.message) if isinstance(why, BusinessError) else (None, why)
                out.append(ActionResult(id=rid, status="skipped", reason=words, code=code, version=getattr(record, "version", None)))
                continue
            if version is not None and action.versioned:
                check_version(record, version, "this record")
            if not body.dry_run:
                await action.run(uow, record, data, *([ctx] if action.context else []))
                await s.flush()
            out.append(ActionResult(id=rid, status="done", version=getattr(record, "version", None), note=ctx.note, problem=ctx.problem))
            if body.dry_run:
                await sp.undo()
            else:
                await sp.keep()
        except (Stale, StaleDataError):
            await sp.undo()
            out.append(ActionResult(id=rid, status="failed", reason="changed since you selected it: reload and look again", code="entity.stale"))
        except BusinessError as e:
            await sp.undo()
            out.append(ActionResult(id=rid, status="failed", reason=e.message, code=e.code))
    if scope is not None and not body.dry_run and len(out) == 1 and out[0].status != "done":
        # One record refused: kept on the audit log as a refused request is (the answer itself is a result, not an error).
        scope.add_exception(f"{out[0].code or 'action.skipped'}: {out[0].reason}", code=out[0].code or "action.skipped")
    counts = {k: sum(1 for r in out if r.status == k) for k in ("done", "skipped", "failed")}
    return ActionOut(action=action_id, dry_run=body.dry_run, results=out, counts=counts)


def _no_runtime() -> None:
    return None


def action_router(registry: ActionRegistry, get_uow: Callable[..., Any], get_runtime: Callable[..., Any] | None = None) -> APIRouter:
    """POST /actions/{action_id}: include it in the list's router BEFORE its /{id}/… routes. `get_runtime`: the module's
    long-lived objects, for the actions that need them (ActionContext.runtime)."""
    router = Router()

    @router.post("/actions/{action_id}", response_model=ActionOut)
    @own_commit("a dry run keeps nothing")
    async def run(action_id: str, body: ActionIn, u: Annotated[Any, Depends(get_uow)],
                  rt: Annotated[Any, Depends(get_runtime or _no_runtime)] = None) -> ActionOut:
        out = await run_action(u, registry, action_id, body, rt)
        if not body.dry_run:
            await u.commit()
        return out

    return router
