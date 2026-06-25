"""Tax determination matrix (Phase 0-B2, FIN-TAX-002/003).

Fixtures seed a representative subset of the migration seed — tests assert
the RULE ENGINE semantics (wildcards, priority, specificity, effective
dates, explicit no-match error), not the full Canadian rate table.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.crud.tax import TaxDeterminationError, determine
from app.models.tax import TaxCode, TaxRule

_FROM = date(2020, 1, 1)


def _code(code, ttype, prov, rate, recoverable=True, frm=_FROM, to=None):
    return TaxCode(code=code, name=code, tax_type=ttype, province=prov,
                   rate=Decimal(str(rate)), recoverable=recoverable,
                   effective_from=frm, effective_to=to, active=True)


def _rule(priority, codes, prov=None, cust=None, klass=None, direction="any"):
    return TaxRule(priority=priority, direction=direction, province=prov,
                   customer_type=cust, item_tax_class=klass,
                   tax_code_list=codes, active=True)


@pytest.fixture
async def seeded(db_session):
    db_session.add_all([
        _code("GST", "GST", None, 0.05),
        _code("HST_ON", "HST", "ON", 0.13),
        _code("HST_NS", "HST", "NS", 0.15, frm=_FROM, to=date(2025, 3, 31)),
        _code("HST_NS", "HST", "NS", 0.14, frm=date(2025, 4, 1)),
        _code("PST_BC", "PST", "BC", 0.07, recoverable=False),
        _code("QST", "QST", "QC", 0.09975),
        _code("ZERO", "NONE", None, 0.0),
    ])
    db_session.add_all([
        _rule(100, ["ZERO"], klass="zero_rated"),
        _rule(100, ["ZERO"], cust="export"),
        _rule(10, ["HST_ON"], prov="ON"),
        _rule(10, ["HST_NS"], prov="NS"),
        _rule(10, ["GST", "QST"], prov="QC"),
        _rule(10, ["GST", "PST_BC"], prov="BC"),
        _rule(1, ["GST"]),  # fallback
    ])
    await db_session.flush()
    return db_session


async def test_ontario_standard_is_hst(seeded):
    r = await determine(seeded, province="ON", item_tax_class="standard")
    assert [c["code"] for c in r["codes"]] == ["HST_ON"]
    assert r["combined_rate"] == Decimal("0.13000")


async def test_quebec_is_gst_plus_qst(seeded):
    r = await determine(seeded, province="QC")
    assert [c["code"] for c in r["codes"]] == ["GST", "QST"]
    assert r["combined_rate"] == Decimal("0.14975")


async def test_bc_pst_not_recoverable(seeded):
    r = await determine(seeded, province="BC")
    by_code = {c["code"]: c for c in r["codes"]}
    assert by_code["GST"]["recoverable"] is True
    assert by_code["PST_BC"]["recoverable"] is False


async def test_alberta_falls_back_to_gst_only(seeded):
    r = await determine(seeded, province="AB")
    assert [c["code"] for c in r["codes"]] == ["GST"]


async def test_zero_rated_beats_province(seeded):
    """Milk powder (zero-rated groceries) is ZERO even in Ontario — driven by
    the priority-100 rule, never by hardcoded product logic."""
    r = await determine(seeded, province="ON", item_tax_class="zero_rated")
    assert [c["code"] for c in r["codes"]] == ["ZERO"]
    assert r["combined_rate"] == Decimal("0.00000")


async def test_export_is_zero_rated(seeded):
    r = await determine(seeded, province="ON", customer_type="export")
    assert [c["code"] for c in r["codes"]] == ["ZERO"]


async def test_effective_dated_rate_switch(seeded):
    """NS HST 15% → 14% on 2025-04-01 — two effective-dated rows, same code."""
    before = await determine(seeded, province="NS", as_of=date(2025, 3, 1))
    after = await determine(seeded, province="NS", as_of=date(2025, 5, 1))
    assert before["combined_rate"] == Decimal("0.15000")
    assert after["combined_rate"] == Decimal("0.14000")


async def test_no_match_raises_explicitly(db_session):
    """Empty rule table → loud error, never a silent default (PRD §8.6)."""
    with pytest.raises(TaxDeterminationError):
        await determine(db_session, province="ON")


async def test_rule_with_unknown_code_raises(db_session):
    db_session.add(_rule(10, ["NOT_A_CODE"], prov="ON"))
    await db_session.flush()
    with pytest.raises(TaxDeterminationError):
        await determine(db_session, province="ON")
