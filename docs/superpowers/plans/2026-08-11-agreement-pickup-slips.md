# Pickup Slip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 house_account 协议的发票能对上真实凭证——后勤代录纸小票（OCR 预填、照片留存），发票匹配时勾选它覆盖的小票，并把 `legacy_settlement` 收窄回真正的例外通道。

**Architecture:** 新增 `agreement_pickup_slips` + `agreement_slip_attachments` 两张表，`invoices` 加 `slip_ids`(JSONB) 与 `slip_variance_reason`，单个迁移 `ag04`。匹配的**基线是人工按日期+金额勾选**，编号匹配与金额+日期预选只是加速路径，失效时无声退化。凭证释放**扩进 Phase 1B 已有的那个函数**，不新写平行实现。OCR 在 expense-api 加一个 `slip` 模式，**不碰 OA 在生产使用的 `receipt`**。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic（epms-api / expense-api）；React 18 + TanStack Query v5 + Tailwind（epms）；Claude Haiku 4.5 视觉（OCR）。

**Spec:** [docs/superpowers/specs/2026-08-11-agreement-pickup-slips-design.md](../specs/2026-08-11-agreement-pickup-slips-design.md)

---

## Global Constraints

以下约束适用于**每一个**任务。

- **跑测试的唯一正确姿势（已实测，别自己发明）**：`uniops_epms_api` 容器里**没有 pytest**，`docker exec ... pytest` 一定报 `No module named pytest`。测试在**宿主**跑：

  ```bash
  export PGPW=$(docker exec uniops_postgres env | sed -n 's/^POSTGRES_PASSWORD=//p')
  cd epms-api      # expense-api 同理，见该任务
  # 跑任何测试前先断言目标库是本地的 —— conftest 会 drop_all,
  # 而仓库根 .env 指向**生产库**，打错一次就是生产数据没了
  POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
    POSTGRES_PASSWORD="$PGPW" POSTGRES_DB=epms JWT_SECRET_KEY=test-secret \
    python -c "from app.core.config import settings; u=settings.DATABASE_URL; \
      assert 'localhost' in u or '127.0.0.1' in u, 'REFUSING'; print('DB OK')"
  ```
  之后 `$PYTEST` 一律指这一整串带 `POSTGRES_*` 覆盖的 `python -m pytest`。
- **测试库的表来自 `Base.metadata.create_all`（`tests/conftest.py:247-252`），不是 alembic。** 新模型**必须注册进 `app/models/__init__.py`**，否则建不出表——迁移写得再对也救不了。迁移是给 dev 库（人工点验）用的：`docker exec -w /app uniops_epms_api alembic upgrade head`（Git Bash 下要加 `MSYS_NO_PATHCONV=1`）。
- **已认证的测试 fixture 是 `admin_client`**（`tests/conftest.py:379`）。**没有 `auth_headers`**；裸 `client` 未认证。`seed_vendor_and_user(test_engine)` 返回 `(vendor_id, vendor_name, user_id)` 三元组。
- **测试库同一时刻只能跑一个套件**（`drop_all`）。**全量必须一次跑完**——分段跑会改变失败集合（存在跨文件顺序依赖），分段比对是软证据。
- **回归基线自己量，不许照抄**：动手前在未改动的 HEAD 上跑一次并记下失败集合。当前记录：epms-api **69 failed / 716 passed**（集合在 `.superpowers/sdd/2026-08-10-agreement-phase1b/baseline-failures.txt`）、epms tsc **58**（TS **5.9.3**，版本不对说明 node_modules 掉了 typescript，数字无意义）。**expense-api 基线本计划首次涉及，第一个碰它的任务负责测量并记录。**
- **迁移**：`ag04` 的 `down_revision` 必须是 `"ag03_agreement_schedule"`。若届时 head 已变（合并了 main），挂到真实链尾，**绝不手工 INSERT `alembic_version`**。
- **Decimal 序列化成字符串**：Pydantic 把 `Decimal` 发成 JSON **字符串**，前端一切算术/比较前必须 `Number()`。
- **UI 文案全英文**，注释可中文。前端浮层用 `createPortal` 到 `body`；动作按钮 `disabled={...isPending}`，mutation 的 `onSuccess` 要 `await` 会影响控件显隐的 `invalidateQueries`。
- **解析人名用 `/users/directory`**（任何登录用户可用），**不是** `GET /users`（system_admin 专属，对其他人 403）。`directoryAll()` 已有分页循环，用它。
- **权限走矩阵**：小票读写跟随已有的 `epms.agreement.read` / `epms.agreement.write`，AP 裁定用 `ApDep`（`epms-api/app/api/v1/invoices.py:606` 同款），**不新建权限键**。
- **不要在任何 UI 文案或注释里宣称"领用人已确认"**——系统里没有领用人的数字签认，只有代录人据交接事实填写的 `picked_by`（设计 §0 决策 7）。
- **提交**：每任务至少一个提交，说清"为什么"。commit 无需额外同意；**push 需要用户同意**。

## 文件结构

**epms-api 新建**

| 文件 | 职责 |
|---|---|
| `alembic/versions/ag04_pickup_slips.py` | 单迁移：2 新表 + `invoices` 2 列 + 2 索引 |
| `app/models/agreement_slip.py` | `AgreementPickupSlip` ORM |
| `app/models/agreement_slip_attachment.py` | `AgreementSlipAttachment` ORM |
| `app/schemas/agreement_slip.py` | Create/Update/Response/ListResponse/ApReview |
| `app/crud/agreement_slip.py` | 落库、候选池查询、认领、释放、老化查询 |
| `app/api/v1/agreement_slips.py` | 小票 CRUD + 作废 + AP 裁定 |
| `app/api/v1/agreement_slip_attachments.py` | 附件 list/upload/download/delete |

**epms-api 修改**：`app/models/__init__.py`、`app/models/invoice.py`（+2 列）、`app/crud/invoice.py`（释放扩容 + house_account 匹配分支）、`app/schemas/invoice.py`（`InvoiceMatchRequest` +`slip_ids`/`slip_variance_reason`）、`app/api/v1/pa.py`（house_account 闸门）、`app/api/v1/__init__.py`。

**expense-api 修改**：`app/services/ocr_service.py`（+`_SLIP_PROMPT` +`extract_slip`）、`app/api/v1/ocr.py`（mode 加 `slip`）。

**epms 前端新建**：`src/services/agreementSlips.ts`、`src/services/agreementSlipAttachments.ts`、`src/hooks/useAgreementSlips.ts`、`src/components/agreements/SlipEntryForm.tsx`、`src/components/agreements/SlipTable.tsx`。
**epms 前端修改**：`src/pages/agreements/AgreementDetailPage.tsx`、`src/pages/invoices/MatchPanel.tsx`、`src/hooks/useChainAttachments.ts`、`src/services/invoices.ts`。

---

## Task 1: 迁移 ag04 + 两个模型 + invoices 两列

