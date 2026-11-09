"""The 9.1 machines against the PRD itself (design §13.6). Every machine is
compared with the text of docs/prd/ORDER-BRIDGE.md:

- fulfillment and invoice: the 9.1 tables, parsed row by row;
- transfer, inbound / outbound file and ACK: the prose lines, parsed for their
  arrow chains; each extra clause ("Failed on 997 rejection", "plus
  Duplicate", …) is matched VERBATIM and mapped to its edges below, so a
  reworded PRD fails here until the mapping is reviewed.

The only differences allowed are the listed additions and hold entries
(product owner, 2026-10-27 / 2026-10-28, noted under PRD 9.1). Each machine is
then enumerated over every (from, to) pair against that PRD-derived set — not
against the machine's own tables."""
import os
import re
from pathlib import Path

import pytest

from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain import status as st
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.events import EventBuffer

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


def _enumerate(machine, expected_pairs: set[tuple[str, str]]):
    """Every (from, to) over the machine's states: allowed exactly when the
    PRD-derived set says so. Returns from a hold state to the status held
    before are dynamic and checked separately."""
    states = {str(s): s for s in machine.states}
    assert set(states) >= {a for a, _ in expected_pairs} | {b for _, b in expected_pairs}
    for a, sa in states.items():
        for b, sb in states.items():
            assert machine.can(sa, sb) == ((a, b) in expected_pairs), (machine.name, a, b)


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
    pairs = {(s, t) for s, ts in expected.items() for t in ts}
    pairs |= {(str(s), str(h)) for h, src in holds.items() for s in src}
    _enumerate(machine, pairs)


def _line(label: str) -> str:
    text = PRD.read_text(encoding="utf-8")
    start = text.index(f"**{label}:**") + len(label) + 5
    return text[start:text.index("\n", start)].strip()


def _chain(chain: str) -> tuple[list[str], set[tuple[str, str]]]:
    """`A → B → C / D` → states in order and the edges between neighbours."""
    steps = [[s.strip() for s in step.split(" / ")] for step in chain.split(" → ")]
    edges = {(a, b) for left, right in zip(steps, steps[1:]) for a in left for b in right}
    return [s for step in steps for s in step], edges


def _clauses(found: list[str], meaning: dict[str, set[tuple[str, str]]]) -> set[tuple[str, str]]:
    """Each clause the PRD has must be one whose meaning is written down here,
    and each written-down clause must still be in the PRD."""
    assert sorted(found) == sorted(meaning), "PRD 9.1 wording changed — review the clause mapping"
    return set().union(*meaning.values()) if meaning else set()


def _actual(machine) -> set[tuple[str, str]]:
    return {(str(s), str(d)) for s, ds in machine.transitions.items() for d in ds}


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
        # + Shipped from every status before our 940 is out: only a 945 proves shipment (user 2026-11-09)
        F.GENERATED_940: {F.CANCELLED, F.SHIPPED},
        F.TIMEOUT_940: {F.ACCEPTED_940, F.REJECTED_940, F.SHIPPED},
        F.PENDING: {F.REVIEW, F.SHIPPED},           # FR-OB-15 + FR-OB-16 (2026-10-29)
        F.PAYMENT_HOLD: {F.REVIEW, F.SHIPPED},      # FR-OB-15 + FR-OB-16
        F.REVIEW: {F.PAYMENT_HOLD, F.SHIPPED},      # FR-OB-12
        F.ARCHIVE_SUPPRESSED: {F.SHIPPED},
        F.REJECTED_940: {F.SHIPPED},
        F.FAILED_940: {F.SHIPPED},
        F.SENT_940: {F.GENERATED_940},   # FR-OB-03c confirmed replacement (P4, 2026-10-30)
        F.ACCEPTED_940: {F.GENERATED_940},
    }
    assert st.TRANSFER_ADDITIONS == {st.Transfer.FAILED: {st.Transfer.GENERATED_943, st.Transfer.CANCELLED},
                                     st.Transfer.GENERATED_943: {st.Transfer.FAILED}}  # FR-OB-81 (P7, 2026-11-02)
    assert st.ACK_ADDITIONS == {st.Ack.TIMEOUT: {st.Ack.ACCEPTED, st.Ack.REJECTED, st.Ack.ACCEPTED_BY_945}}
    assert st.FULFILLMENT_HOLD_ENTRIES == {F.ON_HOLD: {F.SHIPPED, F.SENT_940, F.ACCEPTED_940, F.TIMEOUT_940}}
    assert F.REJECTED_940 not in st.FULFILLMENT_HOLD_ENTRIES[F.ON_HOLD]
    assert st.INVOICE_HOLD_ENTRIES[Inv.BLOCKED] == set(Inv) - {Inv.NOT_REQUIRED, Inv.BLOCKED}


class _Base(DeclarativeBase):
    pass


class Order(_Base):
    """Never created in a database; only the machines' columns."""
    __tablename__ = "orders_under_test"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fulfillment_status: Mapped[str] = mapped_column(String(40))
    fulfillment_status_before_hold: Mapped[str | None] = mapped_column(String(40), nullable=True)
    invoice_status: Mapped[str] = mapped_column(String(40))
    invoice_status_before_hold: Mapped[str | None] = mapped_column(String(40), nullable=True)


st.FULFILLMENT.bind(Order)
st.INVOICE.bind(Order)


