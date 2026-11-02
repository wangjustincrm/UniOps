"""Canadian address rules, offline: the province of a postal code (its first
letter) and a PO box."""
import pytest

from app.domain.address import address_flags, address_problem, is_po_box, postal_mismatch

LETTERS = {"A": "NL", "B": "NS", "C": "PE", "E": "NB", "G": "QC", "H": "QC", "J": "QC", "K": "ON", "L": "ON", "M": "ON",
           "N": "ON", "P": "ON", "R": "MB", "S": "SK", "T": "AB", "V": "BC", "X": "NT", "Y": "YT"}


@pytest.mark.parametrize(("letter", "province"), sorted(LETTERS.items()))
def test_every_first_letter_belongs_to_its_province_and_no_other(letter, province):
    code = f"{letter}1A 1A1"
    assert postal_mismatch(province, code) is None
    other = "BC" if province != "BC" else "ON"
    assert postal_mismatch(other, code) == f"postal code {code} is in {'/'.join(['NT', 'NU'] if letter == 'X' else [province])}, not {other}"


def test_x_serves_both_territories_and_nothing_is_said_without_both_parts():
    assert postal_mismatch("NU", "X0A 0H0") is None and postal_mismatch("NT", "X1A 2P8") is None
    assert postal_mismatch("YT", "X0A 0H0") == "postal code X0A 0H0 is in NT/NU, not YT"
    assert postal_mismatch(None, "K1A 0B1") is None and postal_mismatch("ON", "") is None
    assert postal_mismatch("on", "k1a 0b1") is None  # case does not matter


@pytest.mark.parametrize("line", ["PO Box 12", "P.O. Box 12", "p.o.box 7", "Post Office Box 3", "CP 34", "C.P. 34",
                                  "Case postale 5", "Box 56", "BOX #9", "RR 1 Box 4"])
def test_po_box_variants(line):
    assert is_po_box(line) and is_po_box(None, line)


@pytest.mark.parametrize("line", ["1 Boxwood Cres", "12 Main St", "Postal Plaza", "CPR Station Rd", "Unit 4, 55 Champlain"])
def test_not_a_po_box(line):
    assert not is_po_box(line)


def test_an_imported_ship_to_problem_and_flags():
    shop = {"address1": "1 rue", "city": "Montréal", "province_code": "QC", "country_code": "CA", "zip": "K1A 0B1"}
    assert address_problem(shop) == "postal code K1A 0B1 is in ON, not QC"
    assert address_problem(shop | {"zip": "H2X 1Y4"}) is None
    assert address_problem(shop | {"country_code": "US", "zip": "K1A"}) is None  # outside Canada: no Canadian rules
    assert address_problem({"province": "ON", "zip": "K1A 0B1"}) is None          # an 850 without a country is CA
    assert address_problem(None) is None
    assert address_flags({"address1": "C.P. 220"}) == ["PO box: the carrier may not deliver there"]
    assert address_flags({"address1": "1 Main"}) == [] and address_flags(None) == []
