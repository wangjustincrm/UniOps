from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError, InvalidInput
from app.platform.query import QuerySpec
from app.platform.services import Command, CommandService
from app.platform.state import StateMachine
from tests.platform.conftest import AuditRow, Thing, Widget, WidgetCreate, WidgetService, WidgetUpdate
from app.platform.repository import Repository


async def _create(uow_factory, actor, **kw):
    async with uow_factory(actor) as uow:
        out = await WidgetService(uow).create(WidgetCreate(**kw))
        await uow.commit()
    return out


# ── exclude_unset: null clears, absent is untouched (the PA tax-clearing lesson)

async def test_null_clears_the_field(uow_factory, person):
    w = await _create(uow_factory, person, code="A", name="Alpha", email="a@x.com")
    async with uow_factory(person) as uow:
        out = await WidgetService(uow).update(w.id, WidgetUpdate.model_validate({"email": None}), w.version)
        await uow.commit()
    assert out.email is None and out.name == "Alpha"


async def test_absent_field_is_untouched(uow_factory, person):
    w = await _create(uow_factory, person, code="A", name="Alpha", email="a@x.com")
    async with uow_factory(person) as uow:
        out = await WidgetService(uow).update(w.id, WidgetUpdate.model_validate({}), w.version)
        await uow.commit()
    assert out.email == "a@x.com" and out.name == "Alpha" and out.version == w.version
    async with uow_factory(person) as uow:
        audit = (await uow.session.execute(select(AuditRow.action))).scalars().all()
    assert audit == ["create"]  # an empty update writes nothing


async def test_stale_version_is_refused(uow_factory, person):
    w = await _create(uow_factory, person, code="A", name="Alpha")
    async with uow_factory(person) as uow:
        await WidgetService(uow).update(w.id, WidgetUpdate(name="B"), w.version)
        await uow.commit()
    async with uow_factory(person) as uow:
        with pytest.raises(BusinessError) as e:
            await WidgetService(uow).update(w.id, WidgetUpdate(name="C"), w.version)
    assert e.value.code == "entity.stale"
    async with uow_factory(person) as uow:
        with pytest.raises(InvalidInput):
            await WidgetService(uow).update(w.id, WidgetUpdate(name="C"), None)


async def test_validate_create_hook_and_deactivate(uow_factory, person):
    with pytest.raises(BusinessError) as e:
        await _create(uow_factory, person, code="X", name="forbidden")
    assert e.value.code == "widget.name_forbidden"
    w = await _create(uow_factory, person, code="A", name="Alpha")
    async with uow_factory(person) as uow:
        out = await WidgetService(uow).deactivate(w.id)
        await uow.commit()
    assert out.active is False
    assert not hasattr(WidgetService, "delete")


# ── CSV: validate every row, then all or nothing

CSV_OK = b"code,name,email,price,active\nA,Alpha,,1.50,true\nB,Beta,b@x.com,2,true\n"


async def test_import_writes_all_rows_in_one_go(uow_factory, person):
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(CSV_OK, dry_run=False)
        await uow.commit()
    assert rep.ok and rep.written and rep.counts == {"create": 2, "update": 0, "unchanged": 0}
    async with uow_factory(person) as uow:
        rows = (await uow.session.execute(select(Widget).order_by(Widget.code))).scalars().all()
    assert [(w.code, w.price, w.email) for w in rows] == [("A", Decimal("1.50"), None), ("B", Decimal("2.00"), "b@x.com")]


async def test_one_bad_row_reports_every_error_and_writes_nothing(uow_factory, person):
    bad = (b"code,name,email,price,active\n"
           b"A,Alpha,,1.50,true\n"
           b"B,Beta,,not-a-number,true\n"
           b"A,Again,,1,true\n"
           b"C,forbidden,,1,true\n"
           b",NoKey,,1,true\n")
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(bad, dry_run=False)
        await uow.commit()
    assert not rep.ok and not rep.written and rep.rows == []
    assert sorted((e.row, e.code) for e in rep.errors) == [
        (2, "csv.invalid"), (3, "csv.duplicate_key"), (4, "widget.name_forbidden"), (5, "csv.missing_key"),
    ]
    async with uow_factory(person) as uow:
        assert (await uow.session.execute(select(Widget))).scalars().all() == []