**Files:**
- Create: `epms-api/alembic/versions/ag04_pickup_slips.py`
- Create: `epms-api/app/models/agreement_slip.py`
- Create: `epms-api/app/models/agreement_slip_attachment.py`
- Modify: `epms-api/app/models/invoice.py`, `epms-api/app/models/__init__.py`
- Test: `epms-api/tests/test_pickup_slip_model.py`

**Interfaces:**
- Produces: `AgreementPickupSlip`（表 `agreement_pickup_slips`）、`AgreementSlipAttachment`（表 `agreement_slip_attachments`）、`Invoice.slip_ids`、`Invoice.slip_variance_reason`

- [ ] **Step 1: 确认 alembic head**

Run:
```bash
cd epms-api/alembic/versions && python3 -c "
import re,glob
revs={};downs=set()
for f in glob.glob('*.py'):
    s=open(f,encoding='utf-8').read()
    m=re.search(r'^revision(?::\s*str)?\s*=\s*[\'\"]([^\'\"]+)',s,re.M)
    for x in re.findall(r'^down_revision(?::[^=]+)?\s*=\s*(.+)\$',s,re.M):
        downs.update(re.findall(r'[\'\"]([^\'\"]+)[\'\"]',x))
    if m: revs[m.group(1)]=f
print('HEADS:',[h for h in revs if h not in downs])"
```
Expected: `HEADS: ['ag03_agreement_schedule']`。不是的话挂到真实链尾。

- [ ] **Step 2: 写失败测试**

Create `epms-api/tests/test_pickup_slip_model.py`：

```python
"""agreement_pickup_slips / agreement_slip_attachments — model roundtrip."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_slip import AgreementPickupSlip
from app.models.agreement_slip_attachment import AgreementSlipAttachment
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _seed_agreement(db):
    """种子建在同一个 async session 里 —— conftest 的 seeded_vendor 走的是另一条
    未提交的 psycopg2 连接，async engine 看不见(FK 违约)。"""
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Princess Auto",
                    category="supplier", contact_name="AP", contact_email="ap@pa.example")
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"slip-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Slip Tester", role="procurement_officer"))
    await db.flush()
    agr = PurchaseAgreement(
        number=f"AGR-202608-T{uuid.uuid4().hex[:11]}", title="PA house account",
        agreement_type="house_account", vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31), created_by=user.id)
    db.add(agr)
    await db.flush()
    return agr, user


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def test_slip_roundtrip(test_engine):
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        slip = AgreementPickupSlip(
            agreement_id=agr.id, slip_date=date(2026, 7, 27), slip_ref="1-510076",
            amount=Decimal("100.00"), tax_amount=Decimal("13.00"),
            total_amount=Decimal("113.00"), picked_by=user.id, created_by=user.id,
            status="open")
        db.add(slip)
        await db.commit()
        slip_id = slip.id

    async with _factory(test_engine)() as db:
        got = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == slip_id))).scalar_one()
        assert got.slip_ref == "1-510076"
        assert got.status == "open"
        assert got.invoice_id is None
        assert got.missing_slip_reason is None


async def test_slip_ref_may_be_null_and_nulls_do_not_collide(test_engine):
    """基线匹配不依赖编号，所以 slip_ref 可空；部分唯一索引必须放过多行 NULL。"""
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        for _ in range(3):
            db.add(AgreementPickupSlip(
                agreement_id=agr.id, slip_date=date(2026, 7, 1), slip_ref=None,
                amount=Decimal("10.00"), tax_amount=Decimal("0"),
                total_amount=Decimal("10.00"), picked_by=user.id,
                created_by=user.id, status="open"))
        await db.commit()
        rows = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.agreement_id == agr.id))).scalars().all()
        assert len(rows) == 3


async def test_duplicate_slip_ref_on_same_agreement_is_rejected(test_engine):
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        for _ in range(2):
            db.add(AgreementPickupSlip(
                agreement_id=agr.id, slip_date=date(2026, 7, 1), slip_ref="DUP-1",
                amount=Decimal("10.00"), tax_amount=Decimal("0"),
                total_amount=Decimal("10.00"), picked_by=user.id,
                created_by=user.id, status="open"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_attachment_roundtrip(test_engine):
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        slip = AgreementPickupSlip(
            agreement_id=agr.id, slip_date=date(2026, 7, 1), amount=Decimal("10.00"),
            tax_amount=Decimal("0"), total_amount=Decimal("10.00"),
            picked_by=user.id, created_by=user.id, status="open")
        db.add(slip)
        await db.flush()
        att = AgreementSlipAttachment(
            slip_id=slip.id, filename="slip.jpg", content_type="image/jpeg",
            file_size=2048, storage_key=uuid.uuid4())
        db.add(att)
        await db.commit()
        att_id = att.id

    async with _factory(test_engine)() as db:
        got = (await db.execute(select(AgreementSlipAttachment).where(
            AgreementSlipAttachment.id == att_id))).scalar_one()
        assert got.filename == "slip.jpg"
        assert got.file_data is None


async def test_invoice_carries_slip_columns(test_engine):
    from app.models.invoice import Invoice
    cols = {c.name for c in Invoice.__table__.columns}
    assert "slip_ids" in cols
    assert "slip_variance_reason" in cols
```

- [ ] **Step 3: 跑测试确认失败**

Run: `$PYTEST tests/test_pickup_slip_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.agreement_slip'`

- [ ] **Step 4: 建 `app/models/agreement_slip.py`**

```python
"""ORM model for house-account pickup slips.

一张纸质凭证(柜台小票/送货单)的数字记录。它在 house_account 这条免收货链路上
扮演 GR 的角色 —— 但 ⚠️ **它不是领用人的数字签认**:小票由员工交给财务、财务
代录(设计 §0 决策 7),picked_by 是代录人据交接事实填的。任何 UI 文案都不得
写成"领用人已确认"。
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class AgreementPickupSlip(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "agreement_pickup_slips"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_agreements.id", ondelete="CASCADE"),
        nullable=False, index=True)

    # slip_date 与 total_amount 是基线匹配仅有的两个依据(设计 §4.1),都不可空。
    slip_date: Mapped[date] = mapped_column(Date, nullable=False)

    # 凭证上的参考号 —— **什么都行**:小票号、交易号、送货单号。
    # Princess Auto 的情况下由 OCR 抽出的 TILL + TRANS 拼成 "1-510076",
    # 但模型不关心它怎么来的,只当它是个不透明字符串。抽不到就是 NULL,
    # 基线匹配照常工作。
    slip_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default="0")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # 领用人 = 交单人。见类文档:这不是数字签认。
    picked_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    missing_slip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ap_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    ap_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # pending_ap_review | open | reconciled | voided | rejected
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="open", index=True)

    # 认领它的发票。一张小票只属于一张发票;反过来一张发票可覆盖多张小票
    # (invoices.slip_ids 是数组),所以这里**不加唯一索引**。
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
```

- [ ] **Step 5: 建 `app/models/agreement_slip_attachment.py`**

