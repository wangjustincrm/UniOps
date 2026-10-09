"""AI Financial Analysis — NC cost mirror + material trace + anomalies.

The scene is the case the module was built for, in miniature and with NC's own
figures (production, read 2026-10-09):

  * CR0069 milk protein: received PROVISIONALLY on 29 July at 5.058
    (CR2026072900030224), consumed in July, settled on 14 August at 4.2005
    (JS202608140002). Variance -21,572.27 arrives in August as an amount-only
    issue adjustment.
  * CR0059 Pasteurized Skim Milk: 981,405 kg in July at 0.4854; in August one
    39,411 kg run absorbs the settlement variances → -47,735.79 (-1.2112/kg),
    is not consumed, and is carried into September, where NC's tail
    adjustment IA2026092900003299 (-46,588.85) dumps it on the last issue —
    production order 55A22026082400006505, i.e. CS0080.
  * CR0177: a production-picking receipt in February whose issue leg is a
    hand-made other-issue in March. Netted by year it is zero; it is still
    an unpaired transfer.

Spec acceptance cases T1–T5, T8, T9 are asserted here by number.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import text

from app.api.v1 import cost_analysis as api
from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.services import cost_analysis as ca
from app.services import nc_cost_sync as svc

D = Decimal

_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
        f"port={os.getenv('TEST_PG_PORT', '5432')} "
        f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
        f"user={os.getenv('TEST_PG_USER', 'epms')} "
        f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


# ── the fake NC snapshot ─────────────────────────────────────────────────────

_seq = iter(range(1, 100000))


def L(period, bill_type, mat, qty, amount, *, direction=None, est=None, src_type=None,
      src_code=None, batch=None, mo=None, trantype=None, bill_code=None, name=None,
      upstream=None, dr=0):
    n = next(_seq)
    if direction is None:
        direction = 0 if bill_type in ("I0", "I2", "I3", "I4", "I9") else 1
    row = {
        "nc_pk": f"LED{n:019d}", "period": period, "bill_type": bill_type,
        "bill_code": bill_code or f"{bill_type}{period.replace('-', '')}{n:06d}",
        "bill_id": None, "bill_bid": None, "row_no": "1", "biz_date": f"{period}-15 00:00:00",
        "direction": direction, "material_pk": f"PK{mat}", "material_code": mat,
        "material_name": name or mat,
        "qty": None if qty is None else D(str(qty)),
        "price": None if (qty in (None, 0)) else (D(str(amount)) / D(str(qty))),
        "amount": D(str(amount)), "batch_code": batch, "mo_code": mo,
        "src_type": src_type, "src_code": src_code, "first_code": None,
        "upstream_type": ("4N" if upstream.startswith("XT") else "4K") if upstream else None,
        "upstream_code": upstream, "upstream_bid": None,
        "trantype_code": trantype, "trantype_name": trantype, "estimate_flag": est,
        "audit_seq": n, "price_mode": 3, "note": None, "dr": dr, "nc_ts": "2026-10-01 00:00:00",
    }
    return tuple(row[c] for c in svc.LEDGER.cols)


def _ledger_rows():
    return [
        # CR0069 — provisional July, consumed July, settled August
        L("2026-07", "I2", "CR0069", "25156.79", "127243.04", est="Y", src_type="45",
          src_code="CR2026072900030224", batch="202607"),
        L("2026-07", "I6", "CR0069", "24255.3356", "122683.49", trantype="4D-Cxx-CRM02"),
        L("2026-07", "I6", "CR0069", "901.4544", "4559.56", trantype="4D-Cxx-CRM02"),
        L("2026-07", "IA", "CR0069", None, "-0.01"),
        L("2026-08", "I2", "CR0069", "-25156.79", "-127243.04", est="Y", src_type="27",
          src_code="JS202608140002", batch="202607"),
        L("2026-08", "I2", "CR0069", "25156.79", "105670.77", est="N", src_type="27",
          src_code="JS202608140002", batch="202607"),
        L("2026-08", "I2", "CR0069", "1264.77", "5312.03", est="Y", src_type="45",
          src_code="CR2026083000030239", batch="202608"),
        L("2026-08", "I6", "CR0069", "1264.77", "5312.03", trantype="4D-Cxx-CRM02"),
        L("2026-08", "IA", "CR0069", None, "-21572.27"),
        # an ordinary purchase invoiced the month it arrived — NOT a variance
        L("2026-08", "I2", "CP0105", "8000", "3766.40", est="N", src_type="27",
          src_code="JS202608200001", batch="2511-J000019627"),
        L("2026-08", "I6", "CP0105", "8000", "3766.40", trantype="4D-Cxx-001"),
        # CR0059 — July normal, August negative, September dumped
        L("2026-07", "I3", "CR0059", "981405", "476363.27", batch="P202607051101"),
        L("2026-07", "I6", "CR0059", "981405", "476363.80", trantype="4D-01",
          mo="55A22026063000006481"),
        L("2026-07", "IA", "CR0059", None, "-0.53"),
        L("2026-08", "I3", "CR0059", "39411", "-47735.79", batch="P202608311101"),
        L("2026-08", "IA", "CR0059", None, "-1.19"),
        L("2026-09", "I3", "CR0059", "84450", "32691.48", batch="P202609061101"),
        L("2026-09", "I6", "CR0059", "10262", "-12429.34", trantype="4D-01",
          mo="55A22026082400006505", batch="P202608311101"),
        L("2026-09", "I6", "CR0059", "113599", "43975.24", trantype="4D-01",
          mo="55A22026082900006507"),
        L("2026-09", "IA", "CR0059", None, "-46588.85", mo="55A22026082400006505",
          src_code="I62026090800055499", bill_code="IA2026092900003299"),
        L("2026-09", "IA", "CR0059", None, "0.01", mo="55A22026082400006505"),
        L("2026-09", "IA", "CR0059", None, "-0.18", mo="55A22026082600006502"),
        # CS0080 — the semi-finished product that ate it
        L("2026-09", "I3", "CS0080", "5432.3", "-13482.75", batch="20262101021",
          mo="55A22026082400006505", name="210-Niuriss Smart2 半成品"),
        # CR0177 — picking receipt in Feb whose transfer order no longer exists
        # in NC; its "issue" a manual other-issue in Mar
        L("2026-02", "I4", "CR0177", "19681", "563669.74", trantype="4A-Cxx-001",
          src_type="4A", src_code="TR2026021600027874"),
        L("2026-03", "I7", "CR0177", "19681", "563669.74"),
        # a transfer that does pair — must NOT be reported. As in NC, the legs
        # carry DIFFERENT source bills (TR other-receipt / TC other-issue) and
        # share only the transfer order they came from.
        L("2026-09", "I4", "S0093", "12604.2", "257477.34", trantype="4A-Cxx-093",
          src_type="4A", src_code="TR2026092900029685", upstream="ZK2026092900061001"),
        L("2026-09", "I7", "S0093", "12604.2", "257477.34", trantype="4I-Cxx-093",
          src_type="4I", src_code="TC2026092900612001", upstream="ZK2026092900061001"),
        # a form conversion turns one material into another — pairs on the XT
        # document across materials
        L("2026-09", "I7", "S0064", "1870", "45990.12", trantype="4I-Cxx-015",
          src_type="4I", src_code="TC2026100900612002", upstream="XT2026100900570001"),
        L("2026-09", "I4", "CR0042", "1870", "45990.12", trantype="4A-Cxx-010",
          src_type="4A", src_code="TR2026100900029001", upstream="XT2026100900570001"),
        # a logically deleted row — mirrored, never analysed
        L("2026-09", "I3", "CR0059", "999999", "999999", dr=1),
    ]


def _balance_rows():
    def B(period, mat, qty, amount):
        n = next(_seq)
        return (f"BAL{n:019d}", period, f"PK{mat}", mat, D(qty), D(amount),
                D(amount) / D(qty), 0, "2026-10-01 00:00:00")
    # As in NC on 2026-10-09: balances exist for closed months only, so
    # September (open) has none — CS0080's September stock has no NC balance.
    return [B("2026-08", "CR0059", "39411", "-47734.60"),
            B("2026-02", "CR0177", "19681", "563669.74")]


def _stuff_rows():
    def S(period, obj, obj_name, st, mat, qty, amount):
        n = next(_seq)
        return (f"STB{n:019d}", f"ST{period}{obj}{st}", f"CMM2{n:06d}", period, f"OBJ{obj}",
                obj, obj_name, st, f"{period}-28 00:00:00", "100101", "生牛乳",
                f"PK{mat}", mat, mat, None if qty is None else D(qty), None, D(amount),
                0, "2026-10-01 00:00:00")
    return [
        S("2026-07", "CR0059", "Pasteurized Skim Milk", 18, "CR0069", "24255.3356", "122683.49"),
        S("2026-07", "CR0059", "Pasteurized Skim Milk", 18, "CR0070", "45397.8775", "229622.46"),
        S("2026-07", "CR0059", "Pasteurized Skim Milk", 18, "CR0016", "9848.6551", "124057.32"),
        S("2026-09", "CS0080", "210-Niuriss Smart2 半成品", 18, "CP0105", "8000", "45535.43"),
        S("2026-08", "CR0059", "Pasteurized Skim Milk", 18, "CR0069", "1195.9", "5022.78"),
        S("2026-08", "CR0059", "Pasteurized Skim Milk", 18, "CR0070", "2178.09", "9147.98"),
        S("2026-08", "CR0059", "Pasteurized Skim Milk", 31, "CR0016", None, "27.26"),
        S("2026-08", "CR0059", "Pasteurized Skim Milk", 31, "CR0069", None, "-21572.27"),
        S("2026-08", "CR0059", "Pasteurized Skim Milk", 31, "CR0070", None, "-40361.54"),
        S("2026-09", "CS0080", "210-Niuriss Smart2 半成品", 18, "CR0059", "10262", "-12429.34"),
        # NC files the tail dump as source 60, not 31 (measured on dev1 2026-10-09)
        S("2026-09", "CS0080", "210-Niuriss Smart2 半成品", 60, "CR0059", None, "-46588.84"),
        S("2026-09", "CS0026", "251基粉半成品", 18, "CR0059", "48787", "18885.45"),
    ]


def _prod_rows():
    def H(pk, period, obj, mat, name, qty, amount):
        return (pk, period, f"OBJ{obj}", f"PK{mat}", mat, name, "CC1", 1, D(qty), D(amount), 0,
                "2026-10-01 00:00:00")
    heads = [
        H("PC0000000000000000007", "2026-07", "CR0059", "CR0059", "Pasteurized Skim Milk",
          "981405", "476363.27"),
        H("PC0000000000000000001", "2026-08", "CR0059", "CR0059", "Pasteurized Skim Milk",
          "39411", "-47735.79"),
        H("PC0000000000000000009", "2026-09", "CS0080", "CS0080", "210-Niuriss Smart2 半成品",
          "5432.3", "-13482.75"),
    ]

    def PL(n, head, period, mat, qty, amount):
        return (f"PCB{n:018d}", head, period, "EL1", "100101", "生牛乳",
                f"PK{mat}", mat, mat, 1, None if qty is None else D(qty), None, D(amount), 0,
                "2026-10-01 00:00:00")
    lines = [PL(1, "PC0000000000000000001", "2026-08", "CR0016", None, "27.26"),
             PL(2, "PC0000000000000000001", "2026-08", "CR0069", "1195.9", "-16549.49"),
             PL(3, "PC0000000000000000001", "2026-08", "CR0070", "2178.09", "-31213.56"),
             PL(4, "PC0000000000000000007", "2026-07", "CR0069", "24255.3356", "122683.49"),
             PL(5, "PC0000000000000000007", "2026-07", "CR0070", "45397.8775", "229622.46"),
             PL(6, "PC0000000000000000007", "2026-07", "CR0016", "9848.6551", "124057.32"),
             PL(7, "PC0000000000000000009", "2026-09", "CR0059", "10262", "-59018.18"),
             PL(8, "PC0000000000000000009", "2026-09", "CP0105", "8000", "45535.43")]
    return heads, lines


def _cost_objects():
    return [("OBJCR0059", "CR0059", "Pasteurized Skim Milk", "PKCR0059", "CR0059", 0, None),
            ("OBJCS0080", "CS0080", "210-Niuriss Smart2 半成品", "PKCS0080", "CS0080", 0, None)]


def _invoice_rows():
    return [("SIB0000000000000000001", "SI1", "SI2026033000126201", "2026-03-30 00:00:00", 2,
             "C1", "Munchkin", "USD", D("1.3939"), "1", "PKS0138", "S0138", "913 Munchkin",
             D("13720.35"), D("375684.35"), D("269520.30"), D("0"), 0, "2026-10-01 00:00:00")]


def _nc_totals_from(rows_by_table: dict) -> dict:
    """What NC's own aggregates would say — computed from the same rows, so a
    clean run ties out and a tampered one does not."""
    out = {}
    for spec in svc.SPECS:
        rows = rows_by_table.get(spec.name, [])
        idx = {c: i for i, c in enumerate(spec.cols)}
        agg = {}
        for r in rows:
            if spec.group == ("period", "bill_type"):
                key = f"{r[idx['period']]}|{r[idx['bill_type']]}"
            elif spec.group == ("period",):
                key = r[idx["period"]]
            elif spec.group == ("month",):
                key = r[idx["bill_date"]][:7]
            else:
                key = "*"
            amt = r[idx["amount"]] if "amount" in idx else D0
            c = agg.setdefault(key, [0, D("0")])
            c[0] += 1
            c[1] += amt or D("0")
        out[spec.name] = {k: [v[0], str(v[1])] for k, v in agg.items()}
    return out


D0 = D("0")


def fake_extract():
    heads, lines = _prod_rows()
    rows = {
        "nc_ia_ledger": _ledger_rows(),
        "nc_ia_month_balances": _balance_rows(),
        "nc_cm_cost_objects": _cost_objects(),
        "nc_cm_prod_costs": heads,
        "nc_cm_prod_cost_lines": lines,
        "nc_cm_stuff_lines": _stuff_rows(),
        "nc_so_invoice_lines": _invoice_rows(),
    }
    return svc.CostExtract(rows=rows, nc_totals=_nc_totals_from(rows))


# ── fixtures ─────────────────────────────────────────────────────────────────

def _h(role="finance_manager", uid=None):
    tok = jwt.encode({"sub": str(uid or uuid.uuid4()), "role": role, "name": "FA Test",
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def nc_on(monkeypatch):
    """Pretend NC is configured — and make sure nothing can reach the real one."""
    monkeypatch.setattr(svc, "nc_configured", lambda: True)
    monkeypatch.setattr(svc, "fetch_from_nc", lambda: pytest.fail("real NC read"))
    monkeypatch.setattr(api, "_fetch", fake_extract)
    monkeypatch.setattr(api, "_worker_dsn", lambda: _DSN)


@pytest_asyncio.fixture
async def synced(db_session, nc_on):
    run_id = svc.start_run(None, fetch=fake_extract, pg_dsn=_DSN, run_worker=True)
    row = (await db_session.execute(text(
        "select status, tie_out_ok, error from nc_cost_sync_runs where id = :i"),
        {"i": run_id})).one()
    assert row[0] == "success", row[2]
    return run_id


# ── sync (T8) ────────────────────────────────────────────────────────────────

async def test_sync_mirrors_every_table_and_ties_out(db_session, synced):
    ok, counts = (await db_session.execute(text(
        "select tie_out_ok, counts from nc_cost_sync_runs where id = :i"), {"i": synced})).one()
    assert ok is True
    assert counts["nc_ia_ledger"] == len(_ledger_rows())
    # the deleted row is mirrored (faithful), and only filtered by the analysis
    assert (await db_session.execute(text(
        "select count(*) from nc_ia_ledger where dr = 1"))).scalar() == 1
    # revenue basis D5: local currency, tax excluded — not the USD original
    amt = (await db_session.execute(text(
        "select amount from nc_so_invoice_lines"))).scalar()
    assert amt == D("375684.35")


async def test_a_second_sync_replaces_rather_than_accumulates(db_session, synced):
    def smaller():
        ex = fake_extract()
        ex.rows["nc_ia_ledger"] = ex.rows["nc_ia_ledger"][:3]
        ex.nc_totals = _nc_totals_from(ex.rows)
        return ex
    svc.start_run(None, fetch=smaller, pg_dsn=_DSN, run_worker=True)
    assert (await db_session.execute(text("select count(*) from nc_ia_ledger"))).scalar() == 3


async def test_tie_out_names_the_cell_that_disagrees(db_session, nc_on):
    def lossy():
        ex = fake_extract()
        ex.nc_totals["nc_ia_ledger"]["2026-08|I3"] = [2, "-47735.79"]   # NC says two rows
        return ex
    run_id = svc.start_run(None, fetch=lossy, pg_dsn=_DSN, run_worker=True)
    status, ok, tie = (await db_session.execute(text(
        "select status, tie_out_ok, tie_out from nc_cost_sync_runs where id = :i"),
        {"i": run_id})).one()
    assert status == "success" and ok is False
    assert list(tie["diffs"]["nc_ia_ledger"]) == ["2026-08|I3"]


async def test_a_row_without_its_nc_id_aborts_and_keeps_the_old_snapshot(db_session, synced):
    def broken():
        ex = fake_extract()
        bad = list(ex.rows["nc_ia_ledger"][0])
        bad[0] = "~"
        ex.rows["nc_ia_ledger"] = [tuple(bad)]
        return ex
    run_id = svc.start_run(None, fetch=broken, pg_dsn=_DSN, run_worker=True)
    status = (await db_session.execute(text(
        "select status from nc_cost_sync_runs where id = :i"), {"i": run_id})).scalar()
    assert status == "failed"
    assert (await db_session.execute(text("select count(*) from nc_ia_ledger"))).scalar() \
        == len(_ledger_rows())


# ── material trace (T1, T3, T4) ──────────────────────────────────────────────

async def test_t1_august_cost_of_cr0059_and_where_it_came_from(db_session, synced):
    t = await ca.material_trace(db_session, "CR0059", "2026-07", "2026-09")
    aug = next(m for m in t["months"] if m["period"] == "2026-08")
    assert aug["production_unit_cost"] == pytest.approx(-1.2112)
    assert aug["closing"] == {"qty": 39411.0, "amount": -47734.60, "unit": pytest.approx(-1.2112)}
    assert aug["balance_matches"] is True and aug["period_closed"] is True
    pc = next(p for p in t["product_cost"] if p["period"] == "2026-08")
    assert pc["amount"] == -47735.79 and pc["qty"] == 39411.0
    assert pc["adjustment_total"] == pytest.approx(-61906.55)
    cr0069 = next(i for i in pc["inputs"] if i["material_code"] == "CR0069")
    assert (cr0069["consumed_amount"], cr0069["adjustment"], cr0069["carried_in"]) == \
        (5022.78, -21572.27, 0.0)
    # the deleted 999,999 row never reaches the analysis
    sep = next(m for m in t["months"] if m["period"] == "2026-09")
    assert sep["closing"]["qty"] == 0.0 and abs(sep["closing"]["amount"]) < 0.005
    # September has no NC month-end balance at all (not closed in this scene):
    # unchecked, not "matches"
    assert sep["period_closed"] is False and sep["balance_matches"] is None


async def test_t3_september_dump_lands_on_cs0080(db_session, synced):
    t = await ca.material_trace(db_session, "CR0059", "2026-09", "2026-09")
    dump = next(a for a in t["out_adjustments"] if a["bill_code"] == "IA2026092900003299")
    assert dump["amount"] == -46588.85
    assert dump["landed_on"] == [{"material_code": "CS0080",
                                  "material_name": "210-Niuriss Smart2 半成品"}]
    cs0080 = [c for c in t["consumers"] if c["cost_object_code"] == "CS0080"]
    assert sum(c["amount"] for c in cs0080) == pytest.approx(-59018.18)


async def test_t4_provisional_receipt_paired_with_its_settlement(db_session, synced):
    t = await ca.material_trace(db_session, "CR0069", "2026-07", "2026-08")
    ev = next(e for e in t["settlements"] if e["settlement_code"] == "JS202608140002")
    assert ev["provisional"]["bill_code"] == "CR2026072900030224"
    assert ev["provisional"]["unit"] == pytest.approx(5.058)
    assert ev["settled"]["unit"] == pytest.approx(4.2005, abs=1e-4)
    assert ev["variance"] == pytest.approx(-21572.27)


async def test_usage_is_consumption_not_picking(db_session, synced):
    """§6.0: the CR0177 picking receipt is a transfer, never consumption."""
    t = await ca.material_trace(db_session, "CR0177", "2026-02", "2026-03")
    feb = next(m for m in t["months"] if m["period"] == "2026-02")
    assert "consumption" not in feb["flows"]
    assert feb["flows"]["transfer_in"]["amount"] == 563669.74


# ── anomalies (T2, T3, T5) ───────────────────────────────────────────────────

def _hits(res, rule, mat=None, period=None):
    return [a for a in res["anomalies"] if a["rule"] == rule
            and (mat is None or a["material_code"] == mat)
            and (period is None or a["period"] == period)]


async def test_t2_cr0059_august_hits_a1_a2_a3(db_session, synced):
    res = await ca.anomalies(db_session, "2026-07", "2026-09")
    assert _hits(res, "A1", "CR0059", "2026-08")
    a2 = _hits(res, "A2", "CR0059", "2026-08")[0]
    assert a2["metrics"]["previous_period"] == "2026-07"
    a3 = _hits(res, "A3", "CR0059", "2026-08")[0]
    assert a3["detail"] == "negative value on hand"
    # carried across the month-end: September's orders consumed it
    nxt = {f["mo_code"]: f for f in a3["next_month"]}
    assert nxt["55A22026082400006505"]["amount"] == -12429.34
    assert nxt["55A22026082400006505"]["products"][0]["material_code"] == "CS0080"
    # July was normal
    assert not _hits(res, "A1", "CR0059", "2026-07")


async def test_t2_settlement_variance_is_a4_but_a_same_month_purchase_is_not(
        db_session, synced):
    res = await ca.anomalies(db_session, "2026-07", "2026-09")
    a4 = _hits(res, "A4", "CR0069", "2026-08")[0]
    assert a4["metrics"]["variance"] == pytest.approx(-21572.27)
    assert "JS202608140002" in a4["refs"] and "CR2026072900030224" in a4["refs"]
    assert not _hits(res, "A4", "CP0105")


async def test_t3_tail_dump_is_a6_with_its_landing(db_session, synced):
    res = await ca.anomalies(db_session, "2026-09", "2026-09")
    a6 = _hits(res, "A6", "CR0059")
    assert len(a6) == 1          # the 0.01 / -0.18 tails are below the threshold
    assert a6[0]["metrics"]["landed_on"][0]["material_code"] == "CS0080"


async def test_t5_unpaired_transfer_is_reported_and_a_paired_one_is_not(db_session, synced):
    res = await ca.anomalies(db_session, "2026-01", "2026-09")
    a5 = _hits(res, "A5", "CR0177")
    assert len(a5) == 1
    assert a5[0]["metrics"]["family"] == "line_picking"
    assert a5[0]["metrics"]["difference"]["amount"] == 563669.74
    assert a5[0]["detail"] == "no upstream transfer document in NC"
    assert not _hits(res, "A5", "S0093")
    assert not _hits(res, "A5", "S0064") and not _hits(res, "A5", "CR0042")


async def test_no_balance_mismatch_on_a_clean_snapshot(db_session, synced):
    res = await ca.anomalies(db_session, "2026-01", "2026-09")
    assert not _hits(res, "A8")


async def test_an_open_period_is_not_held_against_a_balance_nc_has_not_written(
        db_session, synced):
    """NC writes month-end balances at close. September is open here, CS0080
    has value on hand, and there is no balance row for it — that is not a
    mismatch. The closed months still are checked: break one and it shows."""
    res = await ca.anomalies(db_session, "2026-01", "2026-09")
    assert "2026-09" in res["open_periods"] and "2026-08" not in res["open_periods"]
    assert all(a["period_closed"] is False for a in res["anomalies"] if a["period"] == "2026-09")
    await db_session.execute(text(
        "update nc_ia_month_balances set amount = amount + 5 where material_code = 'CR0059'"))
    await db_session.commit()
    res = await ca.anomalies(db_session, "2026-08", "2026-08")
    assert _hits(res, "A8", "CR0059", "2026-08")


async def test_an_unpriced_open_month_is_not_a_zero_quantity_anomaly(db_session, synced):
    """October: received at a price, issued before NC's month-end cost run
    priced the issue — zero quantity with value left, which closes itself at
    month-end. Not reported while the month is open; reported once closed."""
    await db_session.execute(text(
        "insert into nc_ia_ledger (id, nc_pk, period, bill_type, direction, material_pk, "
        "material_code, qty, amount, dr, synced_at) values "
        "(gen_random_uuid(), 'OCT1', '2026-10', 'I2', 0, 'PKX', 'CPOCT', 10, 50, 0, now()), "
        "(gen_random_uuid(), 'OCT2', '2026-10', 'I6', 1, 'PKX', 'CPOCT', 10, 0, 0, now())"))
    await db_session.commit()
    res = await ca.anomalies(db_session, "2026-10", "2026-10")
    assert not _hits(res, "A3", "CPOCT")
    await db_session.execute(text(
        "insert into nc_ia_month_balances (id, nc_pk, period, material_pk, material_code, qty, "
        "amount, dr, synced_at) values (gen_random_uuid(), 'BOCT', '2026-10', 'PKX', 'CPOCT', "
        "0, 50, 0, now())"))
    await db_session.commit()
    res = await ca.anomalies(db_session, "2026-10", "2026-10")
    assert _hits(res, "A3", "CPOCT")


async def test_component_adjustment_without_an_order_still_shows_where_it_landed(
        db_session, synced):
    t = await ca.material_trace(db_session, "CR0069", "2026-08", "2026-08")
    adj = next(a for a in t["out_adjustments"] if a["amount"] == -21572.27)
    assert adj["mo_code"] is None
    assert [x["material_code"] for x in adj["landed_on"]] == ["CR0059"]


async def test_thresholds_are_read_from_config(db_session, synced):
    await db_session.execute(text(
        "update fa_config set thresholds = '{\"a6_min_amount\": 100000}' where id = 1"))
    await db_session.commit()
    res = await ca.anomalies(db_session, "2026-09", "2026-09")
    assert not _hits(res, "A6")


# ── access (T9) ──────────────────────────────────────────────────────────────

async def test_t9_who_may_look(client, db_session, synced):
    url = "/finance/v1/cost-analysis/trace?material=CR0059&from=2026-08&to=2026-08"
    # admission first, so the denial below cannot pass by denying everyone
    assert (await client.get(url, headers=_h("finance_manager"))).status_code == 200
    assert (await client.get(url, headers=_h("system_admin"))).status_code == 200
    director = uuid.uuid4()
    await db_session.execute(text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_director')"),
        {"u": director})
    await db_session.commit()
    assert (await client.get(url, headers=_h("requester", director))).status_code == 200
    # view_finance roles without the key do not see unit costs
    for role in ("requester", "ap_clerk", "finance_bp"):
        assert (await client.get(url, headers=_h(role))).status_code == 403, role
        assert (await client.get("/finance/v1/cost-analysis/anomalies?from=2026-08&to=2026-08",
                                 headers=_h(role))).status_code == 403, role


async def test_only_system_admin_triggers_or_reconfigures(client, nc_on):
    assert (await client.post("/finance/v1/cost-analysis/sync",
                              headers=_h("finance_manager"))).status_code == 403
    assert (await client.patch("/finance/v1/cost-analysis/config", json={"interval_minutes": 60},
                               headers=_h("finance_manager"))).status_code == 403
    r = await client.patch("/finance/v1/cost-analysis/config",
                           json={"thresholds": {"a2_unit_cost_change": 0.5}},
                           headers=_h("system_admin"))
    assert r.status_code == 200 and r.json()["thresholds"]["a2_unit_cost_change"] == 0.5
    assert (await client.patch("/finance/v1/cost-analysis/config",
                               json={"thresholds": {"nonsense": 1}},
                               headers=_h("system_admin"))).status_code == 422


async def test_bad_period_is_422_not_500(client, synced):
    r = await client.get("/finance/v1/cost-analysis/trace?material=CR0059&from=2026-13&to=2026-08",
                         headers=_h())
    assert r.status_code == 422


# ── diagnosis: the conclusion first, the data as evidence ───────────────────

async def test_diagnosis_explains_cr0059_august_with_its_evidence(db_session, synced):
    from app.services import cost_diagnosis as dx
    d = await dx.diagnose(db_session, "CR0059", "2026-07", "2026-09")
    cost = next(f for f in d["findings"] if f["kind"] == "cost" and f["period"] == "2026-08")
    assert cost["severity"] == "high"
    assert "settlement variances on CR0069" in cost["title"]
    because = " ".join(cost["because"])
    # the provisional receipt, its settlement, and that the stock was already used
    assert "CR2026072900030224" in because and "JS202608140002" in because
    assert "all of it had been consumed by the end of 2026-07" in because
    # the timing mismatch, and what the months would have cost
    assert "charged to 2026-08's run of 39,411, 4% of that volume" in because
    assert "2026-08 would have cost 0.3596/unit" in because
    refs = {e["ref"] for e in cost["evidence"] if e["ref"]}
    assert {"CR2026072900030224", "JS202608140002"} <= refs
    # carried into September, then dumped on CS0080 — with the arithmetic
    assert any(f["kind"] == "carry" and f["period"] == "2026-08" for f in d["findings"])
    tail = next(f for f in d["findings"] if f["kind"] == "tail")
    assert "IA2026092900003299" in tail["because"][0]
    assert "= -46,589.02 still on the books" in tail["because"][0]
    assert "CS0080" in tail["title"]
    # the story in date order
    periods = [s["period"] for s in d["chain"]]
    assert periods == sorted(periods) and periods[0] == "2026-07"


async def test_diagnosis_follows_inherited_value_to_its_root(db_session, synced):
    from app.services import cost_diagnosis as dx
    d = await dx.diagnose(db_session, "CS0080", "2026-09", "2026-09")
    cost = next(f for f in d["findings"] if f["kind"] == "cost")
    assert "inherited from CR0059" in cost["title"]
    assert [h["material"] for h in d["root_chain"]] == ["CR0059"]
    assert d["root_chain"][0]["period"] == "2026-08"
    assert "Root cause upstream: CR0059" in d["headline"]


async def test_diagnosis_names_the_settlement_behind_a_raw_material_adjustment(db_session, synced):
    from app.services import cost_diagnosis as dx
    d = await dx.diagnose(db_session, "CR0069", "2026-08", "2026-08")
    f = next(f for f in d["findings"] if f["kind"] == "tail")
    assert "Settlement JS202608140002 repriced the 2026-07 provisional receipt CR2026072900030224" \
        in f["because"][0]
    assert f["related"] == ["CR0059"]


async def test_a_quiet_material_says_so(db_session, synced):
    from app.services import cost_diagnosis as dx
    d = await dx.diagnose(db_session, "CP0105", "2026-08", "2026-08")
    assert d["findings"] == [] and "nothing abnormal" in d["headline"]


async def test_trace_carries_its_diagnosis_and_access_is_gated(client, synced):
    r = await client.get("/finance/v1/cost-analysis/trace?material=CR0059&from=2026-07&to=2026-09",
                         headers=_h())
    assert r.status_code == 200 and r.json()["diagnosis"]["findings"]
    url = "/finance/v1/cost-analysis/diagnosis?material=CR0059&from=2026-08&to=2026-08"
    assert (await client.get(url, headers=_h())).status_code == 200
    assert (await client.get(url, headers=_h("requester"))).status_code == 403
