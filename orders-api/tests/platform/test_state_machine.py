from types import SimpleNamespace

import pytest

from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.events import EventBuffer, StatusChanged
from app.platform.state import StateMachine

M = StateMachine(
    {"a": {"b"}, "b": {"c", "a"}, "c": set(), "hold": {"c"}},
    "demo",
    holds={"hold": {"a", "b"}},
)


def test_allowed_and_refused_pairs():
    assert M.can("a", "b") and M.can("b", "c")
    assert not M.can("a", "c") and not M.can("c", "a")


def test_transition_sets_field_and_raises_event():
    e = SimpleNamespace(status="a", id=7)
    buf = EventBuffer()
    ev = M.transition(e, "b", actor=SYSTEM_ACTOR, reason="why", events=buf)
    assert e.status == "b"
    assert isinstance(ev, StatusChanged) and (ev.old, ev.new, ev.reason, ev.entity_id) == ("a", "b", "why", 7)
    assert buf.drain() == [ev]


def test_invalid_transition_is_a_business_error_and_changes_nothing():
    e = SimpleNamespace(status="a")
    with pytest.raises(BusinessError) as exc:
        M.transition(e, "c", actor=SYSTEM_ACTOR)
    assert exc.value.code == "state.invalid_transition"
    assert exc.value.details == {"machine": "demo", "from": "a", "to": "c"}
    assert e.status == "a"


def test_hold_state_returns_only_to_the_status_held_before():
    e = SimpleNamespace(status="b", status_before_hold=None)
    M.transition(e, "hold", actor=SYSTEM_ACTOR)
    assert e.status_before_hold == "b"
    with pytest.raises(BusinessError):
        M.transition(e, "a", actor=SYSTEM_ACTOR)  # not where it came from
    M.transition(e, "b", actor=SYSTEM_ACTOR)
    assert e.status == "b" and e.status_before_hold is None


def test_hold_state_explicit_exit_clears_memory():
    e = SimpleNamespace(status="a", status_before_hold=None)
    M.transition(e, "hold", actor=SYSTEM_ACTOR)
    M.transition(e, "c", actor=SYSTEM_ACTOR)
    assert e.status == "c" and e.status_before_hold is None


def test_hold_cannot_be_entered_from_unlisted_state():
    e = SimpleNamespace(status="c", status_before_hold=None)
    with pytest.raises(BusinessError):
        M.transition(e, "hold", actor=SYSTEM_ACTOR)


def test_table_with_unknown_target_is_rejected():
    with pytest.raises(ValueError):
        StateMachine({"a": {"zzz"}}, "bad")
    with pytest.raises(ValueError):
        StateMachine({"a": set()}, "bad", holds={"h": {"a"}})


def test_pairs_lists_table_and_hold_entries():
    assert M.pairs() == {("a", "b"), ("b", "c"), ("b", "a"), ("hold", "c"), ("a", "hold"), ("b", "hold")}