照抄 `app/models/pr_attachment.py`，逐项替换：`PrAttachment` → `AgreementSlipAttachment`、`pr_attachments` → `agreement_slip_attachments`、`pr_id` → `slip_id`、外键 `purchase_requests.id` → `agreement_pickup_slips.id`。其余（`filename` / `content_type` / `file_size` / `file_data` / `storage_key`）一字不改。

- [ ] **Step 6: `Invoice` 加 2 列**

在 `app/models/invoice.py` 的 `schedule_id` 之后：

```python
    # 本次对账覆盖的小票集合 —— 对应 gr_ids 的角色。**数组**:逐笔发票下长度为 1,
    # 月结汇总单下长度为 N。用单值会把"一张发票只对一张小票"这个供应商的偶然
    # 事实固化成模型(设计 §2.3)。
    slip_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 小票合计与发票金额的差额说明。**与 legacy_settlement_reason 分开存**:
    # "对上了但差几块"与"根本没有凭证"是两件事,混存会让 legacy 计数失去意义。
    slip_variance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

`JSONB` 已在该文件 import（`gr_ids` / `line_items` 在用）。

- [ ] **Step 7: 注册模型**

`app/models/__init__.py` 按既有 `# noqa: F401` 写法追加两个新模型的 import。**这一步是载荷性的**——测试库的表来自 `create_all`，不注册就没有表。

- [ ] **Step 8: 写迁移 `ag04_pickup_slips.py`**

```python
"""house-account pickup slips, their attachments, and invoice slip links

Revision ID: ag04_pickup_slips
Revises: ag03_agreement_schedule
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag04_pickup_slips"
down_revision = "ag03_agreement_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agreement_pickup_slips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_agreements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slip_date", sa.Date(), nullable=False),
        sa.Column("slip_ref", sa.String(64), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("picked_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("missing_slip_reason", sa.Text(), nullable=True),
        sa.Column("ap_reviewed_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("ap_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_agr_slip_agreement_id", "agreement_pickup_slips", ["agreement_id"])
    op.create_index("ix_agr_slip_ref", "agreement_pickup_slips", ["slip_ref"])
    op.create_index("ix_agr_slip_status", "agreement_pickup_slips", ["status"])
    op.create_index("ix_agr_slip_invoice_id", "agreement_pickup_slips", ["invoice_id"])
    # 部分唯一索引:slip_ref 为 NULL 的多行必须能共存 —— 参考号可能糊了、
    # 可能压根没有、OCR 可能抽不到,那些行之间没有可去重的依据。
    op.create_index(
        "uq_agr_slip_ref_per_agreement", "agreement_pickup_slips",
        ["agreement_id", "slip_ref"], unique=True,
        postgresql_where=sa.text("slip_ref IS NOT NULL"))

    op.create_table(
        "agreement_slip_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agreement_pickup_slips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False,
                  server_default="application/octet-stream"),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("file_data", sa.LargeBinary(), nullable=True),
        sa.Column("storage_key", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_agr_slip_att_slip_id", "agreement_slip_attachments", ["slip_id"])

    # invoices 是共享表,但 expense-api / finance-api / approval-api 三个镜像都是
    # 显式列清单,看不见这两个新的可空列 —— 因此**没有部署顺序约束**。
    op.add_column("invoices", sa.Column("slip_ids", postgresql.JSONB(), nullable=True))
    op.add_column("invoices", sa.Column("slip_variance_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "slip_variance_reason")
    op.drop_column("invoices", "slip_ids")
    op.drop_index("ix_agr_slip_att_slip_id", table_name="agreement_slip_attachments")
    op.drop_table("agreement_slip_attachments")
    for ix in ("uq_agr_slip_ref_per_agreement", "ix_agr_slip_invoice_id",
               "ix_agr_slip_status", "ix_agr_slip_ref", "ix_agr_slip_agreement_id"):
        op.drop_index(ix, table_name="agreement_pickup_slips")
    op.drop_table("agreement_pickup_slips")
```

> ⚠️ `Base.metadata.create_all` **不会**建 `postgresql_where` 那个部分唯一索引——它只按模型定义建。要让 `test_duplicate_slip_ref_on_same_agreement_is_rejected` 在测试库里真的触发 `IntegrityError`，必须在模型里也声明它。在 `AgreementPickupSlip` 类体末尾加：
> ```python
>     __table_args__ = (
>         sa.Index("uq_agr_slip_ref_per_agreement", "agreement_id", "slip_ref",
>                  unique=True, postgresql_where=sa.text("slip_ref IS NOT NULL")),
>     )
> ```
> 并 `import sqlalchemy as sa`。模型与迁移必须逐字一致——这个代码库已经三次被"镜像/模型与物理表漂移"咬过。

- [ ] **Step 9: 对 dev 库跑迁移**

Run:
```bash
MSYS_NO_PATHCONV=1 docker exec -w /app uniops_epms_api alembic upgrade head
MSYS_NO_PATHCONV=1 docker exec -w /app uniops_epms_api alembic current
```
Expected: `ag04_pickup_slips (head)`

- [ ] **Step 10: 跑测试确认通过**

Run: `$PYTEST tests/test_pickup_slip_model.py -v`
Expected: 5 passed

- [ ] **Step 11: 提交**

```bash
git add epms-api/alembic/versions/ag04_pickup_slips.py epms-api/app/models/agreement_slip.py \
        epms-api/app/models/agreement_slip_attachment.py epms-api/app/models/invoice.py \
        epms-api/app/models/__init__.py epms-api/tests/test_pickup_slip_model.py
git commit -m "feat(agreement): pickup slip tables and invoice slip links"
```

---

## Task 2: 小票 CRUD + 端点（录入 / 列表 / 作废 / AP 裁定）

**Files:**
- Create: `epms-api/app/schemas/agreement_slip.py`, `epms-api/app/crud/agreement_slip.py`, `epms-api/app/api/v1/agreement_slips.py`
- Modify: `epms-api/app/api/v1/__init__.py`
- Test: `epms-api/tests/test_pickup_slip_api.py`

**Interfaces:**
- Consumes: Task 1 的 `AgreementPickupSlip`
- Produces:
  ```python
  # schemas
  class SlipCreate(BaseModel):
      slip_date: date; slip_ref: str | None; amount: Decimal; tax_amount: Decimal
      total_amount: Decimal; picked_by: uuid.UUID; missing_slip_reason: str | None
      notes: str | None
  class SlipUpdate(BaseModel): ...        # 同上全可选
  class SlipResponse(BaseModel): ...      # from_attributes，含 status/invoice_id/ap_reviewed_*
  class SlipListResponse(BaseModel): items: list[SlipResponse]; total: int
  class SlipApReview(BaseModel): action: str   # approve | reject

  # crud
  async def create(db, agr, body: SlipCreate, created_by) -> AgreementPickupSlip
  async def list_for_agreement(db, agreement_id, status: str | None) -> list[...]
  async def void(db, slip) -> None          # 只允许 open / pending_ap_review
  async def ap_review(db, slip, action: str, reviewer_id) -> AgreementPickupSlip

  # 端点
  # GET/POST /agreements/{agreement_id}/slips
  # PATCH/DELETE /agreements/{agreement_id}/slips/{slip_id}
  # POST /agreements/{agreement_id}/slips/{slip_id}/ap-review
  ```

