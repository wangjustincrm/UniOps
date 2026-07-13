# JV Plan 5 — gl.py GL 读取层切换到已过账 JV Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GL 报表(试算/明细账/Journal/利润表/资产负债表/年结)从 `posting_events+posting_lines` 切换为只读**已过账** `journal_vouchers+journal_voucher_lines`(本位币),使「凭证过账才进 GL」真正生效;开账/结转凭证系统直接 posted。

**Architecture:** spec `2026-07-07-finance-jv-subsystem-design.md` §5(读取层迁移)+ §4(开账/结转直接 posted)。改动集中在 `crud/gl.py` 的 5 个读函数 + 2 个写流程尾部自动过账;`posting_events` 保留(业务脊柱/Chain)。切换零差异前提 = 存量 posting_events 已回填 posted JV(`backfill_posted_jvs` 幂等,dev 切换时容器内重跑)。GL 汇总口径从 posting 原币 debit/credit 改为 JV `local_debit/local_credit`(现网数据全 CAD fx=1,两口径数值相同)。

**Tech Stack:** FastAPI + SQLAlchemy async + pytest;前端 React(仅 GeneralLedgerPage 小改)。

## Global Constraints

- **分支** `feature/finance-jv-subsystem`(主目录 c:/Project/uniops);开工前 `git branch --show-current` 确认。
- **⚠️ 工作树有用户未提交 WIP**(epms/、epms-api/、expense-api/、portal/):**只 `git add <指定文件>`,禁止 `-A`/`.`/`stash`/`reset`/`checkout --`**。
- **后端测试命令**:`cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <file> -q`;**单进程串行,禁止后台/并发 pytest**。
- **前端 typecheck**:`cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`,显式确认 exit 0。
- **⚠️ 一切对 dev 库的操作(迁移/回填)必须容器内跑**(`docker exec uniops_finance_api …`)——宿主 finance-api/.env 的 DATABASE_URL 指向生产!绝不在宿主直接跑连库命令。
- **改 finance-api 代码要 `docker restart uniops_finance_api`**。
- GL 只统计 `status='posted'` 的 JV;金额一律 `local_debit/local_credit`(本位币 CAD)。
- **测试期间必须动态**(`datetime.now(timezone.utc).strftime("%Y-%m")`)——emit_event 用当天定 fiscal_period,硬编码期间是 test_gl 现在 3 个失败的根因,本计划顺带修掉。
- UI 文案纯英文。

---

## 现状(实现者需知)

- `finance-api/app/crud/gl.py`(318 行):`trial_balance` / `account_ledger` / `journal` / `_balances_through`(被 `income_statement`/`balance_sheet`/`close_year` 共用)读 `PostingLine join PostingEvent`;`post_opening_balance`/`close_year` 走 `emit_event`(其尾部已内联生成 **draft** JV)。
- JV 模型:`app.models.journal_voucher` 的 `JournalVoucher`(`status`/`fiscal_period`/`voucher_date`/`jv_number`/`source_doc_type`/`source_doc_number`/`summary`,常量 `POSTED`/`DRAFT`)与 `JournalVoucherLine`(`jv_id`/`line_no`/`account_code`/`summary`/`local_debit`/`local_credit`/`currency`/`partner_name`)。
- 回填:`app.crud.journal_voucher.backfill_posted_jvs(db)`(幂等,给无 JV 的 event 生成并把 draft 直接置 posted,绕过期间门)。
- 现 `tests/test_gl.py` 10 个测试,其中 3 个因硬编码 `2026-06` 跨月失效(emit 落在当月)。
- 前端 `finance/src/pages/finance/GeneralLedgerPage.tsx` 的 `JournalTab` 消费 `e.event_id/e.date/e.source/e.event_type/l.account_code/l.partner_name||l.line_role/l.debit/l.credit`;`AccountLedgerModal` 消费 `en.date/en.source/en.partner_name/en.debit/en.credit/en.balance`(不用 event_type/line_role)。
- dev 库:39,896 张 NC posted JV + 少量业务事件 JV;`/gl/account-balance`(已是 JV 口径)可作切换后对照。

