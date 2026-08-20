# EPMS 单据 PDF 申请人 / 审批人姓名 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 epms-api 自动生成的 PR / PA / GR PDF 印出经手人姓名 —— PR/PA 印申请人与全审批链审批人，GR 印创建人。

**Architecture:** PDF 渲染函数是同步的、经 `loop.run_in_executor` 调用，拿不到 DB session，因此所有姓名由一个新的共享 async 模块 `app/crud/signatories.py` 在调用方查好后作为**位置参数**传入（`run_in_executor` 只接受位置参数，不接受 kwargs）。渲染层新增的参数全部可选并追加在参数列表末尾，既兼容仓库里两处遗留死代码，也让 PR/PA 共用一个放在 `pdf_template.py` 的 `approvals_element()`。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.x async / ReportLab / pytest + pytest-asyncio

## Global Constraints

- 工作区：`c:/Project/uniops-pdf-names`，分支 `feature/pdf-signatories`（基 `main` = `d57578d`）。所有路径相对该 worktree。
- 只改 `epms-api`。**不碰** `app/services/pdf_po.py`。
- **无数据库迁移。**
- 不修改、不删除 `app/crud/pr.py::action` / `_attach_pr_pdf` / `app/crud/pa.py::_attach_pa_pdf`（已无调用方的遗留代码）。新增参数必须全部可选，保证它们仍能编译运行。
- PDF 上所有 user-facing 文案为英文（`Requested By` / `Applied By` / `Approvals` / `Step` / `Approved By` / `Date` / `Created By`）。中文只出现在代码注释与本仓库文档里。
- 日期一律 `strftime("%Y-%m-%d")`；缺值渲染 `—`（U+2014，与现有 `_cell` 保持一致）。
- `approval_events` 里 `comment` **包含** `Auto-skipped` 的事件必须排除（那一步没有真人，`actor_id` 是提交人）；`Auto-approved (same approver holds both roles)` 必须保留（真人一人兼两角）。判定用子串包含，与前端 `PrDetailPage.tsx` 的 `comment.includes('Auto-skipped')` 一致。

## 跑测环境（每个任务的测试步骤都用这条）

