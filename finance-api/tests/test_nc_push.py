"""AP push to NC via XChange — rules, plan, XML, NC response, orchestration, API.

NC itself is faked (NcLookup / XChangeClient); the NC side of these rules was
proven on the NC test server on 2026-10-27 (spec §3), and the two response
bodies below are verbatim answers from it.
"""
import os
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings as app_settings
from app.db.base import get_db
from app.main import app
from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine
from app.models.bank import ExchangeRate
from app.models.mirrors import (CostCenter, Department, FreightInvoiceLine, Invoice,
                                InvoicePoAllocation, PurchaseAgreement, PurchaseOrder,
                                PurchaseRequest, User)
from app.models.nc_push import NcPushLog
from app.models.pa import PaymentApplication
from app.services import nc_push as svc
from app.services.nc_push import NcFacts, PushRefused, XResult
from app.services.posting import emit_event

OK_RESPONSE = """<?xml version="1.0" encoding='UTF-8'?>
<ufinterface billtype="F1" filename="UNIOPS-AP-20261015-0007-T1.xml" isexchange="Y" replace="Y" roottag="sendresult" sender="uniops" successful="Y">
    <sendresult>
        <billpk>
        </billpk>
        <bdocid>UNIOPS-AP-20261015-0007-T1</bdocid>
        <filename>UNIOPS-AP-20261015-0007-T1.xml</filename>
        <resultcode>1</resultcode>
        <resultdescription>单据  UNIOPS-AP-20261015-0007-T1  开始处理...
单据  UNIOPS-AP-20261015-0007-T1  处理完毕!</resultdescription>
        <content>1001A1100000003I85WJ</content>
    </sendresult>
</ufinterface>"""

APPROVED_RESPONSE = """<?xml version="1.0" encoding='UTF-8'?>
<ufinterface billtype="F1" filename="x.xml" isexchange="Y" replace="Y" roottag="sendresult" sender="uniops" successful="N">
    <sendresult>
        <billpk>
        </billpk>
        <bdocid>UNIOPS-AP-20261015-0007-T1</bdocid>
        <filename>x.xml</filename>
        <resultcode>-32000</resultcode>
        <resultdescription>单据  UNIOPS-AP-20261015-0007-T1  开始处理...
单据  UNIOPS-AP-20261015-0007-T1  处理错误:业务插件处理错误:插件类=nc.bs.arap.plugin.ArapExpPfxxPlugin,异常信息:单据已经审核，不允许重复导入单据。</resultdescription>
        <content></content>
    </sendresult>
</ufinterface>"""

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _exec(sql, params=()):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, params)
    con.close()


def _vendor(erp_id="0000522"):
    _exec("create table if not exists vendors (id uuid primary key, erp_id varchar(50))")
    vid = uuid.uuid4()
    _exec("insert into vendors values (%s,%s)", (str(vid), erp_id))
    return vid


def _h(role="ap_clerk", sub=None):
    tok = jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     app_settings.jwt_secret_key, algorithm=app_settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class FakeLookup:
    def __init__(self, country="CA", users=("Raghav",), pks=None):
        self.country = country
        self.users = list(users)
        self.pks = pks if pks is not None else {
            "E04": "1001A1100000003CGYIR", "ENG": "1001A1100000003CGYIK",
            "Q01": "PKQ01", "QA": "PKQA", "H01": "PKH01"}
        self.calls = []

    def facts(self, supplier_code, cc_codes, person_code, currency="CAD"):
        self.calls.append((supplier_code, set(cc_codes), person_code))
        return NcFacts(self.country, {c: p for c, p in self.pks.items() if c in cc_codes}, self.users,
                       currency == "CNY")

    def bill_no(self, pk):
        return "D12026102800139886"


class FakeClient:
    def __init__(self, raw=OK_RESPONSE):
        self.raw = raw
        self.sent = []

    async def post(self, xml):
        self.sent.append(xml)
        return svc.parse_response(self.raw)


# ── rules ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cc,dept,rev,want", [
    ("MOH-0106-E01", "0106", "CRM0090201", "510102"),      # MOH default variable
    ("MOH-0106-E01", "0106", "CRM00503", "510101"),        # rent -> fixed
    ("MOH-0105-LAB", "0105", "CRM01005", "510101"),        # QA lab -> fixed
    ("MOH-0105-LAB", "0105", "CRM0111301", "510102"),      # lab exception -> variable
    ("GA-0105", "0105", "CRM01004", "510101"),             # GA-0105 is manufacturing
    ("GA-0101", "0101", "CRM01004", "6602"),
    ("SELL-0107-S03", "0107", "CRM01004", "660101"),
    ("RD-0109", "0109", "CRM01004", "5301"),
    (None, "0106", "CRM00901", "510102"),                  # no CC: by department
    (None, "0111", "CRM00901", "660101"),
    (None, "0109", "CRM00901", "5301"),
    (None, "0101", "CRM00901", "6602"),
])
def test_account_rule(cc, dept, rev, want):
    assert svc.account_for(cc, dept, rev) == want