---

### Task 1: 开账/结转凭证系统直接过账

**Files:**
- Modify: `finance-api/app/crud/journal_voucher.py`(加 `post_system_jv`)
- Modify: `finance-api/app/crud/gl.py`(`post_opening_balance`/`close_year` 尾部调用)
- Test: `finance-api/tests/test_jv_lifecycle.py`(追加)

**Interfaces:**
- Consumes: `app.services.posting.emit_event`(已在尾部生成 draft JV)、`JournalVoucher.posting_event_id`。
- Produces: `journal_voucher.post_system_jv(db: AsyncSession, event_id: uuid.UUID) -> JournalVoucher | None` —— 按 posting_event_id 找 JV,若 draft 则直接置 posted(posted_at=now,posted_by 留空=系统),**绕过角色/SoD/期间门**(spec §4:开账/结转系统权威)。幂等(非 draft 原样返回)。

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_jv_lifecycle.py` 末尾;该文件已有 `db_session` 使用与 emit 辅助,若无合适 helper 用下面自带的)

```python
async def test_gl_opening_and_close_jvs_post_immediately(db_session):
    """spec §4: 开账/结转 are system-authoritative — their JVs skip the human
    review/post flow and land already posted."""
    from datetime import date
    from sqlalchemy import select
    from app.crud.gl import post_opening_balance
    from app.models.journal_voucher import JournalVoucher

    r = await post_opening_balance(db_session, as_of=date(2026, 1, 1), lines=[
        {"account_code": "1010", "debit": "100.00"},
        {"account_code": "3100", "credit": "100.00"},
    ])
    assert r["posting_event_id"] is not None
    jv = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == r["posting_event_id"]))).scalar_one()
    assert jv.status == "posted"
    assert jv.posted_at is not None
```

注:`post_opening_balance` 校验 account_code 在 COA 里 —— conftest 迁移含 COA 种子?若 `1010`/`3100` 不存在,测试前先插入:

```python
    from app.models.coa import ChartOfAccount
    for code, name, t in (("1010", "Cash", "asset"), ("3100", "Retained Earnings", "equity")):
        if (await db_session.execute(select(ChartOfAccount).where(
                ChartOfAccount.code == code))).scalar_one_or_none() is None:
            db_session.add(ChartOfAccount(code=code, name=name, account_type=t,
                                          normal_balance="debit" if t == "asset" else "credit",
                                          is_postable=True))
    await db_session.flush()
```

(把这段放在调用 `post_opening_balance` 之前。)

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py::test_gl_opening_and_close_jvs_post_immediately -q`
Expected: FAIL(`assert jv.status == "posted"` → 实际 'draft')

- [ ] **Step 3: 实现 `post_system_jv`**(`crud/journal_voucher.py`,放在 `backfill_posted_jvs` 之前)

```python
async def post_system_jv(db: AsyncSession, event_id: uuid.UUID) -> JournalVoucher | None:
    """开账/结转 JV 系统直接过账(spec §4 系统权威):绕过角色/SoD/期间门。
    幂等 — 非 draft 原样返回。"""
    jv = (await db.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == event_id))).scalar_one_or_none()
    if jv is not None and jv.status == DRAFT:
        jv.status = POSTED
        jv.posted_at = datetime.now(timezone.utc)
        await db.flush()
    return jv
```

(该文件已 import `DRAFT/POSTED/datetime/timezone/select`——缺哪个补哪个。)

- [ ] **Step 4: gl.py 两个写流程尾部接线** —— `post_opening_balance` 的 return 前:

```python
    if event_id is not None:
        from app.crud.journal_voucher import post_system_jv
        await post_system_jv(db, event_id)
    return {"posting_event_id": event_id, "already_posted": event_id is None}
```

`close_year` 的 return 前同样:

```python
    if event_id is not None:
        from app.crud.journal_voucher import post_system_jv
        await post_system_jv(db, event_id)
    return {"fiscal_year": fiscal_year, "posting_event_id": event_id,
            "already_closed": event_id is None, "net_income": _s(net)}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py -q`
