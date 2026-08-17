"""GET /api/v1/inventory/lots — response shape (items/total/page/page_size,
matching mdm-api/app/api/v1/materials.py's MaterialListResponse idiom)."""
import pytest

from app.services.wms_sync import service

FAKE_ROWS = [
    {
        "warehouseid": "CANADA", "sku": "CF0086", "lotnum": "HGC1976532",
        "qty": 420, "qtyallocated": 0, "qtyonhold": 0,
        "lotatt01": "2025-01-03", "lotatt02": "2027-01-02", "lotatt03": "2025-01-20",
        "lotatt05": "20250103 291041001", "lotatt08": "02", "lotatt13": "0000131",
        "lotatt14": "CASN2502100006*189", "edittime": None,
    },
]


@pytest.mark.anyio
async def test_list_lots_echoes_page_and_page_size(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)
    await service.run_wms_sync(db_session)

    resp = await client.get(
        "/api/v1/inventory/lots",
        params={"page": 1, "page_size": 20},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["items"]) == 1


# ── search, filters and paging (Inventory feature, 2026-08-17) ───────────
#
# Lots are inserted directly rather than driven through the WMS sync: these
# tests are about the read surface, and the sync has its own suite.

import uuid
from datetime import date, timedelta
from decimal import Decimal

from app.models.epms_mirror import EpmsPoLineItem, EpmsPurchaseOrder, MdmMaterial
from app.models.wms_inventory import WmsInventoryLot

TODAY = date.today()
AUTH = "Authorization"


async def _lot(db, code, lot_no, qty="100", *, expiry=None, status="available",
               onhold="0", allocated="0", supplier_batch=None, warehouse="CANADA"):
    db.add(WmsInventoryLot(
        warehouse_id=warehouse, material_code=code, lot_no=lot_no,
        qty=Decimal(qty), qty_allocated=Decimal(allocated), qty_onhold=Decimal(onhold),
        wms_status="02", mapped_status=status, expiry_date=expiry,
        supplier_batch=supplier_batch, sync_batch_id="test",
    ))
    await db.commit()


async def _material(db, code, name, *, erp_class="0102", uom="KGM"):
    db.add(MdmMaterial(
        id=uuid.uuid4(), code=code, name=name, base_uom=uom,
        shelf_life_months=None, erp_class_code=erp_class, erp_class_name=None))
    await db.commit()


async def _open_po(db, code, qty, received="0", *, arrival=None, status="issued", type_=1):
    po = EpmsPurchaseOrder(
        id=uuid.uuid4(), number=f"PO-{uuid.uuid4().hex[:6]}", type=type_,
        status=status, vendor_name="Test Vendor", expected_delivery=None)
    db.add(po)
    await db.commit()
    db.add(EpmsPoLineItem(
        id=uuid.uuid4(), po_id=po.id, material_id=code, qty=Decimal(qty),
        received_qty=Decimal(received), unit="KGM", planned_arrival_date=arrival))
    await db.commit()
    return po


# ── /inventory/lots ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_lots_search_matches_code_name_lot_and_supplier_batch(
    client, db_session, admin_token,
):
    """People look a lot up by whichever of the four they happen to be holding."""
    await _material(db_session, "CR0025", "Lactose Edible Grade")
    await _lot(db_session, "CR0025", "LOT-AAA", supplier_batch="SUP-BATCH-9")
    await _material(db_session, "CR0031", "Whey Powder")
    await _lot(db_session, "CR0031", "LOT-BBB", supplier_batch="OTHER")

    headers = {AUTH: f"Bearer {admin_token}"}
    for needle, expected in [
        ("CR0025", "LOT-AAA"),       # material code
        ("lactose", "LOT-AAA"),      # material name, case-insensitive
        ("LOT-BBB", "LOT-BBB"),      # lot number
        ("SUP-BATCH", "LOT-AAA"),    # supplier batch
    ]:
        r = await client.get("/api/v1/inventory/lots",
                             params={"search": needle}, headers=headers)
        assert r.status_code == 200, (needle, r.text)
        assert [i["lot_no"] for i in r.json()["items"]] == [expected], needle


@pytest.mark.anyio
async def test_lots_filters_are_applied_before_paging(client, db_session, admin_token):
    """The trap: filter after cutting the page and you get short pages plus a
    total that counts rows the caller never sees."""
    for i in range(5):
        await _lot(db_session, "CR0025", f"KEEP-{i}")
    for i in range(7):
        await _lot(db_session, "CR0031", f"DROP-{i}")

    r = await client.get("/api/v1/inventory/lots",
                         params={"material_code": "CR0025", "page": 1, "page_size": 3},
                         headers={AUTH: f"Bearer {admin_token}"})
    body = r.json()
    assert body["total"] == 5, "total must describe the filtered set, not the table"
    assert len(body["items"]) == 3, "a full page, not 3-of-12-minus-the-others"
    assert all(i["material_code"] == "CR0025" for i in body["items"])