宿主 `.env` 指向**生产库** 10.10.50.20，必须覆盖 `POSTGRES_*` 到本地 docker `uniops_postgres`。在 `c:/Project/uniops-pdf-names/epms-api` 目录下执行：

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest <目标> -q
```

已验证事实（本计划撰写时实测，勿再假设）：

- 上述命令在宿主可跑通，`tests/test_pr_pdf_department.py` **PASS**。
- `tests/test_pr.py` 有 **9 个存量失败**（`KeyError: 'status'` / `assert 404 == 409` / `assert 0 == 2`），原因是宿主跑测时 epms→approval-api 的审批委派代理不可达。**这是基线，不是你引入的**。本计划的新测试一律**直接播种 `approval_events` 行**，绝不经过审批委派链路。
- 测试库 `epms_test` 是所有 worktree 共用的。撰写本计划时它里面残留了别的分支的 `purchase_agreements` 表，导致 `Base.metadata.drop_all` 报 `DependentObjectsStillExistError`。若再遇到同类报错，清掉残留表即可：
  `docker exec uniops_postgres psql -U epms -d epms_test -c "DROP TABLE IF EXISTS <表名> CASCADE"`
- **同一时刻只能跑一个 epms-api 套件**，测试库不支持并发。
- 全量基线数字见本计划末尾"验收"一节；实施完成后必须与之比对，不能只看"没报错"。

## File Structure

| 文件 | 职责 |
|---|---|
| `epms-api/app/crud/signatories.py` | **新建**。唯一的姓名取数处：批量查 `users.full_name`、组装 PR/PA 审批链、解析 GR 的三个姓名（含 UUID 存量脏数据反查）。 |
| `epms-api/app/crud/current_step.py` | 私有 `_label()` 改名为公开 `role_label()`，供上面复用，避免第二份 ROLE_LABELS。 |
| `epms-api/app/services/pdf_template.py` | 新增 `approvals_element()` —— PR 与 PA 共用的审批表格 flowable。 |
| `epms-api/app/services/pdf_pr.py` | 元信息格加 `Requested By \| Submitted`；Total 后插 Approvals 小节。 |
| `epms-api/app/services/pdf_pa.py` | 元信息格加 `Applied By \| Date Approved`；Total 后插 Approvals 小节。 |
| `epms-api/app/services/pdf_gr.py` | Signatures 小节加 `Created By`；姓名可由参数覆盖。 |
| `epms-api/app/api/v1/pr.py` / `pr_attachments.py` | PR 两个生成入口接线。 |
| `epms-api/app/api/v1/pa.py` / `pa_attachments.py` | PA 两个生成入口接线。 |
| `epms-api/app/crud/gr.py` | GR 生成入口接线 + 6 处 UUID 兜底改查真名。 |
| `epms-api/tests/test_signatories.py` | **新建**。取数层单测。 |
| `epms-api/tests/test_pdf_signatories.py` | **新建**。三种 PDF 的渲染断言。 |

---

### Task 1: 共享取数模块 `signatories.py`

**Files:**
- Create: `epms-api/app/crud/signatories.py`
- Modify: `epms-api/app/crud/current_step.py:37`（`_label` → `role_label`）与 `:81`（唯一内部调用点）
- Test: `epms-api/tests/test_signatories.py`

**Interfaces:**
- Consumes: `app.models.approval.ApprovalEvent`、`app.models.user.User`、`app.crud.current_step.role_label`
- Produces:
  - `async def resolve_user_names(db: AsyncSession, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]`
  - `async def approval_signatories(db: AsyncSession, doc_type: str, doc_id: uuid.UUID, created_by: uuid.UUID | None) -> tuple[str | None, list[dict]]`，审批人 dict 形如 `{"role": str, "name": str | None, "at": datetime}`
  - `async def gr_signatories(db: AsyncSession, gr) -> dict`，键为 `"created_by_name"` / `"received_by"` / `"acknowledged_by"`
  - `def role_label(role: str) -> str`（从 `current_step` 导出）

- [ ] **Step 1: 写失败测试**

创建 `epms-api/tests/test_signatories.py`：

```python
"""Unit tests for app/crud/signatories.py — the single place PDF names come from.

Approval events are seeded directly rather than driven through POST /pr/{id}/action:
that endpoint delegates to approval-api, which is not reachable from a host-run
test suite (see tests/test_pr.py's pre-existing failures).
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.crud.signatories import approval_signatories, gr_signatories, resolve_user_names
from app.models.approval import ApprovalEvent
from app.models.gr import GoodsReceipt
from app.models.po import PurchaseOrder
from app.models.pr import PrLineItem, PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _user(db: AsyncSession, full_name: str):
    u = await user_crud.create(db, RegisterRequest(
        email=f"sig-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name=full_name, role="requester"))
    await db.flush()
    return u


async def _pr(db: AsyncSession, created_by: uuid.UUID) -> PurchaseRequest:
    pr = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="Signatory PR", type=2,
        status="approved", amount=Decimal("150.00"), currency="CAD",
        created_by=created_by,
        line_items=[PrLineItem(
            description="Widget", qty=Decimal("3"), unit="ea",
            unit_price=Decimal("50.00"), line_total=Decimal("150.00"), sort_order=0)],
    )
    db.add(pr)
    await db.flush()
    return pr


def _event(pr: PurchaseRequest, step: int, actor_id: uuid.UUID, role: str, comment=None):
    return ApprovalEvent(
        document_type="pr", document_id=pr.id, document_number=pr.number,
        step_idx=step, action="approve", actor_id=actor_id,
        actor_role=role, comment=comment,
    )


@pytest.mark.asyncio
async def test_approval_signatories_skips_auto_skipped_steps(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rita Requester")
        manager = await _user(db, "Manny Manager")
        gm = await _user(db, "Gina General")
        pr = await _pr(db, requester.id)
        # step 1 has no real approver: approval-api records the submitter as actor
        db.add_all([
            _event(pr, 0, manager.id, "dept_manager"),
            _event(pr, 1, requester.id, "director",
                   comment="Auto-skipped (department has no Director)"),
            _event(pr, 2, gm.id, "gm_or_opm"),
        ])
        await db.commit()

        name, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)

        assert name == "Rita Requester"
        assert [a["name"] for a in approvals] == ["Manny Manager", "Gina General"]
        assert [a["role"] for a in approvals] == ["Dept Manager", "GM / OPM"]


@pytest.mark.asyncio
async def test_approval_signatories_keeps_auto_approved_dual_role(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rhonda Requester")
        dual = await _user(db, "Dana Dualrole")
        pr = await _pr(db, requester.id)
        db.add_all([
            _event(pr, 0, dual.id, "dept_manager"),
            _event(pr, 1, dual.id, "finance_bp",
                   comment="Auto-approved (same approver holds both roles)"),
        ])
        await db.commit()

        _, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)

        assert [(a["role"], a["name"]) for a in approvals] == [
            ("Dept Manager", "Dana Dualrole"),
            ("Finance BP", "Dana Dualrole"),
        ]


@pytest.mark.asyncio
async def test_gr_signatories_resolves_uuid_shaped_names(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        creator = await _user(db, "Wanda Warehouse")
        acker = await _user(db, "Aaron Acknowledger")
        vendor = Vendor(code=f"V{uuid.uuid4().hex[:6]}", name="Sig Vendor")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(
            number=f"PO-{uuid.uuid4().hex[:8]}", title="Sig PO", type=2,
            status="issued", vendor_id=vendor.id, vendor_name=vendor.name,
            currency="CAD", subtotal=Decimal("0"), total=Decimal("0"),
            created_by=creator.id,
        )
        db.add(po)
        await db.flush()
        gr = GoodsReceipt(
            number=f"GR-{uuid.uuid4().hex[:8]}", title="Sig GR",
            po_id=po.id, po_number=po.number, vendor_id=vendor.id,
            vendor_name=vendor.name, gr_type="physical", procurement_type=2,
            currency="CAD", status="collection_pending",
            received_by="Wanda Warehouse",          # browser path: a real name
            acknowledged_by=str(acker.id),          # non-browser path: a raw UUID
            created_by=creator.id,
        )
        db.add(gr)
        await db.commit()

        sig = await gr_signatories(db, gr)

        assert sig["created_by_name"] == "Wanda Warehouse"
        assert sig["received_by"] == "Wanda Warehouse"
        assert sig["acknowledged_by"] == "Aaron Acknowledger"   # UUID resolved to a name


@pytest.mark.asyncio
async def test_resolve_user_names_ignores_none_and_unknown(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        known = await _user(db, "Ken Known")
        await db.commit()

        names = await resolve_user_names(db, [known.id, None, uuid.uuid4()])

        assert names == {known.id: "Ken Known"}
```

- [ ] **Step 2: 跑测确认失败**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_signatories.py -q
```

Expected: collection error —— `ModuleNotFoundError: No module named 'app.crud.signatories'`

- [ ] **Step 3: 把 `_label` 改名为 `role_label`**

在 `epms-api/app/crud/current_step.py`：第 37 行 `def _label(role: str) -> str:` 改为 `def role_label(role: str) -> str:`，第 81 行 `"label": _label(t.assigned_role),` 改为 `"label": role_label(t.assigned_role),`。

确认全仓库没有其他引用：

```bash
grep -rn "_label(" epms-api/app | grep -v role_label
```

Expected: 无输出（只剩 `role_label`）

- [ ] **Step 4: 实现 `signatories.py`**

创建 `epms-api/app/crud/signatories.py`：

```python
"""Resolves every person's name that a generated document PDF prints.