Expected: 全部 PASS(原 13 + 新 1)

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/crud/journal_voucher.py finance-api/app/crud/gl.py finance-api/tests/test_jv_lifecycle.py
git commit -m "feat(finance): gl opening/close JVs post immediately (system-authoritative, spec §4)"
```

---

### Task 2: trial_balance + account_ledger 切换到 posted JV(+ test_gl 动态期间重写上半)

**Files:**
- Modify: `finance-api/app/crud/gl.py`(`trial_balance`/`account_ledger`,import 区)
- Test: `finance-api/tests/test_gl.py`(重写)

**Interfaces:**
- Consumes: `JournalVoucher`(POSTED)/`JournalVoucherLine`、`backfill_posted_jvs`、Task 1 的自动过账(opening 测试不再需要手工回填)。
- Produces: `trial_balance(db, period)` 与 `account_ledger(db, code, period)` **响应 shape 不变**(键名/金额字符串同现状;`account_ledger` entries 的 `event_type`/`line_role` 两键删除,`memo` 改取 JV 行 summary)——前端只消费保留键。

- [ ] **Step 1: 重写 `tests/test_gl.py` 头部与 helpers**(整文件按下述替换;逐个测试重写为动态期间 + JV 过账语义)

```python
"""General Ledger over POSTED journal vouchers (Plan 5 switchover): trial
balance, statements, account ledger, opening balances, year-end close.

Periods are DYNAMIC (emit_event stamps fiscal_period with today's month) —
hardcoding a month rots the suite at month rollover (the pre-Plan-5 failure).
"""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app

NOW = datetime.now(timezone.utc)
PERIOD = NOW.strftime("%Y-%m")          # current month — where emits land
YEAR = NOW.year


def _h(role="finance_manager"):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _post_ar_invoice(client, amount="1000.00", tax="130.00", tax_code="HST_ON"):
    d = NOW.date().isoformat()
    body = {"customer_id": str(uuid.uuid4()), "customer_name": "Loblaw Inc",
            "invoice_date": d, "due_date": d, "currency": "CAD",
            "amount": amount, "tax_lines": [{"tax_code": tax_code, "tax_amount": tax}]}
    r = await client.post("/finance/v1/ar/invoices", headers=_h(), json=body)
    inv_id = r.json()["id"]
    await client.post(f"/finance/v1/ar/invoices/{inv_id}/post", headers=_h())
    return inv_id


async def _post_all_draft_jvs(db_session):
    """Business-event JVs are born draft; GL only sees POSTED. Tests use the
    (idempotent) cut-over backfill to post them without the human flow."""
    from app.crud.journal_voucher import backfill_posted_jvs
    await backfill_posted_jvs(db_session)
    await db_session.flush()
```

- [ ] **Step 2: 重写各测试**(替换原 10 个;开账 3 个不变逻辑只留原样,其余动态期间+过账步骤;新增 draft 不进 GL + parity 两个)

```python
# ── opening balances(逻辑不变,原样保留) ────────────────────────────────────────

async def test_opening_balance_must_balance(client):
    r = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json={
        "as_of": f"{YEAR}-01-01",
        "lines": [{"account_code": "1010", "debit": "100.00"},
                  {"account_code": "3100", "credit": "90.00"}]})
    assert r.status_code == 409 and "unbalanced" in r.json()["detail"].lower()


async def test_opening_balance_posts_and_is_idempotent(client):
    body = {"as_of": f"{YEAR}-01-01",
            "lines": [{"account_code": "1010", "debit": "50000.00"},
                      {"account_code": "3100", "credit": "50000.00"}]}
    r1 = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json=body)
    r2 = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json=body)
    assert r1.json()["posting_event_id"] is not None
    assert r2.json()["already_posted"] is True


async def test_opening_unknown_account_rejected(client):
    r = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json={
        "as_of": f"{YEAR}-01-01",
        "lines": [{"account_code": "9999", "debit": "1.00"},
                  {"account_code": "3100", "credit": "1.00"}]})
    assert r.status_code == 409 and "unknown account" in r.json()["detail"].lower()


