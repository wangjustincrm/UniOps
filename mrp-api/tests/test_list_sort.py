"""Click-to-sort on mrp-api's paged lists (sort + dir contract).

For every endpoint: asc / desc order, empties last in BOTH directions, an
unknown key is a 422, no sort = the previous default order, and page 2
continues page 1's order. /inventory/batches also keeps its legacy
`descending` flag working.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.api.v1 import consignment
from app.models.consignment import ConsignmentStock
from app.models.forecast import ForecastVersion
from app.models.wms_inventory import WmsInventoryLot

pytestmark = pytest.mark.anyio
TODAY = date.today()


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _get(client, token, url, **params):
    r = await client.get(url, params=params, headers=_h(token))
    return r


# ── /inventory/batches ────────────────────────────────────────────────────

async def _lots(db):
    def lot(code, lot_no, qty, *, batch, expiry, qc=None):
        return WmsInventoryLot(
            warehouse_id="CANADA", material_code=code, lot_no=lot_no, uom="KG",
            qty=Decimal(qty), qty_allocated=Decimal(0), qty_onhold=Decimal(0),
            wms_status="02", mapped_status="available", expiry_date=expiry,
            supplier_batch=batch, qc_batch=qc, sync_batch_id="t")
    db.add_all([
        lot("CR0001", "L1", "30", batch="b-200", expiry=TODAY + timedelta(days=50), qc="Q2"),
        lot("CR0002", "L2", "10", batch="B-100", expiry=TODAY + timedelta(days=10), qc="Q1"),
        lot("CR0003", "L3", "20", batch=None, expiry=None),           # empty batch + no expiry
        lot("CR0004", "L4", "40", batch="B-300", expiry=TODAY + timedelta(days=90)),
    ])
    await db.commit()


def _codes(body):
    return [i["material_code"] for i in body["items"]]


async def test_batches_sort_asc_desc_nulls_last(client, db_session, admin_token):
    await _lots(db_session)
    url = "/api/v1/inventory/batches"
    asc = (await _get(client, admin_token, url, sort="supplier_batch", dir="asc")).json()
    # case-insensitive ("b-200" between B-100 and B-300), empty batch last
    assert _codes(asc) == ["CR0002", "CR0001", "CR0004", "CR0003"]
    desc = (await _get(client, admin_token, url, sort="supplier_batch", dir="desc")).json()
    assert _codes(desc) == ["CR0004", "CR0001", "CR0002", "CR0003"]
    q = (await _get(client, admin_token, url, sort="qty", dir="desc")).json()
    assert _codes(q) == ["CR0004", "CR0001", "CR0003", "CR0002"]
    e = (await _get(client, admin_token, url, sort="expiry_date", dir="desc")).json()
    assert _codes(e) == ["CR0004", "CR0001", "CR0002", "CR0003"]       # no expiry still last
    qc = (await _get(client, admin_token, url, sort="qc_batches", dir="asc")).json()
    assert _codes(qc)[:2] == ["CR0002", "CR0001"]
    assert (await _get(client, admin_token, url, sort="lots", dir="asc")).status_code == 200
    assert (await _get(client, admin_token, url, sort="quality_status", dir="asc")).status_code == 200
    assert (await _get(client, admin_token, url, sort="mapped_status", dir="desc")).status_code == 200


async def test_batches_default_legacy_and_422(client, db_session, admin_token):
    await _lots(db_session)
    url = "/api/v1/inventory/batches"
    default = (await _get(client, admin_token, url)).json()
    assert _codes(default) == ["CR0002", "CR0001", "CR0004", "CR0003"]  # earliest expiry first
    legacy = (await _get(client, admin_token, url, sort="qty", descending="true")).json()
    assert _codes(legacy) == ["CR0004", "CR0001", "CR0003", "CR0002"]
    assert (await _get(client, admin_token, url, sort="lot_no")).status_code == 422
    assert (await _get(client, admin_token, url, sort="qty", dir="sideways")).status_code == 422


async def test_batches_page_two_continues(client, db_session, admin_token):
    await _lots(db_session)
    url = "/api/v1/inventory/batches"
    full = _codes((await _get(client, admin_token, url, sort="qty", dir="asc")).json())
    p1 = _codes((await _get(client, admin_token, url, sort="qty", dir="asc", page=1, page_size=2)).json())
    p2 = _codes((await _get(client, admin_token, url, sort="qty", dir="asc", page=2, page_size=2)).json())
    assert p1 + p2 == full == ["CR0002", "CR0003", "CR0001", "CR0004"]


# ── /inventory/materials ──────────────────────────────────────────────────

async def test_materials_sort(client, db_session, admin_token):
    await _lots(db_session)
    url = "/api/v1/inventory/materials"
    default = _codes((await _get(client, admin_token, url)).json())
    assert default == ["CR0001", "CR0002", "CR0003", "CR0004"]
    assert _codes((await _get(client, admin_token, url, sort="on_hand", dir="desc")).json()) == \
        ["CR0004", "CR0001", "CR0003", "CR0002"]
    asc = _codes((await _get(client, admin_token, url, sort="next_expiry", dir="asc")).json())
    desc = _codes((await _get(client, admin_token, url, sort="next_expiry", dir="desc")).json())
    assert asc == ["CR0002", "CR0001", "CR0004", "CR0003"]
    assert desc == ["CR0004", "CR0001", "CR0002", "CR0003"]           # no expiry last both ways
    p1 = _codes((await _get(client, admin_token, url, sort="on_hand", dir="asc", page=1, page_size=3)).json())
    p2 = _codes((await _get(client, admin_token, url, sort="on_hand", dir="asc", page=2, page_size=3)).json())
    assert p1 + p2 == ["CR0002", "CR0003", "CR0001", "CR0004"]
    assert (await _get(client, admin_token, url, sort="nope")).status_code == 422


# ── /consignment/stock ────────────────────────────────────────────────────

async def _no_names(token):
    return {}


async def test_consignment_sort(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(consignment, "resolve_material_names", _no_names)
    db_session.add_all([
        ConsignmentStock(material_code="S1", lot_no="a2", qty=Decimal(5), count_date=date(2026, 8, 1),
                         expiry_date=date(2027, 1, 1), expiry_source="wms"),
        ConsignmentStock(material_code="S2", lot_no="A1", qty=Decimal(9), count_date=date(2026, 9, 1),
                         expiry_date=None, expiry_source=None),
        ConsignmentStock(material_code="S3", lot_no="a3", qty=Decimal(1), count_date=date(2026, 7, 1),
                         expiry_date=date(2026, 12, 1), expiry_source="manual"),
    ])
    await db_session.commit()
    url = "/api/v1/consignment/stock"
    assert _codes((await _get(client, admin_token, url)).json()) == ["S2", "S1", "S3"]   # newest count first
    assert _codes((await _get(client, admin_token, url, sort="lot_no", dir="asc")).json()) == ["S2", "S1", "S3"]
    assert _codes((await _get(client, admin_token, url, sort="expiry_date", dir="asc")).json()) == ["S3", "S1", "S2"]
    assert _codes((await _get(client, admin_token, url, sort="expiry_date", dir="desc")).json()) == ["S1", "S3", "S2"]
    # by the label shown: "from WMS" < "manual", none last
    assert _codes((await _get(client, admin_token, url, sort="expiry_source", dir="asc")).json()) == ["S1", "S3", "S2"]
    assert _codes((await _get(client, admin_token, url, sort="expiry_source", dir="desc")).json()) == ["S3", "S1", "S2"]
    p1 = _codes((await _get(client, admin_token, url, sort="qty", dir="desc", page=1, page_size=2)).json())
    p2 = _codes((await _get(client, admin_token, url, sort="qty", dir="desc", page=2, page_size=2)).json())
    assert p1 + p2 == ["S2", "S1", "S3"]
    # the page now pages for real: default order, page 2 of 2, total intact
    b1 = (await _get(client, admin_token, url, page=1, page_size=2)).json()
    b2 = (await _get(client, admin_token, url, page=2, page_size=2)).json()
    assert _codes(b1) + _codes(b2) == ["S2", "S1", "S3"] and b1["total"] == b2["total"] == 3
    assert (await _get(client, admin_token, url, sort="id")).status_code == 422


# ── /forecast/versions ────────────────────────────────────────────────────

def _versions(body):
    return [i["version_no"] for i in body["items"]]


async def test_forecast_versions_sort(client, db_session, admin_token):
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db_session.add_all([
        ForecastVersion(version_no="fcv-b", status="confirmed", horizon_start_month="2026-09",
                        horizon_months=12, source_anchor_month="2026-08", created_at=base),
        ForecastVersion(version_no="FCV-A", status="confirmed", horizon_start_month="2026-09",
                        horizon_months=18, source_anchor_month=None, created_at=base + timedelta(days=2)),
        ForecastVersion(version_no="FCV-C", status="confirmed", horizon_start_month="2026-09",
                        horizon_months=6, source_anchor_month="2026-07", created_at=base + timedelta(days=1)),
    ])
    await db_session.commit()
    url = "/api/v1/forecast/versions"
    assert _versions((await _get(client, admin_token, url)).json()) == ["FCV-A", "FCV-C", "fcv-b"]  # newest first
    assert _versions((await _get(client, admin_token, url, sort="version_no", dir="asc")).json()) == ["FCV-A", "fcv-b", "FCV-C"]
    assert _versions((await _get(client, admin_token, url, sort="source_anchor_month", dir="asc")).json()) == ["FCV-C", "fcv-b", "FCV-A"]
    assert _versions((await _get(client, admin_token, url, sort="source_anchor_month", dir="desc")).json()) == ["fcv-b", "FCV-C", "FCV-A"]
    p1 = _versions((await _get(client, admin_token, url, sort="horizon_months", dir="asc", page=1, page_size=2)).json())
    p2 = _versions((await _get(client, admin_token, url, sort="horizon_months", dir="asc", page=2, page_size=2)).json())
    assert p1 + p2 == ["FCV-C", "fcv-b", "FCV-A"]
    assert (await _get(client, admin_token, url, sort="note")).status_code == 422