def test_tax_codes():
    D = Decimal
    assert svc.nc_tax("HST_ON", D("639.50"), D("83.14")) == ("001", D("13"))
    assert svc.nc_tax("HST_NS", D("100"), D("15")) == ("001", D("15"))
    assert svc.nc_tax("HST_ON", D("29.98"), D("3.90")) == ("001", D("13"))   # 13.01% snapped
    assert svc.nc_tax("GST", D("695.00"), D("34.75")) == ("005", D("5"))
    assert svc.nc_tax(None, D("680"), D("0")) == ("006", D("0"))
    assert svc.nc_tax("HST_ON", D("680"), D("0")) == ("006", D("0"))   # no tax = zero-rated
    assert svc.nc_tax(None, D("100"), D("13")) == ("001", D("13"))  # old invoice, no tax line
    with pytest.raises(PushRefused):
        svc.nc_tax("PST_BC", D("100"), D("7"))
    with pytest.raises(PushRefused):
        svc.nc_tax(None, D("100"), D("9"))


def test_parse_response():
    ok = svc.parse_response(OK_RESPONSE)
    assert ok.ok and ok.bill_pk == "1001A1100000003I85WJ" and ok.code == "1"
    bad = svc.parse_response(APPROVED_RESPONSE)
    assert not bad.ok and bad.code == "-32000" and "单据已经审核" in bad.message
    assert "开始处理" not in bad.message
    junk = svc.parse_response("<html>502</html")
    assert not junk.ok and junk.bill_pk is None


# ── fixtures for plan / push ─────────────────────────────────────────────────

async def _ap(db, *, vendor_id, route="po", amount="639.50", tax="83.14", currency="CAD",
              number="AP-20261015-0007", inv_no="120542", tax_lines=(("HST_ON", "639.50", "83.14"),),
              invoice_date=date(2026, 10, 15), due=date(2026, 11, 14), status="posted",
              accrual=True, po_titles=(("PO-633-2610-01", "Globe valve for Daearator"),),
              cc="MOH-0106-E01", dept=("0106", "Engineering"), budget="CRM0090201"):
    dep = Department(code=dept[0], name=dept[1])
    db.add(dep)
    await db.flush()
    ccrow = CostCenter(code=cc, name=cc, department_id=dep.id)
    db.add(ccrow)
    await db.flush()
    inv = Invoice(internal_ref="INV-1", vendor_invoice_number=inv_no, vendor_id=vendor_id,
                  vendor_name="Process and Steam", amount=Decimal(amount), tax_amount=Decimal(tax),
                  total_amount=Decimal(amount) + Decimal(tax), currency=currency,
                  invoice_date=invoice_date, due_date=due, status="matched", match_route=route)
    db.add(inv)
    await db.flush()
    pos = []
    for n, t in po_titles:
        po = PurchaseOrder(number=n, title=t)
        db.add(po)
        await db.flush()
        pos.append(po)
        db.add(PurchaseRequest(po_id=po.id, cost_center_id=ccrow.id, budget_code=budget,
                               department_name=dept[1], created_by=uuid.uuid4()))
    if len(pos) == 1:
        inv.po_id = pos[0].id
    elif pos:
        for i, po in enumerate(pos):
            db.add(InvoicePoAllocation(invoice_id=inv.id, po_id=po.id,
                                       allocated_amount=Decimal("100") * (i + 1),
                                       allocated_tax=Decimal("0")))
    ap = ApInvoice(ap_invoice_number=number, source="epms", source_invoice_id=inv.id,
                   vendor_id=vendor_id, vendor_name="Process and Steam", vendor_invoice_number=inv_no,
                   amount=Decimal(amount), tax_amount=Decimal(tax),
                   total_amount=Decimal(amount) + Decimal(tax), currency=currency,
                   invoice_date=invoice_date, due_date=due, status=status)
    db.add(ap)
    await db.flush()
    for i, (code, base, tx) in enumerate(tax_lines):
        db.add(ApInvoiceTaxLine(invoice_id=ap.id, line_no=i + 1, tax_code=code,
                                taxable_base=Decimal(base), tax_amount=Decimal(tx)))
    if accrual:
        await emit_event(db, source_service="finance", source_doc_type="ap_invoice",
                         source_doc_id=ap.id, source_doc_number=number, event_type="accrual",
                         prepared_by=uuid.uuid4(),
                         lines=[{"line_role": "purchase_expense", "account_code": "510102",
                                 "debit": ap.total_amount, "currency": "CAD"},
                                {"line_role": "accounts_payable", "account_code": "220201",
                                 "credit": ap.total_amount, "currency": "CAD"}])
    await db.flush()
    await db.refresh(ap)
    return ap, inv, ccrow, dep


async def _usd_rate(db, d=date(2026, 10, 9), rate="1.4222"):
    db.add(ExchangeRate(from_currency="USD", to_currency="CAD", rate=Decimal(rate),
                        effective_date=d, source="boc"))
    await db.flush()