def _order(f, i=st.Invoice.PENDING):
    return Order(id=1, fulfillment_status=f, invoice_status=i)


def _go(machine, entity, dst):
    machine.transition(entity, dst, actor=SYSTEM_ACTOR, events=EventBuffer())


def test_on_hold_returns_to_the_status_it_held_before():
    F = st.Fulfillment
    o = _order(F.ACCEPTED_940)
    _go(st.FULFILLMENT, o, F.ON_HOLD)
    with pytest.raises(BusinessError):
        _go(st.FULFILLMENT, o, F.SENT_940)
    _go(st.FULFILLMENT, o, F.ACCEPTED_940)
    assert o.fulfillment_status == F.ACCEPTED_940 and o.fulfillment_status_before_hold is None


def test_blocked_invoice_returns_only_to_the_status_held_before():
    Inv = st.Invoice
    o = _order(st.Fulfillment.SHIPPED, Inv.READY)
    _go(st.INVOICE, o, Inv.BLOCKED)
    for other in (Inv.SENT, Inv.QUEUED, Inv.PENDING):
        with pytest.raises(BusinessError):
            _go(st.INVOICE, o, other)
    _go(st.INVOICE, o, Inv.READY)


def test_transfer_matches_prd_prose(prd_present):
    line = _line("Transfer status")
    chain, *clauses = [c.strip() for c in line.split("; ")]
    states, edges = _chain(chain)
    before_sent = states[:states.index("Waiting for 997")]
    received = ["Received", "Partially Received", "Discrepancy"]
    edges |= _clauses(clauses, {
        "Failed on 997 rejection": {("Waiting for 997", "Failed")},
        "Cancelled before the 943 is sent, or after it was rejected (Failed)":
            {(s, "Cancelled") for s in before_sent} | {("Failed", "Cancelled")},
        "On Hold when a second, different 944 arrives (FR-OB-80 to FR-OB-84).": set(),  # a hold entry
    })
    holds = {"On Hold": set(received)}
    assert {str(s) for s in st.Transfer} == set(states) | {"Failed", "Cancelled", "On Hold"}
    additions = {(str(a), str(b)) for a, bs in st.TRANSFER_ADDITIONS.items() for b in bs}
    assert _actual(st.TRANSFER) == edges | additions
    assert {str(h): {str(s) for s in src} for h, src in st.TRANSFER.holds.items()} == holds
    _enumerate(st.TRANSFER, edges | additions | {(s, "On Hold") for s in received})


def _file_parts() -> tuple[str, str, str]:
    line = _line("File and ACK status")
    m = re.fullmatch(r"inbound processing (.*?); outbound (.*?)\. ACK: (.*)\.", line)
    assert m, line
    return m.group(1), m.group(2), m.group(3)


def test_inbound_file_matches_prd_prose(prd_present):
    inbound, _, _ = _file_parts()
    chain, plus = inbound.split(", plus ")
    states, edges = _chain(chain)
    edges |= _clauses([plus], {"Duplicate (FR-OB-71)": {("Pending", "Duplicate")}})
    assert {str(s) for s in st.InboundFile} == set(states) | {"Duplicate"}
    additions = {(str(a), str(b)) for a, bs in st.INBOUND_FILE_ADDITIONS.items() for b in bs}
    assert _actual(st.INBOUND_FILE) == edges | additions
    _enumerate(st.INBOUND_FILE, edges | additions)


def test_outbound_file_matches_prd_prose(prd_present):
    _, outbound, _ = _file_parts()
    chain, rest = outbound.split(", plus ", 1)
    plus, suppressed = rest.split(". ", 1)
    states, edges = _chain(chain)
    edges |= _clauses([plus, suppressed], {
        "Replaced for a 940 superseded by a confirmed replacement (FR-OB-03c) and Cancelled":
            {("Sent", "Replaced"), ("Ready To Send", "Cancelled"), ("Failed", "Cancelled")},
        "Suppressed means the send or write was refused because its environment switch is off (dev and test "
        "never reach partners; product owner, 2026-10-28): it is never sent automatically, not even when the "
        "switch is turned on — a person reviews it and resends (Suppressed → Sending) or cancels it":
            {("Suppressed", "Sending"), ("Suppressed", "Cancelled")},
    })
    assert {str(s) for s in st.OutboundFile} == set(states) | {"Replaced", "Cancelled"}
    assert _actual(st.OUTBOUND_FILE) == edges
    _enumerate(st.OUTBOUND_FILE, edges)


def test_ack_matches_prd_values(prd_present):
    """9.1 lists the ACK values only. Reading of the list: Not Required and
    Waiting are where an ACK starts; every later value is an answer to
    Waiting; plus the listed late-answer additions."""
    _, _, ack = _file_parts()
    values = [v.strip() for v in ack.split(",")]
    assert [str(a) for a in st.Ack] == values
    answers = values[values.index("Waiting") + 1:]
    edges = {("Waiting", a) for a in answers}
    additions = {(str(a), str(b)) for a, bs in st.ACK_ADDITIONS.items() for b in bs}
    assert _actual(st.ACK) == edges | additions
    assert {str(s) for s in st.ACK.initial} == {"Not Required", "Waiting"}
    _enumerate(st.ACK, edges | additions)


def test_every_machine_is_compared_with_the_prd():
    assert {m.name for m in st.MACHINES} == {"fulfillment", "invoice", "transfer", "inbound_file", "outbound_file", "ack"}


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