- [ ] **Step 1: 写失败测试**

Create `epms-api/tests/test_pickup_slip_api.py`。用 `admin_client`、`seed_vendor_and_user`，协议用 `POST /api/v1/agreements` 建（payload 照抄 `tests/test_agreements.py::_agr_payload`，`agreement_type="house_account"`）。覆盖：

```python
async def test_create_slip_with_photo_lands_open(admin_client, ...):
    # 有附件的情形由 Task 3 覆盖;这里传 missing_slip_reason=None 且不带附件,
    # 仍应落 open —— 附件必填是**前端**规则,后端只在给了 missing_slip_reason
    # 时才走 pending_ap_review。理由:后端无法可靠判断"用户是否打算传附件",
    # 把它做成后端强制会让"先建行再传附件"的两步上传流程无法进行。

async def test_create_slip_with_missing_reason_lands_pending_ap_review(...):

async def test_ap_review_approve_moves_to_open_and_stamps_reviewer(...):

async def test_ap_review_reject_moves_to_rejected(...):

async def test_ap_review_on_an_open_slip_is_409(...):
    # 只有 pending_ap_review 可裁定

async def test_void_open_slip_succeeds(...):

async def test_void_reconciled_slip_is_409(...):
    # 已认领的必须先释放 —— 直接作废会让发票挂着一张 voided 凭证

async def test_list_filters_by_status(...):

async def test_slips_of_another_agreement_are_not_listed(...):
```

每条写成完整可运行的用例，不留 `...`。

- [ ] **Step 2: 跑测试确认失败** — 404（路由不存在）

- [ ] **Step 3: 实现 schemas**

`app/schemas/agreement_slip.py`，按上面 Interfaces 的签名写。`SlipCreate` 加一个校验器：

```python
    @model_validator(mode="after")
    def _totals_are_consistent(self):
        if self.total_amount != self.amount + self.tax_amount:
            raise ValueError("total_amount must equal amount + tax_amount")
        return self
```

理由：三个金额都由 OCR 预填且都可编辑，人改了一个忘了另一个是常态；不校验的话对账差额会莫名其妙。

- [ ] **Step 4: 实现 crud**

`app/crud/agreement_slip.py`：

```python
async def create(db, agr, body, created_by):
    slip = AgreementPickupSlip(
        agreement_id=agr.id, created_by=created_by,
        status="pending_ap_review" if body.missing_slip_reason else "open",
        **body.model_dump(exclude={"missing_slip_reason"}),
        missing_slip_reason=body.missing_slip_reason)
    db.add(slip)
    await db.flush()
    return slip


VOIDABLE = ("open", "pending_ap_review")

async def void(db, slip):
    if slip.status not in VOIDABLE:
        raise ValueError(
            f"A {slip.status} slip cannot be voided; detach its invoice first")
    slip.status = "voided"
    await db.flush()


async def ap_review(db, slip, action, reviewer_id):
    if slip.status != "pending_ap_review":
        raise ValueError(f"Slip is {slip.status}; only a slip awaiting AP review can be decided")
    if action not in ("approve", "reject"):
        raise ValueError("action must be 'approve' or 'reject'")
    slip.status = "open" if action == "approve" else "rejected"
    slip.ap_reviewed_by = reviewer_id
    slip.ap_reviewed_at = datetime.now(timezone.utc)
    await db.flush()
    return slip
```

- [ ] **Step 5: 实现端点**

`app/api/v1/agreement_slips.py`，前缀 `/agreements/{agreement_id}/slips`。读用 `require_permission("epms.agreement.read")`，写/作废用 `require_permission("epms.agreement.write")`，**AP 裁定用 `ApDep`**（照 `app/api/v1/invoices.py:606` 的用法 import）。`ValueError` → 409；协议不存在 → 404；小票不属于该协议 → 404。

在 `app/api/v1/__init__.py` include 路由。

- [ ] **Step 6: 跑测试确认通过** — 9 passed，且 `tests/test_agreements.py` 不回归

- [ ] **Step 7: 提交**

```bash
git add epms-api/app/schemas/agreement_slip.py epms-api/app/crud/agreement_slip.py \
        epms-api/app/api/v1/agreement_slips.py epms-api/app/api/v1/__init__.py \
        epms-api/tests/test_pickup_slip_api.py
git commit -m "feat(agreement): pickup slip entry, void and AP review"
```

---

## Task 3: 小票附件路由

**Files:**
- Create: `epms-api/app/api/v1/agreement_slip_attachments.py`
- Modify: `epms-api/app/api/v1/__init__.py`
- Test: `epms-api/tests/test_pickup_slip_attachments.py`

- [ ] **Step 1: 写失败测试**

覆盖：上传后能列出、下载拿到原字节、删除后列表为空、上传到不存在的小票是 404。

**测试必须把 file-api 打桩**——直连真实 `uniops_file_api` 会因为 JWT 密钥不同而 401，那种测试在 CI 和别人机器上永远是红的。照抄 `tests/test_agreement_attachments.py` 里那个 `autouse=True` 的 `fake_file_server` fixture，patch **模块内绑定名**：`app.api.v1.agreement_slip_attachments.upload_to_file_server` / `.proxy_download` / `.delete_from_file_server`（patch 定义处无效）。

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

**照抄 `epms-api/app/api/v1/pr_attachments.py`**，替换：`PrAttachment`→`AgreementSlipAttachment`、`pr` crud→`agreement_slip` crud、前缀→`/agreements/{agreement_id}/slips/{slip_id}/attachments`、`pr_id`→`slip_id`、404 文案→`"Slip not found"`、权限→读 `epms.agreement.read` / 写删 `epms.agreement.write`。**不要照抄 `regenerate-pdf`**。

这是第 6 份同款附件路由，重复由 owner 拍板接受，不要抽公共工厂。

- [ ] **Step 4: 跑测试确认通过** — 4 passed

- [ ] **Step 5: 提交**

```bash
git add epms-api/app/api/v1/agreement_slip_attachments.py epms-api/app/api/v1/__init__.py \
        epms-api/tests/test_pickup_slip_attachments.py
git commit -m "feat(agreement): pickup slip attachments via file-api"
```

---

## Task 4: 凭证释放扩容（排期行 + 小票）

**这是本计划风险最高的一处。** Phase 1B 在"认领了不释放"上栽过两次，第二次的后果是重新认领时确认痕迹还在，**无人确认就付了款**。小票比排期行更容易出问题，因为是**多张**。

**Files:**
- Modify: `epms-api/app/crud/invoice.py`（`_release_schedule_row` 及其 3 个调用点）
- Test: `epms-api/tests/test_slip_release.py`