# ── plan ─────────────────────────────────────────────────────────────────────

async def test_plan_single_po(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid)
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    assert plan.summary == "PO-633-2610-01 Globe valve for Daearator"
    assert plan.supplier_code == "0000522" and plan.rate == 1 and plan.currency == "CAD"
    assert plan.external_id == "UNIOPS-AP-20261015-0007" and plan.invoice_no == "120542"
    [row] = plan.rows
    assert (row.notax, row.tax, row.nc_tax_code, row.tax_rate) == (
        Decimal("639.50"), Decimal("83.14"), "001", Decimal("13"))
    assert row.account == "510102"
    assert (row.dims.dept_code, row.dims.revexp, row.dims.cc_nc, row.dims.def5_code) == (
        "0106", "CRM0090201", "ENG", "E04")


async def test_plan_multi_po_lists_numbers_only(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid, po_titles=(("PO-2", "Bolts"), ("PO-1", "Nuts")))
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    assert plan.summary == "PO-1 / PO-2"


async def test_plan_refuses_a_po_imported_from_nc(db_session):
    """NC-sourced POs (raw material / packaging) get their payable in NC from the
    purchase invoice (D1); an F1 on top would book it twice."""
    vid = _vendor()
    ap, inv, *_ = await _ap(db_session, vendor_id=vid)
    po = (await db_session.execute(select(PurchaseOrder).where(PurchaseOrder.id == inv.po_id))).scalar_one()
    po.source = "nc"
    await db_session.flush()
    with pytest.raises(PushRefused, match="came from NC"):
        await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))


async def test_plan_agreement(db_session):
    vid = _vendor()
    ap, inv, cc, dep = await _ap(db_session, vendor_id=vid, route="agreement", po_titles=())
    agr = PurchaseAgreement(number="AGR-202607-0003", title="Pest control 2026",
                            budget_code="CRM01114", cost_center_id=cc.id, department_id=dep.id)
    db_session.add(agr)
    await db_session.flush()
    inv.agreement_id = agr.id
    await db_session.flush()
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    assert plan.summary == "AGR-202607-0003 Pest control 2026"
    assert plan.rows[0].dims.revexp == "CRM01114" and plan.rows[0].account == "510101"


async def test_plan_direct_uses_the_pa(db_session):
    vid = _vendor()
    ap, inv, cc, dep = await _ap(db_session, vendor_id=vid, route="direct", po_titles=())
    db_session.add(PaymentApplication(pa_number="PA-20261020-0004", title="Hydro October",
                                      pa_type="regular", status="approved", source="direct",
                                      vendor_id=vid, vendor_name="Hydro", invoice_ids=[str(inv.id)],
                                      payment_amount=ap.total_amount, currency="CAD",
                                      cost_center_id=cc.id, department_id=dep.id,
                                      budget_account_code="CRM00301", created_by=uuid.uuid4()))
    await db_session.flush()
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    assert plan.summary == "PA-20261020-0004 Hydro October"
    assert plan.rows[0].account == "510101"           # CRM00301 is a fixed MOH item


async def test_plan_freight_one_row_per_cost_centre(db_session):
    vid = _vendor()
    ap, inv, cc, dep = await _ap(db_session, vendor_id=vid, route="freight", po_titles=(),
                                 amount="150.00", tax="13.00", inv_no="UPS-77",
                                 tax_lines=(("HST_ON", "100.00", "13.00"),))
    cc2 = CostCenter(code="GA-0101", name="HR", department_id=dep.id)
    db_session.add(cc2)
    await db_session.flush()
    db_session.add_all([
        FreightInvoiceLine(invoice_id=inv.id, cost_center_id=cc.id, amount=Decimal("100.00"),
                           tax_amount=Decimal("13.00")),
        FreightInvoiceLine(invoice_id=inv.id, cost_center_id=cc2.id, amount=Decimal("50.00"),
                           tax_amount=Decimal("0")),           # cross-border, zero-rated
    ])
    await db_session.flush()
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    assert plan.summary == "Freight UPS-77"
    got = sorted((str(r.notax), str(r.tax), r.nc_tax_code, r.dims.revexp) for r in plan.rows)
    assert got == [("100.00", "13.00", "001", "CRM0150102"), ("50.00", "0.00", "006", "CRM0150102")]


async def test_plan_mixed_rate_without_tax_lines_infers_the_taxed_part(db_session):
    """Culligan 64069TP (user, 2026-10-30): 0.98 tax on 197.50 and no tax line.
    The taxed part is inferred at 13% (7.54), the rest is its own zero-rated row."""
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid, amount="197.50", tax="0.98", tax_lines=())
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    got = [(str(r.notax), str(r.tax), r.nc_tax_code, str(r.tax_rate)) for r in plan.rows]
    assert got == [("7.54", "0.98", "001", "13"), ("189.96", "0", "006", "0")]
    assert plan.total == ap.total_amount


