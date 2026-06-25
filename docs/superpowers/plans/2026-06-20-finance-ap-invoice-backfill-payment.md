# Finance AP Invoice — 存量回填 + 付款回写 Implementation Plan (Plan 4 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) 付款执行器把付款结果回写到 `ap_invoices`（paid/partially_paid），让 AP open-items/aging 反映已付状态；(2) 把存量 EPMS `invoices` + OA `expense_invoices` 回填进 `ap_invoices`。

**Architecture:** 所有服务共用同一物理库（`epms` @ 10.10.50.20），故 `ap_invoices` / `invoices`(epms) / `expense_invoices`(oa) 同库。付款回写：在 finance `crud/payment_execute.py` 的 PA 付款分支，按 `source_invoice_id` 更新 `ap_invoices`（覆盖 pa 与 pa_dir）。回填：finance 一次性脚本读两源表，经 `crud.ap_invoice.upsert` 幂等写入。

**Tech Stack:** finance-api（FastAPI + SQLAlchemy async + alembic）。测试：finance_test（本地 docker `uniops_postgres`，conftest 跑真实 alembic）。

**前置依赖：** Plan 1（ap_invoices + crud.upsert）、Plan 2/3（EPMS/OA 已实时同步新发票）完成。本计划补「付款状态」与「存量数据」两块。

**本轮 Git 策略：** 不提交。Task 末「Checkpoint」只验证留工作区，**不要 git commit/push/branch/stash**。

**环境：** finance-api dev 容器 `--reload` 连生产库 10.10.50.20（无正式数据，可当测试库直接跑回填）。单测走本地 finance_test。

---

## File Structure

- `finance-api/app/crud/payment_execute.py` — 修改：PA 付款时回写 `ap_invoices`（按 source_invoice_id）。
- `finance-api/scripts/backfill_ap_invoices.py` — 新建：存量 EPMS + OA 发票回填（幂等）。
- `finance-api/tests/test_ap_payment_writeback.py` — 新建：付款回写测试。
- `finance-api/tests/test_ap_backfill.py` — 新建：回填映射单测。

类型口径：
- 回填映射纯函数：`_epms_args(inv_row, tax_rows) -> (source, source_invoice_id, payload, tax_lines)`、`_oa_args(exp_row) -> (...)`。
- 状态映射（回填，完整）：EPMS matched/approved→posted、paid→paid、partially_paid→partially_paid、其余→draft；OA used→posted、其余→draft。

---

## Task 1: 付款回写 ap_invoices（payment_execute）

**Files:**
- Modify: `finance-api/app/crud/payment_execute.py`
- Test: `finance-api/tests/test_ap_payment_writeback.py`

当前（约 243-258 行）：`if doc_kind == "pa":` 才循环 `pa.invoice_ids` 翻 mirror `Invoice` 状态——**pa_dir(OA) 被跳过**。新增的 ap_invoices 回写要对 **pa 和 pa_dir 都生效**，按 `source_invoice_id` 匹配（同库、uuid 全局唯一）。

- [ ] **Step 1: 写失败测试**

新建 `finance-api/tests/test_ap_payment_writeback.py`：

```python
"""Paying a PA flips the linked ap_invoices to paid (Plan 4)."""
import uuid
from datetime import date

import pytest


def _ap_payload(**over):
    p = dict(source_ref="INV-1", vendor_id=uuid.uuid4(), vendor_name="V",
             vendor_invoice_number="X", amount="1000.00", tax_amount="0.00",
             total_amount="1000.00", currency="CAD",
             invoice_date=date(2026, 6, 17), due_date=date(2026, 7, 17),
             status="posted", source_status="matched", po_id=None, po_number=None)
    p.update(over)
    return p


@pytest.mark.anyio
async def test_pa_payment_flips_ap_invoice_paid(db_session):
    from app.crud import ap_invoice as ap_crud
    from app.crud import payment_execute as pe
    from app.models.pa import PaymentApplication

    src_id = uuid.uuid4()
    ap = await ap_crud.upsert(db_session, source="epms", source_invoice_id=src_id,
                              payload=_ap_payload(), tax_lines=[])
    pa = PaymentApplication(
        pa_number="PA-WB-1", pa_type="regular", status="approved",
        vendor_id=uuid.uuid4(), vendor_name="V", po_id=uuid.uuid4(),
        payment_amount=1000, currency="CAD", invoice_ids=[str(src_id)],
        created_by=uuid.uuid4(),
    )
    db_session.add(pa)
    await db_session.commit()

    # execute payment for this PA
    from app.crud.payment_execute import execute_payment
    class _Req:
        doc_kind = "pa"; doc_id = pa.id; payment_method = "bank_transfer"
        amount_paid = None; reference = None; notes = None; payment_date = None
    await execute_payment(db_session, _Req(), recorded_by=uuid.uuid4())
    await db_session.commit()

    refreshed = await ap_crud.get(db_session, ap.id)
    assert refreshed.status == "paid"
    assert str(refreshed.paid_amount) == "1000.00"
```