**Interfaces:**
- Produces：`_release_schedule_row` 改名 `_release_agreement_evidence(db, invoice)`，语义扩为"释放该发票持有的**全部**协议侧凭证"——排期行（单个）与小票（多张）。

- [ ] **Step 1: 写失败测试**

Create `epms-api/tests/test_slip_release.py`：

```python
"""凭证释放 —— 排期行与小票必须一起放回,否则重新认领会带着旧痕迹。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.crud.invoice import _release_agreement_evidence
from app.models.agreement import PurchaseAgreement
from app.models.agreement_slip import AgreementPickupSlip
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed(db):
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Princess Auto",
                    category="supplier", contact_name="AP", contact_email="ap@pa.example")
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"rel-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="T", role="procurement_officer"))
    await db.flush()
    agr = PurchaseAgreement(
        number=f"AGR-202608-T{uuid.uuid4().hex[:11]}", title="PA", 
        agreement_type="house_account", vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31), created_by=user.id)
    db.add(agr)
    await db.flush()
    return agr, vendor, user


async def _invoice(db, vendor, user, total="100.00"):
    inv = Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
        vendor_invoice_number=f"P{uuid.uuid4().hex[:6]}",
        vendor_id=vendor.id, vendor_name=vendor.name,
        amount=Decimal(total), tax_amount=Decimal("0"), total_amount=Decimal(total),
        currency="CAD", invoice_date=date(2026, 8, 1), due_date=date(2026, 9, 1),
        status="matched", line_items=[], uploaded_by=user.id)
    db.add(inv)
    await db.flush()
    return inv


async def _slip(db, agr, user, *, invoice=None, ref=None, total="10.00"):
    slip = AgreementPickupSlip(
        agreement_id=agr.id, slip_date=date(2026, 7, 15), slip_ref=ref,
        amount=Decimal(total), tax_amount=Decimal("0"), total_amount=Decimal(total),
        picked_by=user.id, created_by=user.id,
        status="reconciled" if invoice else "open",
        invoice_id=invoice.id if invoice else None)
    db.add(slip)
    await db.flush()
    return slip


async def test_release_frees_every_claimed_slip_not_just_the_first(test_engine):
    """三张小票必须全部释放。只断言一张会漏掉"只释放了第一张"——
    这是遍历写错时最可能的表现,而且线上表现为部分期次永久锁死。"""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        inv = await _invoice(db, vendor, user)
        slips = [await _slip(db, agr, user, invoice=inv, ref=f"R-{i}") for i in range(3)]
        inv.slip_ids = [str(s.id) for s in slips]
        inv.slip_variance_reason = "rounding"
        await db.flush()

        await _release_agreement_evidence(db, inv)
        await db.commit()

    async with _factory(test_engine)() as db:
        rows = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id.in_([s.id for s in slips])))).scalars().all()
        assert len(rows) == 3
        assert all(r.status == "open" for r in rows)
        assert all(r.invoice_id is None for r in rows)
        fresh = (await db.execute(select(Invoice).where(Invoice.id == inv.id))).scalar_one()
        assert fresh.slip_ids is None
        assert fresh.slip_variance_reason is None


async def test_release_leaves_slips_claimed_by_another_invoice_alone(test_engine):
    """同协议下别的发票认领的小票不能被抢回来。"""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        inv_a = await _invoice(db, vendor, user)
        inv_b = await _invoice(db, vendor, user)
        mine = await _slip(db, agr, user, invoice=inv_a, ref="MINE")
        theirs = await _slip(db, agr, user, invoice=inv_b, ref="THEIRS")
        # inv_a 的 slip_ids 里混进了一张其实属于 inv_b 的小票(数据不一致的情形)
        inv_a.slip_ids = [str(mine.id), str(theirs.id)]
        await db.flush()

        await _release_agreement_evidence(db, inv_a)
        await db.commit()

    async with _factory(test_engine)() as db:
        m = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == mine.id))).scalar_one()
        t = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == theirs.id))).scalar_one()
        assert m.status == "open" and m.invoice_id is None
        assert t.status == "reconciled" and t.invoice_id == inv_b.id


async def test_released_slip_can_be_claimed_again(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        inv1 = await _invoice(db, vendor, user)
        slip = await _slip(db, agr, user, invoice=inv1, ref="REUSE")
        inv1.slip_ids = [str(slip.id)]
        await db.flush()
        await _release_agreement_evidence(db, inv1)
        await db.flush()

        inv2 = await _invoice(db, vendor, user)
        fresh = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == slip.id))).scalar_one()
        assert fresh.status == "open"
        fresh.status = "reconciled"
        fresh.invoice_id = inv2.id
        await db.commit()


async def test_release_is_a_noop_when_the_invoice_holds_no_slips(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        inv = await _invoice(db, vendor, user)
        untouched = await _slip(db, agr, user, ref="UNTOUCHED")
        await _release_agreement_evidence(db, inv)
        await db.commit()

    async with _factory(test_engine)() as db:
        row = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == untouched.id))).scalar_one()
        assert row.status == "open" and row.invoice_id is None
```

另外在 `tests/test_agreement_invoice_match.py` 补两条走**完整 `match()` 入口**的用例（照该文件既有写法）：`test_route_switch_to_po_releases_claimed_slips` 与 `test_match_review_reject_releases_claimed_slips`。单元层证明函数对，不等于三个调用点真的调了它——Phase 1B 的教训就是"钩子删掉测试照样全绿"。

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 改名并扩容**

把 `_release_schedule_row` 改名为 `_release_agreement_evidence`，在原有排期行释放之后追加小票释放：

```python
    # 小票释放 —— 与排期行同理,但小票是**多张**:invoice.slip_ids 是数组。
    # 逐张放回 open 并清 invoice_id;只动确实由这张发票持有的行(防止把别的
    # 发票刚认领的同一张小票抢回来 —— 当前不可达,但 Task 6 的匹配分支会写
    # 这个字段,不变量要自己成立,不能依赖调用方)。
    slip_ids = invoice.slip_ids or []
    if slip_ids:
        rows = (await db.execute(
            select(AgreementPickupSlip).where(AgreementPickupSlip.id.in_(slip_ids))
        )).scalars().all()
        for row in rows:
            if row.invoice_id != invoice.id:
                logger.warning(
                    "_release_agreement_evidence: slip %s is claimed by invoice %s, "
                    "not %s — leaving it alone", row.id, row.invoice_id, invoice.id)
                continue
            row.status = "open"
            row.invoice_id = None
    invoice.slip_ids = None
    invoice.slip_variance_reason = None
    await db.flush()
```

三个调用点（`invoice.py:635`、`:927`，以及 Task 6 会新增的协议改挂路径）随改名更新。文件顶部补 `from app.models.agreement_slip import AgreementPickupSlip`。

- [ ] **Step 4: 跑测试确认通过**

Run: `$PYTEST tests/test_slip_release.py tests/test_agreement_invoice_match.py -v`
Expected: 新增 5 passed；`test_agreement_invoice_match.py` **既有用例全部仍过**（改名不能改行为）

