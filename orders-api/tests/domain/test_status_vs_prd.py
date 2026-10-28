"""The 9.1 machines against the PRD itself (design §13.6): the fulfillment and
invoice tables are parsed from docs/prd/ORDER-BRIDGE.md and compared pair by
pair; the only differences allowed are the listed additions and hold entries
(product owner, 2026-10-27, noted under PRD 9.1)."""
import os
import re
from pathlib import Path

import pytest

from app.domain import status as st
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError

PRD = Path(os.getenv("PRD_PATH", Path(__file__).resolve().parents[3] / "docs" / "prd" / "ORDER-BRIDGE.md"))
HELD = "<held before>"


def _table(title: str) -> dict[str, set[str]]:
    text = PRD.read_text(encoding="utf-8")
    start = text.index(f"**{title}**")
    rows = {}
    for line in text[start:].splitlines()[1:]:
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] in ("Value", "---"):
            continue
        nxt = re.sub(r"\([^)]*\)", "", cells[2]).replace(", once cleared", "")
        targets = set()
        for part in nxt.split(","):
            part = re.sub(r"^or\s+", "", part.strip())
            if not part or part == "—":
                continue
            targets.add(HELD if "held before" in part else part)
        for value in cells[0].split(" / "):
            rows[value.strip()] = targets
    return rows


@pytest.fixture(scope="module")
def prd_present():
    if not PRD.exists():
        pytest.fail(f"PRD not found at {PRD}; run the tests with the repository mounted")


def _compare(machine, table, additions, holds):
    expected = {}
    for state, targets in table.items():
        expected[state] = {t for t in targets if t != HELD} | {str(t) for t in additions.get(state, ())}
    actual = {str(s): {str(t) for t in ts} for s, ts in machine.transitions.items()}
    assert actual == expected
    # A state is a hold state exactly when 9.1 says it returns to "the status it held before".
    assert {s for s, t in table.items() if HELD in t} == {str(h) for h in machine.holds}
    assert {str(h): {str(s) for s in src} for h, src in machine.holds.items()} == {
        str(h): {str(s) for s in src} for h, src in holds.items()}


def test_fulfillment_matches_prd_table(prd_present):
    table = _table("Fulfillment status")
    assert len(table) == 14
    _compare(st.FULFILLMENT, table, st.FULFILLMENT_ADDITIONS, st.FULFILLMENT_HOLD_ENTRIES)


def test_invoice_matches_prd_table(prd_present):
    table = _table("Invoice status")
    assert len(table) == 9
    _compare(st.INVOICE, table, {}, st.INVOICE_HOLD_ENTRIES)


def test_the_listed_additions_are_exactly_the_agreed_ones():
    F, Inv = st.Fulfillment, st.Invoice
    assert st.FULFILLMENT_ADDITIONS == {
        F.GENERATED_940: {F.CANCELLED},
        F.TIMEOUT_940: {F.ACCEPTED_940, F.REJECTED_940, F.SHIPPED},
    }
    assert st.TRANSFER_ADDITIONS == {st.Transfer.FAILED: {st.Transfer.GENERATED_943, st.Transfer.CANCELLED}}
    assert st.ACK_ADDITIONS == {st.Ack.TIMEOUT: {st.Ack.ACCEPTED, st.Ack.REJECTED, st.Ack.ACCEPTED_BY_945}}
    assert st.FULFILLMENT_HOLD_ENTRIES == {F.ON_HOLD: {F.SHIPPED, F.SENT_940, F.ACCEPTED_940, F.TIMEOUT_940}}
    assert F.REJECTED_940 not in st.FULFILLMENT_HOLD_ENTRIES[F.ON_HOLD]
    assert st.INVOICE_HOLD_ENTRIES[Inv.BLOCKED] == set(Inv) - {Inv.NOT_REQUIRED, Inv.BLOCKED}


class Order:
    __tablename__ = "orders_under_test"

    def __init__(self, f, i=st.Invoice.PENDING):
        self.id = 1
        self.fulfillment_status, self.fulfillment_status_before_hold = f, None
        self.invoice_status, self.invoice_status_before_hold = i, None


def test_on_hold_returns_to_the_status_it_held_before():
    F = st.Fulfillment
    o = Order(F.ACCEPTED_940)
    st.FULFILLMENT.transition(o, F.ON_HOLD, actor=SYSTEM_ACTOR)
    with pytest.raises(BusinessError):
        st.FULFILLMENT.transition(o, F.SENT_940, actor=SYSTEM_ACTOR)
    st.FULFILLMENT.transition(o, F.ACCEPTED_940, actor=SYSTEM_ACTOR)
    assert o.fulfillment_status == F.ACCEPTED_940 and o.fulfillment_status_before_hold is None


def test_blocked_invoice_returns_only_to_the_status_held_before():
    Inv = st.Invoice
    o = Order(st.Fulfillment.SHIPPED, Inv.READY)
    st.INVOICE.transition(o, Inv.BLOCKED, actor=SYSTEM_ACTOR)
    for other in (Inv.SENT, Inv.QUEUED, Inv.PENDING):
        with pytest.raises(BusinessError):
            st.INVOICE.transition(o, other, actor=SYSTEM_ACTOR)
    st.INVOICE.transition(o, Inv.READY, actor=SYSTEM_ACTOR)


def test_every_pair_of_every_machine_is_decided():
    """Enumerate all (src, dst) pairs: allowed exactly when the table or a hold entry says so."""
    for m in st.MACHINES:
        allowed = m.pairs()
        for s in m.states:
            for d in m.states:
                assert m.can(s, d) == ((s, d) in allowed), (m.name, s, d)


def test_transfer_chain_from_prd_prose():
    T = st.Transfer
    chain = [T.DRAFT, T.READY_TO_SEND, T.GENERATED_943, T.WAITING_FOR_997, T.WAITING_FOR_944]
    for a, b in zip(chain, chain[1:]):
        assert st.TRANSFER.can(a, b)
    assert {d for d in T if st.TRANSFER.can(T.WAITING_FOR_944, d)} == {T.RECEIVED, T.PARTIALLY_RECEIVED, T.DISCREPANCY}
    assert st.TRANSFER.can(T.WAITING_FOR_997, T.FAILED)
    assert {d for d in T if st.TRANSFER.can(T.FAILED, d)} == {T.GENERATED_943, T.CANCELLED}  # resend or cancel
    # An ACK timeout is not a transfer status: the transfer waits in Waiting for 997.
    assert "Timeout" not in {t.value for t in T}
    # Cancelled before the 943 is sent, or after it was rejected (Failed).
    assert {s for s in T if st.TRANSFER.can(s, T.CANCELLED)} == {T.DRAFT, T.READY_TO_SEND, T.GENERATED_943, T.FAILED}
    assert st.TRANSFER.holds == {T.ON_HOLD: frozenset({T.RECEIVED, T.PARTIALLY_RECEIVED, T.DISCREPANCY})}


def test_late_answers_after_a_timeout_still_apply():
    F, A = st.Fulfillment, st.Ack
    assert {d for d in F if st.FULFILLMENT.can(F.TIMEOUT_940, d)} == {
        F.GENERATED_940, F.CANCELLED, F.ACCEPTED_940, F.REJECTED_940, F.SHIPPED, F.ON_HOLD}
    assert {d for d in A if st.ACK.can(A.TIMEOUT, d)} == {A.ACCEPTED, A.REJECTED, A.ACCEPTED_BY_945}
    # Terminal ACK outcomes stay terminal.
    for s in (A.ACCEPTED, A.REJECTED, A.ACCEPTED_BY_945):
        assert not any(st.ACK.can(s, d) for d in A)