> 注：`PaymentApplication` / `execute_payment` 的真实字段与签名以代码为准——先读 `app/models/pa.py` 与 `app/crud/payment_execute.py` 的 `execute_payment(db, req, recorded_by)` 签名/Req 形状（参照已有 `tests/test_payment_batch.py` 构造 PA 与调用方式），把上面 `_Req`/PA 构造对齐到现有测试的写法（用现成 helper 更稳）。目标断言不变：付款后该 ap_invoice → paid、paid_amount=total。

- [ ] **Step 2: 运行确认失败**

Run: `cd finance-api && python -m pytest tests/test_ap_payment_writeback.py -v`
Expected: FAIL（ap_invoice 仍是 posted——尚未回写）。

- [ ] **Step 3: 实现**

在 `app/crud/payment_execute.py` 顶部 import 区加：`from app.models.ap_invoice import ApInvoice`。

在 PA 分支里、`if doc_kind == "pa":` 那个 for 循环块**之后**（与 `if doc_kind == "pa":` 同级缩进，使其对 pa 与 pa_dir 都执行），`amount = req.amount_paid ...` 那行**之前**，插入：

```python
            # Writeback to finance-owned ap_invoices (EPMS + OA sources): flip
            # status + paid_amount so AP open-items/aging reflect payment (Plan 4).
            ap_paid_status = "partially_paid" if pa.pa_type == "prepayment" else "paid"
            for inv_id_str in pa.invoice_ids:
                try:
                    _aid = uuid.UUID(str(inv_id_str))
                except ValueError:
                    continue
                ap = (await db.execute(
                    select(ApInvoice).where(ApInvoice.source_invoice_id == _aid)
                )).scalar_one_or_none()
                if ap and ap.status in ("posted", "partially_paid"):
                    ap.status = ap_paid_status
                    if ap_paid_status == "paid":
                        ap.paid_amount = ap.total_amount
```

（`select`、`uuid` 已在该模块 import。确认缩进正好让它在「PA 付款处理」块内、`if doc_kind=="pa":` 之外，从而 pa_dir 也走到。）

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `cd finance-api && python -m pytest tests/test_ap_payment_writeback.py tests/test_payment_batch.py tests/test_ap_invoice.py -v`
Expected: 新测过；既有付款/AP 测试不回归。注意：Plan 1 时 `test_payment_batch.py` 曾移除过一条 open-items 断言（因为当时付款没回写 ap_invoices）——现在回写了，如果想恢复该断言可恢复，但非必须；保持绿色即可。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 2: 回填映射纯函数 + 单测

**Files:**
- Create: `finance-api/scripts/backfill_ap_invoices.py`（先放纯映射函数 + 占位 run）
- Test: `finance-api/tests/test_ap_backfill.py`

- [ ] **Step 1: 写失败测试**

新建 `finance-api/tests/test_ap_backfill.py`：

```python
"""Backfill mapping (EPMS invoices + OA expense_invoices → ap_invoices)."""
import uuid
from datetime import date, datetime, timezone

import pytest


def test_epms_args_maps_status_and_fields():
    from scripts.backfill_ap_invoices import _epms_args

    class _Inv:
        id = uuid.uuid4(); internal_ref = "INV-2026-0001"
        vendor_id = uuid.uuid4(); vendor_name = "ULINE"; vendor_invoice_number = "V-1"
        amount = 1000; tax_amount = 130; total_amount = 1130; currency = "CAD"
        invoice_date = date(2026, 6, 17); due_date = date(2026, 7, 17)
        status = "matched"; po_id = uuid.uuid4(); po_number = "PO-1"

    class _Tax:
        line_no = 1; tax_code = "HST_ON"; taxable_amount = 1000; tax_amount = 130; recoverable = True

    source, sid, payload, tax_lines = _epms_args(_Inv(), [_Tax()])
    assert source == "epms" and sid == _Inv.id
    assert payload["status"] == "posted" and payload["source_status"] == "matched"
    assert payload["amount"] == "1000.00" and payload["total_amount"] == "1130.00"
    assert payload["invoice_date"] == date(2026, 6, 17)   # date object (upsert feeds ORM Date)
    assert tax_lines[0]["tax_code"] == "HST_ON" and tax_lines[0]["taxable_base"] == "1000"


def test_epms_args_paid_and_partial():
    from scripts.backfill_ap_invoices import _epms_args
    class _Inv:
        id = uuid.uuid4(); internal_ref = "r"; vendor_id = None; vendor_name = "v"
        vendor_invoice_number = "x"; amount = 1; tax_amount = 0; total_amount = 1
        currency = "CAD"; invoice_date = date(2026, 1, 1); due_date = None
        po_id = None; po_number = None; status = "paid"
    assert _epms_args(_Inv(), [])[2]["status"] == "paid"
    _Inv.status = "partially_paid"
    assert _epms_args(_Inv(), [])[2]["status"] == "partially_paid"
    _Inv.status = "unmatched"
    assert _epms_args(_Inv(), [])[2]["status"] == "draft"


def test_oa_args_maps_status_and_date_fallback():
    from scripts.backfill_ap_invoices import _oa_args

    class _Row:  # row-like (attribute access) from expense_invoices
        id = uuid.uuid4(); invoice_number = "INV-OA-1"
        vendor_id = None; vendor_name = "ULINE"
        invoice_date = None; due_date = None; currency = "CAD"
        subtotal = 1000; tax_amount = 130; total_amount = 1130
        status = "used"; created_at = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

    source, sid, payload, tax_lines = _oa_args(_Row())
    assert source == "oa" and sid == _Row.id
    assert payload["status"] == "posted"
    assert payload["invoice_date"] == date(2026, 6, 20)   # fallback created_at, date object
    assert payload["po_id"] is None and tax_lines == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd finance-api && python -m pytest tests/test_ap_backfill.py -v`