- [ ] **Step 5: 提交**

```bash
git add epms-api/app/crud/invoice.py epms-api/tests/test_slip_release.py
git commit -m "fix(agreement): release claimed slips alongside schedule rows"
```

---

## Task 5: house_account 匹配分支 + legacy_settlement 收窄

**Files:**
- Modify: `epms-api/app/crud/invoice.py`（`_match_to_agreement` 的 house_account 分支）、`epms-api/app/schemas/invoice.py`
- Test: `epms-api/tests/test_slip_match.py`

**Interfaces:**
- Consumes: Task 2 的 `app/crud/agreement_slip.py`
- Produces：
  - `InvoiceMatchRequest` 新增 `slip_ids: list[uuid.UUID] | None` 与 `slip_variance_reason: str | None`
  - `agreement_slip_crud.claim(db, agr, slip_ids: list[uuid.UUID], invoice) -> list[AgreementPickupSlip]` —— 逐张校验归属与 `status == "open"`，不合格抛 `ValueError`；通过则置 `reconciled` + `invoice_id` 并返回

- [ ] **Step 1: 写失败测试**

覆盖：

```python
async def test_house_account_match_with_slips_does_not_flag_legacy(...):
    """选了小票 → legacy_settlement 为 False、reason 为 None、slip_ids 落库、
    每张小票转 reconciled 并记 invoice_id。**这是本特性的核心断言** ——
    协议详情那个 "settled without receipt" 计数从此只数真正无凭证的。"""

async def test_house_account_match_without_slips_still_requires_a_reason(...):
    """一张都不选 → 回到 1A 的无凭证通道:reason 必填,legacy_settlement=True。"""

async def test_match_rejects_a_slip_from_another_agreement(...):

async def test_match_rejects_a_slip_that_is_not_open(...):
    """pending_ap_review / rejected / reconciled / voided 都不可认领。"""

async def test_variance_reason_is_stored_separately_from_legacy_reason(...):
    """差额说明写 slip_variance_reason,不碰 legacy_settlement_reason。"""

async def test_match_accepts_multiple_slips(...):
    """N:1 —— slip_ids 长度 3,三张全部 reconciled。"""
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 改 `_match_to_agreement` 的 house_account 分支**

现在那段无条件要求理由的代码，替换为：

```python
    if agr.agreement_type == "house_account":
        slip_ids = req.slip_ids or []
        if slip_ids:
            claimed = await agreement_slip_crud.claim(db, agr, slip_ids, invoice)
            invoice.slip_ids = [str(s.id) for s in claimed]
            invoice.slip_variance_reason = (req.slip_variance_reason or "").strip() or None
            invoice.legacy_settlement = False
            invoice.legacy_settlement_reason = None
        else:
            # 一张小票都没选 —— 这才是真正的无凭证付款,1A 的通道保留给它。
            # 收窄的意义就在这里:有凭证时不该被问"为什么没有凭证",
            # 否则协议详情那个健康度计数恒等于 100%,什么也暴露不了。
            reason = (req.legacy_settlement_reason or "").strip()
            if not reason:
                raise AgreementMatchInvalid(
                    "Select the pickup slips this invoice covers, or give a reason "
                    "for settling it without any receipt evidence")
            invoice.legacy_settlement = True
            invoice.legacy_settlement_reason = reason
            invoice.slip_ids = None
            invoice.slip_variance_reason = None
```

`agreement_slip_crud.claim(db, agr, slip_ids, invoice)` 新增在 `app/crud/agreement_slip.py`：逐张校验 `agreement_id == agr.id` 且 `status == "open"`，否则 `ValueError`（由 `_match_to_agreement` 转 `AgreementMatchInvalid`）；通过则置 `reconciled` + `invoice_id`，返回列表。

`app/schemas/invoice.py` 的 `InvoiceMatchRequest` 加两个字段，并更新那条现在只提 house_account 必填理由的注释。

- [ ] **Step 4: 跑测试确认通过**

Run: `$PYTEST tests/test_slip_match.py tests/test_agreement_invoice_match.py tests/test_agreements.py -v`

**预期有既有用例失败**：`test_agreement_invoice_match.py` 里断言 house_account 匹配必须填理由的那些，现在在**不选小票**时仍应通过；若有用例在**选了小票**的情形下仍断言 `legacy_settlement=True`，那是旧行为，改测试并在提交信息里说明。**不要为了让测试变绿而削弱断言。**

- [ ] **Step 5: 提交**

```bash
git add epms-api/app/crud/invoice.py epms-api/app/crud/agreement_slip.py \
        epms-api/app/schemas/invoice.py epms-api/tests/test_slip_match.py \
        epms-api/tests/test_agreement_invoice_match.py
git commit -m "feat(agreement): match house-account invoices against pickup slips"
```

---

## Task 6: house_account 的 PA 凭证闸门

**Files:**
- Modify: `epms-api/app/api/v1/pa.py`
- Test: `epms-api/tests/test_slip_pa_gate.py`

- [ ] **Step 1: 写失败测试**

```python
async def test_house_account_pa_refused_when_invoice_has_neither_slips_nor_legacy(...):
    """422,文案要说清缺什么。"""

async def test_house_account_pa_allowed_with_slips(...):

async def test_house_account_pa_allowed_for_a_legacy_settled_invoice(...):
    """存量发票都是这一类 —— 本期改动**不能卡死历史数据**。"""

async def test_recurring_and_milestone_gates_are_unchanged(...):
    """新闸门只针对 house_account;另两类走各自的规则。"""
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

在 `pa.py` 的协议分支里，紧挨 recurring 闸门之后加：

```python
        # house_account 免收货,凭证就是小票。1A 时没有小票可挂,所以每张发票都被
        # 标成 legacy —— 那些存量数据必须继续放行,否则本期改动会卡死历史。
        # pa.py:94 那句 "house_account 走 slip 路径" 的注释从此才是真的。
        if agr.agreement_type == "house_account":
            unsupported = [
                r.internal_ref for r in rows
                if not (r.slip_ids or r.legacy_settlement)
            ]
            if unsupported:
                raise HTTPException(
                    status_code=422,
                    detail=(f"No pickup slips are attached to {', '.join(unsupported)}. "
                            "Match the invoice to the slips it covers, or settle it "
                            "explicitly without receipt evidence, before raising payment."))
```

`rows` 需要多查两列（`slip_ids`、`legacy_settlement`）——扩那条已有的 `select(Invoice.id, Invoice.internal_ref, ...)`。同时更新 `pa.py:94` 那条注释。

- [ ] **Step 4: 跑测试确认通过**

Run: `$PYTEST tests/test_slip_pa_gate.py tests/test_agreement_pa.py -v`
Expected: 新增 4 passed，`test_agreement_pa.py` 既有用例**必须全过**——它们大多是 house_account 且是 legacy，正好验证不卡历史数据

- [ ] **Step 5: 提交**

