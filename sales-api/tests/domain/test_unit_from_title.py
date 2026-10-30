"""PRD A.11 (product owner, 2026-10-30): for Shopify the variant title decides
the unit — a pack unit it names, else EA — with one safety net: a title that
looks like a pack no unit of the item matches goes to review."""
from decimal import Decimal

import pytest

from app.domain.order_rules import looks_like_pack, unit_from_title

EA_ONLY = {"EA": Decimal(1)}
PACKS = {"EA": Decimal(1), "CASE": Decimal(6), "2PACK": Decimal(2)}


@pytest.mark.parametrize("title, unit", [
    ("Case (6 Cans)", "CASE"),                          # the unit's name inside the title
    ("2-Pack", "2PACK"),
    ("Bib", "EA"), ("Can", "EA"), ("Default Title", "EA"), ("Each", "EA"), (None, "EA"), ("Vanilla 900g", "EA"),
    ("2-Can", None),                                    # a pack, but 2PACK has no "2-Can" title: review
    ("Box of 12", None), ("3 x Can", None), ("12ct", None), ("Pack of 3", None),
])
def test_the_title_decides_a_pack_unit_else_ea(title, unit):
    assert unit_from_title(title, PACKS) == unit


def test_an_ea_only_item_follows_the_same_rule():
    assert unit_from_title("Bib", EA_ONLY) == "EA"
    assert unit_from_title("Case (6 Cans)", EA_ONLY) is None  # never ships a case as 1 EA


def test_shopify_titles_name_a_unit_and_the_longest_match_wins():
    titles = {"2PACK": ["2-Can", "Twin pack"], "CASE": ["Case (6 Cans)"], "EA": ["Single can"]}
    assert unit_from_title("2-Can", PACKS, titles) == "2PACK"
    assert unit_from_title("Formula - Twin Pack", PACKS, titles) == "2PACK"
    assert unit_from_title("Case (6 Cans)", PACKS, titles) == "CASE"
    assert unit_from_title("Case of 6", PACKS, titles) == "CASE"
    assert unit_from_title("Single Can", PACKS, titles) == "EA"
    # a deactivated unit's titles are not passed in: "2-Can" names nothing, looks like a pack
    assert unit_from_title("2-Can", {"EA": Decimal(1), "CASE": Decimal(6)}, {"CASE": [], "EA": []}) is None


@pytest.mark.parametrize("title, pack", [
    ("Box of 12", True), ("3 x Can", True), ("3x can", True), ("12ct", True), ("6 cans", True), ("2-Pack", True),
    ("Case", False),          # no number
    ("1 Can", False),         # 1 is not a pack
    ("Stage 2", False),       # a number, no pack word
    ("Bib", False), ("Vanilla 900g", False), ("", False), (None, False),
    ("Boxer 12", False),      # words are whole tokens: "boxer" is no "box"
])
def test_looks_like_pack_needs_a_number_of_two_or_more_and_a_pack_word(title, pack):
    assert looks_like_pack(title) is pack