async def test_unknown_column_is_rejected(uow_factory, person):
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(b"code,name,colour\nA,Alpha,red\n", dry_run=False)
    assert not rep.ok and [(e.code, e.column) for e in rep.errors] == [("csv.unknown_column", "colour")]


async def test_dry_run_shows_before_after_and_writes_nothing(uow_factory, person):
    await _create(uow_factory, person, code="A", name="Alpha", price=Decimal("1.50"))
    csv_bytes = b"code,name,price\nA,Alpha,2.00\nB,Beta,1\n"
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(csv_bytes, dry_run=True)
        await uow.commit()
    assert rep.ok and not rep.written
    plan = {r.key: r for r in rep.rows}
    assert plan["A"].action == "update" and plan["A"].before == {"price": "1.50"} and plan["A"].after == {"price": "2.00"}
    assert plan["B"].action == "create"
    async with uow_factory(person) as uow:
        rows = (await uow.session.execute(select(Widget))).scalars().all()
    assert [(w.code, w.price) for w in rows] == [("A", Decimal("1.50"))]


async def test_export_round_trips(uow_factory, person):
    async with uow_factory(person) as uow:
        await WidgetService(uow).import_csv(CSV_OK, dry_run=False)
        await uow.commit()
    async with uow_factory(person) as uow:
        data = await WidgetService(uow).export_csv(QuerySpec())
    assert data.decode() == "code,name,email,price,active,version\nA,Alpha,,1.50,true,1\nB,Beta,b@x.com,2.00,true,1\n"
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(data, dry_run=True)
    assert rep.ok and rep.counts == {"create": 0, "update": 0, "unchanged": 2}


# ── CommandService

MACHINE = StateMachine({"open": {"done"}, "done": set()}, "thing")
MACHINE.bind(Thing)


class ThingRepository(Repository[Thing]):
    model = Thing


class Finish(CommandService[Thing]):
    repo_cls = ThingRepository
    machine = MACHINE
    permission = "pt.operate"

    def target(self, agg, cmd):
        return "done"

    def check(self, agg, cmd):
        if getattr(cmd, "refuse", False):
            raise BusinessError("thing.refused")


@dataclass(kw_only=True)
class FinishCmd(Command):
    refuse: bool = False


async def test_command_locks_transitions_raises_event_and_the_caller_commits(uow_factory, person):
    async with uow_factory(person) as uow:
        t = Thing(status="open")
        uow.session.add(t)
        await uow.commit()
    async with uow_factory(person) as uow:
        await Finish(uow).execute(FinishCmd(aggregate_id=t.id, reason="all good"))
        await uow.commit()  # the outermost caller
        assert [e.new for e in uow.events.dispatched] == ["done"]
    async with uow_factory(person) as uow:
        assert (await uow.session.get(Thing, t.id)).status == "done"
        with pytest.raises(BusinessError) as e:
            await Finish(uow).execute(FinishCmd(aggregate_id=t.id))
        assert e.value.code == "state.invalid_transition"


async def test_command_refused_by_check_changes_nothing(uow_factory, person):
    async with uow_factory(person) as uow:
        t = Thing(status="open")
        uow.session.add(t)
        await uow.commit()
    async with uow_factory(person) as uow:
        with pytest.raises(BusinessError):
            await Finish(uow).execute(FinishCmd(aggregate_id=t.id, refuse=True))
    async with uow_factory(SYSTEM_ACTOR) as uow:
        assert (await uow.session.get(Thing, t.id)).status == "open"