@pytest.mark.anyio
async def test_lots_paging_does_not_repeat_a_row(client, db_session, admin_token):
    """Every lot of one material sorts equal under the default sort, and without
    a stable tiebreak Postgres is free to order them differently per query --
    so page 2 can repeat a row from page 1 and skip another entirely."""
    for i in range(10):
        await _lot(db_session, "CR0025", f"LOT-{i:02d}")

    headers = {AUTH: f"Bearer {admin_token}"}
    seen = []
    for page in (1, 2):
        r = await client.get("/api/v1/inventory/lots",
                             params={"page": page, "page_size": 5}, headers=headers)
        seen += [i["lot_no"] for i in r.json()["items"]]
    assert len(set(seen)) == 10, f"pages overlapped: {sorted(seen)}"


@pytest.mark.anyio
async def test_lots_carry_the_countdown_and_the_bucket(client, db_session, admin_token):
    """16 days is the number that decides whether somebody picks up the phone;
    the date alone makes every reader do the subtraction."""
    await _lot(db_session, "CR0025", "LOT-SOON", expiry=TODAY + timedelta(days=16))
    r = await client.get("/api/v1/inventory/lots",
                         headers={AUTH: f"Bearer {admin_token}"})
    item = r.json()["items"][0]
    assert item["days_to_expiry"] == 16
    assert item["aging_bucket"] == "under_30"
    assert r.json()["as_of"] == TODAY.isoformat()


@pytest.mark.anyio
async def test_lots_expiry_window_filter(client, db_session, admin_token):
    await _lot(db_session, "CR0025", "LOT-SOON", expiry=TODAY + timedelta(days=10))
    await _lot(db_session, "CR0025", "LOT-LATER", expiry=TODAY + timedelta(days=400))
    r = await client.get(
        "/api/v1/inventory/lots",
        params={"expiring_before": (TODAY + timedelta(days=30)).isoformat()},
        headers={AUTH: f"Bearer {admin_token}"})
    assert [i["lot_no"] for i in r.json()["items"]] == ["LOT-SOON"]


@pytest.mark.anyio
async def test_lots_reject_an_unknown_sort_column(client, db_session, admin_token):
    """`sort` reaches SQL. It is a whitelist, and the rejection is the proof."""
    r = await client.get("/api/v1/inventory/lots",
                         params={"sort": "qty; drop table wms_inventory_lots"},
                         headers={AUTH: f"Bearer {admin_token}"})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_raw_milk_lots_are_never_listed(client, db_session, admin_token):
    """Business rule 2026-08-17: class 0101 Raw Milk is not counted as stock.
    By CLASS, never by code prefix -- CR0059 Pasteurized Milk is 0101 while
    carrying an ordinary raw-material prefix."""
    await _material(db_session, "CR0059", "Pasteurized Milk", erp_class="0101")
    await _lot(db_session, "CR0059", "MILK-LOT")
    await _material(db_session, "CR0025", "Lactose")
    await _lot(db_session, "CR0025", "KEEP-LOT")

    r = await client.get("/api/v1/inventory/lots",
                         headers={AUTH: f"Bearer {admin_token}"})
    assert [i["lot_no"] for i in r.json()["items"]] == ["KEEP-LOT"]
    assert r.json()["total"] == 1


@pytest.mark.anyio
async def test_a_lot_whose_material_is_unknown_is_still_listed(client, db_session, admin_token):
    """The SQL trap: erp_class_code != '0101' is NULL, not true, for a material
    with no master row -- a plain comparison would make a real pallet disappear
    from a stock report."""
    await _lot(db_session, "CR-BRAND-NEW", "ORPHAN-LOT")
    r = await client.get("/api/v1/inventory/lots",
                         headers={AUTH: f"Bearer {admin_token}"})
    assert [i["lot_no"] for i in r.json()["items"]] == ["ORPHAN-LOT"]
    assert r.json()["items"][0]["material_name"] is None


# ── /inventory/aging ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_aging_returns_every_bucket_even_when_empty(client, db_session, admin_token):
    """A band missing from the payload reads as not-computed, not as empty."""
    await _lot(db_session, "CR0025", "LOT-1", expiry=TODAY + timedelta(days=5))
    r = await client.get("/api/v1/inventory/aging",
                         headers={AUTH: f"Bearer {admin_token}"})
    keys = [b["key"] for b in r.json()["buckets"]]
    assert keys == ["expired", "under_30", "30_to_60", "60_to_180", "over_180"]
    assert all("label" in b for b in r.json()["buckets"])


