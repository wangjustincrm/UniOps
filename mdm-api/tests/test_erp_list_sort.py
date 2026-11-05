"""Click-to-sort headers on the ERP mirror lists (Portal Admin → ERP MDM tabs,
EPMS "From ERP" vendor drawer): ?sort=<key>&dir=asc|desc sorted in SQL before
paging, NULLs last in both directions, unknown key → 422, no sort → the old
default order."""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.erp_material import ErpMaterial
from app.models.erp_person import ErpPerson
from app.models.erp_supplier import ErpSupplier

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
BASE = "/mdm/v1/erp"


async def _seed(db, rows):
    db.add_all(rows)
    await db.commit()


def _codes(resp, field):
    assert resp.status_code == 200, resp.text
    return [r[field] for r in resp.json()["items"]]


def _mat(no, desc):
    return ErpMaterial(id=uuid.uuid4(), erp_part_no=no, description=desc,
                       part_status="A", raw_payload={}, synced_at=NOW)


@pytest.mark.anyio
async def test_materials_sort(client, db_session):
    await _seed(db_session, [
        _mat("M3", "banana"), _mat("M1", "Apple"), _mat("M2", None), _mat("M4", "cherry"),
    ])
    get = lambda qs: client.get(f"{BASE}/materials?part_status=A&{qs}")  # noqa: E731
    # default: erp_part_no
    assert _codes(await get(""), "erp_part_no") == ["M1", "M2", "M3", "M4"]
    # asc is case-insensitive ("Apple" before "banana"); NULL last
    assert _codes(await get("sort=description&dir=asc"), "erp_part_no") == ["M1", "M3", "M4", "M2"]
    # desc reverses the values but NULL stays last
    assert _codes(await get("sort=description&dir=desc"), "erp_part_no") == ["M4", "M3", "M1", "M2"]
    # page 2 continues page 1
    p1 = _codes(await get("sort=description&dir=desc&page=1&page_size=2"), "erp_part_no")
    p2 = _codes(await get("sort=description&dir=desc&page=2&page_size=2"), "erp_part_no")
    assert p1 + p2 == ["M4", "M3", "M1", "M2"]
    r = await get("sort=description&page_size=2")
    assert r.json()["total"] == 4
    # unknown key / bad dir
    assert (await get("sort=raw_payload")).status_code == 422
    assert (await get("sort=description;drop table x")).status_code == 422
    assert (await get("sort=description&dir=up")).status_code == 422


@pytest.mark.anyio
async def test_suppliers_sort(client, db_session):
    await _seed(db_session, [
        ErpSupplier(id=uuid.uuid4(), erp_supplier_code=c, supplier_name=n, supplier_type=t,
                    raw_payload={}, synced_at=NOW)
        for c, n, t in [("S1", "zeta", "B"), ("S2", "Alpha", None), ("S3", "beta", "a")]
    ])
    get = lambda qs: client.get(f"{BASE}/suppliers?{qs}")  # noqa: E731
    assert _codes(await get(""), "erp_supplier_code") == ["S1", "S2", "S3"]
    assert _codes(await get("sort=supplier_name"), "erp_supplier_code") == ["S2", "S3", "S1"]
    assert _codes(await get("sort=supplier_name&dir=desc"), "erp_supplier_code") == ["S1", "S3", "S2"]
    assert _codes(await get("sort=supplier_type&dir=asc"), "erp_supplier_code") == ["S3", "S1", "S2"]
    assert _codes(await get("sort=supplier_type&dir=desc"), "erp_supplier_code") == ["S1", "S3", "S2"]
    p1 = _codes(await get("sort=supplier_name&page=1&page_size=2"), "erp_supplier_code")
    p2 = _codes(await get("sort=supplier_name&page=2&page_size=2"), "erp_supplier_code")
    assert p1 + p2 == ["S2", "S3", "S1"]
    # exclude_codes still applies alongside sort
    assert _codes(await get("sort=supplier_name&exclude_codes=S2"), "erp_supplier_code") == ["S3", "S1"]
    assert (await get("sort=nope")).status_code == 422


@pytest.mark.anyio
async def test_persons_sort(client, db_session):
    await _seed(db_session, [
        ErpPerson(id=uuid.uuid4(), erp_person_code=c, person_name=n, department_name=d,
                  is_valid=v, raw_payload={}, synced_at=NOW)
        for c, n, d, v in [("P1", "Carol", "QA", True), ("P2", "alice", None, False),
                           ("P3", "Bob", "it", True)]
    ])
    get = lambda qs: client.get(f"{BASE}/persons?{qs}")  # noqa: E731
    # default = raw person_name (case-sensitive, unchanged from before)
    assert _codes(await get(""), "erp_person_code") == ["P3", "P1", "P2"]
    assert _codes(await get("sort=person_name"), "erp_person_code") == ["P2", "P3", "P1"]
    assert _codes(await get("sort=erp_person_code&dir=desc"), "erp_person_code") == ["P3", "P2", "P1"]
    assert _codes(await get("sort=department_name"), "erp_person_code") == ["P3", "P1", "P2"]
    assert _codes(await get("sort=department_name&dir=desc"), "erp_person_code") == ["P1", "P3", "P2"]
    # Valid shows Yes/No: asc → No first
    assert _codes(await get("sort=is_valid"), "erp_person_code")[0] == "P2"
    p1 = _codes(await get("sort=department_name&page=1&page_size=2"), "erp_person_code")
    p2 = _codes(await get("sort=department_name&page=2&page_size=2"), "erp_person_code")
    assert p1 + p2 == ["P3", "P1", "P2"]
    assert (await get("sort=pk_psndoc")).status_code == 422