async def test_plan_without_tax_lines_unfit_rate_goes_as_hst_with_a_warning(db_session):
    """Ecolab-style 14.54% with no tax line (user, 2026-10-30): not split at 15%;
    pushed as HST at its own rate, and the clerk is told to fix the tax in NC."""
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid, amount="2346.54", tax="341.29", tax_lines=())
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    got = [(str(r.notax), str(r.tax), r.nc_tax_code, str(r.tax_rate)) for r in plan.rows]
    assert got == [("2346.54", "341.29", "001", "14.54")]
    [w] = plan.warnings
    assert "14.54%" in w and "correct it on the NC bill" in w


async def test_push_unfit_tax_reports_the_warning(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid, amount="2346.54", tax="341.29", tax_lines=())
    uid = await _pusher(db_session)
    [out] = await svc.push_invoices(db_session, [ap.id], uid, lookup=FakeLookup(), client=FakeClient())
    assert out.ok and "correct it on the NC bill" in (out.warning or "")
    [log] = (await db_session.execute(select(NcPushLog))).scalars().all()
    assert "correct it on the NC bill" in log.message


async def test_plan_with_a_fitting_tax_has_no_warning(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid, amount="197.50", tax="0.98", tax_lines=())
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    assert plan.warnings == []


async def test_plan_without_tax_lines_at_a_full_rate_stays_one_row(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid, amount="100.00", tax="13.00", tax_lines=())
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    assert [(str(r.notax), r.nc_tax_code) for r in plan.rows] == [("100.00", "001")]


async def test_plan_mixed_rate_splits_untaxed_portion(db_session):
    """Culligan-style: tax lines cover only part of the invoice; the rest is its
    own zero-rated row so every row carries a single rate (user, 2026-10-27)."""
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid, amount="100.00", tax="10.40",
                       tax_lines=(("HST_ON", "80.00", "10.40"),))
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    got = [(str(r.notax), str(r.tax), r.nc_tax_code, str(r.tax_rate)) for r in plan.rows]
    assert got == [("80.00", "10.40", "001", "13"), ("20.00", "0", "006", "0")]
    assert plan.total == ap.total_amount


async def test_plan_tax_line_without_base_takes_the_pretax_amount(db_session):
    """11 production tax lines carry the tax but a 0 taxable base; they must not
    turn into a tax-only row plus a zero-rated remainder."""
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid, amount="1519.39", tax="197.52",
                       tax_lines=(("HST_ON", "0", "197.52"),))
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    got = [(str(r.notax), str(r.tax), r.nc_tax_code, str(r.tax_rate)) for r in plan.rows]
    assert got == [("1519.39", "197.52", "001", "13")]


async def test_plan_usd_takes_the_invoice_date_rate(db_session):
    vid = _vendor("0000880")
    await _usd_rate(db_session)                         # Friday 9th
    ap, *_ = await _ap(db_session, vendor_id=vid, currency="USD", amount="680.00", tax="0",
                       tax_lines=(), invoice_date=date(2026, 10, 11))   # Sunday
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    assert plan.rate == Decimal("1.4222") and plan.rate_date == date(2026, 10, 9)
    assert plan.rows[0].nc_tax_code == "006"


@pytest.mark.parametrize("kw,needle", [
    ({"status": "draft"}, "AP is draft"),
    ({"accrual": False}, "no accrual"),
    ({"invoice_date": date(2026, 12, 7), "due": date(2026, 11, 8)}, "Check the invoice date"),
    ({"currency": "USD"}, "No USD exchange rate"),
])
async def test_plan_refusals(db_session, kw, needle):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid, **kw)
    with pytest.raises(PushRefused, match=needle):
        await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))


async def test_plan_refuses_vendor_without_erp_id(db_session):
    vid = _vendor(None)
    ap, *_ = await _ap(db_session, vendor_id=vid)
    with pytest.raises(PushRefused, match="ERP ID"):
        await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))


async def test_plan_refuses_a_vendor_that_does_not_exist(db_session):
    """dev1, 2026-10-28: an AP whose vendor_id has no vendors row said "no ERP ID",
    though the vendor master shows the ERP ID — the row it points at is gone."""
    _vendor()                                          # the master exists, just not this row
    ap, *_ = await _ap(db_session, vendor_id=uuid.uuid4())
    with pytest.raises(PushRefused, match="not in the vendor master"):
        await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))


# ── XML ──────────────────────────────────────────────────────────────────────