@pytest.mark.anyio
async def test_aging_counts_expired_separately_from_nearly_expired(
    client, db_session, admin_token,
):
    """92 raw-material lots are already past date. A countdown that stops at
    zero never shows them."""
    await _lot(db_session, "CR0025", "GONE", "100", expiry=TODAY - timedelta(days=5))
    await _lot(db_session, "CR0025", "TODAY", "10", expiry=TODAY)
    await _lot(db_session, "CR0025", "SOON", "7", expiry=TODAY + timedelta(days=3))

    r = await client.get("/api/v1/inventory/aging",
                         headers={AUTH: f"Bearer {admin_token}"})
    buckets = {b["key"]: b for b in r.json()["buckets"]}
    assert buckets["expired"]["lots"] == 2, "a lot expiring today is expired"
    assert Decimal(buckets["expired"]["qty"]) == Decimal("110")
    assert buckets["under_30"]["lots"] == 1


@pytest.mark.anyio
async def test_aging_reports_lots_without_an_expiry_date_rather_than_dropping_them(
    client, db_session, admin_token,
):
    """1,640 packaging lots have no expiry because packaging does not expire.
    "Excluded, and here is how many" must not look like "there are none"."""
    await _material(db_session, "CP0133", "Can 400g", erp_class="02")
    await _lot(db_session, "CP0133", "CAN-LOT", "5000", expiry=None)
    await _lot(db_session, "CR0025", "REAL", "1", expiry=TODAY - timedelta(days=1))

    body = (await client.get("/api/v1/inventory/aging",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert body["no_expiry_lots"] == 1
    assert Decimal(body["no_expiry_qty"]) == Decimal("5000")
    assert sum(b["lots"] for b in body["buckets"]) == 1


@pytest.mark.anyio
async def test_aging_can_be_restricted_to_one_material_class(client, db_session, admin_token):
    await _material(db_session, "CR0025", "Lactose", erp_class="0102")
    await _lot(db_session, "CR0025", "RAW", expiry=TODAY - timedelta(days=1))
    await _material(db_session, "CF0086", "Formula", erp_class="05")
    await _lot(db_session, "CF0086", "FIN", expiry=TODAY - timedelta(days=1))

    body = (await client.get("/api/v1/inventory/aging",
                             params={"erp_class_code": "0102"},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert sum(b["lots"] for b in body["buckets"]) == 1
    assert body["erp_class_code"] == "0102"


# ── /inventory/materials ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_materials_combines_stock_with_what_is_on_order(client, db_session, admin_token):
    await _material(db_session, "CR0025", "Lactose")
    await _lot(db_session, "CR0025", "L1", "300")
    await _open_po(db_session, "CR0025", "1000", "400", arrival=date(2026, 9, 1))

    body = (await client.get("/api/v1/inventory/materials",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    row = next(i for i in body["items"] if i["material_code"] == "CR0025")
    assert Decimal(row["on_hand"]) == Decimal("300")
    assert Decimal(row["in_transit"]) == Decimal("600")
    assert row["earliest_arrival"] == "2026-09-01"
    assert row["open_po_lines"] == 1
    assert row["material_name"] == "Lactose"


@pytest.mark.anyio
async def test_materials_shows_something_that_is_only_on_order(client, db_session, admin_token):
    """Nothing in the warehouse, a delivery coming -- exactly the row a planner
    is looking for, and the one a join from the stock side would lose."""
    await _material(db_session, "CR0099", "Arriving Soon")
    await _open_po(db_session, "CR0099", "500", "0")

    body = (await client.get("/api/v1/inventory/materials",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    row = next(i for i in body["items"] if i["material_code"] == "CR0099")
    assert Decimal(row["on_hand"]) == Decimal("0")
    assert Decimal(row["in_transit"]) == Decimal("500")
    assert row["lots"] == 0


@pytest.mark.anyio
async def test_materials_in_transit_is_zero_not_null_when_nothing_is_on_order(
    client, db_session, admin_token,
):
    """"Nothing is on order" is a fact. A null renders as an empty cell, which
    reads as "unknown"."""
    await _material(db_session, "CR0025", "Lactose")
    await _lot(db_session, "CR0025", "L1", "300")

    body = (await client.get("/api/v1/inventory/materials",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    row = next(i for i in body["items"] if i["material_code"] == "CR0025")
    assert Decimal(row["in_transit"]) == Decimal("0")
    assert row["earliest_arrival"] is None
    assert row["open_po_lines"] == 0


@pytest.mark.anyio
async def test_materials_available_excludes_held_and_expired_lots(
    client, db_session, admin_token,
):
    """available = qty - qty_onhold over lots that are actually available --
    the Phase 1 definition, so this screen and the planning engine cannot
    disagree about how much can be used."""
    await _material(db_session, "CR0025", "Lactose")
    await _lot(db_session, "CR0025", "OK", "100", onhold="10", status="available")
    await _lot(db_session, "CR0025", "HELD", "50", status="hold")
    await _lot(db_session, "CR0025", "OLD", "20", status="expired")

    body = (await client.get("/api/v1/inventory/materials",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    row = next(i for i in body["items"] if i["material_code"] == "CR0025")
    assert Decimal(row["on_hand"]) == Decimal("170")
    assert Decimal(row["available"]) == Decimal("90")
    assert Decimal(row["expired_qty"]) == Decimal("20")


@pytest.mark.anyio
async def test_materials_never_lists_raw_milk(client, db_session, admin_token):
    await _material(db_session, "CR0180", "Lactalis-Raw Cows Milk", erp_class="0101")
    await _lot(db_session, "CR0180", "MILK")
    await _open_po(db_session, "CR0180", "1000", "0")

    body = (await client.get("/api/v1/inventory/materials",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert [i["material_code"] for i in body["items"]] == []


@pytest.mark.anyio
async def test_open_po_lines_drill_down_matches_the_figure(client, db_session, admin_token):
    """A planner opening a 600 kg number must not find 2,600 kg of rows."""
    await _material(db_session, "CR0025", "Lactose")
    await _open_po(db_session, "CR0025", "1000", "400", arrival=date(2026, 9, 1))
    await _open_po(db_session, "CR0025", "999", "0", status="approved")   # not placed

    lines = (await client.get("/api/v1/inventory/materials/CR0025/open-po-lines",
                              headers={AUTH: f"Bearer {admin_token}"})).json()
    assert len(lines) == 1
    assert Decimal(lines[0]["remaining"]) == Decimal("600")
    assert lines[0]["expected_arrival"] == "2026-09-01"
    assert lines[0]["arrival_is_from_header"] is False


@pytest.mark.anyio
async def test_lots_can_be_filtered_to_one_aging_band(client, db_session, admin_token):
    """★ The band is sent by NAME and resolved server-side from the same
    thresholds the summary counts with.

    The alternative -- the caller rebuilding the date window from the day
    counts -- was off by one at three of the five boundaries when it was
    written that way, and the symptom is silent: a card says 92, the table
    under it shows 91, and nothing reports an error. The three lots below sit
    exactly on the boundaries that were wrong.
    """
    await _lot(db_session, "CR0025", "TODAY", expiry=TODAY)                      # expired
    await _lot(db_session, "CR0025", "DAY-29", expiry=TODAY + timedelta(days=29))  # under_30
    await _lot(db_session, "CR0025", "DAY-30", expiry=TODAY + timedelta(days=30))  # 30_to_60
    await _lot(db_session, "CR0025", "DAY-60", expiry=TODAY + timedelta(days=60))  # 60_to_180
    await _lot(db_session, "CR0025", "DAY-180", expiry=TODAY + timedelta(days=180))  # over_180
    await _lot(db_session, "CR0025", "NO-DATE", expiry=None)                     # no band

    headers = {AUTH: f"Bearer {admin_token}"}
    for band, expected in [
        ("expired", ["TODAY"]),
        ("under_30", ["DAY-29"]),
        ("30_to_60", ["DAY-30"]),
        ("60_to_180", ["DAY-60"]),
        ("over_180", ["DAY-180"]),
    ]:
        r = await client.get("/api/v1/inventory/lots",
                             params={"aging_bucket": band}, headers=headers)
        assert r.status_code == 200, (band, r.text)
        assert [i["lot_no"] for i in r.json()["items"]] == expected, band


@pytest.mark.anyio
async def test_every_lot_appears_in_exactly_one_band(client, db_session, admin_token):
    """The bands must tile: summing the five filtered totals has to equal the
    number of lots that have an expiry date. A gap loses lots silently and an
    overlap counts them twice, and both look fine one band at a time."""
    for days in (-400, -1, 0, 1, 15, 29, 30, 45, 59, 60, 100, 179, 180, 500):
        await _lot(db_session, "CR0025", f"D{days}", expiry=TODAY + timedelta(days=days))
    await _lot(db_session, "CR0025", "NONE", expiry=None)

    headers = {AUTH: f"Bearer {admin_token}"}
    counted = 0
    for band in ("expired", "under_30", "30_to_60", "60_to_180", "over_180"):
        r = await client.get("/api/v1/inventory/lots",
                             params={"aging_bucket": band, "page_size": 100},
                             headers=headers)
        counted += r.json()["total"]
    assert counted == 14, "14 dated lots must appear in exactly one band each"


@pytest.mark.anyio
async def test_an_unknown_aging_band_is_rejected(client, db_session, admin_token):
    """Not silently ignored -- that would return every lot while the screen
    believes it is showing one band."""
    r = await client.get("/api/v1/inventory/lots",
                         params={"aging_bucket": "under_45"},
                         headers={AUTH: f"Bearer {admin_token}"})
    assert r.status_code == 422
