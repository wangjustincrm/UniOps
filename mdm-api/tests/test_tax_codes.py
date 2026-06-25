"""Tax code admin CRUD (Finance Tax Settings) — create / update / deactivate,
effective-date listing, rate-version lookup.

Mirrors test_uom.py: exercises the crud layer via db_session. Write authorization
(system_admin | finance_manager | ap_clerk) is enforced by the same require_roles
dependency proven in test_uom / test_auth_roles, not re-tested here.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.crud import tax as tax_crud
from app.schemas.tax import TaxCodeCreate, TaxCodeUpdate


def _new(code="HST_ON", rate="0.13", frm=date(2024, 1, 1), **kw) -> TaxCodeCreate:
    base = dict(
        code=code, name=f"{code} {rate}", tax_type="HST", province="ON",
        rate=Decimal(rate), recoverable=True, effective_from=frm,
        effective_to=None, active=True,
    )
    base.update(kw)
    return TaxCodeCreate(**base)


async def test_create_and_read_back(db_session):
    created = await tax_crud.create_code(db_session, _new())
    assert created.code == "HST_ON"
    assert created.rate == Decimal("0.13")
    assert created.active is True

    fetched = await tax_crud.get_code_by_id(db_session, created.id)
    assert fetched is not None and fetched.id == created.id


async def test_update_partial_leaves_other_fields(db_session):
    row = await tax_crud.create_code(db_session, _new())
    updated = await tax_crud.update_code(db_session, row, TaxCodeUpdate(rate=Decimal("0.15")))
    assert updated.rate == Decimal("0.15")
    assert updated.name == "HST_ON 0.13"  # untouched (exclude_unset)
    assert updated.recoverable is True


async def test_update_can_clear_nullable_province(db_session):
    row = await tax_crud.create_code(db_session, _new())
    updated = await tax_crud.update_code(db_session, row, TaxCodeUpdate(province=None))
    # province explicitly passed as null -> cleared, not "leave alone"
    assert updated.province is None


async def test_deactivate_is_soft(db_session):
    row = await tax_crud.create_code(db_session, _new())
    deactivated = await tax_crud.deactivate_code(db_session, row)
    assert deactivated.active is False
    # row still exists (soft delete) so snapshots stay valid
    assert await tax_crud.get_code_by_id(db_session, row.id) is not None


async def test_list_codes_filters_active_and_effective_date(db_session):
    await tax_crud.create_code(db_session, _new(code="GST", tax_type="GST", province=None,
                                                rate="0.05", frm=date(2020, 1, 1)))
    future = await tax_crud.create_code(db_session, _new(code="FUT", rate="0.20",
                                                         frm=date(2099, 1, 1)))
    inactive = await tax_crud.create_code(db_session, _new(code="OLD", rate="0.10"))
    await tax_crud.deactivate_code(db_session, inactive)

    active_today = {c.code for c in await tax_crud.list_codes(db_session)}
    assert "GST" in active_today
    assert "FUT" not in active_today      # effective in the future
    assert "OLD" not in active_today      # inactive

    # admin view returns everything regardless of date/active
    all_codes = {c.code for c in await tax_crud.list_all_codes(db_session)}
    assert {"GST", "FUT", "OLD"} <= all_codes
    assert future.code in all_codes


async def test_get_code_version_enforces_unique_identity(db_session):
    await tax_crud.create_code(db_session, _new(frm=date(2024, 1, 1), rate="0.13"))
    await tax_crud.create_code(db_session, _new(frm=date(2025, 1, 1), rate="0.14"))

    v1 = await tax_crud.get_code_version(db_session, "HST_ON", date(2024, 1, 1))
    v2 = await tax_crud.get_code_version(db_session, "HST_ON", date(2025, 1, 1))
    assert v1 is not None and v1.rate == Decimal("0.13")
    assert v2 is not None and v2.rate == Decimal("0.14")
    assert await tax_crud.get_code_version(db_session, "HST_ON", date(2023, 1, 1)) is None