The PDF renderers in app/services/pdf_*.py are synchronous functions handed to
``loop.run_in_executor`` with nothing but an ORM object — they cannot open a DB
session. Names therefore have to be looked up here, in async context, and passed
in as arguments.
"""
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.current_step import role_label
from app.models.approval import ApprovalEvent
from app.models.user import User

# approval-api records an "approve" event for steps nobody actually acted on
# (the department has no Director, the configured Supervisor is inactive, ...).
# Its actor is the submitter, not an approver, so listing it would print the
# requester's own name in the Approvals table. The sibling marker
# "Auto-approved (same approver holds both roles)" IS a real person and stays.
# Same discriminator the PR/PA detail timelines use client-side.
_AUTO_SKIP_MARKER = "Auto-skipped"


async def resolve_user_names(
    db: AsyncSession, ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    """Map user ids to full names in a single query. Unknown/None ids are absent."""
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await db.execute(
        select(User.id, User.full_name).where(User.id.in_(wanted))
    )
    return {uid: name for uid, name in rows.all()}


async def approval_signatories(
    db: AsyncSession, doc_type: str, doc_id: uuid.UUID, created_by: uuid.UUID | None
) -> tuple[str | None, list[dict]]:
    """Return ``(requester_name, approvals)`` for a PR or PA PDF.

    ``approvals`` is ordered along the workflow chain; each entry is
    ``{"role": <display label>, "name": <full name or None>, "at": <datetime>}``.
    """
    requester = (await resolve_user_names(db, [created_by])).get(created_by)
    rows = (await db.execute(
        select(ApprovalEvent, User.full_name)
        .outerjoin(User, User.id == ApprovalEvent.actor_id)
        .where(
            ApprovalEvent.document_type == doc_type,
            ApprovalEvent.document_id == doc_id,
            ApprovalEvent.action == "approve",
        )
        .order_by(ApprovalEvent.step_idx, ApprovalEvent.created_at)
    )).all()
    approvals = [
        {"role": role_label(ev.actor_role), "name": name, "at": ev.created_at}
        for ev, name in rows
        if _AUTO_SKIP_MARKER not in (ev.comment or "")
    ]
    return requester, approvals


def _as_uuid(value) -> uuid.UUID | None:
    """Return the UUID a name column is really holding, or None if it's a name."""
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


async def gr_signatories(db: AsyncSession, gr) -> dict:
    """Return the names a GR PDF prints: creator, receiver, acknowledger.

    ``received_by`` / ``acknowledged_by`` are free-text columns the browser fills
    with the user's full name, but non-browser callers (NC import, PMS import,
    Teams) leave them empty and crud/gr.py's fallback stored a bare UUID string.
    Anything that parses as a UUID is looked up and replaced so PDFs for those
    rows print a name instead of an id.
    """
    stored = {"received_by": gr.received_by, "acknowledged_by": gr.acknowledged_by}
    as_uuid = {key: _as_uuid(value) for key, value in stored.items()}
    names = await resolve_user_names(
        db, [gr.created_by, *(u for u in as_uuid.values() if u is not None)]
    )
    resolved = {
        key: (names.get(as_uuid[key]) or value) for key, value in stored.items()
    }
    return {"created_by_name": names.get(gr.created_by), **resolved}
```

- [ ] **Step 5: 跑测确认通过**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_signatories.py -q
```

Expected: `4 passed`

若 `test_gr_signatories_resolves_uuid_shaped_names` 因 `PurchaseOrder` 字段不符报错，先跑
`grep -n "Mapped\[" epms-api/app/models/po.py` 核对必填列再补齐，**不要**改测试意图。

- [ ] **Step 6: 跑一遍 current_step 的下游，确认改名没打断别的**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_pr_filters.py tests/test_pr_scoping.py -q
```

Expected: 与基线一致（撰写计划时这两个文件全绿；若有失败，先确认是否与 `role_label` 无关）

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops-pdf-names
git add epms-api/app/crud/signatories.py epms-api/app/crud/current_step.py epms-api/tests/test_signatories.py
git commit -m "feat(epms): resolve requester and approver names for document PDFs

The PDF renderers run in a thread executor with no DB session, so every name
they print has to be looked up in async context first. Adds the single module
that does it, for PR/PA approval chains and for GR.

Auto-skipped approval events are excluded (their actor is the submitter, not an
approver); Auto-approved dual-role events are kept. GR name columns holding a
bare UUID from a non-browser caller are resolved back to a full name.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: PR PDF —— 渲染 + 接线

**Files:**
- Modify: `epms-api/app/services/pdf_template.py`（新增 `approvals_element`）
- Modify: `epms-api/app/services/pdf_pr.py`
- Modify: `epms-api/app/api/v1/pr.py:126-183`（`_generate_pr_pdf_background`）
- Modify: `epms-api/app/api/v1/pr_attachments.py:83-110`（`regenerate_pdf`）
- Test: `epms-api/tests/test_pdf_signatories.py`

**Interfaces:**
- Consumes: Task 1 的 `approval_signatories(db, "pr", pr.id, pr.created_by)`
- Produces:
  - `def approvals_element(approvals: list[dict] | None, W: float) -> list`（`pdf_template.py`，PR 与 PA 共用；`approvals` 为空返回 `[]`）
  - `def generate_pr_pdf(pr, company_name="EPMS", pdf_templates=None, logo_data_url=None, requester_name=None, approvals=None) -> bytes`

- [ ] **Step 1: 写失败测试**

创建 `epms-api/tests/test_pdf_signatories.py`：

```python
"""Renders PR / PA / GR PDFs and asserts the people's names actually land on the page.

Reuses the content-stream decode from tests/test_pr_pdf_department.py: reportlab
writes page text as ASCII85 + Flate, and there is no PDF text-extraction library
in this project's dependencies. All seeded names are ASCII so a raw substring
check on the inflated stream is reliable (non-ASCII would need font subsetting).
"""
import base64
import re
import uuid
import zlib
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.crud.signatories import approval_signatories
from app.models.approval import ApprovalEvent
from app.models.pr import PrLineItem, PurchaseRequest
from app.schemas.auth import RegisterRequest
from app.services.pdf_pr import generate_pr_pdf

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _pdf_text(pdf_bytes: bytes) -> bytes:
    """Inflate a reportlab PDF's page content streams so text can be asserted on."""
    out = bytearray()
    for match in _STREAM_RE.finditer(pdf_bytes):
        raw = match.group(1).strip(b"\r\n")
        try:
            data = raw.rstrip()
            if data.endswith(b"~>"):
                data = data[:-2]
            out += zlib.decompress(base64.a85decode(data))
        except Exception:
            continue  # binary/font streams are not ASCII85+Flate text
    return bytes(out)