```bash
git add epms-api/app/api/v1/pa.py epms-api/tests/test_slip_pa_gate.py
git commit -m "feat(agreement): require slip evidence or an explicit legacy settlement for house-account PAs"
```

---

## Task 7: expense-api 新增 OCR `slip` 模式

**Files:**
- Modify: `expense-api/app/services/ocr_service.py`, `expense-api/app/api/v1/ocr.py`
- Test: `expense-api/tests/test_ocr_slip.py`

**Interfaces:**
- Produces：`POST /api/v1/ocr/slip` → `{slip_ref, date, amount, tax_amount, total_amount, currency}`；`ocr_service.extract_slip(file_bytes, mime_type) -> dict`

- [ ] **Step 0: 量 expense-api 基线**

Run（在 `expense-api/` 下，用该服务 conftest 需要的 env——先读 `expense-api/tests/conftest.py` 确认变量名，**不要照搬 epms-api 的**）：全量套件跑一次，记下 failed/passed 与失败集合，写进报告。**本计划首次涉及这个服务，没有现成基线。**

- [ ] **Step 1: 写失败测试**

Create `expense-api/tests/test_ocr_slip.py`。照抄 `tests/test_ocr_service.py` 的写法——**mock 掉 Anthropic client，不发网络请求**：

```python
async def test_extract_slip_returns_expected_fields(monkeypatch):
    """返回 slip_ref / date / amount / tax_amount / total_amount / currency。"""

async def test_slip_ref_absent_returns_none_not_an_error(monkeypatch):
    """凭证上没有可用编号是**正常情况** —— 基线匹配不需要它。
    返回 None,不是抛异常。"""

async def test_unreadable_file_raises_value_error_not_runtime_error(monkeypatch):
    """anthropic.BadRequestError → ValueError → 调用方转 422 "手动录入",
    **不是** 503。OCR 不可用时录入页必须照常工作。"""

def test_receipt_prompt_is_unchanged():
    """OA 报销在生产使用 receipt 模式。提示词不是加法 —— 改一句可能扰动既有
    字段的抽取。用一个显式断言把它钉死,防止后来的人"顺手统一"两段提示词。"""
    from app.services.ocr_service import _RECEIPT_PROMPT
    assert "vendor_name" in _RECEIPT_PROMPT
    assert "slip_ref" not in _RECEIPT_PROMPT
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`ocr_service.py` 加 `_SLIP_PROMPT` 与 `extract_slip`。`extract_slip` **照抄 `extract_receipt` 的结构**（同样的 base64/document-vs-image 分支、同样的 `BadRequestError → ValueError`、同样的 ```` ``` ```` 剥离），只换提示词与返回字段映射。提示词要求：

```
Extract pickup-slip information from this image.

Return ONLY this JSON (no markdown):
{
  "slip_ref":     {"value": "string or null", "confidence": 0.0-1.0},
  "date":         {"value": "YYYY-MM-DD or null", "confidence": 0.0-1.0},
  "amount":       {"value": number or null, "confidence": 0.0-1.0},
  "tax_amount":   {"value": number or null, "confidence": 0.0-1.0},
  "total_amount": {"value": number or null, "confidence": 0.0-1.0},
  "currency":     {"value": "CAD", "confidence": 0.0-1.0}
}

- "slip_ref": the transaction or receipt reference printed on the slip. If the
  slip prints it as several separate fields (for example a till number and a
  transaction number in adjacent columns), join them with a hyphen in the order
  they appear. If no such reference is printed, return null — this is normal and
  not an error.
- "amount" is the PRE-TAX subtotal; "total_amount" is the amount actually
  charged, tax included.
```

`api/v1/ocr.py` 的 mode 白名单加 `"slip"`，分发到 `extract_slip`。

- [ ] **Step 4: 跑测试确认通过**，并确认 expense-api 全量套件失败集合与 Step 0 的基线**逐条相同**

- [ ] **Step 5: 提交**

```bash
git add expense-api/app/services/ocr_service.py expense-api/app/api/v1/ocr.py \
        expense-api/tests/test_ocr_slip.py
git commit -m "feat(ocr): add a slip mode without touching the receipt prompt"
```

---

## Task 8: 前端服务层与 hooks

**Files:**
- Create: `epms/src/services/agreementSlips.ts`, `epms/src/services/agreementSlipAttachments.ts`, `epms/src/hooks/useAgreementSlips.ts`
- Modify: `epms/src/services/invoices.ts`（`InvoiceMatchBody` 加 `slip_ids` / `slip_variance_reason`）

**Interfaces:**
- Produces：
  ```ts
  export type SlipStatus = 'pending_ap_review' | 'open' | 'reconciled' | 'voided' | 'rejected'
  export interface ApiSlip {
    id: string; agreement_id: string; slip_date: string; slip_ref: string | null
    amount: string; tax_amount: string; total_amount: string      // ← 都是字符串
    picked_by: string; missing_slip_reason: string | null
    ap_reviewed_by: string | null; ap_reviewed_at: string | null
    status: SlipStatus; invoice_id: string | null; notes: string | null
    created_by: string; created_at: string
  }
  agreementSlipService.list(agreementId, opts?) / create / update / void / apReview
  agreementSlipAttachmentService.list / upload / download / delete
  ocrService.slip(file): Promise<{slip_ref, date, amount, tax_amount, total_amount, currency}>
  useAgreementSlips(agreementId, status?) / useCreateSlip / useVoidSlip / useApReviewSlip
  ```

- [ ] **Step 1: 实现**

- 金额与百分比一律 `string | null`。**typed as `number` 会编译通过然后在运行时变成字符串拼接。**
- 附件 `download` **照抄 `src/services/prAttachments.ts` 的带 token blob 取法**，不能用裸 `<a href>`——无 token 的请求会被 nginx 的 SPA 兜底重定向回首页，表现为"点下载弹回主页"。
- OCR 走 expense-api，照 `src/lib/invoice-parser.ts` 里 `${EXPENSE_BASE}/api/v1/ocr/invoice` 的写法，换成 `/ocr/slip`。
- mutation 的 `onSuccess` 要 `await` 会影响控件显隐的失效：
  ```ts
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agreements', agreementId, 'slips'] }),
        queryClient.invalidateQueries({ queryKey: ['agreements'] }),
      ])
    },
  ```

- [ ] **Step 2: 类型门禁**

Run:
```bash
cd /c/Project/uniops && docker compose -f docker-compose.dev.yml -f docker-compose.agreement-test.yml \
  exec -T epms-frontend sh -c 'npx tsc --version && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -cE "error TS"'
```
Expected: 版本 **5.9.3**，错误数 = 基线 **58**

- [ ] **Step 3: 提交**

```bash
git add epms/src/services/agreementSlips.ts epms/src/services/agreementSlipAttachments.ts \
        epms/src/hooks/useAgreementSlips.ts epms/src/services/invoices.ts
git commit -m "feat(agreement/ui): pickup slip services and hooks"
```

---

## Task 9: 协议详情页 —— 小票录入与列表

