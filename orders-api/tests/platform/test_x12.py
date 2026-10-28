from datetime import datetime

import pytest

from app.platform import x12
from app.platform.actors import SYSTEM_ACTOR
from tests.platform.conftest import SEQUENCE

PARAMS = x12.EnvelopeParams(
    sender_qualifier="ZZ", sender_id="CANADAROYALMILK",
    receiver_qualifier="12", receiver_id="9055651212",
    functional_id="OW", isa_version="00401", gs_version="006030",
)


def test_envelope_reproduces_the_prd_a4_header_and_trailer():
    body = [x12.Segment("W06", "F", "00104", "20261007", "00104")]
    raw = x12.Envelope(PARAMS).render(
        [x12.TransactionSet("943", body)], control=104, at=datetime(2026, 10, 7, 10, 9),
    ).decode()
    lines = raw.split("\n")
    assert lines[0] == ("ISA*00*          *00*          *ZZ*CANADAROYALMILK*12*9055651212     "
                        "*261007*1009*U*00401*000000104*0*P*>~")
    assert lines[1] == "GS*OW*CANADAROYALMILK*9055651212*20261007*1009*000000104*X*006030~"
    assert lines[2] == "ST*943*000000104~"
    assert lines[3] == "W06*F*00104*20261007*00104~"
    assert lines[4:7] == ["SE*3*000000104~", "GE*1*000000104~", "IEA*1*000000104~"]
    assert raw.endswith("~\n")
    assert len(lines[0]) == 106  # ISA is fixed-width


def test_separator_characters_inside_elements_are_replaced():
    assert x12.Segment("N1", "ST", "A*B~C>D").render() == "N1*ST*A B C D~"
    assert x12.Segment("N9", "LT", "", "").render() == "N9*LT~"  # no trailing empty elements
    with pytest.raises(x12.X12Error):
        x12.Segment("bad tag")


SAMPLE_944 = (
    "ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261008*0624*U*00401*000016468*0*P*>~\n"
    "GS*RE*9055651212*CANADAROYALMILK*20261008*0624*16468*X*006030~\n"
    "ST*944*16468~\n"
    "W17*F*20261008*00104*00104~\n"
    "LX*1~\nW07*600*EA*628347940140*VN*CF0092~\nN9*LT*26C253125110231~\n"
    "W14*600~\n"
    "SE*7*16468~\n"
    "GE*1*16468~\n"
    "IEA*1*000016468~\n"
)


def test_read_takes_separators_from_isa_and_splits_the_structure():
    ic = x12.read(SAMPLE_944.encode())
    assert (ic.sender_id, ic.receiver_id, ic.control, ic.usage) == ("9055651212", "CANADAROYALMILK", "000016468", "P")
    (g,) = ic.groups
    (t,) = g.transactions
    assert (g.functional_id, g.control, g.version) == ("RE", "16468", "006030")
    assert (t.code, t.control) == ("944", "16468") and t.count_ok
    assert t.segments[0].tag == "W17" and t.segments[0].get(3) == "00104"
    assert t.segments[2].get(5) == "CF0092"


def test_read_with_other_separators():
    raw = SAMPLE_944.replace("*", "|").replace("~\n", "^").replace("|>^", "|:^")
    ic = x12.read(raw)
    assert ic.separators.element == "|" and ic.separators.segment == "^"
    assert next(ic.transactions()).segments[0].get(3) == "00104"


def test_read_refuses_garbage_and_missing_se():
    with pytest.raises(x12.X12Error):
        x12.read(b"{\"json\": true}")
    with pytest.raises(x12.X12Error):
        x12.read(SAMPLE_944.replace("SE*7*16468~\n", ""))


def test_zero_padding_rules():
    assert x12.pad_control(16468) == "000016468"
    assert x12.same_control("000016468", "16468") and x12.same_control(104, "000000104")
    assert not x12.same_control("16468", "16469") and not x12.same_control(None, "1")
    with pytest.raises(x12.X12Error):
        x12.pad_control(1_000_000_000)


async def test_control_sequence_draws_increasing_numbers(uow_factory):
    seq = x12.ControlSequence(SEQUENCE.name)
    async with uow_factory(SYSTEM_ACTOR) as uow:
        a = await seq.next(uow.session)
        b = await seq.next(uow.session)
    assert b == a + 1 and a >= 100
    with pytest.raises(ValueError):
        x12.ControlSequence("x'); drop table y; --")