async def test_render_xml(db_session):
    vid = _vendor("0000880")
    await _usd_rate(db_session)
    ap, *_ = await _ap(db_session, vendor_id=vid, currency="USD", amount="680.00", tax="0",
                       tax_lines=(), invoice_date=date(2026, 10, 9))
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    facts = FakeLookup(country="TW").facts(plan.supplier_code, {"ENG", "E04"}, "000940")
    xml = svc.render_xml(plan, facts=facts, maker="Raghav",
                         now=datetime(2026, 10, 27, 19, 0, tzinfo=timezone.utc))
    root = ET.fromstring(xml)
    assert root.get("sender") == "uniops" and root.get("replace") == "Y"
    assert root.find("bill").get("id") == "UNIOPS-AP-20261015-0007"
    head = root.find("bill/billhead")
    assert head.findtext("billmaker") == "Raghav" and head.findtext("pk_tradetype") == "F1-Cxx-017"
    assert head.findtext("money") == "680.00" and head.findtext("local_money") == "967.10"
    assert head.find("pk_busitype") is None                 # translation trap
    [item] = head.findall("bodys/item")
    assert item.find("pk_fiorg") is None                    # translation trap
    assert item.findtext("rate") == "1.4222" and item.findtext("local_money_cr") == "967.10"
    assert item.findtext("buysellflag") == "4"              # supplier country, not currency
    assert item.findtext("def5") == "1001A1100000003CGYIR"  # PK, not the code
    assert item.findtext("costcenter") == "ENG"
    assert item.findtext("taxcodeid") == "006" and item.findtext("taxrate") == "0"
    assert item.findtext("scomment") == "PO-633-2610-01 Globe valve for Daearator"
    assert item.findtext("def16") == item.findtext("scomment")
    assert head.findtext("billdate") == "2026-10-27 15:00:00"   # Toronto time


async def test_render_foreign_supplier_charging_tax_is_domestic(db_session):
    vid = _vendor("0000183")
    await _usd_rate(db_session, rate="1.42")
    ap, *_ = await _ap(db_session, vendor_id=vid, currency="USD", amount="66.69", tax="8.67",
                       tax_lines=(("HST_ON", "66.69", "8.67"),), invoice_date=date(2026, 10, 9))
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    xml = svc.render_xml(plan, facts=FakeLookup(country="US").facts("x", {"ENG", "E04"}, "1"),
                         maker="Raghav", now=datetime.now(timezone.utc))
    item = ET.fromstring(xml).find("bill/billhead/bodys/item")
    assert item.findtext("buysellflag") == "2"
    assert (item.findtext("local_notax_cr"), item.findtext("local_tax_cr"), item.findtext("local_money_cr")) == (
        "94.70", "12.31", "107.01")


async def test_render_cny_uses_nc_inverse_quotation(db_session):
    """NC quotes CNY as 1 CAD = x CNY (convmode 1) and checks local with zero
    tolerance; BoC gives CAD per CNY."""
    vid = _vendor("0000777")
    db_session.add(ExchangeRate(from_currency="CNY", to_currency="CAD", rate=Decimal("0.2059"),
                                effective_date=date(2026, 8, 24), source="boc"))
    await db_session.flush()
    ap, *_ = await _ap(db_session, vendor_id=vid, currency="RMB", amount="54400.00", tax="0",
                       tax_lines=(), invoice_date=date(2026, 8, 24), due=date(2026, 9, 30))
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    assert plan.currency == "CNY"
    xml = svc.render_xml(plan, facts=FakeLookup(country="CN").facts("x", {"ENG", "E04"}, "1", "CNY"),
                         maker="Raghav", now=datetime.now(timezone.utc))
    head = ET.fromstring(xml).find("bill/billhead")
    item = head.find("bodys/item")
    assert head.findtext("pk_currtype") == "CNY"
    assert item.findtext("rate") == "4.85672657"
    assert item.findtext("local_money_cr") == "11200.96" == head.findtext("local_money")
    assert item.findtext("buysellflag") == "4"


async def test_render_refuses_supplier_missing_in_nc(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid)
    plan = await svc.plan_bill(db_session, ap, today=date(2026, 10, 27))
    facts = NcFacts(None, {"ENG": "x", "E04": "y"}, ["Raghav"])
    with pytest.raises(PushRefused, match="not in NC"):
        svc.render_xml(plan, facts=facts, maker="Raghav", now=datetime.now(timezone.utc))


def test_maker_resolution():
    assert svc.maker_code(NcFacts("CA", {}, ["Raghav"]), "000940") == "Raghav"
    for users, person, needle in (([], "111002", "does not resolve"),
                                  (["A", "B"], "000076", "several NC users"),
                                  (["A"], None, "no ERP ID")):
        with pytest.raises(PushRefused, match=needle):
            svc.maker_code(NcFacts("CA", {}, users), person)


def test_summary_clipped_to_nc_limits():
    s = "PO-1 " + "x" * 300
    assert len(svc.clip(s, svc.SCOMMENT_MAX)) <= 250 and len(svc.clip(s, svc.DEF16_MAX)) <= 101


# ── orchestration ────────────────────────────────────────────────────────────

async def _pusher(db, code="000940"):
    u = User(email=f"{uuid.uuid4()}@x", full_name="Raghav Monga", erp_person_code=code)
    db.add(u)
    await db.flush()
    return u.id