# ── CSV hardening (review 2026-10-28)


async def test_export_escapes_formulas_and_import_restores_them(uow_factory, person):
    await _create(uow_factory, person, code="F", name="=HYPERLINK(\"x\")", email="@evil", price=Decimal("-1.50"))
    async with uow_factory(person) as uow:
        data = await WidgetService(uow).export_csv(QuerySpec())
    line = data.decode().splitlines()[1]
    assert line == "F,\"'=HYPERLINK(\"\"x\"\")\",'@evil,-1.50,true,1"  # numbers stay numbers
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(data, dry_run=True)
    assert rep.ok and rep.counts == {"create": 0, "update": 0, "unchanged": 1}


async def test_oversized_upload_is_refused(uow_factory, person):
    from app.platform.services import MAX_IMPORT_BYTES
    big = b"code,name\n" + b"A,x\n" * (MAX_IMPORT_BYTES // 4 + 1)
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(big, dry_run=True)
    assert not rep.ok and [e.code for e in rep.errors] == ["csv.too_large"]


async def test_keys_match_after_trimming_and_case_folding(uow_factory, person):
    await _create(uow_factory, person, code="ABC", name="Alpha")
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(b"code,name\n abc ,Renamed\n", dry_run=False)
        await uow.commit()
    assert rep.ok and rep.counts == {"create": 0, "update": 1, "unchanged": 0}
    async with uow_factory(person) as uow:
        rows = (await uow.session.execute(select(Widget.code, Widget.name))).all()
    assert [tuple(r) for r in rows] == [("ABC", "Renamed")]  # the stored spelling is kept
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(b"code,name\nabc,One\nABC,Two\n", dry_run=True)
    assert [(e.row, e.code) for e in rep.errors] == [(2, "csv.duplicate_key")]


async def test_an_edited_export_of_a_changed_record_is_refused(uow_factory, person):
    w = await _create(uow_factory, person, code="A", name="Alpha")
    async with uow_factory(person) as uow:
        exported = await WidgetService(uow).export_csv(QuerySpec())
    async with uow_factory(person) as uow:  # someone edits it meanwhile
        await WidgetService(uow).update(w.id, WidgetUpdate(name="Changed"), w.version)
        await uow.commit()
    edited = exported.replace(b"Alpha", b"Mine")
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(edited, dry_run=False)
    assert not rep.ok and [(e.row, e.column, e.code) for e in rep.errors] == [(1, "version", "entity.stale")]
    # The same edit against a fresh export goes through.
    async with uow_factory(person) as uow:
        fresh = (await WidgetService(uow).export_csv(QuerySpec())).replace(b"Changed", b"Mine")
        rep = await WidgetService(uow).import_csv(fresh, dry_run=False)
        await uow.commit()
    assert rep.ok and rep.counts["update"] == 1


async def test_a_database_refusal_is_reported_against_its_row_and_nothing_is_kept(uow_factory, person):
    data = b"code,name\nOK1,Fine\n" + b"X" * 25 + b",Too long for the column\nOK2,Fine\n"
    async with uow_factory(person) as uow:
        rep = await WidgetService(uow).import_csv(data, dry_run=False)
        if rep.ok:
            await uow.commit()
    assert not rep.ok and not rep.written and [(e.row, e.code) for e in rep.errors] == [(2, "csv.rejected_by_database")]
    async with uow_factory(person) as uow:
        assert (await uow.session.execute(select(Widget))).scalars().all() == []


def test_a_json_cell_exports_as_json_text():
    """A jsonb column must read back on import: JSON, not Python's repr."""
    from app.platform.services import _csv_cell
    assert _csv_cell({"b": 1, "a": "x"}) == '{"a": "x", "b": 1}'
    assert _csv_cell(["é"]) == '["é"]'
    assert _csv_cell(True) == "true" and _csv_cell(None) == ""  # the other kinds are unchanged