**Files:**
- Create: `epms/src/components/agreements/SlipEntryForm.tsx`, `epms/src/components/agreements/SlipTable.tsx`
- Modify: `epms/src/pages/agreements/AgreementDetailPage.tsx`

- [ ] **Step 1: `SlipEntryForm`**

流程：选文件 → 自动调 `/ocr/slip` → 预填 `slip_ref` / 日期 / 金额 → **全部可编辑** → 人工补 `picked_by`（用 `useUserDirectory()`，**不是** `useUsers()`）→ 提交建行 → 再上传附件（两步，同 `PrCreatePage` 的模式）。

- 无照片时 `missing_slip_reason` 必填，且要在界面上说明它会进 AP 复核
- OCR 失败（422）时静默降级为手工录入，**不要报错弹窗**——那是正常路径
- `total_amount` 必须等于 `amount + tax_amount`，前端即时校验（后端也校验，两边一致）
- 提交按钮 `disabled={mutation.isPending}`

- [ ] **Step 2: `SlipTable`**

列：日期 / 参考号 / 金额 / 领用人 / 状态 / 附件 / 操作。

- 状态用 app 级 `StatusBadge`，不要手写颜色
- `pending_ap_review` 行给 AP 显示"通过 / 驳回"两个按钮（持 `ApDep` 权限者才显示）
- `open` / `pending_ap_review` 行给写权限者显示"作废"
- **老化提示（E1）**：`open` 且 `slip_date` 距今 > `SLIP_AGING_DAYS`（45，命名常量）的行加一个 warning 徽章，表头汇总"N 张超过 45 天未对账"
- 领用人显示人名：用 `useUserDirectory()` 解析；解析不到显示占位，**绝不渲染裸 UUID**

- [ ] **Step 3: 接进详情页**

`agreement_type === 'house_account'` 时才渲染这个区块——recurring/milestone 没有小票。

- [ ] **Step 4: 手工点验（不能只看 tsc）**

按 `feedback_uniops_reachability_in_done`：**tsc 绿不等于用户走得到**。逐条点：
1. 上传一张小票照片 → OCR 预填出金额和日期
2. 改掉 OCR 抽错的金额 → 能提交
3. 不传照片、填理由 → 落 `pending_ap_review`，AP 能通过/驳回
4. 作废一张 `open` 小票 → 状态变 `voided`，不再出现在候选池
5. 造一张 60 天前的 `open` 小票 → 出现老化徽章

- [ ] **Step 5: 类型门禁 + 提交**

---

## Task 10: MatchPanel 的 house_account 分支重做

**Files:**
- Modify: `epms/src/pages/invoices/MatchPanel.tsx`

- [ ] **Step 1: 替换理由输入框为候选小票列表**

现在 `isHouseAccount`（`MatchPanel.tsx:240`）分支下那个必填理由框（`:448-451`），改为：

**基线（永远可用）**：列出该协议 `status === 'open'` 的小票，按 `slip_date` 倒序，多选。实时显示 **已选合计 / 发票金额 / 差额**（都要 `Number()`）。

**加速路径**（命中即预勾选，操作员可改）：
1. 可选输入框"Reference on the invoice" → 与某张小票 `slip_ref` 相等则预选它
2. 存在 `Number(total_amount)` 与发票金额完全相等、且 `slip_date` 在发票日期前 14 天内的**唯一**一张 → 预选它。**多张匹配时不预选**——预选错一张而操作员顺手接受，比不预选糟得多
3. 都不命中 → 不预选

**差额 ≠ 0** 时要求填 `slip_variance_reason`，**但不阻止提交**（设计决策 3）。

**一张都没选**时，才显示原来那个 `legacy_settlement_reason` 必填框，文案改为说明这是无凭证结算。

- [ ] **Step 2: 提交 payload**

```ts
  ...(isHouseAccount
    ? (selectedSlipIds.length > 0
        ? { slip_ids: selectedSlipIds,
            ...(variance !== 0 ? { slip_variance_reason: varianceReason.trim() } : {}) }
        : { legacy_settlement_reason: legacyReason.trim() })
    : {}),
```

- [ ] **Step 3: 错误态**

小票列表拉取失败时**单独呈现**（"couldn't load slips — you may not have permission to view them"），**不要渲染成"没有小票"**——那会让操作员以为该协议干净，转而去填无凭证理由。也不要因为拉取失败禁用提交。

- [ ] **Step 4: 手工点验**

1. 有 `open` 小票的 house_account 协议 → 看到列表，不再看到理由框
2. 勾选金额相等的一张 → 差额 0，直接可提交；提交后小票转 `reconciled`
3. 勾选后差额非零 → 要求填说明，填了能提交
4. 一张都不勾 → 理由框出现且必填
5. 无 `open` 小票的协议 → 直接是理由框
6. 输入发票上的参考号 → 对应小票被自动勾选

- [ ] **Step 5: 类型门禁 + 提交**

---

## Task 11: 证据包扩展

**Files:**
- Modify: `epms/src/hooks/useChainAttachments.ts`

- [ ] **Step 1: 加一个血缘分支**

现有血缘：PA → 发票（`pa.invoice_ids`）→ GR（`invoice.gr_ids`，去重）→ PO → PR。

加：**发票 → 小票（`invoice.slip_ids`，去重）→ 小票附件**，取数走 `/agreements/{agreementId}/slips/{slipId}/attachments`。与 GR 分支结构对称（同样是数组、同样要跨发票去重）。

分组标签用小票的 `slip_ref ?? 日期+金额`，不要显示裸 UUID。

- [ ] **Step 2: 手工点验**

建一张协议 PA（发票已挂 2 张带照片的小票）→ 打开 PA 详情的附件汇总 → **能看到并下载那 2 张小票照片**。

这是本特性真正的交付物：财务一次性拿到"发票 + 支撑它的全部小票照片"。走不通就等于白做。

- [ ] **Step 3: 类型门禁 + 提交**

---

## 收尾

- [ ] **epms-api 全量**（确认没有别的会话在跑 pytest，**一次跑完不要分段**）

Expected: 失败集合与基线**逐条相同**（当前基线 69 failed / 716 passed）

- [ ] **expense-api 全量**：与 Task 7 Step 0 量的基线逐条比对

- [ ] **前端类型门禁**：TS 5.9.3，58 = 基线

- [ ] **E2 确认（有发票无小票）**：规格 §9 说这条**复用发票匹配已有的指派机制**，不新造。验证它确实已经可用：拿一张 house_account 发票，AP 用现有的 `AssignMatchRequest` 把它指派给某人 → 被指派人在任务箱看到 → 去协议详情补录小票 → 回来完成匹配。**若这条链路走不通，回来加一个任务**；走得通就在报告里写明"零代码验证通过"，不要因为没写代码就当它不存在。

- [ ] **写发布清单** `docs/release-notes/2026-08-11-agreement-pickup-slips.md`：迁移 `ag04`（epms-api），**无部署顺序约束**，需重建 `epms-api` / `epms-web` / `expense-api`，无新环境变量。