async def test_push_success_stamps_ap_and_logs(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid)
    uid = await _pusher(db_session)
    lookup, client = FakeLookup(), FakeClient()
    [out] = await svc.push_invoices(db_session, [ap.id], uid, lookup=lookup, client=client)
    assert out.ok and out.nc_bill_no == "D12026102800139886"
    assert lookup.calls == [("0000522", {"ENG", "E04"}, "000940")]
    assert ap.nc_push_status == "pushed" and ap.nc_bill_pk == "1001A1100000003I85WJ"
    assert ap.nc_pushed_by == uid and ap.nc_push_error is None
    [log] = (await db_session.execute(select(NcPushLog))).scalars().all()
    assert log.ok and "<billmaker>Raghav</billmaker>" in log.request_xml and "successful" in log.response_xml


async def test_push_after_approval_keeps_the_bill(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid)
    uid = await _pusher(db_session)
    await svc.push_invoices(db_session, [ap.id], uid, lookup=FakeLookup(), client=FakeClient())
    [out] = await svc.push_invoices(db_session, [ap.id], uid, lookup=FakeLookup(),
                                    client=FakeClient(APPROVED_RESPONSE))
    assert not out.ok and out.message.startswith("Already approved in NC")
    # the failed re-push does not hide the bill that is in NC
    assert ap.nc_push_status == "pushed" and ap.nc_bill_no == "D12026102800139886"
    assert "审核" in ap.nc_push_error


async def test_push_refusal_sends_nothing(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid)
    uid = await _pusher(db_session, code=None)
    client = FakeClient()
    [out] = await svc.push_invoices(db_session, [ap.id], uid, lookup=FakeLookup(), client=client)
    assert not out.ok and "ERP ID" in out.message and client.sent == []
    assert ap.nc_push_status == "failed"
    assert (await db_session.execute(select(NcPushLog))).scalars().all() == []


def _nc_bill(bill_no, inv_norm, sup, *, src=1, status=1, approve=1, raw=None):
    bid = uuid.uuid4()
    _exec("insert into nc_ap_bills (id, nc_pk, bill_no, trade_type, bill_status, approve_status, "
          "supplier_code, currency, money, src_syscode, nc_ts, synced_at, created_at, updated_at) "
          "values (%s,%s,%s,'F1-Cxx-017',%s,%s,%s,'CAD',722.64,%s,'2026-10-20 00:00:00',now(),now(),now())",
          (str(bid), f"PK{bill_no}", bill_no, status, approve, sup, src))
    _exec("insert into nc_ap_bill_lines (id, bill_id, nc_pk, bill_no, money_cr, money_bal, invoice_no, "
          "invoice_no_norm, supplier_code, nc_ts, synced_at) values "
          "(%s,%s,%s,%s,722.64,722.64,%s,%s,%s,'2026-10-20 00:00:00',now())",
          (str(uuid.uuid4()), str(bid), f"L{bill_no}", bill_no, raw or inv_norm, inv_norm, sup))


async def test_push_refuses_an_ap_already_entered_by_hand(db_session):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid)
    _nc_bill("D12026101900139786", "120542", "0000522")
    uid = await _pusher(db_session)
    client = FakeClient()
    [out] = await svc.push_invoices(db_session, [ap.id], uid, lookup=FakeLookup(), client=client)
    assert not out.ok and "D12026101900139786" in out.message and client.sent == []


async def test_list_shows_nc_ap_doc_and_filters(client, db_session):
    vid = _vendor()
    a1, *_ = await _ap(db_session, vendor_id=vid, number="AP-1", inv_no="120542")
    v2 = _vendor("0000880")
    a2, *_ = await _ap(db_session, vendor_id=v2, number="AP-2", inv_no="NEW 1",
                       cc="MOH-0101", dept=("0101", "HSE"))
    v3 = _vendor("0000999")
    a3, *_ = await _ap(db_session, vendor_id=v3, number="AP-3", inv_no="Q-9",
                       cc="GA-0105", dept=("0105", "QA"))
    _nc_bill("D1-HAND", "120542", "0000522", status=-1, approve=3)     # saved by hand
    _nc_bill("D1-OLD", "Q-9", "0000999", src=17)                         # our replaced bill
    a2.nc_bill_no = "D1-PUSHED"
    await db_session.flush()

    r = await client.get("/finance/v1/ap/invoices", headers=_h())
    assert r.status_code == 200, r.text
    docs = {i["ap_invoice_number"]: i["nc_docs"] for i in r.json()["items"]}
    assert docs["AP-1"] == [{"bill_no": "D1-HAND", "state": "saved", "pushed": False,
                             "bill_date": None, "money": "722.64", "currency": "CAD",
                             "hyphen_match": False, "nc_invoice_no": "120542"}]
    assert [d["bill_no"] for d in docs["AP-2"]] == ["D1-PUSHED"] and docs["AP-2"][0]["state"] == "pushed"
    assert docs["AP-3"] == []                         # a replaced bill of ours is not shown

    r = await client.get("/finance/v1/ap/invoices", params={"in_nc": "false"}, headers=_h())
    assert [i["ap_invoice_number"] for i in r.json()["items"]] == ["AP-3"]
    r = await client.get("/finance/v1/ap/invoices", params={"in_nc": "true"}, headers=_h())
    assert sorted(i["ap_invoice_number"] for i in r.json()["items"]) == ["AP-1", "AP-2"]

    # Sorting the column counts hand-entered bills too, not only our pushes;
    # an AP with no NC doc sinks to the bottom in both directions.
    for d, want in (("asc", ["AP-1", "AP-2", "AP-3"]), ("desc", ["AP-2", "AP-1", "AP-3"])):
        r = await client.get("/finance/v1/ap/invoices", headers=_h(),
                             params={"sort": "nc_bill_no", "dir": d})
        assert r.status_code == 200, r.text
        assert [i["ap_invoice_number"] for i in r.json()["items"]] == want, d