Expected: FAIL（`ModuleNotFoundError: scripts.backfill_ap_invoices`）。

> 确认 `scripts/` 可作为包被 `python -m pytest` 导入：看 finance-api 是否已有 `scripts/__init__.py` 或既有脚本以 `scripts.xxx` 方式导入；若没有，加空 `scripts/__init__.py`。

- [ ] **Step 3: 实现映射 + 脚本骨架**

新建 `finance-api/scripts/backfill_ap_invoices.py`：

```python
"""Backfill ap_invoices from existing EPMS invoices + OA expense_invoices.

All services share one DB, so this reads both source tables directly and upserts
via crud.ap_invoice.upsert (idempotent on (source, source_invoice_id)).

Run:  python -m scripts.backfill_ap_invoices
"""
import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import select, text

from app.crud import ap_invoice as ap_crud
from app.db.session import AsyncSessionLocal
from app.models.mirrors import Invoice, InvoiceTaxLine


def _s(v) -> str:
    return str(Decimal(str(v)).quantize(Decimal("0.01")))


def _epms_status(s: str) -> str:
    if s in ("matched", "approved"):
        return "posted"
    if s == "paid":
        return "paid"
    if s == "partially_paid":
        return "partially_paid"
    return "draft"


def _oa_status(s: str) -> str:
    return "posted" if s == "used" else "draft"


def _epms_args(inv, tax_rows):
    payload = {
        "source_ref": inv.internal_ref,
        "vendor_id": inv.vendor_id, "vendor_name": inv.vendor_name,
        "vendor_invoice_number": inv.vendor_invoice_number,
        "amount": _s(inv.amount), "tax_amount": _s(inv.tax_amount),
        "total_amount": _s(inv.total_amount), "currency": inv.currency,
        "invoice_date": inv.invoice_date,                 # date object (crud.upsert feeds ORM Date)
        "due_date": inv.due_date,
        "status": _epms_status(inv.status), "source_status": inv.status,
        "po_id": inv.po_id, "po_number": inv.po_number,
    }
    tax_lines = [
        {"line_no": t.line_no, "tax_code": t.tax_code,
         "taxable_base": str(t.taxable_amount) if t.taxable_amount is not None else "0",
         "tax_amount": _s(t.tax_amount), "recoverable": t.recoverable}
        for t in tax_rows
    ]
    return "epms", inv.id, payload, tax_lines


def _oa_args(row):
    inv_date = row.invoice_date or row.created_at.date()
    payload = {
        "source_ref": row.invoice_number,
        "vendor_id": row.vendor_id, "vendor_name": row.vendor_name,
        "vendor_invoice_number": row.invoice_number,
        "amount": _s(row.subtotal), "tax_amount": _s(row.tax_amount),
        "total_amount": _s(row.total_amount), "currency": row.currency,
        "invoice_date": inv_date,                          # date object
        "due_date": row.due_date,
        "status": _oa_status(row.status), "source_status": row.status,
        "po_id": None, "po_number": None,
    }
    return "oa", row.id, payload, []


async def run() -> None:
    async with AsyncSessionLocal() as db:
        created = 0
        # EPMS
        invoices = (await db.execute(select(Invoice))).scalars().all()
        for inv in invoices:
            tax = (await db.execute(
                select(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == inv.id)
                .order_by(InvoiceTaxLine.line_no))).scalars().all()
            source, sid, payload, tax_lines = _epms_args(inv, tax)
            await ap_crud.upsert(db, source=source, source_invoice_id=sid,
                                 payload=payload, tax_lines=tax_lines)
            created += 1
        # OA (raw read — no finance model for expense_invoices)
        oa_rows = (await db.execute(text(
            "SELECT id, invoice_number, vendor_id, vendor_name, invoice_date, due_date,"
            " currency, subtotal, tax_amount, total_amount, status, created_at"
            " FROM expense_invoices"))).mappings().all()
        for r in oa_rows:
            class _Row:
                pass
            row = _Row()
            for k, v in r.items():
                setattr(row, k, v)
            source, sid, payload, tax_lines = _oa_args(row)
            await ap_crud.upsert(db, source=source, source_invoice_id=sid,
                                 payload=payload, tax_lines=tax_lines)
            created += 1
        await db.commit()
        print(f"Backfill complete: {created} ap_invoices upserted "
              f"({len(invoices)} epms + {len(oa_rows)} oa).")


if __name__ == "__main__":
    asyncio.run(run())
```

