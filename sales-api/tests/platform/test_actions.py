"""One record action (platform/bulk_actions.py, stage 3): what it declares is what the server does — one or many, a reason,
the version seen, what it needs from outside — and /meta says it."""
import pytest
import sqlalchemy as sa

from app.platform.authorization import PermissionDenied
from app.platform.auditing import begin_scope
from app.platform.bulk_actions import Action, ActionIn, ActionRegistry, refusal, run_action
from app.platform.errors import BusinessError, InvalidInput
from app.platform.resource import Resource
from tests.platform.conftest import Widget, WidgetCreate, WidgetRepository, WidgetService, actor_with

RW = actor_with("pt.read", "pt.config")
seen: list = []


async def rename(uow, w, data, ctx):  # noqa: ARG001
    seen.append((w.code, ctx.reason, ctx.runtime, ctx.prepared))
    w.name = f"{w.name}!"
    ctx.note = f"was {w.code}"


async def plain(uow, w, data):  # noqa: ARG001
    w.name = "plain"


async def already(uow, w):  # noqa: ARG001
    return "already done" if w.name.endswith("!") else None


async def read_ahead(uow, ids, ctx):  # noqa: ARG001
    seen.append(("prepare", sorted(ids)))
    return {"n": len(ids)}


async def not_open(uow, w):  # noqa: ARG001
    return await refusal(rule(w))


async def rule(w):
    if w.name == "closed":
        raise BusinessError("widget.closed", "the widget is closed")


async def half(uow, w, data, ctx):  # noqa: ARG001
    ctx.problem = f"{w.code} was written, and refused by its checks" if w.code == "A" else None

REGISTRY = ActionRegistry(WidgetRepository, [
    Action("open", permission="pt.read", run=half, check=not_open, context=True),
    Action("one", permission="pt.config", run=rename, check=already, bulk=False, reason="required", context=True, label="One", confirm="Sure?", sends="a file"),
    Action("many", permission="pt.read", run=rename, context=True, prepare=read_ahead),
    Action("unversioned", permission="pt.read", run=plain, versioned=False),
    Action("more", permission="pt.read", run=plain, also=lambda data: ["pt.config"])])


@pytest.fixture
async def two(uow_factory):
    seen.clear()
    async with uow_factory(RW) as u:
        out = [await WidgetService(u).create(WidgetCreate(code=c, name=c)) for c in ("A", "B")]
        await u.commit()
    return out


async def run(uow_factory, id, body, actor=RW, runtime="rt"):
    async with uow_factory(actor) as u:
        out = await run_action(u, REGISTRY, id, ActionIn(**body), runtime)
        await u.commit()
        return out


async def test_one_at_a_time_needs_its_reason_and_says_what_it_did(uow_factory, two):
    a, b = two
    for body, code in (({"ids": {a.id: a.version, b.id: b.version}, "reason": "why not"}, "action.single"),
                       ({"query": "", "reason": "why not"}, "action.single"),
                       ({"ids": {a.id: None}, "reason": "why not"}, "entity.version_required")):
        with pytest.raises(InvalidInput) as e:
            await run(uow_factory, "one", body)
        assert e.value.code == code
    for reason in (None, "", " x "):
        with pytest.raises(BusinessError) as e:
            await run(uow_factory, "one", {"ids": {a.id: a.version}, "reason": reason})
        assert (e.value.code, e.value.status_code, e.value.details["field"]) == ("reason.required", 422, "reason")
    dry = await run(uow_factory, "one", {"ids": {a.id: a.version}, "dry_run": True})  # the preview asks for none
    assert dry.counts["done"] == 1 and seen == []
    [r] = (await run(uow_factory, "one", {"ids": {a.id: a.version}, "reason": "  because  "})).results
    assert (r.status, r.note, r.code) == ("done", "was A", None) and seen == [("A", "because", "rt", None)]