async def test_list_marks_nc_flow_aps_and_hides_them(client, db_session):
    """User, 2026-10-28: an AP on a PO imported from NC (type 1) is booked by NC
    from its own purchase invoice — the list says so, offers no push, and
    "Hide in NC" hides it. Holds for a PO reached through an allocation too."""
    vid = _vendor()
    a1, inv1, *_ = await _ap(db_session, vendor_id=vid, number="AP-NC", inv_no="RM-1")
    a2, *_ = await _ap(db_session, vendor_id=vid, number="AP-EXP", inv_no="EX-1",
                       po_titles=(("PO-633-2610-09", "Expense"),))
    po = (await db_session.execute(select(PurchaseOrder).where(PurchaseOrder.id == inv1.po_id))).scalar_one()
    po.source = "nc"
    await db_session.flush()

    r = await client.get("/finance/v1/ap/invoices", headers=_h())
    flow = {i["ap_invoice_number"]: i["nc_flow"] for i in r.json()["items"]}
    assert flow == {"AP-NC": True, "AP-EXP": False}
    r = await client.get("/finance/v1/ap/invoices", params={"in_nc": "false"}, headers=_h())
    assert [i["ap_invoice_number"] for i in r.json()["items"]] == ["AP-EXP"]

    # reached only through a multi-PO allocation
    inv1.po_id = None
    db_session.add(InvoicePoAllocation(invoice_id=inv1.id, po_id=po.id, allocated_amount=Decimal("1"), allocated_tax=Decimal("0")))
    await db_session.flush()
    r = await client.get("/finance/v1/ap/invoices", headers=_h())
    assert {i["ap_invoice_number"]: i["nc_flow"] for i in r.json()["items"]}["AP-NC"] is True


async def test_list_gl_code_is_the_po_budget_code(client, db_session):
    """User, 2026-10-29: the AP list's GL Code column is the budget account code of
    the invoice's PO — every PO of a multi-PO invoice — and empty with no PO."""
    vid = _vendor()
    _, inv1, *_ = await _ap(db_session, vendor_id=vid, number="AP-ONE", inv_no="G-1")
    _, inv2, *_ = await _ap(db_session, vendor_id=vid, number="AP-MULTI", inv_no="G-2",
                            po_titles=(("PO-B", "b"), ("PO-A", "a"), ("PO-A2", "a2")))
    await _ap(db_session, vendor_id=vid, number="AP-NOPO", inv_no="G-3", route="agreement",
              po_titles=())
    codes = {"PO-633-2610-01": "CRM0090201", "PO-B": "CRM00503", "PO-A": "CRM00101",
             "PO-A2": "CRM00101"}
    for po in (await db_session.execute(select(PurchaseOrder))).scalars():
        po.budget_code = codes[po.number]
    await db_session.flush()

    r = await client.get("/finance/v1/ap/invoices", headers=_h())
    assert r.status_code == 200, r.text
    gl = {i["ap_invoice_number"]: i["gl_code"] for i in r.json()["items"]}
    assert gl == {"AP-ONE": "CRM0090201", "AP-MULTI": "CRM00101, CRM00503", "AP-NOPO": None}


async def test_list_gl_code_without_po_follows_the_nc_push(client, db_session):
    """User, 2026-10-30: a courier / Direct / agreement invoice has no PO, yet its
    budget account is known — show the one the NC push uses."""
    vid = _vendor()
    await _ap(db_session, vendor_id=vid, number="AP-FR", inv_no="UPS-1", route="freight",
              po_titles=())
    _, inv_d, cc, dep = await _ap(db_session, vendor_id=vid, number="AP-DIR", inv_no="D-1",
                                  route="direct", po_titles=())
    db_session.add(PaymentApplication(pa_number="PA-20261020-0004", title="Hydro October",
                                      pa_type="regular", status="approved", source="direct",
                                      vendor_id=vid, vendor_name="Hydro", invoice_ids=[str(inv_d.id)],
                                      payment_amount=Decimal("1"), currency="CAD",
                                      cost_center_id=cc.id, department_id=dep.id,
                                      budget_account_code="CRM00301-Hydro", created_by=uuid.uuid4()))
    _, inv_a, *_ = await _ap(db_session, vendor_id=vid, number="AP-AGR", inv_no="A-1",
                             route="agreement", po_titles=())
    agr = PurchaseAgreement(number="AGR-202607-0003", title="Pest control 2026",
                            budget_code="CRM01114")
    db_session.add(agr)
    await db_session.flush()
    inv_a.agreement_id = agr.id
    await _ap(db_session, vendor_id=vid, number="AP-DIR-NOPA", inv_no="D-2", route="direct",
              po_titles=())
    await db_session.flush()

    r = await client.get("/finance/v1/ap/invoices", headers=_h())
    assert r.status_code == 200, r.text
    gl = {i["ap_invoice_number"]: i["gl_code"] for i in r.json()["items"]}
    assert gl == {"AP-FR": "CRM0150102", "AP-DIR": "CRM00301", "AP-AGR": "CRM01114",
                  "AP-DIR-NOPA": None}