# ── draft 不进 GL(切换的核心语义) ────────────────────────────────────────────────

async def test_draft_jv_excluded_until_posted(client, db_session):
    await _post_ar_invoice(client)          # emits → draft JV
    r = await client.get(f"/finance/v1/gl/trial-balance?period={PERIOD}", headers=_h())
    assert r.json()["totals"]["period_debit"] == "0.00"   # draft invisible
    await _post_all_draft_jvs(db_session)
    r2 = await client.get(f"/finance/v1/gl/trial-balance?period={PERIOD}", headers=_h())
    assert r2.json()["totals"]["period_debit"] == "1130.00"


# ── trial balance ─────────────────────────────────────────────────────────────────

async def test_trial_balance_balances(client, db_session):
    await _post_ar_invoice(client)  # DR AR 1130 / CR rev 1000 / CR output tax 130
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/trial-balance?period={PERIOD}", headers=_h())
    assert r.status_code == 200
    tb = r.json()
    assert tb["balanced"] is True
    assert tb["totals"]["period_debit"] == tb["totals"]["period_credit"] == "1130.00"
    rows = {row["account_code"]: row for row in tb["rows"]}
    assert rows["1100"]["closing"] == "1130.00"
    assert rows["4000"]["closing"] == "-1000.00"
    assert rows["2200"]["closing"] == "-130.00"


async def test_opening_carries_into_next_period_opening_column(client):
    # opening JV auto-posts (Task 1) — no backfill needed
    await client.post("/finance/v1/gl/opening-balance", headers=_h(), json={
        "as_of": f"{YEAR}-01-01",
        "lines": [{"account_code": "1010", "debit": "9000.00"},
                  {"account_code": "3100", "credit": "9000.00"}]})
    later = f"{YEAR}-12"                    # any period after January
    r = await client.get(f"/finance/v1/gl/trial-balance?period={later}", headers=_h())
    rows = {row["account_code"]: row for row in r.json()["rows"]}
    assert rows["1010"]["opening"] == "9000.00"


# ── parity: posting-spine sums == JV-based trial balance(切换零差异证明) ──────────

async def test_parity_posting_spine_vs_jv_trial_balance(client, db_session):
    from sqlalchemy import func, select
    from app.models.posting import PostingEvent, PostingLine
    await _post_ar_invoice(client, amount="777.00", tax="101.01")
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/trial-balance?period={PERIOD}", headers=_h())
    jv_rows = {row["account_code"]: Decimal(row["closing"]) for row in r.json()["rows"]}
    spine = (await db_session.execute(
        select(PostingLine.account_code,
               func.coalesce(func.sum(PostingLine.debit - PostingLine.credit), 0))
        .join(PostingEvent, PostingLine.event_id == PostingEvent.id)
        .where(PostingEvent.fiscal_period <= PERIOD)
        .group_by(PostingLine.account_code))).all()
    for code, net in spine:
        assert jv_rows.get(code, Decimal("0")) == Decimal(net), f"parity break at {code}"


# ── account ledger ────────────────────────────────────────────────────────────────

async def test_account_ledger_running_balance(client, db_session):
    await _post_ar_invoice(client, amount="1000.00", tax="130.00")
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/account/1100?period={PERIOD}", headers=_h())
    led = r.json()
    assert led["opening"] == "0.00"
    assert led["closing"] == "1130.00"
    assert led["entries"][-1]["balance"] == "1130.00"


async def test_reports_require_valid_period(client):
    r = await client.get("/finance/v1/gl/trial-balance?period=2026-13", headers=_h())
    assert r.status_code == 422


# ── financial statements(期间动态 + 事件过账;Task 3 切 _balances_through 后仍绿) ──

