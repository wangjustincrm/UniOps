"""NC sync runs — model/migration + service + API tests."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.nc_sync import RUNNING, SUCCESS, NcSyncRun


async def test_nc_sync_run_roundtrip(db_session):
    run = NcSyncRun(id=uuid.uuid4(), mode="incremental", status=RUNNING,
                    started_by=uuid.uuid4(),
                    started_at=datetime.now(timezone.utc))
    db_session.add(run)
    await db_session.flush()
    got = (await db_session.execute(
        select(NcSyncRun).where(NcSyncRun.id == run.id))).scalar_one()
    assert got.status == RUNNING
    assert got.vouchers_inserted == 0          # server default
    assert got.watermark_to is None


def test_nc_configured_all_or_nothing(monkeypatch):
    from app.core.config import settings
    from app.services import nc_sync
    for f in ("nc_host", "nc_service", "nc_user", "nc_password"):
        monkeypatch.setattr(settings, f, "x")
    assert nc_sync.nc_configured() is True
    monkeypatch.setattr(settings, "nc_password", None)
    assert nc_sync.nc_configured() is False


def _mini_extract():
    from app.services.nc_sync import NcExtract
    # one voucher, two lines (5101 with cc E01 -> MOH-0106-E01; 2202 no dims);
    # line 1 is NC "both-sided" (dr and cr both >0) -> must net to one side.
    return NcExtract(
        ccy={"CADPK": "CAD"},
        aux={"ASS1": ("0104", "E01", "CRM004")},
        vouchers=[("NCPK1", "2026", "07", 12, "test voucher",
                   "2026-07-10 09:00:00", "2026-07-11 08:00:00")],
        details=[
            ("NCPK1", 1, "5101", 150, 50, 150, 50, "CADPK", 1, "expense", "ASS1"),
            ("NCPK1", 2, "2202", 0, 100, 0, 100, "CADPK", 1, "payable", None),
        ],
        max_creationtime="2026-07-11 08:00:00",
    )


def test_transform_maps_dims_and_nets_sides():
    from decimal import Decimal
    from app.services.nc_sync import transform
    cc_id, dept_id, ba_id = object(), object(), object()
    vouchers, lines, dims, unmapped = transform(
        _mini_extract(), uni_cc={"MOH-0106-E01": cc_id},
        uni_dept={"0104": dept_id}, uni_ba={"CRM004": ba_id}, skip_pks=set())
    assert len(vouchers) == 1 and vouchers[0]["jv_number"] == "记-202607-12"
    assert vouchers[0]["nc_pk"] == "NCPK1"
    l1 = next(l for l in lines if l[2] == 1)
    assert (l1[5], l1[6]) == (Decimal("100"), Decimal("0"))   # netted to debit
    assert l1[11] is cc_id and l1[12] is dept_id
    assert len(dims) == 1 and dims[0][2] == "income_expense_item" and dims[0][3] is ba_id
    assert unmapped == 0


def test_transform_skips_existing_and_counts_unmapped():
    from app.services.nc_sync import NcExtract, transform
    ex = _mini_extract()
    # skip the only voucher -> nothing out
    v, l, d, _ = transform(ex, {}, {}, {}, skip_pks={"NCPK1"})
    assert v == [] and l == [] and d == []
    # unknown cc code -> unmapped counted (line still produced, cc_id None)
    ex2 = NcExtract(ccy=ex.ccy, aux={"ASS1": ("", "ZZZ", "")},
                    vouchers=ex.vouchers, details=ex.details[:1],
                    max_creationtime=ex.max_creationtime)
    v2, l2, _, unmapped2 = transform(ex2, {}, {}, {}, skip_pks=set())
    assert len(l2) == 1 and l2[0][11] is None and unmapped2 == 1