> 日期口径：`crud.ap_invoice.upsert` 直接把 `payload["invoice_date"]` 赋给 ORM 的 Date 列（Plan 2/3 的 HTTP 路径里 pydantic 已把 ISO 串解析成 date 对象再调 upsert）。所以本脚本作为**直调 crud** 的路径，`invoice_date`/`due_date` 一律传 **date 对象**（上面映射函数已如此），单测也按 date 对象断言。`amount`/`tax_amount`/`total_amount` 传字符串（upsert 内 `_q` 量化）。

- [ ] **Step 4: 运行确认通过**

Run: `cd finance-api && python -m pytest tests/test_ap_backfill.py -v`
Expected: PASS（映射 + 状态 + 日期回退，date 对象口径）。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 3: 跑回填 + 全量验证

**Files:** （无新文件）

- [ ] **Step 1: 对真实库跑回填（生产=测试库，安全）**

在 finance-api 容器内运行（它连 10.10.50.20/epms，所有源表都在）：
Run: `docker exec uniops_finance_api python -m scripts.backfill_ap_invoices`
Expected: 打印 `Backfill complete: N ap_invoices upserted (X epms + Y oa).`，无异常。

- [ ] **Step 2: 幂等复跑**

Run: `docker exec uniops_finance_api python -m scripts.backfill_ap_invoices`
Expected: 再次成功，计数相同（upsert 幂等，不产生重复行；可用下一步核对总数不翻倍）。

- [ ] **Step 3: 核对 ap_invoices 行数 = 源表合计**

Run:
```
docker exec uniops_finance_api python -c "
import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as db:
        ap = (await db.execute(text('select count(*) from ap_invoices'))).scalar()
        ep = (await db.execute(text('select count(*) from invoices'))).scalar()
        oa = (await db.execute(text('select count(*) from expense_invoices'))).scalar()
        print('ap=',ap,'epms=',ep,'oa=',oa)
asyncio.run(main())
"
```
Expected: `ap == epms + oa`（考虑 Plan 2/3 已实时同步的新发票也在 ap 内，幂等不重复；若 ap > epms+oa 说明有源已删但 ap 残留 void/历史——可接受，至少 ap >= epms+oa 且每个现存源都有对应 ap）。

- [ ] **Step 4: 全量单测回归**

Run: `cd finance-api && python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 5: 容器健康**

Run: `docker logs uniops_finance_api --tail 10 | grep -iE 'startup complete|error|Traceback'; docker inspect uniops_finance_api --format '{{.State.Health.Status}}'`
Expected: startup complete + healthy。

- [ ] **Step 6: Checkpoint（不提交）**

---

## Final Verification（本计划）

- [ ] `cd finance-api && python -m pytest tests/ -q` → 全绿。
- [ ] 回填脚本对真实库跑通且幂等；`ap_invoices` 覆盖现存源发票。
- [ ] 付款后 ap_invoice 状态翻 paid（测试证明）。
- [ ] 改动留工作区，不提交。

---

## 验收对照（spec → 本计划）

| spec 需求 | 本计划 Task |
|---|---|
| 存量 EPMS+OA 发票回填 ap_invoices（幂等）| Task 2, 3 |
| 付款 → ap_invoices 回写（open-items/aging 反映已付）| Task 1（pa + pa_dir）|
| 单库直读两源表 | Task 2 run() |
| 状态完整映射(paid/partially_paid)| Task 2 |
| 前端 AP 管理 + Aging | **Plan 5** |
| OA void 同步 | 仍为后续（admin 删除钩子）|