async def _user(db: AsyncSession, full_name: str):
    u = await user_crud.create(db, RegisterRequest(
        email=f"pdfsig-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name=full_name, role="requester"))
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_pr_pdf_prints_requester_and_approvers(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rex Requester")
        manager = await _user(db, "Mona Manager")
        gm = await _user(db, "Gus General")
        pr = PurchaseRequest(
            number=f"PR-{uuid.uuid4().hex[:8]}", title="PDF signatory PR", type=2,
            status="approved", amount=Decimal("150.00"), currency="CAD",
            created_by=requester.id,
            line_items=[PrLineItem(
                description="Widget", qty=Decimal("3"), unit="ea",
                unit_price=Decimal("50.00"), line_total=Decimal("150.00"), sort_order=0)],
        )
        db.add(pr)
        await db.flush()
        db.add_all([
            ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                          step_idx=0, action="approve", actor_id=manager.id,
                          actor_role="dept_manager"),
            ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                          step_idx=1, action="approve", actor_id=requester.id,
                          actor_role="director",
                          comment="Auto-skipped (department has no Director)"),
            ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                          step_idx=2, action="approve", actor_id=gm.id,
                          actor_role="gm_or_opm"),
        ])
        await db.commit()

        requester_name, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)
        pdf_bytes = generate_pr_pdf(pr, "Test Co", None, None, requester_name, approvals)

        assert pdf_bytes[:4] == b"%PDF"
        text = _pdf_text(pdf_bytes)
        assert b"Requested By" in text
        assert b"Rex Requester" in text
        assert b"Approvals" in text
        assert b"Mona Manager" in text
        assert b"Gus General" in text
        # the auto-skipped step contributes neither a row nor a role label
        assert b"Director" not in text


