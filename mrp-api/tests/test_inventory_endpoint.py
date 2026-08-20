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
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: [])
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
               onhold="0", allocated="0", supplier_batch=None, warehouse="CANADA",
               production=None, wms_status="02", uom="KG"):
    db.add(WmsInventoryLot(
        warehouse_id=warehouse, material_code=code, lot_no=lot_no, uom=uom,
        qty=Decimal(qty), qty_allocated=Decimal(allocated), qty_onhold=Decimal(onhold),
        wms_status=wms_status, mapped_status=status, expiry_date=expiry,
        production_date=production,
        supplier_batch=supplier_batch, sync_batch_id="test",
    ))
    await db.commit()


async def _status_mapping(db, wms_code, mapped_status, description):
    """The warehouse's QLT_STS dictionary, which turns '02' into 'Release'.

    Upserts rather than inserts: 01/02/04 are SEEDED BY A MIGRATION, so a plain
    insert collides. Tests state the mapping they rely on anyway, so that
    reading one does not require knowing what the migration happened to seed.
    """
    import sqlalchemy as sa

    from app.models.status_mapping import MrpStatusMapping

    existing = (await db.execute(sa.select(MrpStatusMapping).where(
        MrpStatusMapping.wms_code == wms_code))).scalar_one_or_none()
    if existing is None:
        db.add(MrpStatusMapping(wms_code=wms_code, mapped_status=mapped_status,
                                description=description))
    else:
        existing.mapped_status = mapped_status
        existing.description = description
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
    assert keys == ["expired", "under_30", "30_to_60", "60_to_90", "90_to_180",
                    "over_180"]
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
    await _lot(db_session, "CR0025", "DAY-60", expiry=TODAY + timedelta(days=60))  # 60_to_90
    await _lot(db_session, "CR0025", "DAY-90", expiry=TODAY + timedelta(days=90))  # 90_to_180
    await _lot(db_session, "CR0025", "DAY-180", expiry=TODAY + timedelta(days=180))  # over_180
    await _lot(db_session, "CR0025", "NO-DATE", expiry=None)                     # no band

    headers = {AUTH: f"Bearer {admin_token}"}
    for band, expected in [
        ("expired", ["TODAY"]),
        ("under_30", ["DAY-29"]),
        ("30_to_60", ["DAY-30"]),
        ("60_to_90", ["DAY-60"]),
        ("90_to_180", ["DAY-90"]),
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
    for band in ("expired", "under_30", "30_to_60", "60_to_90", "90_to_180",
                 "over_180"):
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


# ── /inventory/batches — grouped by supplier batch ───────────────────────
#
# The list the screen shows. `lot_no` is Flux's internal identifier and is
# never displayed; the supplier batch is the unit the plant works in.


@pytest.mark.anyio
async def test_batches_group_many_lots_into_one_row(client, db_session, admin_token):
    """★ Why this endpoint exists. CP0080's "Old Wooden Racking Pallet" is one
    supplier batch spread over 192 WMS lots; 2,143 of the plant's 3,532 lots
    are visually identical to another lot once the internal number is removed.
    Hiding the column without grouping gives page after page of repeated rows.
    """
    await _material(db_session, "CP0080", "Wooden Pallet", erp_class="02")
    for i in range(5):
        await _lot(db_session, "CP0080", f"WMS-{i}", "10", supplier_batch="OLD PALLET")

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert body["total"] == 1, "five lots of one supplier batch are one row"
    assert body["total_lots"] == 5, "and the lot count is still reported"
    row = body["items"][0]
    assert row["supplier_batch"] == "OLD PALLET"
    assert row["lots"] == 5
    assert Decimal(row["qty"]) == Decimal("50")
    assert "lot_no" not in row, "the internal WMS lot must not be exposed here"


@pytest.mark.anyio
async def test_the_same_supplier_batch_on_two_materials_stays_two_rows(
    client, db_session, admin_token,
):
    """Supplier batch numbers belong to the supplier, not to us, and two
    products can share one. Identity is the material AND the batch."""
    await _material(db_session, "CR0025", "Lactose")
    await _material(db_session, "CR0031", "Whey")
    await _lot(db_session, "CR0025", "L1", supplier_batch="SAME-42")
    await _lot(db_session, "CR0031", "L2", supplier_batch="SAME-42")

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert body["total"] == 2


@pytest.mark.anyio
async def test_a_batch_with_no_supplier_batch_is_its_own_row_per_material(
    client, db_session, admin_token,
):
    """92 lots carry no supplier batch. They group per material rather than
    collapsing into one meaningless plant-wide row."""
    await _lot(db_session, "CR0025", "L1", "10", supplier_batch=None)
    await _lot(db_session, "CR0025", "L2", "20", supplier_batch=None)
    await _lot(db_session, "CR0031", "L3", "5", supplier_batch=None)

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert body["total"] == 2
    row = next(i for i in body["items"] if i["material_code"] == "CR0025")
    assert row["supplier_batch"] is None
    assert row["lots"] == 2
    assert Decimal(row["qty"]) == Decimal("30")


@pytest.mark.anyio
async def test_batch_expiry_is_the_earliest_and_says_when_it_spans_dates(
    client, db_session, admin_token,
):
    """42 of the plant's 877 batches carry more than one expiry date. The row
    shows the EARLIEST -- when the batch starts going out of date, the date
    somebody has to act on -- and flags that it is not the whole story, rather
    than quietly describing only part of the quantity."""
    await _lot(db_session, "CR0025", "L1", "10",
               expiry=TODAY + timedelta(days=10), supplier_batch="SB-1")
    await _lot(db_session, "CR0025", "L2", "90",
               expiry=TODAY + timedelta(days=300), supplier_batch="SB-1")

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    row = body["items"][0]
    assert row["expiry_date"] == (TODAY + timedelta(days=10)).isoformat()
    assert row["expiry_spans_dates"] is True
    assert row["days_to_expiry"] == 10
    assert row["aging_bucket"] == "under_30"


@pytest.mark.anyio
async def test_a_batch_whose_lots_disagree_on_status_says_mixed(
    client, db_session, admin_token,
):
    """27 batches have lots in different states. Picking the first would hide
    that part of the batch is on hold."""
    await _lot(db_session, "CR0025", "L1", "10", supplier_batch="SB-1", status="available")
    await _lot(db_session, "CR0025", "L2", "10", supplier_batch="SB-1", status="hold")
    await _lot(db_session, "CR0031", "L3", "10", supplier_batch="SB-2", status="available")

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    mixed = next(i for i in body["items"] if i["material_code"] == "CR0025")
    clean = next(i for i in body["items"] if i["material_code"] == "CR0031")
    assert mixed["mapped_status"] == "mixed"
    assert clean["mapped_status"] == "available"


@pytest.mark.anyio
async def test_batches_are_filtered_before_grouping_not_after(
    client, db_session, admin_token,
):
    """A batch straddling two bands genuinely has stock in each, and each band
    must report only the quantity that is actually in it. Filtering after
    grouping would put the whole 100 in whichever band the batch was assigned."""
    await _lot(db_session, "CR0025", "L1", "10",
               expiry=TODAY - timedelta(days=1), supplier_batch="SB-1")
    await _lot(db_session, "CR0025", "L2", "90",
               expiry=TODAY + timedelta(days=300), supplier_batch="SB-1")

    headers = {AUTH: f"Bearer {admin_token}"}
    expired = (await client.get("/api/v1/inventory/batches",
                                params={"aging_bucket": "expired"}, headers=headers)).json()
    later = (await client.get("/api/v1/inventory/batches",
                              params={"aging_bucket": "over_180"}, headers=headers)).json()
    assert Decimal(expired["items"][0]["qty"]) == Decimal("10")
    assert expired["items"][0]["lots"] == 1
    assert Decimal(later["items"][0]["qty"]) == Decimal("90")


@pytest.mark.anyio
async def test_batches_search_still_matches_the_internal_lot_number(
    client, db_session, admin_token,
):
    """The WMS lot is not displayed, but somebody reading a Flux screen may
    still paste one. Matching it costs nothing and finding nothing would be
    worse."""
    await _lot(db_session, "CR0025", "HGC1995762", supplier_batch="SB-1")
    body = (await client.get("/api/v1/inventory/batches",
                             params={"search": "HGC1995762"},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert body["total"] == 1
    assert body["items"][0]["supplier_batch"] == "SB-1"


@pytest.mark.anyio
async def test_batches_total_counts_batches_not_lots(client, db_session, admin_token):
    """The trap in grouping: count(*) over an ungrouped query counts lots, and
    the pager would then offer pages that do not exist."""
    for i in range(7):
        await _lot(db_session, "CR0025", f"L{i}", supplier_batch="ONE")

    body = (await client.get("/api/v1/inventory/batches",
                             params={"page_size": 5},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert body["total"] == 1
    assert body["total_lots"] == 7
    assert len(body["items"]) == 1


@pytest.mark.anyio
async def test_batches_paging_does_not_repeat_a_row(client, db_session, admin_token):
    for i in range(10):
        await _lot(db_session, "CR0025", f"L{i}", supplier_batch=f"SB-{i:02d}")

    headers = {AUTH: f"Bearer {admin_token}"}
    seen = []
    for page in (1, 2):
        r = await client.get("/api/v1/inventory/batches",
                             params={"page": page, "page_size": 5}, headers=headers)
        seen += [i["supplier_batch"] for i in r.json()["items"]]
    assert len(set(seen)) == 10, f"pages overlapped: {sorted(seen)}"


@pytest.mark.anyio
async def test_raw_milk_batches_are_never_listed(client, db_session, admin_token):
    await _material(db_session, "CR0059", "Pasteurized Milk", erp_class="0101")
    await _lot(db_session, "CR0059", "L1", supplier_batch="MILK-1")
    await _material(db_session, "CR0025", "Lactose")
    await _lot(db_session, "CR0025", "L2", supplier_batch="KEEP")

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert [i["supplier_batch"] for i in body["items"]] == ["KEEP"]


@pytest.mark.anyio
async def test_aging_cards_count_batches_so_they_match_the_table(
    client, db_session, admin_token,
):
    """★ The card and the rows beneath it must be the same unit. Three lots of
    one supplier batch are ONE row, and a card reading 3 above a table of 1 is
    a discrepancy nobody can explain and nothing reports."""
    for i in range(3):
        await _lot(db_session, "CR0025", f"L{i}", "10",
                   expiry=TODAY - timedelta(days=1), supplier_batch="SB-1")

    headers = {AUTH: f"Bearer {admin_token}"}
    aging = (await client.get("/api/v1/inventory/aging", headers=headers)).json()
    expired = next(b for b in aging["buckets"] if b["key"] == "expired")
    listed = (await client.get("/api/v1/inventory/batches",
                               params={"aging_bucket": "expired"}, headers=headers)).json()

    assert expired["lots"] == 3
    assert expired["batches"] == 1
    assert expired["batches"] == listed["total"], "card and table must agree"


@pytest.mark.anyio
async def test_aging_reports_batches_without_an_expiry_date_too(
    client, db_session, admin_token,
):
    await _material(db_session, "CP0133", "Can 400g", erp_class="02")
    await _lot(db_session, "CP0133", "L1", "10", expiry=None, supplier_batch="PALLET")
    await _lot(db_session, "CP0133", "L2", "10", expiry=None, supplier_batch="PALLET")

    body = (await client.get("/api/v1/inventory/aging",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert body["no_expiry_lots"] == 2
    assert body["no_expiry_batches"] == 1


# ── production date, quality status, UOM (2026-08-18) ────────────────────


@pytest.mark.anyio
async def test_the_unit_comes_from_the_warehouse_not_from_the_erp(
    client, db_session, admin_token,
):
    """★ Reported 2026-08-18: S0093 showed as PIECES because that is the ERP's
    unit, while the warehouse holds it in KG. Every quantity on this screen is
    the warehouse's, so the unit has to be the warehouse's too — the ERP counts
    finished goods in the units they are SOLD in and the warehouse weighs what
    it STORES. 237 lots across 16 materials disagreed exactly this way.
    """
    await _material(db_session, "S0093", "Infant Formula FG", uom="PIECES")
    await _lot(db_session, "S0093", "HGC1993989", "2.8",
               supplier_batch="25E347125110391", uom="KG")

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    row = body["items"][0]
    assert row["base_uom"] == "KG", "the ERP's PIECES must not reach this screen"
    assert Decimal(row["qty"]) == Decimal("2.8")


@pytest.mark.anyio
async def test_quantities_are_not_rounded_on_the_wire(client, db_session, admin_token):
    """The other half of the same report: 2.8 was rendered as "3". The API must
    hand over what the warehouse holds, to the stored precision."""
    await _lot(db_session, "S0093", "L1", "2.8", supplier_batch="SB-1")
    row = (await client.get("/api/v1/inventory/batches",
                            headers={AUTH: f"Bearer {admin_token}"})).json()["items"][0]
    assert Decimal(row["qty"]) == Decimal("2.8")


@pytest.mark.anyio
async def test_a_batch_whose_lots_disagree_on_unit_says_mixed(
    client, db_session, admin_token,
):
    """Rather than picking one and mislabelling half the quantity."""
    await _lot(db_session, "CR0025", "L1", "10", supplier_batch="SB-1", uom="KG")
    await _lot(db_session, "CR0025", "L2", "10", supplier_batch="SB-1", uom="PIECES")

    row = (await client.get("/api/v1/inventory/batches",
                            headers={AUTH: f"Bearer {admin_token}"})).json()["items"][0]
    assert row["base_uom"] == "mixed"


@pytest.mark.anyio
async def test_the_summary_groups_by_the_warehouses_unit(client, db_session, admin_token):
    """The block sums per unit, and it must be the same unit the rows show —
    grouping by the ERP's would put KG stock under a PIECES heading."""
    await _material(db_session, "S0093", "Infant Formula FG", uom="PIECES")
    await _lot(db_session, "S0093", "L1", "2.8", supplier_batch="A", uom="KG")

    summary = (await client.get("/api/v1/inventory/batches",
                                headers={AUTH: f"Bearer {admin_token}"})).json()["summary"]
    assert [x["uom"] for x in summary] == ["KG"]
    assert Decimal(summary[0]["total_qty"]) == Decimal("2.8")


@pytest.mark.anyio
async def test_batch_production_date_is_the_earliest_and_flags_a_span(
    client, db_session, admin_token,
):
    """29 of the plant's 877 batches carry more than one production date."""
    await _lot(db_session, "CR0025", "L1", supplier_batch="SB-1",
               production=date(2026, 3, 1))
    await _lot(db_session, "CR0025", "L2", supplier_batch="SB-1",
               production=date(2026, 3, 5))
    await _lot(db_session, "CR0031", "L3", supplier_batch="SB-2",
               production=date(2026, 4, 1))

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    spanning = next(i for i in body["items"] if i["material_code"] == "CR0025")
    single = next(i for i in body["items"] if i["material_code"] == "CR0031")
    assert spanning["production_date"] == "2026-03-01"
    assert spanning["production_spans_dates"] is True
    assert single["production_date"] == "2026-04-01"
    assert single["production_spans_dates"] is False


@pytest.mark.anyio
async def test_quality_status_is_the_warehouses_own_and_is_labelled(
    client, db_session, admin_token,
):
    """★ Flux's QLT_STS is NOT our mapped_status. 278 lots are '02 Release' in
    the warehouse and expired by date; a screen showing only the derived status
    cannot say whether QA blocked something or the calendar did.

    The label comes from mrp_status_mapping, so the screen reads "Release"
    rather than "02".
    """
    await _status_mapping(db_session, "02", "available", "Release")
    await _status_mapping(db_session, "01", "hold", "Block")
    await _lot(db_session, "CR0025", "L1", supplier_batch="OK",
               wms_status="02", status="available")
    await _lot(db_session, "CR0031", "L2", supplier_batch="BLOCKED",
               wms_status="01", status="hold")

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    ok = next(i for i in body["items"] if i["supplier_batch"] == "OK")
    blocked = next(i for i in body["items"] if i["supplier_batch"] == "BLOCKED")
    assert ok["quality_status"] == "02"
    assert ok["quality_status_label"] == "Release"
    assert blocked["quality_status_label"] == "Block"


@pytest.mark.anyio
async def test_released_but_expired_reports_both_states(client, db_session, admin_token):
    """The case that makes two columns necessary rather than one: the warehouse
    still says Release, the calendar says expired, and both are true."""
    await _status_mapping(db_session, "02", "available", "Release")
    await _lot(db_session, "CR0025", "L1", supplier_batch="SB-1",
               wms_status="02", status="expired", expiry=TODAY - timedelta(days=30))

    row = (await client.get("/api/v1/inventory/batches",
                            headers={AUTH: f"Bearer {admin_token}"})).json()["items"][0]
    assert row["quality_status_label"] == "Release"
    assert row["mapped_status"] == "expired"


@pytest.mark.anyio
async def test_a_batch_whose_lots_disagree_on_quality_says_mixed(
    client, db_session, admin_token,
):
    """29 batches have lots in different QA states. Showing whichever sorted
    first would hide that part of the batch is blocked."""
    await _status_mapping(db_session, "02", "available", "Release")
    await _status_mapping(db_session, "01", "hold", "Block")
    await _lot(db_session, "CR0025", "L1", supplier_batch="SB-1", wms_status="02")
    await _lot(db_session, "CR0025", "L2", supplier_batch="SB-1", wms_status="01")

    row = (await client.get("/api/v1/inventory/batches",
                            headers={AUTH: f"Bearer {admin_token}"})).json()["items"][0]
    assert row["quality_status"] == "mixed"
    assert row["quality_status_label"] == "Mixed"


@pytest.mark.anyio
async def test_an_unmapped_quality_code_shows_the_code_not_a_blank(
    client, db_session, admin_token,
):
    """A status nobody can read still beats a status nobody can see: if the
    warehouse adds a QLT_STS value we have not mapped, the cell shows the raw
    code rather than going empty and reading as "no status"."""
    await _lot(db_session, "CR0025", "L1", supplier_batch="SB-1", wms_status="09")
    row = (await client.get("/api/v1/inventory/batches",
                            headers={AUTH: f"Bearer {admin_token}"})).json()["items"][0]
    assert row["quality_status"] == "09"
    assert row["quality_status_label"] == "09"


@pytest.mark.anyio
async def test_batches_can_be_sorted_by_production_date(client, db_session, admin_token):
    """Oldest-produced-first is how stock gets consumed in production order."""
    await _lot(db_session, "CR0025", "L1", supplier_batch="NEW",
               production=date(2026, 6, 1))
    await _lot(db_session, "CR0025", "L2", supplier_batch="OLD",
               production=date(2024, 1, 1))

    body = (await client.get("/api/v1/inventory/batches",
                             params={"sort": "production_date"},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert [i["supplier_batch"] for i in body["items"]] == ["OLD", "NEW"]


# ── /inventory/batches/locations — where a batch physically sits ─────────

from app.models.wms_lot_location import WmsLotLocation


async def _location(db, code, lot_no, location_id, qty="100", *,
                    trace="T1", zone="LIHG", warehouse="CANADA"):
    db.add(WmsLotLocation(
        warehouse_id=warehouse, material_code=code, lot_no=lot_no,
        location_id=location_id, trace_id=trace, zone_id=zone,
        qty=Decimal(qty), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
        sync_batch_id="test",
    ))
    await db.commit()


@pytest.mark.anyio
async def test_locations_of_a_batch_sum_to_the_batch(client, db_session, admin_token):
    """★ The number on the row and the rows behind it must agree. Verified
    against the live warehouse too: CR0025's batch 11/27/2025 is 28,978.43 kg
    over 30 locations and the locations sum to 28,978.4300 exactly."""
    await _lot(db_session, "CR0025", "L1", "600", supplier_batch="SB-1")
    await _lot(db_session, "CR0025", "L2", "400", supplier_batch="SB-1")
    await _location(db_session, "CR0025", "L1", "11030511", "600")
    await _location(db_session, "CR0025", "L2", "11030512", "400")

    headers = {AUTH: f"Bearer {admin_token}"}
    batch = (await client.get("/api/v1/inventory/batches",
                              headers=headers)).json()["items"][0]
    locations = (await client.get("/api/v1/inventory/batches/locations",
                                  params={"material_code": "CR0025",
                                          "supplier_batch": "SB-1"},
                                  headers=headers)).json()
    assert len(locations) == 2
    assert (sum(Decimal(x["qty"]) for x in locations) == Decimal(batch["qty"]))


@pytest.mark.anyio
async def test_one_lot_can_sit_in_several_locations(client, db_session, admin_token):
    """25 lots in the live warehouse do, and one packaging lot is spread over
    28. This is why location could not be a column on the lot."""
    await _lot(db_session, "CR0025", "L1", "975", supplier_batch="SB-1")
    await _location(db_session, "CR0025", "L1", "11010345", "950")
    await _location(db_session, "CR0025", "L1", "DM01", "25", trace="*", zone="WOD")

    rows = (await client.get("/api/v1/inventory/batches/locations",
                             params={"material_code": "CR0025", "supplier_batch": "SB-1"},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert [r["location_id"] for r in rows] == ["11010345", "DM01"], "fullest first"
    assert sum(Decimal(r["qty"]) for r in rows) == Decimal("975")


@pytest.mark.anyio
async def test_a_star_trace_id_is_reported_as_nothing(client, db_session, admin_token):
    """The source writes '*' for "no handling unit" on 122 rows. Passing that
    through would print a character nobody can interpret."""
    await _lot(db_session, "CR0025", "L1", supplier_batch="SB-1")
    await _location(db_session, "CR0025", "L1", "DM01", trace="*")

    row = (await client.get("/api/v1/inventory/batches/locations",
                            params={"material_code": "CR0025", "supplier_batch": "SB-1"},
                            headers={AUTH: f"Bearer {admin_token}"})).json()[0]
    assert row["trace_id"] is None


@pytest.mark.anyio
async def test_locations_of_the_batch_with_no_supplier_batch(client, db_session, admin_token):
    """★ `supplier_batch IS NULL`, not `= NULL`. SQL equality against NULL is
    never true, so the batch of stock carrying no supplier batch would return an
    empty expander — which reads as "we do not know where this is" rather than
    "you asked the wrong way"."""
    await _lot(db_session, "CR0025", "L1", "50", supplier_batch=None)
    await _location(db_session, "CR0025", "L1", "03030406", "50")

    rows = (await client.get("/api/v1/inventory/batches/locations",
                             params={"material_code": "CR0025"},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert [r["location_id"] for r in rows] == ["03030406"]


@pytest.mark.anyio
async def test_location_rows_carry_the_lots_own_dates(client, db_session, admin_token):
    """Received and produced live here, not on the batch row: a batch's lots
    arrive on different days and one date up top would describe only part of
    the quantity. Down here each row belongs to exactly one lot."""
    await _lot(db_session, "CR0025", "L1", supplier_batch="SB-1",
               production=date(2026, 4, 2), expiry=TODAY + timedelta(days=100))
    await _location(db_session, "CR0025", "L1", "11010344", zone="LIHG")

    row = (await client.get("/api/v1/inventory/batches/locations",
                            params={"material_code": "CR0025", "supplier_batch": "SB-1"},
                            headers={AUTH: f"Bearer {admin_token}"})).json()[0]
    assert row["production_date"] == "2026-04-02"
    assert row["zone_id"] == "LIHG"
    assert row["days_to_expiry"] == 100


@pytest.mark.anyio
async def test_a_batch_with_no_location_rows_is_empty_not_an_error(
    client, db_session, admin_token,
):
    """Distinct from a failure and from zero stock: the batch exists, the
    warehouse simply has not said where it is."""
    await _lot(db_session, "CR0025", "L1", supplier_batch="SB-1")
    rows = (await client.get("/api/v1/inventory/batches/locations",
                             params={"material_code": "CR0025", "supplier_batch": "SB-1"},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert rows == []


@pytest.mark.anyio
async def test_inbound_date_is_no_longer_on_the_batch_row(client, db_session, admin_token):
    """It moved into the expander on purpose — see the test above."""
    await _lot(db_session, "CR0025", "L1", supplier_batch="SB-1")
    row = (await client.get("/api/v1/inventory/batches",
                            headers={AUTH: f"Bearer {admin_token}"})).json()["items"][0]
    assert "inbound_date" in row, "still returned for anyone who wants it"
    assert "lot_no" not in row, "the internal lot number stays out of the list"


# ── the summary strip (2026-08-18) ───────────────────────────────────────


@pytest.mark.anyio
async def test_summary_totals_the_filtered_set_not_the_page(
    client, db_session, admin_token,
):
    """It summarises what the filters match, over every row — not the 25 on
    screen. A dashboard that silently described one page would be wrong by
    exactly the amount nobody can see."""
    await _material(db_session, "CR0025", "Lactose", uom="KGM")
    for i in range(30):
        await _lot(db_session, "CR0025", f"L{i}", "100", supplier_batch=f"SB-{i:02d}")

    body = (await client.get("/api/v1/inventory/batches",
                             params={"page_size": 5},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert len(body["items"]) == 5
    line = body["summary"][0]
    assert Decimal(line["total_qty"]) == Decimal("3000")
    assert line["batches"] == 30
    assert line["lots"] == 30


@pytest.mark.anyio
async def test_summary_follows_the_search(client, db_session, admin_token):
    """Scoped to the search results, as asked: the block answers "what am I
    looking at", not "what is in the warehouse"."""
    await _material(db_session, "CR0025", "Lactose")
    await _material(db_session, "CR0031", "Whey Powder")
    await _lot(db_session, "CR0025", "L1", "100", supplier_batch="A")
    await _lot(db_session, "CR0031", "L2", "900", supplier_batch="B")

    body = (await client.get("/api/v1/inventory/batches",
                             params={"search": "lactose"},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert Decimal(body["summary"][0]["total_qty"]) == Decimal("100")


@pytest.mark.anyio
async def test_summary_is_one_line_per_unit_and_never_adds_them_up(
    client, db_session, admin_token,
):
    """★ Packaging spans five units in the real warehouse (EA, KGM, PIECES,
    ROLL, cPs). One combined number would describe nothing at all."""
    await _lot(db_session, "CP0133", "L1", "1000", supplier_batch="A", uom="PIECES")
    await _lot(db_session, "CP0140", "L2", "25", supplier_batch="B", uom="KG")

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    by_uom = {s["uom"]: s for s in body["summary"]}
    assert set(by_uom) == {"PIECES", "KG"}
    assert Decimal(by_uom["PIECES"]["total_qty"]) == Decimal("1000")
    assert Decimal(by_uom["KG"]["total_qty"]) == Decimal("25")


@pytest.mark.anyio
async def test_summary_reports_stock_whose_material_has_no_unit(
    client, db_session, admin_token,
):
    """A lot the warehouse has no packaging row for gets no unit. Dropping it
    would make the total quietly smaller than the table below it."""
    await _lot(db_session, "MYSTERY", "L1", "42", supplier_batch="A", uom=None)
    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    line = body["summary"][0]
    assert line["uom"] is None
    assert Decimal(line["total_qty"]) == Decimal("42")


@pytest.mark.anyio
async def test_blocked_is_the_warehouses_block_not_our_hold(
    client, db_session, admin_token,
):
    """★ QLT_STS 01 (Block) only. Our derived `hold` also covers 04 Under
    Inspection, and counting that as blocked would report material as stopped
    when QA has merely not finished looking at it."""
    await _material(db_session, "CR0025", "Lactose")
    await _lot(db_session, "CR0025", "L1", "100", supplier_batch="A",
               wms_status="01", status="hold")
    await _lot(db_session, "CR0025", "L2", "500", supplier_batch="B",
               wms_status="04", status="hold")

    line = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()["summary"][0]
    assert Decimal(line["blocked_qty"]) == Decimal("100"), "04 is not blocked"
    assert Decimal(line["total_qty"]) == Decimal("600")


@pytest.mark.anyio
async def test_available_uses_the_same_definition_as_everywhere_else(
    client, db_session, admin_token,
):
    """qty - qty_onhold over lots that are actually available — so this block,
    the Materials tab and the planning engine cannot disagree about how much
    can be used."""
    await _material(db_session, "CR0025", "Lactose")
    await _lot(db_session, "CR0025", "OK", "100", onhold="10",
               status="available", supplier_batch="A")
    await _lot(db_session, "CR0025", "HELD", "50", status="hold", supplier_batch="B")

    line = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()["summary"][0]
    assert Decimal(line["available_qty"]) == Decimal("90")


@pytest.mark.anyio
async def test_expiring_soon_excludes_what_has_already_expired(
    client, db_session, admin_token,
):
    """A warning counts what you can still act on. Something 90 days gone is
    not "expiring soon", it is expired, and it has its own figure."""
    await _material(db_session, "CR0025", "Lactose")
    await _lot(db_session, "CR0025", "GONE", "100", supplier_batch="A",
               expiry=TODAY - timedelta(days=5), status="expired")
    await _lot(db_session, "CR0025", "SOON", "7", supplier_batch="B",
               expiry=TODAY + timedelta(days=40))
    await _lot(db_session, "CR0025", "LATER", "999", supplier_batch="C",
               expiry=TODAY + timedelta(days=200))

    line = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()["summary"][0]
    assert Decimal(line["expiring_soon_qty"]) == Decimal("7")
    assert line["expiring_soon_batches"] == 1
    assert Decimal(line["expired_qty"]) == Decimal("100")


@pytest.mark.anyio
async def test_the_expiring_tile_and_the_rows_it_filters_to_are_the_same_set(
    client, db_session, admin_token,
):
    """★ The one that matters. Clicking the tile filters with
    `expiring_after = as_of` and `expiring_before = as_of + N`, and the count on
    the tile came from `expiry > today AND expiry < today + N`. Both API bounds
    are exclusive, so the two must agree exactly — including at the boundaries,
    which is where every version of this bug has lived on this page.
    """
    await _material(db_session, "CR0025", "Lactose")
    for label, days in [("YESTERDAY", -1), ("TODAY", 0), ("TOMORROW", 1),
                        ("DAY-89", 89), ("DAY-90", 90), ("DAY-91", 91)]:
        await _lot(db_session, "CR0025", label, "10", supplier_batch=label,
                   expiry=TODAY + timedelta(days=days),
                   status="expired" if days <= 0 else "available")

    headers = {AUTH: f"Bearer {admin_token}"}
    body = (await client.get("/api/v1/inventory/batches", headers=headers)).json()
    line = body["summary"][0]
    horizon = body["expiry_warning_days"]
    assert horizon == 90

    filtered = (await client.get(
        "/api/v1/inventory/batches",
        params={"expiring_after": body["as_of"],
                "expiring_before": (TODAY + timedelta(days=horizon)).isoformat()},
        headers=headers)).json()

    assert line["expiring_soon_batches"] == filtered["total"], "tile and rows must agree"
    assert sorted(i["supplier_batch"] for i in filtered["items"]) == [
        "DAY-89", "TOMORROW"], "today is expired, day 90 is beyond the horizon"
    assert Decimal(line["expiring_soon_qty"]) == Decimal("20")


# ── byproducts filed as finished goods (2026-08-18) ──────────────────────
#
# CF00AF Animal Feed and CF00WT Waste Powder carry the ERP's finished-goods
# class and MES type, and CF00AF alone is 80,394 kg over 478 lots -- 45% of all
# finished-goods stock by quantity and 68% of its lots, more than every real
# product put together.


@pytest.mark.anyio
async def test_byproducts_are_excluded_from_batches_by_default(
    client, db_session, admin_token,
):
    """A planner looking at finished goods is looking at what can be shipped."""
    await _material(db_session, "CF00AF", "Animal Feed", erp_class="05")
    await _material(db_session, "CF00WT", "Waste Powder", erp_class="05")
    await _material(db_session, "S0093", "Infant Formula", erp_class="05")
    await _lot(db_session, "CF00AF", "L1", "80000", supplier_batch="FEED")
    await _lot(db_session, "CF00WT", "L2", "180", supplier_batch="WASTE")
    await _lot(db_session, "S0093", "L3", "3000", supplier_batch="REAL")

    headers = {AUTH: f"Bearer {admin_token}"}
    body = (await client.get("/api/v1/inventory/batches",
                             params={"erp_class_code": "05"}, headers=headers)).json()
    assert [i["material_code"] for i in body["items"]] == ["S0093"]
    assert Decimal(body["summary"][0]["total_qty"]) == Decimal("3000"), (
        "the summary must exclude them too, or the total describes the feed pile")


@pytest.mark.anyio
async def test_the_switch_brings_them_back(client, db_session, admin_token):
    """Excluded by default, never hidden without a way to show them."""
    await _material(db_session, "CF00AF", "Animal Feed", erp_class="05")
    await _material(db_session, "S0093", "Infant Formula", erp_class="05")
    await _lot(db_session, "CF00AF", "L1", "80000", supplier_batch="FEED")
    await _lot(db_session, "S0093", "L3", "3000", supplier_batch="REAL")

    body = (await client.get("/api/v1/inventory/batches",
                             params={"erp_class_code": "05", "include_byproducts": "true"},
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert sorted(i["material_code"] for i in body["items"]) == ["CF00AF", "S0093"]
    assert Decimal(body["summary"][0]["total_qty"]) == Decimal("83000")


@pytest.mark.anyio
async def test_byproducts_are_excluded_from_aging_too(client, db_session, admin_token):
    """★ Every tab, not just the list. 478 feed lots in the shelf-life bands
    would bury the products that actually need attention."""
    await _material(db_session, "CF00AF", "Animal Feed", erp_class="05")
    await _material(db_session, "S0093", "Infant Formula", erp_class="05")
    await _lot(db_session, "CF00AF", "L1", "80000", supplier_batch="FEED",
               expiry=TODAY - timedelta(days=5), status="expired")
    await _lot(db_session, "S0093", "L2", "3000", supplier_batch="REAL",
               expiry=TODAY - timedelta(days=5), status="expired")

    headers = {AUTH: f"Bearer {admin_token}"}
    off = (await client.get("/api/v1/inventory/aging",
                            params={"erp_class_code": "05"}, headers=headers)).json()
    on = (await client.get("/api/v1/inventory/aging",
                           params={"erp_class_code": "05", "include_byproducts": "true"},
                           headers=headers)).json()
    expired_off = next(b for b in off["buckets"] if b["key"] == "expired")
    expired_on = next(b for b in on["buckets"] if b["key"] == "expired")
    assert Decimal(expired_off["qty"]) == Decimal("3000")
    assert Decimal(expired_on["qty"]) == Decimal("83000")


@pytest.mark.anyio
async def test_byproducts_are_excluded_from_the_materials_tab_too(
    client, db_session, admin_token,
):
    await _material(db_session, "CF00AF", "Animal Feed", erp_class="05")
    await _material(db_session, "S0093", "Infant Formula", erp_class="05")
    await _lot(db_session, "CF00AF", "L1", "80000", supplier_batch="FEED")
    await _lot(db_session, "S0093", "L2", "3000", supplier_batch="REAL")

    headers = {AUTH: f"Bearer {admin_token}"}
    off = (await client.get("/api/v1/inventory/materials",
                            params={"erp_class_code": "05"}, headers=headers)).json()
    on = (await client.get("/api/v1/inventory/materials",
                           params={"erp_class_code": "05", "include_byproducts": "true"},
                           headers=headers)).json()
    assert [i["material_code"] for i in off["items"]] == ["S0093"]
    assert sorted(i["material_code"] for i in on["items"]) == ["CF00AF", "S0093"]


@pytest.mark.anyio
async def test_a_byproduct_on_order_cannot_slip_back_in(client, db_session, admin_token):
    """The Materials tab is a union of stock AND open orders, so the exclusion
    has to cover both sides — otherwise a purchase order the ERP happened to
    raise for waste powder would put the row back."""
    await _material(db_session, "CF00AF", "Animal Feed", erp_class="05")
    await _open_po(db_session, "CF00AF", "500", "0")

    body = (await client.get("/api/v1/inventory/materials",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert [i["material_code"] for i in body["items"]] == []


@pytest.mark.anyio
async def test_byproducts_are_excluded_from_all_classes_not_just_05(
    client, db_session, admin_token,
):
    """They are class-05 materials, so an unfiltered view would show them too.
    The switch is offered on "All classes" for exactly that reason — the screen
    must never remove rows with no visible way to bring them back."""
    await _material(db_session, "CF00AF", "Animal Feed", erp_class="05")
    await _material(db_session, "CR0025", "Lactose", erp_class="0102")
    await _lot(db_session, "CF00AF", "L1", "80000", supplier_batch="FEED")
    await _lot(db_session, "CR0025", "L2", "100", supplier_batch="RAW")

    headers = {AUTH: f"Bearer {admin_token}"}
    off = (await client.get("/api/v1/inventory/batches", headers=headers)).json()
    on = (await client.get("/api/v1/inventory/batches",
                           params={"include_byproducts": "true"}, headers=headers)).json()
    assert [i["material_code"] for i in off["items"]] == ["CR0025"]
    assert len(on["items"]) == 2


@pytest.mark.anyio
async def test_excluding_byproducts_does_not_touch_other_classes(
    client, db_session, admin_token,
):
    """The rule is two codes, not a pattern: no other CF material is affected."""
    await _material(db_session, "CF0086", "Real Powder", erp_class="05")
    await _lot(db_session, "CF0086", "L1", "500", supplier_batch="KEEP")

    body = (await client.get("/api/v1/inventory/batches",
                             headers={AUTH: f"Bearer {admin_token}"})).json()
    assert [i["material_code"] for i in body["items"]] == ["CF0086"]