async def test_match_ignores_hyphens_and_says_so(client, db_session):
    """User, 2026-10-28: "IN-22-1014797" in UniOps is "IN-221014797" in NC — match it,
    but flag that the hyphens were dropped; an exact match carries no flag."""
    vid = _vendor("0000119")
    await _ap(db_session, vendor_id=vid, number="AP-H", inv_no="IN-22-1014797")
    await _ap(db_session, vendor_id=vid, number="AP-E", inv_no="in-22 1014915",
              po_titles=(("PO-633-2610-02", "Second"),))
    v2 = _vendor("0000777")
    await _ap(db_session, vendor_id=v2, number="AP-X", inv_no="IN-22-1014797",   # other supplier
              po_titles=(("PO-633-2610-03", "Third"),))
    _nc_bill("D1-H", "IN-221014797", "0000119")
    _nc_bill("D1-E", "IN-221014915", "0000119", raw="IN-22 1014915")
    await db_session.flush()

    r = await client.get("/finance/v1/ap/invoices", headers=_h())
    docs = {i["ap_invoice_number"]: i["nc_docs"] for i in r.json()["items"]}
    assert [(d["bill_no"], d["hyphen_match"], d["nc_invoice_no"]) for d in docs["AP-H"]] == [
        ("D1-H", True, "IN-221014797")]
    assert [(d["bill_no"], d["hyphen_match"]) for d in docs["AP-E"]] == [("D1-E", False)]
    assert docs["AP-X"] == []
    r = await client.get("/finance/v1/ap/invoices", params={"in_nc": "false"}, headers=_h())
    assert [i["ap_invoice_number"] for i in r.json()["items"]] == ["AP-X"]


async def test_push_refusal_names_a_hyphen_match(db_session):
    vid = _vendor("0000119")
    ap, *_ = await _ap(db_session, vendor_id=vid, inv_no="IN-22-1014797")
    _nc_bill("D1-H", "IN-221014797", "0000119")
    uid = await _pusher(db_session)
    client = FakeClient()
    [out] = await svc.push_invoices(db_session, [ap.id], uid, lookup=FakeLookup(), client=client)
    assert not out.ok and client.sent == []
    assert "D1-H" in out.message and "IN-221014797" in out.message and "ignoring hyphens" in out.message


def test_match_invoice_no():
    assert svc.match_invoice_no(" in-22 1014797 ") == "IN221014797"
    assert svc.match_invoice_no("--") is None and svc.match_invoice_no(None) is None


async def test_push_endpoint_gates(client, db_session, monkeypatch):
    body = {"ap_ids": [str(uuid.uuid4())]}
    monkeypatch.setattr(app_settings, "nc_xchange_url", None)
    r = await client.post("/finance/v1/ap/nc-push", json=body, headers=_h("requester"))
    assert r.status_code == 403
    r = await client.post("/finance/v1/ap/nc-push", json=body, headers=_h())
    assert r.status_code == 503
    assert (await client.post("/finance/v1/ap/nc-export", json=body, headers=_h())).status_code in (404, 405)


async def test_push_endpoint_end_to_end(client, db_session, monkeypatch):
    vid = _vendor()
    ap, *_ = await _ap(db_session, vendor_id=vid)
    uid = await _pusher(db_session)
    for k, v in (("nc_xchange_url", "http://nc/service/XChangeServlet"), ("nc_host", "h"),
                 ("nc_service", "s"), ("nc_user", "u"), ("nc_password", "p")):
        monkeypatch.setattr(app_settings, k, v)
    monkeypatch.setattr(svc, "NcLookup", FakeLookup)
    monkeypatch.setattr(svc, "XChangeClient", FakeClient)
    r = await client.post("/finance/v1/ap/nc-push", json={"ap_ids": [str(ap.id)]}, headers=_h(sub=uid))
    assert r.status_code == 200, r.text
    [res] = r.json()["results"]
    assert res["ok"] and res["nc_bill_no"] == "D12026102800139886"
    log = (await client.get(f"/finance/v1/ap/invoices/{ap.id}/nc-push-log", headers=_h())).json()
    assert len(log) == 1 and log[0]["ok"] is True