async def test_income_statement(client, db_session):
    await _post_ar_invoice(client, amount="1000.00", tax="0.00", tax_code="ZERO")
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/income-statement?period={PERIOD}&ytd=true",
                         headers=_h())
    s = r.json()
    assert s["revenue_total"] == "1000.00"
    assert s["net_income"] == "1000.00"
    assert any(row["account_code"] == "4000" for row in s["revenue"])


async def test_balance_sheet_balances_with_net_income(client, db_session):
    await _post_ar_invoice(client, amount="1000.00", tax="130.00")
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/balance-sheet?period={PERIOD}", headers=_h())
    bs = r.json()
    assert bs["balanced"] is True
    assert bs["assets_total"] == bs["liabilities_and_equity_total"]
    assert any("Current Year Earnings" in e["account_name"] for e in bs["equity"])


# ── year-end close ────────────────────────────────────────────────────────────────

async def test_close_year_sweeps_pl_to_retained_earnings(client, db_session):
    await _post_ar_invoice(client, amount="1000.00", tax="0.00", tax_code="ZERO")
    await _post_all_draft_jvs(db_session)
    r = await client.post("/finance/v1/gl/close-year", headers=_h(),
                          json={"fiscal_year": YEAR})
    assert r.status_code == 200, r.text
    assert r.json()["net_income"] == "1000.00"

    # P&L zeroed in the YTD income statement after the close period (Dec);
    # the closing JV auto-posts (Task 1) — no backfill needed for it
    r = await client.get(f"/finance/v1/gl/income-statement?period={YEAR}-12&ytd=true",
                         headers=_h())
    assert r.json()["net_income"] == "0.00"

    r = await client.get(f"/finance/v1/gl/account/3100?period={YEAR}-12", headers=_h())
    assert r.json()["closing"] == "-1000.00"

    r2 = await client.post("/finance/v1/gl/close-year", headers=_h(),
                           json={"fiscal_year": YEAR})
    assert r2.json()["already_closed"] is True
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_gl.py -q`
Expected: `test_draft_jv_excluded_until_posted` FAIL(旧读层 draft 也计入→period_debit 已是 1130)——证明切换尚未发生;其余因 posting 读层大多 PASS。

- [ ] **Step 4: 切换 `trial_balance` 与 `account_ledger`** —— `crud/gl.py`:

import 区加:

```python
from app.models.journal_voucher import POSTED, JournalVoucher, JournalVoucherLine
```

`trial_balance` 的内部 `sums` 整体替换:

```python
    async def sums(where):
        q = (select(JournalVoucherLine.account_code,
                    func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                    func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .where(JournalVoucher.status == POSTED).where(where)
             .group_by(JournalVoucherLine.account_code))
        return {code: (Decimal(d), Decimal(c)) for code, d, c in (await db.execute(q)).all()}

    opening = await sums(JournalVoucher.fiscal_period < period)
    movement = await sums(JournalVoucher.fiscal_period == period)
```

`account_ledger` 中段替换(opening 行/明细行/entries 构造):

```python
    opening_row = (await db.execute(
        select(func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
               func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
        .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
        .where(JournalVoucherLine.account_code == code,
               JournalVoucher.status == POSTED,
               JournalVoucher.fiscal_period < period)
    )).one()
    running = Decimal(opening_row[0]) - Decimal(opening_row[1])
    opening = running

    lines = (await db.execute(
        select(JournalVoucherLine, JournalVoucher)
        .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
        .where(JournalVoucherLine.account_code == code,
               JournalVoucher.status == POSTED,
               JournalVoucher.fiscal_period == period)
        .order_by(JournalVoucher.voucher_date, JournalVoucher.jv_number,
                  JournalVoucherLine.line_no)
    )).all()

    entries = []
    for ln, jv in lines:
        running += ln.local_debit - ln.local_credit
        entries.append({
            "date": jv.voucher_date.isoformat(),
            "source": f"{jv.source_doc_type}:{jv.source_doc_number}"
                      if jv.source_doc_type else jv.jv_number,
            "partner_name": ln.partner_name, "memo": ln.summary,
            "debit": _s(ln.local_debit), "credit": _s(ln.local_credit),
            "balance": _s(running),
        })
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_gl.py -q`
Expected: 全部 PASS(13 个:原 10 重写 + draft 排除 + parity)。注意 Task 3 未做前 `income_statement`/`balance_sheet`/`close_year` 仍读 posting——它们的测试在本 Task 已把事件全部过账,两口径同值,应绿(close_year 测试依赖 Task 1 的结转自动过账)。

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/crud/gl.py finance-api/tests/test_gl.py
git commit -m "feat(finance): trial balance + account ledger read posted JVs (Plan 5 switchover, part 1)"
```

---

### Task 3: journal + 报表(_balances_through)切换 + 文档口径

**Files:**
- Modify: `finance-api/app/crud/gl.py`(`journal`/`_balances_through` + 模块 docstring)
- Test: `finance-api/tests/test_gl.py`(journal 新测试 + 三个报表测试终版)

**Interfaces:**
- Consumes: Task 2 已切换的读层与 test helpers(`PERIOD`/`YEAR`/`_post_all_draft_jvs`)。
- Produces: `journal(db, period, limit=200)` **响应 shape 变更**(前端 Task 4 依赖):每项 `{jv_id, jv_number, date, source, summary, lines: [{account_code, partner_name, summary, debit, credit, currency}]}`(金额=本位币字符串)。`_balances_through(db, lo, hi, exclude_closing=False)` 签名不变,内部读 posted JV,`exclude_closing` 按 `source_doc_type != 'gl_close'`(注意 NULL 安全)。

- [ ] **Step 1: 写失败测试**(三个报表测试在 Task 2 已是终版,本 Task 只追加 journal 测试)

```python
# ── journal(posted JV 凭证列表) ──────────────────────────────────────────────────

async def test_journal_lists_posted_jvs(client, db_session):
    await _post_ar_invoice(client)
    r0 = await client.get(f"/finance/v1/gl/journal?period={PERIOD}", headers=_h())
    assert r0.json() == []                          # draft not in the journal
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/journal?period={PERIOD}", headers=_h())
    entries = r.json()
    assert len(entries) == 1
    e = entries[0]
    assert e["jv_number"].startswith("JV-")
    assert e["source"] == "ar_invoice:" + e["source"].split(":", 1)[1]
    assert len(e["lines"]) == 3                     # AR / revenue / output tax
    assert {l["account_code"] for l in e["lines"]} == {"1100", "4000", "2200"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_gl.py::test_journal_lists_posted_jvs -q`
Expected: FAIL(`KeyError: 'jv_number'` —— 旧 journal 返回 event 形状,且 draft 已计入 r0)

- [ ] **Step 3: 切换 `journal`**(整函数替换)

```python
async def journal(db: AsyncSession, period: str, limit: int = 200) -> list[dict]:
    """Posted journal vouchers as journal entries (was posting_events pre-Plan-5)."""
    jvs = (await db.execute(
        select(JournalVoucher)
        .where(JournalVoucher.status == POSTED, JournalVoucher.fiscal_period == period)
        .order_by(JournalVoucher.voucher_date.desc(), JournalVoucher.jv_number.desc())
        .limit(limit)
    )).scalars().all()
    if not jvs:
        return []
    ids = [j.id for j in jvs]
    lines = (await db.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id.in_(ids))
        .order_by(JournalVoucherLine.line_no)
    )).scalars().all()
    by_jv: dict[uuid.UUID, list] = {}
    for ln in lines:
        by_jv.setdefault(ln.jv_id, []).append(ln)
    out = []
    for jv in jvs:
        jlines = by_jv.get(jv.id, [])
        out.append({
            "jv_id": str(jv.id), "jv_number": jv.jv_number,
            "date": jv.voucher_date.isoformat(),
            "source": f"{jv.source_doc_type}:{jv.source_doc_number}"
                      if jv.source_doc_type else jv.jv_number,
            "summary": jv.summary,
            "lines": [{"account_code": l.account_code, "partner_name": l.partner_name,
                       "summary": l.summary, "debit": _s(l.local_debit),
                       "credit": _s(l.local_credit), "currency": l.currency}
                      for l in jlines],
        })
    return out
```

- [ ] **Step 4: 切换 `_balances_through`**(整函数替换;`or_` 记得进 import)

```python
async def _balances_through(db: AsyncSession, period_lo: str | None, period_hi: str,
                            exclude_closing: bool = False):
    """{code: signed local balance (debit-positive)} over POSTED JVs in [lo, hi].
    exclude_closing drops year-end close vouchers (source_doc_type='gl_close',
    NULL-safe) so close_year measures operational P&L idempotently."""
    conds = [JournalVoucher.status == POSTED, JournalVoucher.fiscal_period <= period_hi]
    if period_lo is not None:
        conds.append(JournalVoucher.fiscal_period >= period_lo)
    if exclude_closing:
        conds.append(or_(JournalVoucher.source_doc_type.is_(None),
                         JournalVoucher.source_doc_type != "gl_close"))
    q = (select(JournalVoucherLine.account_code,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0)
                - func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(*conds).group_by(JournalVoucherLine.account_code))
    return {code: Decimal(bal) for code, bal in (await db.execute(q)).all()}
```

import 行 `from sqlalchemy import func, select` 改为 `from sqlalchemy import func, or_, select`。

- [ ] **Step 5: 更新模块 docstring**(gl.py 顶部第 12-14 行的 Reporting basis 段替换)

```python
Reporting basis (Plan 5): the GL reads POSTED journal_vouchers only —
business-event JVs are born draft and enter the GL when finance reviews and
posts them; opening/close/NC-import vouchers post immediately (system-
authoritative). posting_events remain as the business spine / Document Chain.
```

- [ ] **Step 6: 全套 test_gl 确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_gl.py -q`
Expected: 14 passed(13 + journal)

- [ ] **Step 7: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/crud/gl.py finance-api/tests/test_gl.py
git commit -m "feat(finance): journal + statements read posted JVs (Plan 5 switchover, part 2)"
```

---

### Task 4: 前端 GL 页适配(JournalTab 新 shape + 文案)

**Files:**
- Modify: `finance/src/pages/finance/GeneralLedgerPage.tsx`(JournalTab + subtitle)

**Interfaces:**
- Consumes: Task 3 的 journal 新 shape `{jv_id, jv_number, date, source, summary, lines:[{account_code, partner_name, summary, debit, credit}]}`。

- [ ] **Step 1: 改 `JournalTab`**(整个函数替换,文件内约 303-331 行)

```tsx
function JournalTab({ q }: { q: any }) {
  if (q.isFetching && !q.data) return <Loading />
  const entries: any[] = q.data ?? []
  if (entries.length === 0) return <div className="rounded-lg border border-dashed border-neutral-300 p-10 text-center text-sm text-neutral-500">No posted journal vouchers this period.</div>
  return (
    <div className="space-y-3">
      {entries.map((e) => (
        <div key={e.jv_id} className="overflow-hidden rounded-lg border border-neutral-200">
          <div className="flex items-center justify-between bg-neutral-50 px-3 py-2 text-sm">
            <span className="font-mono text-xs">{e.date} · {e.source}</span>
            <span className="rounded-full bg-white px-2 py-0.5 font-mono text-xs text-neutral-600">{e.jv_number}</span>
          </div>
          <table className="w-full text-sm">
            <tbody>
              {e.lines.map((l: any, i: number) => (
                <tr key={i} className="border-t border-neutral-100">
                  <td className="px-3 py-1.5 w-20 font-mono text-xs text-neutral-500">{l.account_code ?? '—'}</td>
                  <td className="px-3 py-1.5">{l.partner_name || l.summary || '—'}</td>
                  <td className="px-3 py-1.5 w-28 text-right font-mono">{num(l.debit) ? money(l.debit) : ''}</td>
                  <td className="px-3 py-1.5 w-28 text-right font-mono">{num(l.credit) ? money(l.credit) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: 改页面 subtitle**(PortalChromeLayout 处)

```tsx
      subtitle="Trial balance, financial statements, and period close over posted journal vouchers"
```

- [ ] **Step 3: Typecheck**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0(显式确认)

- [ ] **Step 4: Commit**

```bash
cd c:/Project/uniops
git add finance/src/pages/finance/GeneralLedgerPage.tsx
git commit -m "feat(finance-ui): GL journal tab renders posted JV entries (jv_number chip)"
```

---

### Task 5: dev 切换执行 + 实测对照 + 全量回归(控制器亲自执行)

**Files:**
- Create: `finance-api/scripts/backfill_jvs.py`(切换/生产复用的回填入口)

- [ ] **Step 1: 写回填脚本**(完整文件;跑在**容器内**,用服务自己的 DATABASE_URL)

```python
"""Cut-over backfill: ensure every posting_event has a POSTED JV (idempotent).

Run INSIDE the finance-api container (its DATABASE_URL points at the right DB):
    docker exec uniops_finance_api python scripts/backfill_jvs.py
NEVER run from the host — the host .env points at production.
"""
import asyncio

from app.crud.journal_voucher import backfill_posted_jvs
from app.db.base import AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as session:
        res = await backfill_posted_jvs(session)
        await session.commit()
        print(f"backfill: generated={res['generated']} posted={res['posted']}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 容器内跑回填**(dev 库;把切换前的存量业务事件全部置 posted)

Run: `docker exec uniops_finance_api python scripts/backfill_jvs.py`
Expected: 打印 generated/posted 计数(可为 0 —— 幂等)

- [ ] **Step 3: 重启容器 + 实测对照**

```bash
docker restart uniops_finance_api
```

等健康后,同一 token 分别调:
- `GET /finance/v1/gl/trial-balance?period=2024-06`
- `GET /finance/v1/gl/account-balance?period=2024-06`(Plan 3 起就是 JV 口径)

Expected: 两端点逐科目 `closing` 完全一致(用 python 脚本比对,不要目测);`balanced: true`。

- [ ] **Step 4: 全量回归(单进程串行)**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_gl.py tests/test_journal_voucher.py tests/test_jv_lifecycle.py tests/test_jv_api.py tests/test_jv_backfill.py tests/test_account_balance.py tests/test_nc_sync.py tests/test_ar.py tests/test_ap_accrual.py -q`
Expected: 全部 PASS(test_gl 不再有跨月失效)

- [ ] **Step 5: Commit + 记忆更新**

```bash
cd c:/Project/uniops
git add finance-api/scripts/backfill_jvs.py
git commit -m "feat(finance): cut-over backfill script (run in-container only)"
```

---

## Self-Review 记录

- **Spec 覆盖**:§5 读取层五函数(trial_balance/account_ledger T2;journal/income_statement/balance_sheet 经 _balances_through T3)✅;§5 切换零差异前提(T5 回填+双口径对照+T2 parity 测试)✅;§4 开账/结转直接 posted(T1)✅;posting_events 保留(无删除动作)✅。**顺带修复**:test_gl 3 个跨月失效(动态期间)。**明确不做**:红冲自动过账已有(reverse 生成即 posted,Plan 2);手工凭证、审核≠过账可配(spec future/待解决)。
- **Placeholder 扫描**:通过,所有步骤含完整代码。
- **类型一致性**:`post_system_jv(db, event_id)`(T1 定义,gl.py 两处消费);journal 新 shape(T3 定义,T4 消费键名逐一对应);`_post_all_draft_jvs(db_session)`(T2 定义,T2/T3 测试消费);响应保留键(trial_balance totals/rows、account_ledger date/source/partner_name/memo/debit/credit/balance)与前端消费一致。
- **已知口径差异(记录)**:旧 GL 汇总 posting 原币 debit/credit,新 GL 汇总 JV 本位币——现网/测试数据全 CAD fx=1,数值等同;NC 多币种历史本来就只在 JV 侧。exclude_closing 从 `event_type != 'closing'` 改 `source_doc_type != 'gl_close'`(语义等价,gl_close 事件 1:1 对应)。