async def test_one_record_still_says_which_of_the_two_happened(uow_factory, two):
    """Condition of 2026-11-24: already done is skipped whatever the version; moved meanwhile is stale. Both with words."""
    a, b = two
    await run(uow_factory, "one", {"ids": {a.id: a.version}, "reason": "because"})
    [r] = (await run(uow_factory, "one", {"ids": {a.id: a.version}, "reason": "because"})).results
    assert (r.status, r.reason, r.code) == ("skipped", "already done", None)
    [r] = (await run(uow_factory, "one", {"ids": {b.id: b.version + 5}, "reason": "because"})).results
    assert (r.status, r.code) == ("failed", "entity.stale") and "reload" in r.reason
    [r] = (await run(uow_factory, "one", {"ids": {99_999: 1}, "reason": "because"})).results
    assert (r.status, r.code) == ("failed", "entity.not_found")
    async with uow_factory(RW) as u:
        assert (await u.session.execute(sa.select(Widget.name).order_by(Widget.code))).scalars().all() == ["A!", "B"]


async def test_what_comes_from_outside_is_read_once_before_any_record_and_not_for_a_preview(uow_factory, two):
    ids = {w.id: w.version for w in two}
    await run(uow_factory, "many", {"ids": ids, "dry_run": True})
    assert seen == []
    out = await run(uow_factory, "many", {"ids": ids})
    assert out.counts["done"] == 2 and seen[0] == ("prepare", sorted(ids)) and [x[3] for x in seen[1:]] == [{"n": 2}] * 2


async def test_a_record_without_a_version_and_more_permissions(uow_factory, two):
    a, _ = two
    assert (await run(uow_factory, "unversioned", {"ids": {a.id: None}})).counts["done"] == 1
    with pytest.raises(PermissionDenied):
        await run(uow_factory, "more", {"ids": {a.id: a.version}}, actor=actor_with("pt.read"))
    with pytest.raises(PermissionDenied):
        await run(uow_factory, "one", {"ids": {a.id: a.version}, "reason": "because"}, actor=actor_with("pt.read"))


async def test_meta_says_each_action(uow_factory):
    svc = type("Acting", (WidgetService,), {"resource": Resource(actions=REGISTRY)})
    async with uow_factory(actor_with("pt.read")) as u:
        acts = {a["id"]: a for a in (await svc(u).meta())["capabilities"]["actions"]}
    assert acts["one"] == {"id": "one", "allowed": False, "label": "One", "bulk": False, "reason": "required", "confirm": "Sure?", "sends": "a file"}
    assert acts["many"]["allowed"] is True and acts["many"]["bulk"] is True and acts["many"]["reason"] == "none"


async def test_a_rule_s_own_code_is_said_with_its_words(uow_factory, two):
    a, b = two
    async with uow_factory(RW) as u:
        (await u.session.get(Widget, a.id)).name = "closed"
        await u.commit()
    [x, y] = (await run(uow_factory, "open", {"ids": {a.id: a.version, b.id: b.version}})).results
    assert (x.status, x.code, x.reason) == ("skipped", "widget.closed", "the widget is closed")
    assert (y.status, y.code, y.reason, y.problem) == ("done", None, None, None)


async def test_done_and_yet_not_what_was_asked_for_is_said_apart_and_not_carried_to_the_next(uow_factory, two):
    a, b = two
    [x, y] = (await run(uow_factory, "open", {"ids": {a.id: a.version, b.id: b.version}})).results
    assert (x.status, x.problem, x.note) == ("done", "A was written, and refused by its checks", None) and y.problem is None


async def test_one_record_refused_is_an_error_on_the_audit_log_many_and_a_preview_are_results(uow_factory, two):
    a, b = two

    async def logged(body):
        async with begin_scope("request", "test") as scope:
            await run(uow_factory, "open", body)
        return scope.log.error_code, scope.log.exceptions

    stale = {"ids": {a.id: a.version + 5}}
    assert await logged(stale) == ("entity.stale", ["entity.stale: changed since you selected it: reload and look again"])
    assert await logged(stale | {"dry_run": True}) == (None, [])
    assert await logged({"ids": {a.id: a.version + 5, b.id: b.version}}) == (None, [])
    assert await logged({"ids": {b.id: b.version}}) == (None, [])  # done
    async with uow_factory(RW) as u:
        (await u.session.get(Widget, a.id)).name = "closed"
        await u.commit()
    assert await logged({"ids": {a.id: a.version}}) == ("widget.closed", ["widget.closed: the widget is closed"])
