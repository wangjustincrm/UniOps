import pytest
from sqlalchemy import select

from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.events import EventBuffer, StatusChanged
from app.platform.state import StateMachine
from tests.platform.conftest import AuditRow, Doc, Note, Thing

M = StateMachine(
    {"a": {"b"}, "b": {"c", "a"}, "c": set(), "hold": {"c"}},
    "demo",
    holds={"hold": {"a", "b"}},
)
M.bind(Thing)


def _go(e, dst, **kw):
    return M.transition(e, dst, actor=SYSTEM_ACTOR, events=EventBuffer(), **kw)


def test_allowed_and_refused_pairs():
    assert M.can("a", "b") and M.can("b", "c")
    assert not M.can("a", "c") and not M.can("c", "a")


def test_transition_sets_field_and_raises_event():
    e = Thing(status="a", id=7)
    buf = EventBuffer()
    ev = M.transition(e, "b", actor=SYSTEM_ACTOR, reason="why", events=buf)
    assert e.status == "b"
    assert isinstance(ev, StatusChanged) and (ev.old, ev.new, ev.reason, ev.entity_id) == ("a", "b", "why", 7)
    assert buf.drain() == [ev]


def test_events_buffer_is_required():
    with pytest.raises(TypeError):
        M.transition(Thing(status="a"), "b", actor=SYSTEM_ACTOR)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        M.transition(Thing(status="a"), "b", actor=SYSTEM_ACTOR, events=None)  # type: ignore[arg-type]


def test_invalid_transition_is_a_business_error_and_changes_nothing():
    e = Thing(status="a")
    with pytest.raises(BusinessError) as exc:
        _go(e, "c")
    assert exc.value.code == "state.invalid_transition"
    assert exc.value.details == {"machine": "demo", "from": "a", "to": "c"}
    assert e.status == "a"


def test_hold_state_returns_only_to_the_status_held_before():
    e = Thing(status="b", status_before_hold=None)
    _go(e, "hold")
    assert e.status_before_hold == "b"
    with pytest.raises(BusinessError):
        _go(e, "a")  # not where it came from
    _go(e, "b")
    assert e.status == "b" and e.status_before_hold is None


def test_hold_state_explicit_exit_clears_memory():
    e = Thing(status="a", status_before_hold=None)
    _go(e, "hold")
    _go(e, "c")
    assert e.status == "c" and e.status_before_hold is None


def test_hold_cannot_be_entered_from_unlisted_state():
    e = Thing(status="c", status_before_hold=None)
    with pytest.raises(BusinessError):
        _go(e, "hold")


def test_a_table_edge_into_a_hold_state_stores_the_status_held_before():
    m = StateMachine({"x": {"h"}, "y": {"h"}, "h": set()}, "edge", holds={"h": {"y"}})
    m.bind(Thing)
    assert m.holds == {"h": frozenset({"x", "y"})}
    e = Thing(status="x", status_before_hold=None)
    m.transition(e, "h", actor=SYSTEM_ACTOR, events=EventBuffer())
    assert e.status_before_hold == "x"
    m.transition(e, "x", actor=SYSTEM_ACTOR, events=EventBuffer())  # and it can return
    assert e.status == "x"


def test_bind_checks_the_columns_at_once():
    with pytest.raises(TypeError, match="status_before_hold"):
        StateMachine({"a": {"h"}, "h": set()}, "needs_memory", holds={"h": {"a"}}).bind(Note)
    with pytest.raises(TypeError, match="file_status"):
        StateMachine({"a": set()}, "wrong_field", field="file_status").bind(Thing)
    with pytest.raises(TypeError, match="not a mapped model"):
        StateMachine({"a": set()}, "plain").bind(dict)
    # No hold states: no memory column needed.
    StateMachine({"a": {"b"}, "b": set()}, "plain_docs", field="file_status").bind(Doc)


def test_an_unbound_entity_is_refused():
    unbound = StateMachine({"a": {"b"}, "b": set()}, "unbound")
    with pytest.raises(TypeError, match="bind"):
        unbound.transition(Thing(status="a"), "b", actor=SYSTEM_ACTOR, events=EventBuffer())


def test_table_with_unknown_target_is_rejected():
    with pytest.raises(ValueError):
        StateMachine({"a": {"zzz"}}, "bad")
    with pytest.raises(ValueError):
        StateMachine({"a": set()}, "bad", holds={"h": {"a"}})


def test_pairs_lists_table_and_hold_entries():
    assert M.pairs() == {("a", "b"), ("b", "c"), ("b", "a"), ("hold", "c"), ("a", "hold"), ("b", "hold")}


FILES = StateMachine({"Generated": {"Sent", "Failed"}, "Sent": set(), "Failed": set()}, "file", field="file_status")
FILES.bind(Doc)


async def test_transition_of_a_creation_audited_document_is_audited(uow_factory, person):
    async with uow_factory(person) as uow:
        d = Doc(name="940-1.edi", file_status="Generated")
        uow.session.add(d)
        await uow.commit()
    async with uow_factory(person, reason="sent by the cycle") as uow:
        d = await uow.session.get(Doc, d.id)
        FILES.transition(d, "Sent", actor=person, events=uow.events)
        d.name = "renamed.edi"  # not a machine field: not audited on a CreationAudited row
        await uow.commit()
    async with uow_factory(person) as uow:
        rows = (await uow.session.execute(select(AuditRow).where(AuditRow.entity == "pt_docs"))).scalars().all()
    assert [(r.action, r.before, r.after, r.reason, r.user_name) for r in rows] == [
        ("update", {"file_status": "Generated"}, {"file_status": "Sent"}, "sent by the cycle", "Pat Person"),
    ]


async def test_a_plain_document_update_without_a_transition_writes_no_audit(uow_factory, person):
    async with uow_factory(person) as uow:
        d = Doc(name="a", file_status="Generated")
        uow.session.add(d)
        await uow.commit()
    async with uow_factory(person) as uow:
        (await uow.session.get(Doc, d.id)).name = "b"
        await uow.commit()
    async with uow_factory(person) as uow:
        assert (await uow.session.execute(select(AuditRow).where(AuditRow.entity == "pt_docs"))).all() == []