@pytest.mark.asyncio
async def test_pr_pdf_without_signatories_still_renders(test_engine):
    """Guards the two legacy in-repo callers that pass only (pr, company_name)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Solo Requester")
        pr = PurchaseRequest(
            number=f"PR-{uuid.uuid4().hex[:8]}", title="Bare PR", type=2,
            status="approved", amount=Decimal("50.00"), currency="CAD",
            created_by=requester.id,
            line_items=[PrLineItem(
                description="Widget", qty=Decimal("1"), unit="ea",
                unit_price=Decimal("50.00"), line_total=Decimal("50.00"), sort_order=0)],
        )
        db.add(pr)
        await db.commit()

        pdf_bytes = generate_pr_pdf(pr, "Test Co")

        assert pdf_bytes[:4] == b"%PDF"
        assert b"Approvals" not in _pdf_text(pdf_bytes)
```

- [ ] **Step 2: 跑测确认失败**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_pdf_signatories.py -q
```

Expected: `test_pr_pdf_prints_requester_and_approvers` FAIL —— `generate_pr_pdf() takes from 1 to 4 positional arguments but 6 were given`

- [ ] **Step 3: 在 `pdf_template.py` 加共用的 `approvals_element`**

`epms-api/app/services/pdf_template.py` 顶部导入行补上 `Table` 与 `TableStyle`：

```python
from reportlab.platypus import HRFlowable, Image, Paragraph, Spacer, Table, TableStyle
```

在 `_DARK` 那组常量下面补一个（其余模块已各有一份同色值）：

```python
_LIGHT = colors.HexColor("#F5F5F5")
```

在 `terms_element` 之后追加：

```python
def approvals_element(approvals: list[dict] | None, W: float) -> list:
    """Approval-history table shared by the PR and PA PDFs.

    `approvals` comes from app/crud/signatories.approval_signatories(); each entry
    is {"role", "name", "at"}. Returns [] when there is nothing to show so the
    section disappears entirely rather than printing an empty header.
    """
    if not approvals:
        return []
    sec_style = _s("appr_sec", fontSize=10, textColor=_PRIMARY,
                   fontName="Helvetica-Bold", spaceAfter=4)
    th_style = _s("appr_th", fontSize=8, textColor=colors.white, fontName="Helvetica-Bold")
    td_style = _s("appr_td", fontSize=8, textColor=_DARK, fontName="Helvetica")

    rows = [[Paragraph(h, th_style) for h in ("Step", "Approved By", "Date")]]
    for entry in approvals:
        at = entry.get("at")
        rows.append([
            Paragraph(entry.get("role") or "—", td_style),
            Paragraph(entry.get("name") or "—", td_style),
            Paragraph(at.strftime("%Y-%m-%d") if at else "—", td_style),
        ])

    tbl = Table(rows, colWidths=[W * 0.30, W * 0.45, W * 0.25], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0), _PRIMARY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT]),
        ("TOPPADDING",     (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 4),
        ("LEFTPADDING",    (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 4),
        ("VALIGN",         (0, 0), (-1, -1), "TOP"),
    ]))
    return [Spacer(1, 5 * mm), Paragraph("Approvals", sec_style), tbl]
```

- [ ] **Step 4: 改 `pdf_pr.py`**

导入行加上 `approvals_element`：

```python
from app.services.pdf_template import (
    approvals_element, build_logo, footer_note_element, get_tmpl,
    header_note_element, terms_element,
)
```

函数签名改为（新参数**必须**追加在末尾：`run_in_executor` 只传位置参数）：

```python
def generate_pr_pdf(
    pr: PurchaseRequest,
    company_name: str = "EPMS",
    pdf_templates: dict | None = None,
    logo_data_url: str | None = None,
    requester_name: str | None = None,
    approvals: list[dict] | None = None,
) -> bytes:
```

在 `pr_type_str = ...` 那行下面补一个日期字符串：

```python
    submitted_str = pr.submitted_at.strftime("%Y-%m-%d") if pr.submitted_at else "—"
```

元信息格 `meta = Table([...])` 的行列表末尾追加第 6 行：

```python
            [*_cell("Requested By", requester_name), *_cell("Submitted", submitted_str)],
```

在 `elements.append(total_tbl)` 之后、`# ── Notes ──` 之前插入：

```python
    # ── Approvals ────────────────────────────────────────────────────────────
    elements.extend(approvals_element(approvals, W))
```

- [ ] **Step 5: 跑测确认通过**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_pdf_signatories.py tests/test_pr_pdf_department.py -q
```

Expected: `3 passed`

- [ ] **Step 6: 接线两个 PR 生成入口**

`epms-api/app/api/v1/pr.py::_generate_pr_pdf_background` —— 在 `loop = asyncio.get_event_loop()` 之前查名字，并把两个值追加为 `run_in_executor` 的位置参数：

```python
            from app.crud.signatories import approval_signatories
            requester_name, approvals = await approval_signatories(
                fresh_db, "pr", pr_id, pr_row.created_by
            )

            loop = asyncio.get_event_loop()
            pdf_bytes = await loop.run_in_executor(
                None, generate_pr_pdf, pr_row, company_name,
                cfg.pdf_templates if cfg else None,
                cfg.logo_data_url if cfg else None,
                requester_name, approvals,
            )
```

`epms-api/app/api/v1/pr_attachments.py::regenerate_pdf` —— 同样处理（该文件顶部已有 import 区，把 `from app.crud.signatories import approval_signatories` 加进去）：

```python
    requester_name, approvals = await approval_signatories(db, "pr", pr_id, pr.created_by)
    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(
        None, generate_pr_pdf, pr, company_name,
        cfg.pdf_templates if cfg else None,
        cfg.logo_data_url if cfg else None,
        requester_name, approvals,
    )
```

- [ ] **Step 7: 跑 PR 相关套件，对基线**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_pr.py tests/test_pr_filters.py tests/test_pr_scoping.py \
  tests/test_pr_pdf_department.py tests/test_pdf_signatories.py -q
```

Expected: `test_pr.py` 仍是那 **9 个存量失败**（`KeyError: 'status'` / `404 == 409` / `0 == 2`），失败数**不得增加**；其余全绿。

- [ ] **Step 8: Commit**

```bash
cd /c/Project/uniops-pdf-names
git add epms-api/app/services/pdf_template.py epms-api/app/services/pdf_pr.py \
        epms-api/app/api/v1/pr.py epms-api/app/api/v1/pr_attachments.py \
        epms-api/tests/test_pdf_signatories.py
git commit -m "feat(epms): print requester and approval chain on the PR PDF

Adds Requested By to the meta grid and an Approvals table (Step / Approved By /
Date) below the total, wired into both PDF entry points: the post-approval
background job and the Regenerate PDF endpoint. The table flowable lives in
pdf_template.py because the PA PDF needs the same one.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: PA PDF —— 渲染 + 接线

**Files:**
- Modify: `epms-api/app/services/pdf_pa.py`
- Modify: `epms-api/app/api/v1/pa.py:355-375`（审批通过分支）
- Modify: `epms-api/app/api/v1/pa_attachments.py:84-110`（`regenerate_pdf`）
- Test: `epms-api/tests/test_pdf_signatories.py`（追加用例）

**Interfaces:**
- Consumes: Task 1 的 `approval_signatories(db, "pa", pa.id, pa.created_by)`；Task 2 的 `approvals_element(approvals, W)`
- Produces: `def generate_pa_pdf(pa, company_name="EPMS", pdf_templates=None, logo_data_url=None, requester_name=None, approvals=None) -> bytes`

- [ ] **Step 1: 写失败测试**

在 `epms-api/tests/test_pdf_signatories.py` 顶部 import 区补上：

```python
from app.models.pa import PaLineItem, PaymentApplication
from app.models.vendor import Vendor
from app.services.pdf_pa import generate_pa_pdf
```

文件末尾追加：

```python
@pytest.mark.asyncio
async def test_pa_pdf_prints_applicant_and_approvers(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        applicant = await _user(db, "Amy Applicant")
        finance = await _user(db, "Fred Finance")
        vendor = Vendor(code=f"V{uuid.uuid4().hex[:6]}", name="PA PDF Vendor")
        db.add(vendor)
        await db.flush()
        pa = PaymentApplication(
            pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="PDF signatory PA",
            vendor_id=vendor.id, vendor_name=vendor.name, pa_type="regular",
            subtotal=Decimal("100.00"), tax_amount=Decimal("0"),
            payment_amount=Decimal("100.00"), currency="CAD", status="approved",
            created_by=applicant.id,
            line_items=[PaLineItem(
                description="Service", qty=Decimal("1"), unit="ea",
                unit_price=Decimal("100.00"), line_total=Decimal("100.00"), sort_order=0)],
        )
        db.add(pa)
        await db.flush()
        db.add(ApprovalEvent(
            document_type="pa", document_id=pa.id, document_number=pa.pa_number,
            step_idx=0, action="approve", actor_id=finance.id,
            actor_role="finance_manager"))
        await db.commit()

        requester_name, approvals = await approval_signatories(db, "pa", pa.id, pa.created_by)
        pdf_bytes = generate_pa_pdf(pa, "Test Co", None, None, requester_name, approvals)

        assert pdf_bytes[:4] == b"%PDF"
        text = _pdf_text(pdf_bytes)
        assert b"Applied By" in text
        assert b"Amy Applicant" in text
        assert b"Approvals" in text
        assert b"Fred Finance" in text
        assert b"Finance Manager" in text
```

- [ ] **Step 2: 跑测确认失败**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_pdf_signatories.py::test_pa_pdf_prints_applicant_and_approvers -q
```

Expected: FAIL —— `generate_pa_pdf() takes from 1 to 4 positional arguments but 6 were given`

- [ ] **Step 3: 改 `pdf_pa.py`**

导入行加上 `approvals_element`（按该文件现有的导入形式补进去，不要整段替换）：

```python
from app.services.pdf_template import (
    approvals_element, build_logo, footer_note_element, get_tmpl,
    header_note_element, terms_element,
)
```

签名改为：

```python
def generate_pa_pdf(
    pa: PaymentApplication,
    company_name: str = "EPMS",
    pdf_templates: dict | None = None,
    logo_data_url: str | None = None,
    requester_name: str | None = None,
    approvals: list[dict] | None = None,
) -> bytes:
```

在 `submitted_str = ...` 那行下面补：

```python
    approved_str = pa.approved_at.strftime("%Y-%m-%d") if pa.approved_at else "—"
```

元信息格行列表末尾追加第 5 行：

```python
            [*_cell("Applied By", requester_name), *_cell("Date Approved", approved_str)],
```

在 `elements.append(total_tbl)` 之后、`# ── Prepayment details ──` 之前插入：

```python
    # ── Approvals ────────────────────────────────────────────────────────────
    elements.extend(approvals_element(approvals, W))
```

- [ ] **Step 4: 跑测确认通过**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_pdf_signatories.py -q
```

Expected: `3 passed`

- [ ] **Step 5: 接线两个 PA 生成入口**

`epms-api/app/api/v1/pa.py` 审批通过分支 —— 在 `loop = asyncio.get_event_loop()` 之前：

```python
        from app.crud.signatories import approval_signatories
        requester_name, approvals = await approval_signatories(
            db, "pa", pa_id, pa.created_by
        )
        loop = asyncio.get_event_loop()
        pdf_bytes = await loop.run_in_executor(
            None, generate_pa_pdf, pa, company_name,
            cfg.pdf_templates if cfg else None,
            cfg.logo_data_url if cfg else None,
            requester_name, approvals,
        )
```

`epms-api/app/api/v1/pa_attachments.py::regenerate_pdf` —— 顶部 import 区加
`from app.crud.signatories import approval_signatories`，然后：

```python
    requester_name, approvals = await approval_signatories(db, "pa", pa_id, pa.created_by)
    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(
        None, generate_pa_pdf, pa, company_name,
        cfg.pdf_templates if cfg else None,
        cfg.logo_data_url if cfg else None,
        requester_name, approvals,
    )
```

- [ ] **Step 6: 跑 PA 相关套件，对基线**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/ -q -k "pa or pdf"
```

Expected: 失败数不高于基线（基线数字见末尾"验收"）。任何**新增**失败必须先修好再往下走。

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops-pdf-names
git add epms-api/app/services/pdf_pa.py epms-api/app/api/v1/pa.py \
        epms-api/app/api/v1/pa_attachments.py epms-api/tests/test_pdf_signatories.py
git commit -m "feat(epms): print applicant and approval chain on the PA PDF

Same treatment as the PR PDF, reusing pdf_template.approvals_element: Applied By
plus Date Approved in the meta grid, Approvals table below the total, wired into
the post-approval path and the Regenerate PDF endpoint.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: GR PDF —— Created By + 接线

**Files:**
- Modify: `epms-api/app/services/pdf_gr.py`
- Modify: `epms-api/app/crud/gr.py:497-527`（`_attach_gr_pdf`）
- Test: `epms-api/tests/test_pdf_signatories.py`（追加用例）

**Interfaces:**
- Consumes: Task 1 的 `gr_signatories(db, gr)`
- Produces: `def generate_gr_pdf(gr, company_name="EPMS", pdf_templates=None, logo_data_url=None, created_by_name=None, received_by=None, acknowledged_by=None) -> bytes`

GR 没有审批工作流（`approval_events` 里没有 `document_type='gr'` 的行），所以不套 Approvals 表格，改为扩充它现有的 Signatures 小节。GR PDF 在 acknowledge 时定版、之后不再重生成，因此 `Collected By`（更晚才写入）**刻意不做**。

- [ ] **Step 1: 写失败测试**

在 `epms-api/tests/test_pdf_signatories.py` import 区补上（`Vendor` 已在 Task 3 导入过，不要重复）：

```python
from app.crud.signatories import gr_signatories
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.po import PurchaseOrder
from app.services.pdf_gr import generate_gr_pdf
```

文件末尾追加：

```python
@pytest.mark.asyncio
async def test_gr_pdf_prints_creator_and_resolves_uuid_acknowledger(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        creator = await _user(db, "Wally Warehouse")
        acker = await _user(db, "Adam Acknowledger")
        vendor = Vendor(code=f"V{uuid.uuid4().hex[:6]}", name="GR PDF Vendor")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(
            number=f"PO-{uuid.uuid4().hex[:8]}", title="GR PDF PO", type=2,
            status="issued", vendor_id=vendor.id, vendor_name=vendor.name,
            currency="CAD", subtotal=Decimal("0"), total=Decimal("0"),
            created_by=creator.id,
        )
        db.add(po)
        await db.flush()
        gr = GoodsReceipt(
            number=f"GR-{uuid.uuid4().hex[:8]}", title="GR PDF signatories",
            po_id=po.id, po_number=po.number, vendor_id=vendor.id,
            vendor_name=vendor.name, gr_type="physical", procurement_type=2,
            currency="CAD", status="collection_pending",
            received_by="Wally Warehouse",
            acknowledged_by=str(acker.id),   # non-browser caller stored a raw UUID
            created_by=creator.id,
            line_items=[GrLineItem(
                description="Widget", qty_ordered=Decimal("3"), qty_received=Decimal("3"),
                unit="ea", unit_price=Decimal("50.00"), line_total=Decimal("150.00"),
                condition="good", sort_order=0)],
        )
        db.add(gr)
        await db.commit()

        sig = await gr_signatories(db, gr)
        pdf_bytes = generate_gr_pdf(
            gr, "Test Co", None, None,
            sig["created_by_name"], sig["received_by"], sig["acknowledged_by"],
        )

        assert pdf_bytes[:4] == b"%PDF"
        text = _pdf_text(pdf_bytes)
        assert b"Created By" in text
        assert b"Wally Warehouse" in text
        assert b"Adam Acknowledger" in text
        assert str(acker.id).encode("ascii") not in text   # the raw UUID never prints
```

- [ ] **Step 2: 跑测确认失败**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_pdf_signatories.py::test_gr_pdf_prints_creator_and_resolves_uuid_acknowledger -q
```

Expected: FAIL —— `generate_gr_pdf() takes from 1 to 4 positional arguments but 7 were given`

若报 `GrLineItem` 字段不符，先 `grep -n "Mapped\[" epms-api/app/models/gr.py` 核对必填列。

- [ ] **Step 3: 改 `pdf_gr.py`**

签名改为：

```python
def generate_gr_pdf(
    gr: GoodsReceipt,
    company_name: str = "EPMS",
    pdf_templates: dict | None = None,
    logo_data_url: str | None = None,
    created_by_name: str | None = None,
    received_by: str | None = None,
    acknowledged_by: str | None = None,
) -> bytes:
```

在 `gr_type_label = ...` 那行下面补上回落逻辑（参数为 None 时用 ORM 字段，保证老调用方行为不变）：

```python
    # crud/gr.py passes names already resolved against the users table; falling
    # back to the columns keeps the two legacy no-argument callers working.
    received_name = received_by if received_by is not None else gr.received_by
    acknowledged_name = acknowledged_by if acknowledged_by is not None else gr.acknowledged_by
```

把文件末尾的 Signatures 小节整段替换为：

```python
    # ── Created / Received / Acknowledged by ─────────────────────────────────
    if created_by_name or received_name or acknowledged_name:
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Signatures", sec_style))
        sig_data = []
        if created_by_name:
            sig_data.append([*_cell("Created By", created_by_name)])
        if received_name:
            sig_data.append([*_cell("Received By", received_name)])
        if acknowledged_name:
            sig_data.append([*_cell("Acknowledged By", acknowledged_name)])
        sig_tbl = Table(sig_data, colWidths=[W * 0.12, W * 0.38])
        sig_tbl.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(sig_tbl)
```

注意小节的渲染条件从 `if gr.received_by or gr.acknowledged_by` 放宽到把
`created_by_name` 也算进去 —— 否则两个字符串列都为空的 GR 会把 Created By 一起丢掉。

- [ ] **Step 4: 跑测确认通过**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_pdf_signatories.py -q
```

Expected: `4 passed`

- [ ] **Step 5: 接线 `_attach_gr_pdf`**

`epms-api/app/crud/gr.py` 顶部 import 区加 `from app.crud.signatories import gr_signatories`，
然后把 `_attach_gr_pdf` 里的 `run_in_executor` 调用改为：

```python
    sig = await gr_signatories(db, gr)
    loop = asyncio.get_running_loop()
    pdf_bytes = await loop.run_in_executor(
        None, generate_gr_pdf, gr, company_name,
        cfg.pdf_templates if cfg else None,
        cfg.logo_data_url if cfg else None,
        sig["created_by_name"], sig["received_by"], sig["acknowledged_by"],
    )
```

- [ ] **Step 6: 跑 GR 套件，对基线**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/ -q -k "gr or pdf"
```

Expected: 失败数不高于基线，无新增失败。

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops-pdf-names
git add epms-api/app/services/pdf_gr.py epms-api/app/crud/gr.py \
        epms-api/tests/test_pdf_signatories.py
git commit -m "feat(epms): print the creator on the GR PDF and stop printing raw UUIDs

GR has no approval chain, so its existing Signatures section grows a Created By
row resolved from goods_receipts.created_by. Received/Acknowledged names now come
in pre-resolved, so rows where a non-browser caller stored a bare UUID print a
person instead of an id.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 修 GR 写入侧的 UUID 兜底

**Files:**
- Modify: `epms-api/app/crud/gr.py:134, 218, 233, 249, 264, 267`
- Test: `epms-api/tests/test_signatories.py`（追加用例）

**Interfaces:**
- Consumes: Task 1 的 `resolve_user_names`
- Produces: `async def _actor_name(db: AsyncSession, actor_id: uuid.UUID) -> str`（`crud/gr.py` 内部私有）

浏览器会在请求体里带上 `user?.name`；NC 导入、PMS 导入、Teams 操作不带，现有兜底直接把
`str(actor_id)` 存进姓名列。共 **6 处**，用
`grep -n "or str(created_by)\|or str(actor_id)" epms-api/app/crud/gr.py` 核对。

- [ ] **Step 1: 写失败测试**

在 `epms-api/tests/test_signatories.py` import 区补 `from app.crud.gr import _actor_name`，文件末尾追加：

```python
@pytest.mark.asyncio
async def test_actor_name_prefers_full_name_over_uuid(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await _user(db, "Nina Named")
        await db.commit()

        assert await _actor_name(db, user.id) == "Nina Named"


@pytest.mark.asyncio
async def test_actor_name_falls_back_to_id_when_user_is_gone(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        missing = uuid.uuid4()

        assert await _actor_name(db, missing) == str(missing)
```

- [ ] **Step 2: 跑测确认失败**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_signatories.py -q
```

Expected: collection error —— `ImportError: cannot import name '_actor_name' from 'app.crud.gr'`

- [ ] **Step 3: 加 `_actor_name` 并替换 6 处兜底**

`epms-api/app/crud/gr.py` 顶部 import 区补 `from app.crud.signatories import resolve_user_names`
（Task 4 已加过 `gr_signatories`，追加到同一行即可），在 `_next_number` 之后加：

```python
async def _actor_name(db: AsyncSession, actor_id: uuid.UUID) -> str:
    """Display name for the acting user, falling back to the raw id if unknown.

    The browser sends the display name in the request body; NC/PMS imports and
    Teams actions do not. Storing a bare UUID in these name columns is what used
    to print an id on the GR PDF.
    """
    return (await resolve_user_names(db, [actor_id])).get(actor_id) or str(actor_id)
```

然后逐处替换（行号以改动前为准，改完会顺移）：

| 行 | 改前 | 改后 |
|---|---|---|
| 134 | `received_by=payload.received_by or str(created_by),` | `received_by=received_by_name,`（见下方说明） |
| 218 | `gr.acknowledged_by = req.acknowledged_by or str(actor_id)` | `gr.acknowledged_by = req.acknowledged_by or await _actor_name(db, actor_id)` |
| 233 | `gr.collected_by = req.acknowledged_by or str(actor_id)` | `gr.collected_by = req.acknowledged_by or await _actor_name(db, actor_id)` |
| 249 | `gr.collected_by = req.collected_by or str(actor_id)` | `gr.collected_by = req.collected_by or await _actor_name(db, actor_id)` |
| 264 | `gr.acknowledged_by = req.collected_by or str(actor_id)` | `gr.acknowledged_by = req.collected_by or await _actor_name(db, actor_id)` |
| 267 | `gr.collected_by = req.collected_by or str(actor_id)` | `gr.collected_by = req.collected_by or await _actor_name(db, actor_id)` |

第 134 行位于 `create()` 内、构造 `GoodsReceipt(...)` 的关键字参数里。Python 允许在参数
表达式里 `await`，但为可读性起见先在 `gr = GoodsReceipt(` 之前算好：

```python
    received_by_name = payload.received_by or await _actor_name(db, created_by)
```

改完核对没有漏网：

```bash
grep -n "or str(created_by)\|or str(actor_id)" epms-api/app/crud/gr.py
```

Expected: 无输出

- [ ] **Step 4: 跑测确认通过**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/test_signatories.py tests/test_pdf_signatories.py -q
```

Expected: `10 passed`（`test_signatories.py` 6 + `test_pdf_signatories.py` 4）

- [ ] **Step 5: 跑全量，与基线逐项比对**

```bash
set -a && . /c/Project/uniops/.env 2>/dev/null; set +a; \
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_DB=epms \
POSTGRES_PASSWORD="$DB_PASSWORD" JWT_SECRET_KEY="$JWT_SECRET_KEY" \
python -m pytest tests/ -q 2>&1 | tail -30
```

Expected: 失败数 = 基线失败数（见"验收"），passed 数 = 基线 + 10。**"无报错"不算通过 —— 必须把数字对上。**

- [ ] **Step 6: Commit**

```bash
cd /c/Project/uniops-pdf-names
git add epms-api/app/crud/gr.py epms-api/tests/test_signatories.py
git commit -m "fix(epms): stop storing raw UUIDs in the GR name columns

received_by/acknowledged_by/collected_by fell back to str(actor_id) whenever the
caller omitted a display name — every non-browser path (NC import, PMS import,
Teams) did. They now resolve against the users table, with the id kept only as a
last resort for deleted users.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## 验收

**基线数字：** 见本文件末尾"基线数字"一节（实施开始前由主会话实测填入）。跑完全量后必须做到：

1. 失败数**等于**基线失败数，且失败用例名逐一对得上（不是"差不多"）。
2. `tests/test_signatories.py` 6 passed、`tests/test_pdf_signatories.py` 4 passed。
3. `grep -n "or str(created_by)\|or str(actor_id)" epms-api/app/crud/gr.py` 无输出。
4. `grep -rn "_label(" epms-api/app | grep -v role_label` 无输出。
5. `git log --oneline main..HEAD` 应有 5 个 feat/fix commit + 2 个 docs commit（spec + 本计划）。

**人工点验（部署到 dev 后）：**

- 找一张已审批 PR，点 Detail 页的 **Regenerate PDF**，打开 PDF 确认元信息格有 `Requested By`、下方有 `Approvals` 表且角色/姓名/日期正确；若该 PR 的审批链里有被自动跳过的步骤，确认那一行**不出现**、且申请人姓名没被误列为审批人。
- 找一张已审批 PA 同样点 **Regenerate PDF**，确认 `Applied By` 与 `Approvals`。
- 新建一张 GR 走到 acknowledge，确认 PDF 的 Signatures 小节有 `Created By`，且三行都是人名不是 UUID。

**发布注意：**

- 无迁移，只需重建 `epms-api` 镜像。
- 存量已生成的 PDF 不会自动刷新：PR/PA 可用 Detail 页的 Regenerate PDF 按钮逐张重生成；GR 无该按钮，只有新单会带 Created By。

## 基线数字

**已取得（2026-08-07，独占跑，无并发）：`69 failed, 542 passed` in 839.86s。**

命令即"跑测环境"一节那条，目标 `tests/`。逐条失败清单存于
`.superpowers/sdd/2026-08-07-pdf-signatories/baseline-failures.txt`（69 行，含
`tests/test_pr.py` 的 9 条）。**PDF 相关测试在基线里全绿** —— 本次改动碰的任何
PDF 测试出现失败，一定是新引入的。

首次尝试测得的 `118 failed, 403 passed, 91 errors` **作废**：91 个 error 全是
`DependentObjectsStillExistError: cannot drop table business_partners ... constraint
purchase_agreements_vendor_id_fkey`，起因是另一个会话在并发跑 epms-api 套件、把
`purchase_agreements` 建回了共用的 `epms_test` 库，两边的 `Base.metadata.drop_all`
互相打架。重测前的处置：确认无并发 python 进程 →
`docker exec uniops_postgres psql -U epms -d epms_test -c "DROP TABLE IF EXISTS purchase_agreements CASCADE"`
→ 独占跑。**测试库是所有 worktree 共用的，跑套件前务必确认没有第二个 pytest 在跑。**
