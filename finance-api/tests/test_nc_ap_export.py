"""NC AP export (parallel-run) — batches, assembly, xlsx, API."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from app.services.posting import emit_event


async def _mk_ap(db, *, source="epms", amount="100.00", tax="13.00",
                 number="AP-2026-0100", vendor="ACME Ltd", src_id=None):
    from app.models.ap_invoice import ApInvoice
    inv = ApInvoice(ap_invoice_number=number, source=source,
                    source_invoice_id=src_id or uuid.uuid4(),
                    vendor_name=vendor, amount=Decimal(amount),
                    tax_amount=Decimal(tax),
                    total_amount=Decimal(amount) + Decimal(tax),
                    currency="CAD", invoice_date=date(2026, 7, 10), status="posted")
    db.add(inv)
    await db.flush()
    return inv


async def _mk_accrual(db, ap, account="510102"):
    await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=ap.id, source_doc_number=ap.ap_invoice_number,
        event_type="accrual", prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": account,
                "debit": ap.amount, "currency": "CAD"},
               {"line_role": "sales_tax", "account_code": "1180",
                "debit": ap.tax_amount, "tax_code": "HST_ON", "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "220201",
                "credit": ap.total_amount, "currency": "CAD"}])


def test_account_full_path():
    from types import SimpleNamespace as NS
    from app.services.nc_ap_export import account_full_path
    coa = {"5101": NS(code="5101", name="Manufacturing Overhead", parent_code=None),
           "510102": NS(code="510102", name="Repairs", parent_code="5101")}
    assert account_full_path(coa, "510102") == "510102\\Manufacturing Overhead\\Repairs"
    assert account_full_path(coa, "9999") == "9999"


async def test_build_rows_epms_allocations(db_session):
    from app.models.mirrors import CostCenter, InvoicePoAllocation, PurchaseRequest, BudgetAccount
    from app.services.nc_ap_export import build_export_rows
    src = uuid.uuid4()
    po1, po2 = uuid.uuid4(), uuid.uuid4()
    cc = uuid.uuid4()
    db_session.add(CostCenter(id=cc, code="MOH-0106-E01", name="Engineering CC"))
    db_session.add(BudgetAccount(id=uuid.uuid4(), code="CRM004", name="Depreciation", is_active=True))
    db_session.add_all([
        PurchaseRequest(id=uuid.uuid4(), po_id=po1, cost_center_id=cc,
                        budget_code="CRM004", department_name="Engineering",
                        created_by=uuid.uuid4()),
        PurchaseRequest(id=uuid.uuid4(), po_id=po2, cost_center_id=None,
                        budget_code=None, department_name="Maintenance",
                        created_by=uuid.uuid4()),
        InvoicePoAllocation(id=uuid.uuid4(), invoice_id=src, po_id=po1,
                            allocated_amount=Decimal("60.00"), allocated_tax=Decimal("7.80")),
        InvoicePoAllocation(id=uuid.uuid4(), invoice_id=src, po_id=po2,
                            allocated_amount=Decimal("40.00"), allocated_tax=Decimal("5.20")),
    ])
    await db_session.flush()
    ap = await _mk_ap(db_session, src_id=src)
    await _mk_accrual(db_session, ap)

    heads, bodies, errors = await build_export_rows(db_session, [ap.id])
    assert errors == []
    assert len(heads) == 1 and len(bodies) == 2
    h = heads[0]
    assert h["seq"] == 0 and h["billno"] == "AP-2026-0100"
    assert h["department"] == "Engineering"          # first body row's dept
    b1 = next(b for b in bodies if b["notax"] == "60.00")
    assert b1["cost_center"] == "Engineering CC"
    assert b1["revexp"] == "Depreciation"
    assert b1["tax"] == "7.80" and b1["money"] == "67.80"
    assert b1["account_path"].startswith("510102")
    assert b1["tax_code"] == "001" and b1["tax_rate"] == "13.00"
    b2 = next(b for b in bodies if b["notax"] == "40.00")
    assert b2["cost_center"] == "" and b2["revexp"] == ""
    assert b2["department"] == "Maintenance"


async def test_build_rows_oa_pa_chain(db_session):
    from app.models.mirrors import CostCenter, ExpenseInvoice, User, BudgetAccount
    from app.models.pa import PaymentApplication
    from app.services.nc_ap_export import build_export_rows
    src, pa_id, cc, creator = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(CostCenter(id=cc, code="GA-0100", name="Admin CC"))
    db_session.add(BudgetAccount(id=uuid.uuid4(), code="CRM010", name="Office Supplies", is_active=True))
    db_session.add(User(id=creator, email="a@x.com", full_name="Alice Wong"))
    db_session.add(ExpenseInvoice(id=src, pa_id=pa_id))
    db_session.add(PaymentApplication(
        id=pa_id, pa_number="PA-1", title="t", pa_type="PA-DIR", status="paid",
        vendor_id=uuid.uuid4(), vendor_name="ACME Ltd",
        payment_amount=Decimal("113.00"), currency="CAD",
        cost_center_id=cc, budget_account_code="CRM010", created_by=creator))
    await db_session.flush()
    ap = await _mk_ap(db_session, source="oa", number="AP-2026-0101", src_id=src)
    await _mk_accrual(db_session, ap)

    heads, bodies, errors = await build_export_rows(db_session, [ap.id])
    assert errors == [] and len(bodies) == 1
    b = bodies[0]
    assert b["cost_center"] == "Admin CC" and b["revexp"] == "Office Supplies"
    assert b["employee"] == "Alice Wong"
    assert heads[0]["employee"] == "Alice Wong"


async def test_build_rows_errors(db_session):
    from app.services.nc_ap_export import build_export_rows
    ap_draft = await _mk_ap(db_session, number="AP-2026-0102")
    ap_draft.status = "draft"
    ap_noacc = await _mk_ap(db_session, number="AP-2026-0103")
    await db_session.flush()
    heads, bodies, errors = await build_export_rows(db_session, [ap_draft.id, ap_noacc.id])
    assert heads == [] and bodies == []
    reasons = {e["ap_number"]: e["reason"] for e in errors}
    assert reasons["AP-2026-0102"] == "status draft"
    assert reasons["AP-2026-0103"] == "no accrual posting"


async def test_export_batch_and_ap_columns(db_session):
    from app.models.ap_invoice import ApInvoice
    from app.models.nc_export import NcExportBatch
    b = NcExportBatch(exported_by=uuid.uuid4(),
                      exported_at=datetime.now(timezone.utc), ap_count=2,
                      filename="NC-AP-x.xlsx")
    db_session.add(b)
    inv = ApInvoice(ap_invoice_number="AP-2026-0001", source="epms",
                    source_invoice_id=uuid.uuid4(), amount=Decimal("100"),
                    tax_amount=Decimal("13"), total_amount=Decimal("113"),
                    currency="CAD", invoice_date=date(2026, 7, 1), status="posted")
    db_session.add(inv)
    await db_session.flush()
    inv.nc_exported_at = datetime.now(timezone.utc)
    inv.nc_export_batch_id = b.id
    await db_session.flush()
    got = (await db_session.execute(select(ApInvoice).where(
        ApInvoice.id == inv.id))).scalar_one()
    assert got.nc_export_batch_id == b.id


async def test_new_mirror_subsets_readable(db_session):
    from app.models.mirrors import ExpenseInvoice, InvoicePoAllocation, PurchaseRequest
    po = uuid.uuid4()
    db_session.add(PurchaseRequest(id=uuid.uuid4(), po_id=po,
                                   cost_center_id=uuid.uuid4(), budget_code="CRM004",
                                   department_name="Engineering", created_by=uuid.uuid4()))
    db_session.add(InvoicePoAllocation(id=uuid.uuid4(), invoice_id=uuid.uuid4(),
                                       po_id=po, allocated_amount=Decimal("50"),
                                       allocated_tax=Decimal("6.50")))
    db_session.add(ExpenseInvoice(id=uuid.uuid4(), pa_id=uuid.uuid4()))
    await db_session.flush()
    got = (await db_session.execute(select(PurchaseRequest).where(
        PurchaseRequest.po_id == po))).scalar_one()
    assert got.department_name == "Engineering"


def test_write_xlsx_structure(tmp_path):
    import openpyxl
    from app.services.nc_ap_export import write_xlsx
    heads = [{"seq": 0, "billno": "AP-1", "ap_type": "Payable of Expense",
              "busi_process": "选择付款", "billdate": "2026-07-10",
              "busidate": "2026-07-10", "obj_type": "Supplier", "supplier": "ACME",
              "department": "Engineering", "employee": "", "revexp": "Depreciation",
              "currency": "CAD", "ap_type_code": "F1-Cxx-017", "tax_country": "Canada"},
             {"seq": 1, "billno": "AP-2", "ap_type": "Payable of Expense",
              "busi_process": "选择付款", "billdate": "2026-07-11",
              "busidate": "2026-07-11", "obj_type": "Supplier", "supplier": "Beta",
              "department": "", "employee": "Alice Wong", "revexp": "",
              "currency": "CAD", "ap_type_code": "F1-Cxx-017", "tax_country": "Canada"}]
    body_base = {"account_path": "510102\\MOH\\Repairs", "invoice_no": "INV-9",
                 "summary": "ACME PO-1", "pay_term": "net 30 days",
                 "obj_type": "Supplier", "supplier": "ACME", "department": "Engineering",
                 "cost_center": "Engineering CC", "employee": "", "revexp": "Depreciation",
                 "currency": "CAD", "rate": "1", "money": "67.80", "qty": "",
                 "tax_code": "001", "tax_rate": "13.00", "tax_price": "0.00000000",
                 "notax": "60.00", "tax": "7.80", "taxtype": "Tax Excluded",
                 "department2": "Engineering", "buysell": "Domestic Purchases"}
    bodies = [dict(body_base, seq=0), dict(body_base, seq=0, notax="40.00"),
              dict(body_base, seq=1)]
    data = write_xlsx(heads, bodies)
    p = tmp_path / "out.xlsx"
    p.write_bytes(data)
    ws = openpyxl.load_workbook(p)["Sheet1"]
    assert str(ws["A2"].value).startswith('"payablebill_$head')   # tech row kept
    assert ws["A3"].value == "0" and ws["C3"].value == "AP-1"     # head seq + billno
    assert ws["A4"].value == "1" and ws["C4"].value == "AP-2"
    assert ws.cell(5, 1).value in (None, "")                      # blank separator
    assert str(ws["A6"].value).startswith('"bodys')               # body tech row
    assert ws["A7"].value == "0" and ws["B7"].value.startswith("510102")
    assert ws["A9"].value == "1"                                  # 3rd body row -> doc 1
    assert ws["U7"].value == "60.00"                              # notax col
