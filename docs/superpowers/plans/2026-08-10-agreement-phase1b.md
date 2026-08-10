# Purchase Agreement Phase 1B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `recurring` 协议按周期自动排期、自动认领发票、缺票告警、履约确认后才可付款；让 `milestone` 协议能录入阶段并由人工把发票挂到阶段；并补上协议的预算科目选择器与真实附件。

**Architecture:** 全部改动落在 **epms-api** 与 **epms 前端**两处。新增一张排期表 `agreement_payment_schedule`（period / milestone 共用）和一张附件表 `agreement_attachments`，`purchase_agreements` 加 7 列、`invoices` 加 1 列，单个迁移 `ag03`。排期在**审批通过转 `active` 时由 epms-api 的 action 端点生成**——approval-api 完全不改，不让它知道排期表的存在。逾期扫描挂在 epms-api 已有的每日 asyncio 循环上。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic（epms-api）；React 18 + TanStack Query v5 + Tailwind（epms）。

**Spec:** [docs/superpowers/specs/2026-08-10-agreement-recurring-milestone-design.md](../specs/2026-08-10-agreement-recurring-milestone-design.md)

---

## Global Constraints

以下约束适用于**每一个**任务，不再逐条重复。

- **数据库**：仓库根 `.env` 指向**生产库**。绝不在宿主 shell 直接跑 alembic 或脚本。迁移一律在容器内跑；跑测试必须覆盖 `POSTGRES_*` 指向本地 `uniops_postgres`、库名 `epms_test`，worktree 里还需传 `JWT_SECRET_KEY`。
- **测试库禁止并发**：同一时刻只允许一个 epms-api 套件在跑。开跑前先确认没有别的会话的 pytest 进程（查进程，不只查 DB 连接）。
- **回归基线自己量，不许照抄本文档里的数字**：动手前先在**未改动的 HEAD** 上跑一次目标套件与 `npx tsc`，记下失败集合与错误数；改完后比对的是**集合逐条相同**，不是"数字小于等于"。
- **新迁移前先查 head**：`ag03` 的 `down_revision` 必须是 `"ag02_agreement_links"`。若届时 head 已变（例如合并了 main），挂到真实链尾，**绝不手工 INSERT `alembic_version`**。
- **Decimal 序列化**：Pydantic 把 `Decimal` 发成 JSON **字符串**。前端一切算术/比较前必须 `Number()`。
- **UI 文案全英文**。代码注释可中文。
- **前端浮层**（下拉、popover）必须 `createPortal` 到 `body` + `position: fixed`，否则被父级 `overflow` 裁掉。
- **动作按钮**必须 `disabled={...isPending}`，且 mutation 的 `onSuccess` 要 `await` 会影响按钮显隐的 `invalidateQueries`（见 `feature/purchase-agreement` 提交 `e2f8fab` / `f63a4d2`）。
- **不改 approval-api、不改 identity-api**。本期无新权限键，无 identity 迁移。
- **权限走矩阵**：排期行与附件的读写跟随已有的 `epms.agreement.read` / `epms.agreement.write`，不新建权限键、不硬编码角色。
- **提交粒度**：每个任务至少一个提交，提交信息写清"为什么"。**commit 前不需要用户同意**（本分支已获授权），但 **push 需要**。

## 文件结构

**epms-api 新建**

| 文件 | 职责 |
|---|---|
| `alembic/versions/ag03_agreement_schedule.py` | 单个迁移：2 张新表 + 8 个新列 |
| `app/models/agreement_schedule.py` | `AgreementPaymentSchedule` ORM |
| `app/models/agreement_attachment.py` | `AgreementAttachment` ORM |
| `app/services/agreement_schedule.py` | **纯函数**周期计算：日期序列、期次标签、边界规则。不碰 DB |
| `app/crud/agreement_schedule.py` | 排期行的落库、认领、逾期扫描查询 |
| `app/api/v1/agreement_attachments.py` | 附件 list/upload/download/delete |
| `app/tasks/agreement_overdue.py` | 每日逾期扫描 + 通知 |

**epms-api 修改**

| 文件 | 改什么 |
|---|---|
| `app/models/agreement.py` | +7 列 |
| `app/models/invoice.py` | +`schedule_id` |
| `app/schemas/agreement.py` | 新字段 + 校验 + 阶段行嵌套 schema |
| `app/crud/agreement.py` | create/update 带阶段行 |
| `app/api/v1/agreements.py` | action 端点转 active 时生成排期；排期行只读端点 |
| `app/crud/invoice.py` | `_match_to_agreement` 按 `agreement_type` 分支 |
| `app/api/v1/pa.py` | recurring 的履约确认闸门 |
| `app/crud/config.py` | `agreement_overdue_enabled` 默认 `True` |
| `app/main.py` | 启动逾期循环 |
| `app/api/v1/__init__.py` | 挂附件路由 |

**epms 前端修改**

| 文件 | 改什么 |
|---|---|
| `src/services/agreement.ts` | 新字段类型、排期行 API、附件 API |
| `src/hooks/useAgreements.ts` | 排期/附件 hooks |
| `src/pages/agreements/AgreementCreatePage.tsx` | 预算级联 + recurring 组 + 阶段编辑器 + 附件 |
| `src/pages/agreements/AgreementEditPage.tsx` | 同上 |
| `src/pages/agreements/AgreementDetailPage.tsx` | 排期/阶段表格 + 附件面板 |
| `src/pages/invoices/MatchPanel.tsx` | recurring 期次预览 / milestone 阶段选择 |
| `src/lib/taskTypes.ts` | `confirm_period` |

**新建前端组件**（从大页面里切出来，避免 Create/Edit 两页各写一份）

- `src/components/agreements/BudgetAccountCascade.tsx` — 四级级联，Create/Edit 共用
- `src/components/agreements/RecurringFields.tsx` — recurring 字段组
- `src/components/agreements/MilestoneEditor.tsx` — 阶段行增删改
- `src/components/agreements/ScheduleTable.tsx` — 详情页的排期/阶段表

---

## Task 1: 迁移 ag03 + 两个新模型 + 8 个新列

**Files:**
- Create: `epms-api/alembic/versions/ag03_agreement_schedule.py`
- Create: `epms-api/app/models/agreement_schedule.py`
- Create: `epms-api/app/models/agreement_attachment.py`
- Modify: `epms-api/app/models/agreement.py`
- Modify: `epms-api/app/models/invoice.py`
- Modify: `epms-api/app/models/__init__.py`
- Test: `epms-api/tests/test_agreement_schedule_model.py`

**Interfaces:**
- Produces: `AgreementPaymentSchedule`（表 `agreement_payment_schedule`）、`AgreementAttachment`（表 `agreement_attachments`）；`PurchaseAgreement` 新增属性 `cost_center_id` / `recurring_type` / `expected_invoice_day` / `anchor_month` / `expected_amount_per_period` / `tolerance_pct` / `overdue_after_days`；`Invoice.schedule_id`。

- [ ] **Step 1: 确认 alembic head 仍是 ag02**

Run:
```bash
cd epms-api/alembic/versions && python3 -c "
import re,glob
revs={};downs=set()
for f in glob.glob('*.py'):
    s=open(f,encoding='utf-8').read()
    m=re.search(r\"^revision(?::\s*str)?\s*=\s*['\\\"]([^'\\\"]+)\",s,re.M)
    for x in re.findall(r'^down_revision(?::[^=]+)?\s*=\s*(.+)$',s,re.M):
        downs.update(re.findall(r\"['\\\"]([^'\\\"]+)['\\\"]\",x))
    if m: revs[m.group(1)]=f
print('HEADS:',[h for h in revs if h not in downs])"
```
Expected: `HEADS: ['ag02_agreement_links']`。若不是，把下面的 `down_revision` 换成真实链尾。

- [ ] **Step 2: 写失败测试**

Create `epms-api/tests/test_agreement_schedule_model.py`：

```python
"""agreement_payment_schedule / agreement_attachments — model roundtrip."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.agreement_attachment import AgreementAttachment
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _seed_agreement(db, agreement_type="recurring"):
    """种子在同一个 async session 里建 —— conftest 的 seeded_vendor 走的是
    另一条未提交的 psycopg2 连接，async engine 看不见(会 FK 违约)。"""
    vendor = Vendor(
        code=f"V-{uuid.uuid4().hex[:8]}", name="Bell Canada", category="supplier",
        contact_name="AP", contact_email="ap@bell.example",
    )
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Agreement Tester", role="procurement_officer",
    ))
    await db.flush()
    agr = PurchaseAgreement(
        number=f"AGR-202608-{uuid.uuid4().hex[:4]}",
        title="Bell monthly circuit",
        agreement_type=agreement_type,
        vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1200.00"),
        tolerance_pct=Decimal("5.00"), overdue_after_days=7,
        created_by=user.id,
    )
    db.add(agr)
    await db.flush()
    return agr


async def test_period_row_roundtrip(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_agreement(db)
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="period", sequence=1,
            expected_amount=Decimal("1200.00"), expected_date=date(2026, 1, 5),
            status="pending", period_label="2026-01",
            tolerance_pct=Decimal("5.00"), overdue_after_days=7,
        )
        db.add(row)
        await db.commit()
        row_id = row.id

    async with factory() as db:
        got = (await db.execute(
            select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
        )).scalar_one()
        assert got.schedule_type == "period"
        assert got.period_label == "2026-01"
        assert got.expected_timing is None
        assert got.status == "pending"
        assert got.invoice_id is None


async def test_milestone_row_uses_text_timing_not_a_date(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_agreement(db, agreement_type="milestone")
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit on signing",
            expected_timing="Within 1 week after contract signing",
            amount_pct=Decimal("30.00"), expected_amount=Decimal("15000.00"),
            status="pending",
        )
        db.add(row)
        await db.commit()
        row_id = row.id

    async with factory() as db:
        got = (await db.execute(
            select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
        )).scalar_one()
        assert got.expected_date is None
        assert got.expected_timing == "Within 1 week after contract signing"
        assert got.accepted_by is None


async def test_agreement_carries_recurrence_columns(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_agreement(db)
        agr.anchor_month = 2
        agr.cost_center_id = uuid.uuid4()
        await db.commit()
        agr_id = agr.id

    async with factory() as db:
        got = (await db.execute(
            select(PurchaseAgreement).where(PurchaseAgreement.id == agr_id)
        )).scalar_one()
        assert got.recurring_type == "monthly"
        assert got.expected_invoice_day == 5
        assert got.anchor_month == 2
        assert got.overdue_after_days == 7
        assert got.cost_center_id is not None


async def test_attachment_roundtrip(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_agreement(db)
        att = AgreementAttachment(
            agreement_id=agr.id, filename="contract.pdf",
            content_type="application/pdf", file_size=1234,
            storage_key=uuid.uuid4(),
        )
        db.add(att)
        await db.commit()
        att_id = att.id

    async with factory() as db:
        got = (await db.execute(
            select(AgreementAttachment).where(AgreementAttachment.id == att_id)
        )).scalar_one()
        assert got.filename == "contract.pdf"
        assert got.file_data is None
```

- [ ] **Step 3: 跑测试确认失败**

Run:
```bash
docker exec -e POSTGRES_HOST=postgres -e POSTGRES_DB=epms_test \
  -e JWT_SECRET_KEY=test-secret uniops_epms_api \
  python -m pytest tests/test_agreement_schedule_model.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.agreement_schedule'`

- [ ] **Step 4: 建 `app/models/agreement_schedule.py`**

```python
"""ORM model for agreement payment schedule rows (period + milestone).

一张表带 schedule_type 区分两种排期,因为两者结构高度相同(都是"预先排好的
若干期,每期等一张发票")。差异全在可空列上,建两张几乎一样的表不划算。

⚠️ accepted_by / accepted_at 是**双语义**列,由 schedule_type 决定含义:
  - period    → 履约确认("本期服务正常"),Phase 1B 实现
  - milestone → 阶段验收,Phase 1C 才实现,本期一律为 NULL
读这两列的代码必须先看 schedule_type。
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class AgreementPaymentSchedule(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "agreement_payment_schedule"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_agreements.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # period | milestone
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    # period 专用。milestone 永远 NULL —— 阶段时间是相对合同事件的,见 expected_timing。
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # milestone 专用。纯文本,如 "Within 1 week after contract signing"。
    expected_timing: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # pending | received | overdue | waived
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending", index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # period 专用
    period_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 百分数:5.00 = ±5%
    tolerance_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    overdue_after_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # milestone 专用
    milestone_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Phase 1C 的验收判定条件("设备验收合格")—— 与 expected_timing 的"什么时候"
    # 是两回事,本期不写、不在 UI 出现。
    trigger_condition: Mapped[str | None] = mapped_column(Text, nullable=True)

    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 5: 建 `app/models/agreement_attachment.py`**

照抄 `app/models/pr_attachment.py`，逐字替换：`PrAttachment` → `AgreementAttachment`、`pr_attachments` → `agreement_attachments`、`pr_id` → `agreement_id`、外键 `purchase_requests.id` → `purchase_agreements.id`。其余（`filename` / `content_type` / `file_size` / `file_data` / `storage_key`）一字不改。

- [ ] **Step 6: 给 `PurchaseAgreement` 加 7 列**

在 `app/models/agreement.py` 的 `budget_code` 之后插入：

```python
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True)

    # ── recurring 专用(agreement_type='recurring' 时必填,其余类型必须为空) ──
    # weekly | monthly | quarterly | yearly
    recurring_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 到票日。weekly=1..7(ISO,周一=1);其余=1..31,遇短月钳到月末。
    expected_invoice_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 1..12,仅 quarterly / yearly 使用。**不从 valid_from 推导** —— 很多季度账单
    # 按合同起始月走(起始月 2 月 → 2/5/8/11),而签订日未必等于账单周期起点。
    anchor_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_amount_per_period: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2), nullable=True)
    # 百分数:5.00 = ±5%
    tolerance_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    overdue_after_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default="7")
```

- [ ] **Step 7: 给 `Invoice` 加 `schedule_id`**

在 `app/models/invoice.py` 的 `agreement_number` 之后：

```python
    # 认领到的排期行(recurring 自动 FIFO / milestone 人工选)。
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```

- [ ] **Step 8: 在 `app/models/__init__.py` 注册两个新模型**

按该文件既有写法追加 `AgreementPaymentSchedule` 与 `AgreementAttachment` 的 import 与 `__all__` 条目。

- [ ] **Step 9: 写迁移 `ag03_agreement_schedule.py`**

```python
"""agreement payment schedule, attachments, recurrence columns

Revision ID: ag03_agreement_schedule
Revises: ag02_agreement_links
Create Date: 2026-08-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag03_agreement_schedule"
down_revision = "ag02_agreement_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agreement_payment_schedule",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_agreements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schedule_type", sa.String(20), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("expected_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("expected_timing", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("period_label", sa.String(20), nullable=True),
        sa.Column("tolerance_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("overdue_after_days", sa.Integer(), nullable=True),
        sa.Column("milestone_name", sa.String(255), nullable=True),
        sa.Column("amount_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("trigger_condition", sa.Text(), nullable=True),
        sa.Column("accepted_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_agr_sched_agreement_id", "agreement_payment_schedule", ["agreement_id"])
    op.create_index("ix_agr_sched_type", "agreement_payment_schedule", ["schedule_type"])
    op.create_index("ix_agr_sched_status", "agreement_payment_schedule", ["status"])

    op.create_table(
        "agreement_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_agreements.id", ondelete="CASCADE"), nullable=False),
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
    op.create_index("ix_agr_att_agreement_id", "agreement_attachments", ["agreement_id"])

    op.add_column("purchase_agreements",
                  sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_purchase_agreements_cost_center_id",
                    "purchase_agreements", ["cost_center_id"])
    op.add_column("purchase_agreements", sa.Column("recurring_type", sa.String(10), nullable=True))
    op.add_column("purchase_agreements", sa.Column("expected_invoice_day", sa.Integer(), nullable=True))
    op.add_column("purchase_agreements", sa.Column("anchor_month", sa.Integer(), nullable=True))
    op.add_column("purchase_agreements",
                  sa.Column("expected_amount_per_period", sa.Numeric(15, 2), nullable=True))
    op.add_column("purchase_agreements", sa.Column("tolerance_pct", sa.Numeric(5, 2), nullable=True))
    op.add_column("purchase_agreements",
                  sa.Column("overdue_after_days", sa.Integer(), nullable=True, server_default="7"))

    # invoices 是共享表,但 expense-api / finance-api / approval-api 三个镜像都是
    # 显式列清单,看不见这个新列 —— 因此本迁移**没有部署顺序约束**(不同于 ag02 的
    # payment_applications.agreement_id,那次镜像声明了该列)。
    op.add_column("invoices", sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "schedule_id")
    for col in ("overdue_after_days", "tolerance_pct", "expected_amount_per_period",
                "anchor_month", "expected_invoice_day", "recurring_type"):
        op.drop_column("purchase_agreements", col)
    op.drop_index("ix_purchase_agreements_cost_center_id", table_name="purchase_agreements")
    op.drop_column("purchase_agreements", "cost_center_id")
    op.drop_index("ix_agr_att_agreement_id", table_name="agreement_attachments")
    op.drop_table("agreement_attachments")
    for ix in ("ix_agr_sched_status", "ix_agr_sched_type", "ix_agr_sched_agreement_id"):
        op.drop_index(ix, table_name="agreement_payment_schedule")
    op.drop_table("agreement_payment_schedule")
```

- [ ] **Step 10: 对 dev 库跑迁移**

Run（**容器内**，不要在宿主 shell）：
```bash
docker exec -w /app uniops_epms_api alembic upgrade head
docker exec -w /app uniops_epms_api alembic current
```
Expected: `current` 显示 `ag03_agreement_schedule (head)`

- [ ] **Step 11: 跑测试确认通过**

Run:
```bash
docker exec -e POSTGRES_HOST=postgres -e POSTGRES_DB=epms_test \
  -e JWT_SECRET_KEY=test-secret uniops_epms_api \
  python -m pytest tests/test_agreement_schedule_model.py -v
```
Expected: 4 passed

- [ ] **Step 12: 提交**

```bash
git add epms-api/alembic/versions/ag03_agreement_schedule.py \
        epms-api/app/models/agreement_schedule.py \
        epms-api/app/models/agreement_attachment.py \
        epms-api/app/models/agreement.py epms-api/app/models/invoice.py \
        epms-api/app/models/__init__.py epms-api/tests/test_agreement_schedule_model.py
git commit -m "feat(agreement): schedule + attachment tables, recurrence columns"
```

---

## Task 2: 周期日期计算（纯函数）

这是本期最容易算错的部分，所以单独成任务、完全不碰数据库。

**Files:**
- Create: `epms-api/app/services/agreement_schedule.py`
- Test: `epms-api/tests/test_agreement_period_math.py`

**Interfaces:**
- Produces:
  ```python
  class PeriodRow(NamedTuple):
      sequence: int
      period_label: str
      expected_date: date

  MAX_PERIOD_ROWS = 500

  class TooManyPeriods(ValueError): ...

  def build_period_rows(
      *, recurring_type: str, valid_from: date, valid_to: date,
      expected_invoice_day: int, anchor_month: int | None,
  ) -> list[PeriodRow]: ...
  ```

- [ ] **Step 1: 写失败测试**

Create `epms-api/tests/test_agreement_period_math.py`：

```python
"""周期排期的日期算法 —— 纯函数,不碰 DB。"""
from datetime import date

import pytest

from app.services.agreement_schedule import (
    MAX_PERIOD_ROWS, TooManyPeriods, build_period_rows,
)


def test_monthly_generates_one_row_per_calendar_month():
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2026, 1, 1),
        valid_to=date(2026, 3, 31), expected_invoice_day=5, anchor_month=None,
    )
    assert [r.period_label for r in rows] == ["2026-01", "2026-02", "2026-03"]
    assert [r.expected_date for r in rows] == [
        date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5)]
    assert [r.sequence for r in rows] == [1, 2, 3]


def test_monthly_day_31_clamps_to_end_of_february():
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2026, 1, 1),
        valid_to=date(2026, 2, 28), expected_invoice_day=31, anchor_month=None,
    )
    assert rows[0].expected_date == date(2026, 1, 31)
    assert rows[1].expected_date == date(2026, 2, 28)


def test_monthly_day_31_clamps_to_february_29_in_a_leap_year():
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2028, 2, 1),
        valid_to=date(2028, 2, 29), expected_invoice_day=31, anchor_month=None,
    )
    assert rows[0].expected_date == date(2028, 2, 29)


def test_first_period_is_skipped_when_its_invoice_day_precedes_valid_from():
    # 协议 8/20 生效、每月 5 号到票 —— 8 月那张票在生效前就该到了,不该排。
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2026, 8, 20),
        valid_to=date(2026, 10, 31), expected_invoice_day=5, anchor_month=None,
    )
    assert [r.period_label for r in rows] == ["2026-09", "2026-10"]
    assert rows[0].sequence == 1     # 序号从留下的第一期重新起算


def test_last_period_is_kept_even_when_its_invoice_day_falls_after_valid_to():
    # 月结票总在期末之后才到 —— 期起始日 <= valid_to 就该排。
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2026, 1, 1),
        valid_to=date(2026, 3, 2), expected_invoice_day=25, anchor_month=None,
    )
    assert [r.period_label for r in rows] == ["2026-01", "2026-02", "2026-03"]
    assert rows[-1].expected_date == date(2026, 3, 25)   # 晚于 valid_to,仍保留


def test_quarterly_follows_anchor_month_not_calendar_quarters():
    # 决策 7 的核心场景:起始月 2 月 → 2/5/8/11(日历季度会是 1/4/7/10),
    # 且 anchor_month 与 valid_from 的月份**不同**,证明它没被 valid_from 推导。
    rows = build_period_rows(
        recurring_type="quarterly", valid_from=date(2026, 1, 5),
        valid_to=date(2026, 12, 31), expected_invoice_day=10, anchor_month=2,
    )
    assert [r.period_label for r in rows] == ["2026-02", "2026-05", "2026-08", "2026-11"]
    assert [r.expected_date for r in rows] == [
        date(2026, 2, 10), date(2026, 5, 10), date(2026, 8, 10), date(2026, 11, 10)]


def test_quarterly_covering_period_is_dropped_when_its_invoice_day_precedes_valid_from():
    # valid_from=6/1 落在 2026-05 那一期里,但该期的到票日是 5/10 —— 早于生效日,
    # 由"首期跳过"规则丢掉。两条规则叠加后的结果必须是确定的,不是二选一。
    rows = build_period_rows(
        recurring_type="quarterly", valid_from=date(2026, 6, 1),
        valid_to=date(2026, 12, 31), expected_invoice_day=10, anchor_month=2,
    )
    assert [r.period_label for r in rows] == ["2026-08", "2026-11"]
    assert rows[0].sequence == 1


def test_quarterly_period_spanning_a_year_boundary():
    rows = build_period_rows(
        recurring_type="quarterly", valid_from=date(2026, 11, 1),
        valid_to=date(2027, 6, 30), expected_invoice_day=10, anchor_month=2,
    )
    assert [r.period_label for r in rows] == ["2026-11", "2027-02", "2027-05"]


def test_yearly_uses_anchor_month():
    rows = build_period_rows(
        recurring_type="yearly", valid_from=date(2026, 1, 10),
        valid_to=date(2028, 12, 31), expected_invoice_day=15, anchor_month=3,
    )
    assert [r.period_label for r in rows] == ["2026-03", "2027-03", "2028-03"]
    assert rows[0].expected_date == date(2026, 3, 15)


def test_weekly_uses_iso_weekday_and_iso_week_labels():
    # 2026-01-01 是周四;其 ISO 周是 2026-W01(周一 = 2025-12-29)。
    rows = build_period_rows(
        recurring_type="weekly", valid_from=date(2026, 1, 5),
        valid_to=date(2026, 1, 25), expected_invoice_day=3, anchor_month=None,
    )
    assert rows[0].period_label.startswith("2026-W")
    # day 3 = 周三
    assert all(r.expected_date.isoweekday() == 3 for r in rows)
    assert len(rows) == 3


def test_row_count_over_the_cap_is_rejected():
    with pytest.raises(TooManyPeriods):
        build_period_rows(
            recurring_type="weekly", valid_from=date(2026, 1, 1),
            valid_to=date(2040, 1, 1), expected_invoice_day=1, anchor_month=None,
        )


def test_cap_value_is_500():
    assert MAX_PERIOD_ROWS == 500


def test_unknown_recurring_type_raises():
    with pytest.raises(ValueError):
        build_period_rows(
            recurring_type="fortnightly", valid_from=date(2026, 1, 1),
            valid_to=date(2026, 6, 1), expected_invoice_day=1, anchor_month=None,
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
docker exec -e POSTGRES_HOST=postgres -e POSTGRES_DB=epms_test \
  -e JWT_SECRET_KEY=test-secret uniops_epms_api \
  python -m pytest tests/test_agreement_period_math.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.agreement_schedule'`

- [ ] **Step 3: 实现 `app/services/agreement_schedule.py`**

```python
"""周期排期的日期算法。纯函数,不碰 DB —— 这是本期最容易算错的一块,
把它隔离出来才测得干净。

规则出处:设计文档 §4.1 / §4.2。
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import NamedTuple

MAX_PERIOD_ROWS = 500

RECURRING_TYPES = ("weekly", "monthly", "quarterly", "yearly")


class TooManyPeriods(ValueError):
    """有效期 × 周期长度会生成超过 MAX_PERIOD_ROWS 行。"""


class PeriodRow(NamedTuple):
    sequence: int
    period_label: str
    expected_date: date


def _clamp_day(year: int, month: int, day: int) -> date:
    """把 day 钳进该月的实际天数 —— 31 号遇 2 月取月末(闰年 29)。"""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def _month_starts(valid_from: date, valid_to: date, step: int,
                  anchor_month: int | None) -> list[tuple[int, int]]:
    """产出期起始 (year, month) 序列。

    anchor_month 为 None(monthly)时锚点就是 valid_from 所在月。否则期起始月
    构成序列 {anchor_month, anchor_month+step, ...},首期取**起始月 <= valid_from
    的最后一个**,也就是覆盖 valid_from 的那一期。
    """
    if anchor_month is None:
        y, m = valid_from.year, valid_from.month
    else:
        # 从 valid_from 当年的 anchor_month 起,先退到不晚于 valid_from 的那一期
        y, m = valid_from.year, anchor_month
        while (y, m) > (valid_from.year, valid_from.month):
            y, m = _add_months(y, m, -step)
        while True:
            ny, nm = _add_months(y, m, step)
            if (ny, nm) > (valid_from.year, valid_from.month):
                break
            y, m = ny, nm

    out: list[tuple[int, int]] = []
    while (y, m) <= (valid_to.year, valid_to.month):
        out.append((y, m))
        if len(out) > MAX_PERIOD_ROWS:
            raise TooManyPeriods(
                f"This validity window would generate more than {MAX_PERIOD_ROWS} "
                "schedule rows. Shorten the validity window or use a longer cycle.")
        y, m = _add_months(y, m, step)
    return out


def _weekly_rows(valid_from: date, valid_to: date, weekday: int) -> list[PeriodRow]:
    """期 = ISO 周(周一起)。expected_invoice_day 是 ISO 星期几(1=周一)。"""
    monday = valid_from - timedelta(days=valid_from.isoweekday() - 1)
    raw: list[tuple[str, date]] = []
    while monday <= valid_to:
        iso_year, iso_week, _ = monday.isocalendar()
        raw.append((f"{iso_year}-W{iso_week:02d}", monday + timedelta(days=weekday - 1)))
        if len(raw) > MAX_PERIOD_ROWS:
            raise TooManyPeriods(
                f"This validity window would generate more than {MAX_PERIOD_ROWS} "
                "schedule rows. Shorten the validity window or use a longer cycle.")
        monday += timedelta(days=7)
    return [PeriodRow(0, label, d) for label, d in raw]


def build_period_rows(
    *,
    recurring_type: str,
    valid_from: date,
    valid_to: date,
    expected_invoice_day: int,
    anchor_month: int | None,
) -> list[PeriodRow]:
    """按协议的周期参数产出整个有效期的排期行。

    边界规则(设计文档 §4.1):
      - 首期 expected_date < valid_from → 跳过(那张票在生效前就该到了)
      - 期起始日 <= valid_to 的期都保留,即使 expected_date 晚于 valid_to
        (月结票总在期末之后才到,与 grace_days 的意图一致)
      - 超过 MAX_PERIOD_ROWS 行 → TooManyPeriods
    """
    if recurring_type not in RECURRING_TYPES:
        raise ValueError(f"Unknown recurring_type {recurring_type!r}")

    if recurring_type == "weekly":
        rows = _weekly_rows(valid_from, valid_to, expected_invoice_day)
    else:
        step = {"monthly": 1, "quarterly": 3, "yearly": 12}[recurring_type]
        anchor = None if recurring_type == "monthly" else anchor_month
        rows = [
            PeriodRow(0, f"{y:04d}-{m:02d}", _clamp_day(y, m, expected_invoice_day))
            for y, m in _month_starts(valid_from, valid_to, step, anchor)
        ]

    # 首期跳过:只丢开头连续的、到票日早于生效日的期。
    kept = [r for r in rows if r.expected_date >= valid_from]
    if len(kept) > MAX_PERIOD_ROWS:
        raise TooManyPeriods(
            f"This validity window would generate more than {MAX_PERIOD_ROWS} "
            "schedule rows. Shorten the validity window or use a longer cycle.")
    return [PeriodRow(i + 1, r.period_label, r.expected_date) for i, r in enumerate(kept)]
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
docker exec -e POSTGRES_HOST=postgres -e POSTGRES_DB=epms_test \
  -e JWT_SECRET_KEY=test-secret uniops_epms_api \
  python -m pytest tests/test_agreement_period_math.py -v
```
Expected: 13 passed。**若 `test_quarterly_first_period_is_the_one_covering_valid_from` 失败，先查 `_month_starts` 的回退循环**——这是本任务唯一有二义性的地方，不要靠改测试让它变绿。

- [ ] **Step 5: 提交**

```bash
git add epms-api/app/services/agreement_schedule.py epms-api/tests/test_agreement_period_math.py
git commit -m "feat(agreement): period date math with explicit quarterly/yearly anchor"
```

---

## Task 3: Schema 校验 + 阶段行录入

**Files:**
- Modify: `epms-api/app/schemas/agreement.py`
- Modify: `epms-api/app/crud/agreement.py`
- Create: `epms-api/app/crud/agreement_schedule.py`
- Test: `epms-api/tests/test_agreement_recurrence_schema.py`

**Interfaces:**
- Consumes: Task 1 的 `AgreementPaymentSchedule`；Task 2 的 `build_period_rows` / `TooManyPeriods`
- Produces:
  ```python
  # app/schemas/agreement.py
  class MilestoneRowIn(BaseModel):
      milestone_name: str
      expected_timing: str | None
      expected_amount: Decimal | None
      amount_pct: Decimal | None

  class ScheduleRowResponse(BaseModel): ...     # from_attributes=True，列同 ORM
  class ScheduleListResponse(BaseModel):
      items: list[ScheduleRowResponse]

  # AgreementCreate / AgreementUpdate 新增:cost_center_id, recurring_type,
  # expected_invoice_day, anchor_month, expected_amount_per_period,
  # tolerance_pct, overdue_after_days, milestones: list[MilestoneRowIn]
  # AgreementResponse 新增同名的 7 个标量列(不含 milestones)

  # app/crud/agreement_schedule.py
  async def replace_milestone_rows(db, agr, rows: list[MilestoneRowIn]) -> None: ...
  async def list_rows(db, agreement_id: uuid.UUID) -> list[AgreementPaymentSchedule]: ...
  ```

- [ ] **Step 1: 写失败测试**

Create `epms-api/tests/test_agreement_recurrence_schema.py`：

```python
"""AgreementCreate 的周期字段校验 + milestone 行录入。纯 schema 层,不碰 DB。"""
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.agreement import AgreementCreate, MilestoneRowIn

VENDOR = "11111111-1111-1111-1111-111111111111"


def _base(**over):
    body = dict(
        title="Bell circuit", agreement_type="recurring", vendor_id=VENDOR,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
    )
    body.update(over)
    return body


def test_recurring_requires_recurring_type_and_invoice_day():
    with pytest.raises(ValidationError, match="recurring_type"):
        AgreementCreate(**_base())


def test_recurring_monthly_accepts_a_day_of_month():
    a = AgreementCreate(**_base(recurring_type="monthly", expected_invoice_day=5))
    assert a.expected_invoice_day == 5


def test_recurring_weekly_rejects_a_day_above_7():
    with pytest.raises(ValidationError, match=r"1\.\.7"):
        AgreementCreate(**_base(recurring_type="weekly", expected_invoice_day=15))


def test_quarterly_requires_anchor_month():
    with pytest.raises(ValidationError, match="anchor_month"):
        AgreementCreate(**_base(recurring_type="quarterly", expected_invoice_day=10))


def test_quarterly_with_anchor_month_is_accepted():
    a = AgreementCreate(**_base(
        recurring_type="quarterly", expected_invoice_day=10, anchor_month=2))
    assert a.anchor_month == 2


def test_non_recurring_must_not_carry_recurrence_fields():
    with pytest.raises(ValidationError, match="only apply to a recurring agreement"):
        AgreementCreate(**_base(agreement_type="house_account",
                                recurring_type="monthly", expected_invoice_day=5))


def test_a_window_over_the_row_cap_is_rejected_at_create_time():
    # 生成不出来的排期不该等到审批通过那一刻才炸 —— 建档时就挡住。
    with pytest.raises(ValidationError, match="500"):
        AgreementCreate(**_base(
            recurring_type="weekly", expected_invoice_day=1,
            valid_to=date(2040, 1, 1)))


def test_milestones_only_allowed_on_milestone_agreements():
    with pytest.raises(ValidationError, match="milestone agreement"):
        AgreementCreate(**_base(
            recurring_type="monthly", expected_invoice_day=5,
            milestones=[MilestoneRowIn(milestone_name="Deposit")]))


def test_milestone_amount_pct_requires_not_to_exceed():
    with pytest.raises(ValidationError, match="not_to_exceed"):
        AgreementCreate(**_base(
            agreement_type="milestone",
            milestones=[MilestoneRowIn(milestone_name="Deposit",
                                       amount_pct=Decimal("30"))]))


def test_milestone_with_an_absolute_amount_needs_no_ceiling():
    a = AgreementCreate(**_base(
        agreement_type="milestone",
        milestones=[MilestoneRowIn(
            milestone_name="Deposit on signing",
            expected_timing="Within 1 week after contract signing",
            expected_amount=Decimal("15000"))]))
    assert a.milestones[0].expected_timing == "Within 1 week after contract signing"
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
docker exec -e POSTGRES_HOST=postgres -e POSTGRES_DB=epms_test \
  -e JWT_SECRET_KEY=test-secret uniops_epms_api \
  python -m pytest tests/test_agreement_recurrence_schema.py -v
```
Expected: FAIL — `ImportError: cannot import name 'MilestoneRowIn'`

- [ ] **Step 3: 加 schema**

在 `app/schemas/agreement.py` 的 `AGREEMENT_TYPES` 之后加：

```python
RECURRING_TYPES = ("weekly", "monthly", "quarterly", "yearly")
ANCHORED_TYPES = ("quarterly", "yearly")


class MilestoneRowIn(BaseModel):
    milestone_name: str = Field(min_length=1, max_length=255)
    # 纯文本时间(决策 8):"Within 1 week after contract signing"。阶段时间几乎
    # 总是相对合同事件的,落成日历日期只会得到一个填时就不准、之后没人维护的数字。
    expected_timing: str | None = Field(default=None, max_length=255)
    expected_amount: Decimal | None = Field(default=None, ge=0)
    amount_pct: Decimal | None = Field(default=None, ge=0, le=100)


class ScheduleRowResponse(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    schedule_type: str
    sequence: int
    expected_amount: Decimal | None
    expected_date: date | None
    expected_timing: str | None
    status: str
    invoice_id: uuid.UUID | None
    period_label: str | None
    tolerance_pct: Decimal | None
    overdue_after_days: int | None
    milestone_name: str | None
    amount_pct: Decimal | None
    accepted_by: uuid.UUID | None
    accepted_at: datetime | None

    model_config = {"from_attributes": True}


class ScheduleListResponse(BaseModel):
    items: list[ScheduleRowResponse]
```

`AgreementCreate` 在 `budget_code` 之后加字段：

```python
    cost_center_id: uuid.UUID | None = None
    recurring_type: str | None = None
    expected_invoice_day: int | None = Field(default=None, ge=1, le=31)
    anchor_month: int | None = Field(default=None, ge=1, le=12)
    expected_amount_per_period: Decimal | None = Field(default=None, ge=0)
    tolerance_pct: Decimal | None = Field(default=None, ge=0, le=100)
    overdue_after_days: int | None = Field(default=None, ge=0, le=365)
    milestones: list[MilestoneRowIn] = Field(default_factory=list)
```

在 `_validity_window_is_ordered` 之后追加两个校验器：

```python
    @model_validator(mode="after")
    def _recurrence_is_coherent(self):
        if self.agreement_type == "recurring":
            if self.recurring_type not in RECURRING_TYPES:
                raise ValueError(
                    "recurring_type is required for a recurring agreement "
                    f"(one of {', '.join(RECURRING_TYPES)})")
            if self.expected_invoice_day is None:
                raise ValueError("expected_invoice_day is required for a recurring agreement")
            if self.recurring_type == "weekly" and not 1 <= self.expected_invoice_day <= 7:
                raise ValueError(
                    "For a weekly cycle expected_invoice_day is a weekday, 1..7 (1 = Monday)")
            if self.recurring_type in ANCHORED_TYPES and self.anchor_month is None:
                raise ValueError(
                    f"anchor_month is required for a {self.recurring_type} cycle — real "
                    "billing cycles often do not start in January, and the contract start "
                    "date is not a reliable proxy for the billing anchor")
            # 生成不出来的排期,建档时就该挡住,而不是等审批通过那一刻才炸。
            from app.services.agreement_schedule import TooManyPeriods, build_period_rows
            try:
                build_period_rows(
                    recurring_type=self.recurring_type, valid_from=self.valid_from,
                    valid_to=self.valid_to, expected_invoice_day=self.expected_invoice_day,
                    anchor_month=self.anchor_month)
            except TooManyPeriods as exc:
                raise ValueError(str(exc)) from exc
        else:
            bad = [n for n in ("recurring_type", "expected_invoice_day", "anchor_month",
                               "expected_amount_per_period", "tolerance_pct")
                   if getattr(self, n) is not None]
            if bad:
                raise ValueError(f"{', '.join(bad)} only apply to a recurring agreement")
        return self

    @model_validator(mode="after")
    def _milestones_are_coherent(self):
        if self.milestones and self.agreement_type != "milestone":
            raise ValueError("milestones can only be set on a milestone agreement")
        if any(m.amount_pct is not None for m in self.milestones) and self.not_to_exceed is None:
            raise ValueError(
                "amount_pct needs not_to_exceed as its base — set a ceiling on the "
                "agreement, or enter absolute amounts on the stages")
        return self
```

`AgreementUpdate` 加同样 7 个可选标量字段，外加 `milestones: list[MilestoneRowIn] | None = None`（`None` = 不动阶段行，`[]` = 清空）。`AgreementResponse` 加那 7 个标量列。

> `date` 与 `datetime` 已在该文件顶部 import；`uuid` 同。

- [ ] **Step 4: 建 `app/crud/agreement_schedule.py`**

```python
"""排期行的落库与查询。周期日期算法在 app/services/agreement_schedule.py。"""
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.schemas.agreement import MilestoneRowIn


def _resolve_milestone_amounts(
    row: MilestoneRowIn, ceiling: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """金额与百分比二选一录入,另一个推算。基数是 not_to_exceed —— 协议上唯一的
    总额字段。两个都给了就都存,不去纠正用户。"""
    amount, pct = row.expected_amount, row.amount_pct
    if amount is None and pct is not None and ceiling:
        amount = (ceiling * pct / Decimal("100")).quantize(Decimal("0.01"))
    elif pct is None and amount is not None and ceiling:
        pct = (amount / ceiling * Decimal("100")).quantize(Decimal("0.01"))
    return amount, pct


async def replace_milestone_rows(
    db: AsyncSession, agr: PurchaseAgreement, rows: list[MilestoneRowIn]
) -> None:
    """整体替换阶段行。只在 draft/returned 调用 —— 已认领的阶段不能被抹掉。"""
    existing = (await db.execute(
        select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id,
            AgreementPaymentSchedule.schedule_type == "milestone",
        )
    )).scalars().all()
    claimed = [r for r in existing if r.invoice_id is not None]
    if claimed:
        raise ValueError(
            f"{len(claimed)} milestone stage(s) already have an invoice matched to them "
            "and cannot be re-entered; detach the invoice first")
    for row in existing:
        await db.delete(row)
    await db.flush()

    for i, row in enumerate(rows, start=1):
        amount, pct = _resolve_milestone_amounts(row, agr.not_to_exceed)
        db.add(AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=i,
            milestone_name=row.milestone_name, expected_timing=row.expected_timing,
            expected_amount=amount, amount_pct=pct, status="pending",
        ))
    await db.flush()


async def list_rows(
    db: AsyncSession, agreement_id: uuid.UUID
) -> list[AgreementPaymentSchedule]:
    return list((await db.execute(
        select(AgreementPaymentSchedule)
        .where(AgreementPaymentSchedule.agreement_id == agreement_id)
        .order_by(AgreementPaymentSchedule.schedule_type,
                  AgreementPaymentSchedule.sequence)
    )).scalars().all())
```

- [ ] **Step 5: `app/crud/agreement.py` 的 create/update 落新字段**

`create()`：把 7 个标量列随 `AgreementCreate` 的同名字段一并写入 `PurchaseAgreement(...)`；`flush` 之后若 `body.milestones` 非空则 `await agreement_schedule.replace_milestone_rows(db, agr, body.milestones)`。

`update()`：标量列按 `body.model_dump(exclude_unset=True)` 逐个赋值（跳过 `milestones`）；若 `body.milestones is not None` 则调 `replace_milestone_rows`，其抛出的 `ValueError` 由端点转 409。

- [ ] **Step 6: 跑测试确认通过**

Run:
```bash
docker exec -e POSTGRES_HOST=postgres -e POSTGRES_DB=epms_test \
  -e JWT_SECRET_KEY=test-secret uniops_epms_api \
  python -m pytest tests/test_agreement_recurrence_schema.py tests/test_agreements.py -v
```
Expected: 新增 10 passed；`test_agreements.py` 既有用例**全部仍过**（新字段全可选，不得破坏既有建档）

- [ ] **Step 7: 提交**

```bash
git add epms-api/app/schemas/agreement.py epms-api/app/crud/agreement.py \
        epms-api/app/crud/agreement_schedule.py \
        epms-api/tests/test_agreement_recurrence_schema.py
git commit -m "feat(agreement): recurrence validation and milestone stage entry"
```

---

## Task 4: 审批转 active 时生成排期 + 排期只读端点

**Files:**
- Modify: `epms-api/app/crud/agreement_schedule.py`
- Modify: `epms-api/app/api/v1/agreements.py`
- Test: `epms-api/tests/test_agreement_schedule_generation.py`

**Interfaces:**
- Consumes: Task 2 的 `build_period_rows`；Task 3 的 `ScheduleListResponse`
- Produces:
  ```python
  async def ensure_period_rows(db, agr: PurchaseAgreement) -> int:
      """幂等。返回本次新建的行数(已有则 0，非 recurring 也是 0)。"""
  # GET /agreements/{id}/schedule -> ScheduleListResponse
  ```

- [ ] **Step 1: 写失败测试**

Create `epms-api/tests/test_agreement_schedule_generation.py`：

```python
"""排期行在协议转 active 时生成,且必须幂等。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement_schedule as sched_crud
from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _seed(db, **over):
    """种子必须建在同一个 async session 里 —— conftest 的 seeded_vendor 走的是
    另一条未提交的 psycopg2 连接,async engine 看不见(FK 违约)。"""
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Bell", category="supplier",
                    contact_name="AP", contact_email="ap@bell.example")
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="T", role="procurement_officer"))
    await db.flush()
    kw = dict(
        number=f"AGR-202608-{uuid.uuid4().hex[:4]}", title="Bell", agreement_type="recurring",
        vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 31),
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1200.00"), tolerance_pct=Decimal("5.00"),
        overdue_after_days=7, status="active", created_by=user.id,
    )
    kw.update(over)
    agr = PurchaseAgreement(**kw)
    db.add(agr)
    await db.flush()
    return agr


async def test_generates_one_row_per_period_with_agreement_defaults(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db)
        assert await sched_crud.ensure_period_rows(db, agr) == 3
        await db.commit()
        rows = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence)
        )).scalars().all()
        assert [r.period_label for r in rows] == ["2026-01", "2026-02", "2026-03"]
        assert all(r.expected_amount == Decimal("1200.00") for r in rows)
        assert all(r.tolerance_pct == Decimal("5.00") for r in rows)
        assert all(r.overdue_after_days == 7 for r in rows)
        assert all(r.status == "pending" for r in rows)
        assert all(r.invoice_id is None for r in rows)


async def test_is_idempotent(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db)
        assert await sched_crud.ensure_period_rows(db, agr) == 3
        assert await sched_crud.ensure_period_rows(db, agr) == 0
        await db.commit()


async def test_non_recurring_agreement_generates_nothing(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db, agreement_type="house_account", recurring_type=None,
                          expected_invoice_day=None, expected_amount_per_period=None,
                          tolerance_pct=None)
        assert await sched_crud.ensure_period_rows(db, agr) == 0
        await db.commit()


async def test_null_per_period_amount_leaves_rows_without_an_amount(test_engine):
    # 决策 3:每期金额选填。不填 → 排期行不带金额,认领时不做金额校验。
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db, expected_amount_per_period=None)
        await sched_crud.ensure_period_rows(db, agr)
        await db.commit()
        rows = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all()
        assert rows and all(r.expected_amount is None for r in rows)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `docker exec -e POSTGRES_HOST=postgres -e POSTGRES_DB=epms_test -e JWT_SECRET_KEY=test-secret uniops_epms_api python -m pytest tests/test_agreement_schedule_generation.py -v`
Expected: FAIL — `AttributeError: module 'app.crud.agreement_schedule' has no attribute 'ensure_period_rows'`

- [ ] **Step 3: 实现 `ensure_period_rows`**

追加到 `app/crud/agreement_schedule.py`（顶部补 `from app.services.agreement_schedule import build_period_rows`）：

```python
async def ensure_period_rows(db: AsyncSession, agr: PurchaseAgreement) -> int:
    """协议转 active 时生成整个有效期的排期行。

    幂等:已有 period 行就什么都不做 —— 审批可能因 resync 之类的操作重入。
    非 recurring 协议直接返回 0。
    """
    if agr.agreement_type != "recurring" or not agr.recurring_type:
        return 0
    existing = (await db.execute(
        select(AgreementPaymentSchedule.id).where(
            AgreementPaymentSchedule.agreement_id == agr.id,
            AgreementPaymentSchedule.schedule_type == "period",
        ).limit(1)
    )).first()
    if existing:
        return 0

    rows = build_period_rows(
        recurring_type=agr.recurring_type, valid_from=agr.valid_from,
        valid_to=agr.valid_to, expected_invoice_day=agr.expected_invoice_day,
        anchor_month=agr.anchor_month,
    )
    for r in rows:
        db.add(AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="period", sequence=r.sequence,
            period_label=r.period_label, expected_date=r.expected_date,
            expected_amount=agr.expected_amount_per_period,
            tolerance_pct=agr.tolerance_pct, overdue_after_days=agr.overdue_after_days,
            status="pending",
        ))
    await db.flush()
    return len(rows)
```

- [ ] **Step 4: 在 action 端点挂钩 + 加只读端点**

`app/api/v1/agreements.py` 的 `agreement_action`，把结尾的 `await db.refresh(agr)` / `return agr` 换成：

```python
    await db.refresh(agr)
    # 排期在这里生成,不在 approval-api 的 _post_approve_agr —— 那样 approval-api
    # 就得镜像 agreement_payment_schedule,为一个纯 EPMS 概念增加跨服务耦合点。
    # 状态机归 approval-api,排期归 EPMS。
    if agr.status == "active":
        await agr_sched_crud.ensure_period_rows(db, agr)
        await db.commit()
        await db.refresh(agr)
    return agr
```

文件顶部加 `from app.crud import agreement_schedule as agr_sched_crud`，并从 schemas 补 import `ScheduleListResponse`。追加端点：

```python
@router.get("/{agreement_id}/schedule", response_model=ScheduleListResponse)
async def list_schedule(agreement_id: uuid.UUID, db: SessionDep, user: AgrReadDep):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return {"items": await agr_sched_crud.list_rows(db, agreement_id)}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `... python -m pytest tests/test_agreement_schedule_generation.py tests/test_agreements.py -v`
Expected: 新增 4 passed，既有用例不回归

- [ ] **Step 6: 提交**

```bash
git add epms-api/app/crud/agreement_schedule.py epms-api/app/api/v1/agreements.py \
        epms-api/tests/test_agreement_schedule_generation.py
git commit -m "feat(agreement): generate period rows on approval, expose schedule endpoint"
```

---

## Task 5: 协议附件路由

**Files:**
- Create: `epms-api/app/api/v1/agreement_attachments.py`
- Modify: `epms-api/app/api/v1/__init__.py`
- Test: `epms-api/tests/test_agreement_attachments.py`

**Interfaces:**
- Produces: `GET/POST /agreements/{id}/attachments`、`GET /agreements/{id}/attachments/{att_id}/download`、`DELETE /agreements/{id}/attachments/{att_id}`

- [ ] **Step 1: 写失败测试**

Create `epms-api/tests/test_agreement_attachments.py`。`client` / `auth_headers` 用 `tests/conftest.py` 既有的那套（跟 `tests/test_agreements.py` 里调 `/agreements` 的写法完全一致）；`agreement_id` 在本文件内定义一个 fixture，通过 `POST /api/v1/agreements` 建一张 draft 协议并返回其 id，建档 payload 照抄 `tests/test_agreements.py` 里已能通过的那份。

```python
"""协议附件 —— 上传 / 列出 / 删除。"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_upload_then_list_returns_the_file(client, agreement_id, auth_headers):
    res = await client.post(
        f"/api/v1/agreements/{agreement_id}/attachments",
        files={"file": ("contract.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers=auth_headers)
    assert res.status_code == 201, res.text
    assert res.json()["filename"] == "contract.pdf"

    listed = await client.get(
        f"/api/v1/agreements/{agreement_id}/attachments", headers=auth_headers)
    assert [a["filename"] for a in listed.json()] == ["contract.pdf"]


async def test_delete_removes_it(client, agreement_id, auth_headers):
    up = await client.post(
        f"/api/v1/agreements/{agreement_id}/attachments",
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}, headers=auth_headers)
    att_id = up.json()["id"]
    res = await client.delete(
        f"/api/v1/agreements/{agreement_id}/attachments/{att_id}", headers=auth_headers)
    assert res.status_code == 204
    listed = await client.get(
        f"/api/v1/agreements/{agreement_id}/attachments", headers=auth_headers)
    assert listed.json() == []


async def test_upload_to_a_missing_agreement_is_404(client, auth_headers):
    res = await client.post(
        "/api/v1/agreements/00000000-0000-0000-0000-000000000000/attachments",
        files={"file": ("x.pdf", b"%PDF", "application/pdf")}, headers=auth_headers)
    assert res.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败** — 路由不存在，`POST` 返回 404/405

- [ ] **Step 3: 实现路由**

**照抄 `epms-api/app/api/v1/pr_attachments.py` 整个文件**，逐项替换：

| 原 | 新 |
|---|---|
| `PrAttachment` | `AgreementAttachment` |
| `from app.crud import pr as pr_crud` | `from app.crud import agreement as agr_crud` |
| 路由前缀 `/pr/{pr_id}/attachments` | `/agreements/{agreement_id}/attachments` |
| `PrAttachment.pr_id` | `AgreementAttachment.agreement_id` |
| 404 文案 `"PR not found"` | `"Agreement not found"` |
| 读权限依赖 | `require_permission("epms.agreement.read")` |
| 写/删权限依赖 | `require_permission("epms.agreement.write")` |

`upload_to_file_server` / `proxy_download` / `delete_from_file_server` 的调用**一字不改**。**不要照抄** `regenerate-pdf` 端点——协议没有生成式 PDF。

- [ ] **Step 4: 在 `app/api/v1/__init__.py` include 路由**（按该文件既有写法）

- [ ] **Step 5: 跑测试确认通过** — 3 passed

- [ ] **Step 6: 提交**

```bash
git add epms-api/app/api/v1/agreement_attachments.py epms-api/app/api/v1/__init__.py \
        epms-api/tests/test_agreement_attachments.py
git commit -m "feat(agreement): attachments via file-api, mirroring pr_attachments"
```

---

## Task 6: 发票认领按协议类型分支（含 1A legacy_settlement 遗留修复）

**Files:**
- Modify: `epms-api/app/crud/agreement_schedule.py`
- Modify: `epms-api/app/crud/invoice.py`（`_match_to_agreement`，约 371-430 行）
- Modify: `epms-api/app/schemas/invoice.py`（`InvoiceMatchRequest`）
- Test: `epms-api/tests/test_agreement_schedule_claim.py`

**Interfaces:**
- Consumes: Task 4 的 `ensure_period_rows` 产出的 period 行
- Produces:
  ```python
  async def claim_next_period(db, agr, invoice) -> AgreementPaymentSchedule | None:
      """FIFO 取第一条 pending/overdue 期行;超容差或无候选返回 None。"""
  async def claim_milestone(db, agr, invoice, row_id: uuid.UUID) -> AgreementPaymentSchedule:
      """人工指定阶段;阶段不属于该协议 / 已被认领 → ValueError。"""
  # InvoiceMatchRequest.schedule_id: uuid.UUID | None
  ```

- [ ] **Step 1: 写失败测试**

Create `epms-api/tests/test_agreement_schedule_claim.py`：

```python
"""排期行认领 —— FIFO、容差、milestone 人工指定。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement_schedule as sched_crud
from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _seed(db, **over):
    """种子必须建在同一个 async session 里 —— conftest 的 seeded_vendor 走的是
    另一条未提交的 psycopg2 连接,async engine 看不见(FK 违约)。"""
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Bell", category="supplier",
                    contact_name="AP", contact_email="ap@bell.example")
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="T", role="procurement_officer"))
    await db.flush()
    kw = dict(
        number=f"AGR-202608-{uuid.uuid4().hex[:4]}", title="Bell", agreement_type="recurring",
        vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 31),
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1200.00"), tolerance_pct=Decimal("5.00"),
        overdue_after_days=7, status="active", created_by=user.id,
    )
    kw.update(over)
    agr = PurchaseAgreement(**kw)
    db.add(agr)
    await db.flush()
    return agr, vendor, user


async def _invoice(db, agr, vendor, user, total="1200.00"):
    inv = Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
        vendor_invoice_number=f"B{uuid.uuid4().hex[:6]}",
        vendor_id=vendor.id, vendor_name=vendor.name,
        amount=Decimal(total), tax_amount=Decimal("0"), total_amount=Decimal(total),
        currency="CAD", invoice_date=date(2026, 2, 3), due_date=date(2026, 3, 3),
        status="unmatched", line_items=[], created_by=user.id,
    )
    db.add(inv)
    await db.flush()
    return inv


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def test_claim_takes_the_lowest_pending_sequence(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row is not None and row.sequence == 1 and row.period_label == "2026-01"
        await db.commit()


async def test_claim_skips_already_received_rows(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        first = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence).limit(1))).scalar_one()
        first.status = "received"
        await db.flush()
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row.sequence == 2
        await db.commit()


async def test_overdue_rows_are_still_claimable(test_engine):
    # 逾期只是"还没来票"的标记,票来了照样该认领 —— 否则缺票告警反而堵死了收票。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        first = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence).limit(1))).scalar_one()
        first.status = "overdue"
        await db.flush()
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row.sequence == 1 and row.status == "received"
        await db.commit()


async def test_amount_inside_tolerance_claims_the_row(test_engine):
    # expected 1200, tolerance 5% → 允许区间 [1140, 1260]
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1150.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_amount_outside_tolerance_claims_nothing(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1400.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is None
        rows = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all()
        assert all(r.status == "pending" for r in rows)
        await db.commit()


async def test_null_expected_amount_skips_the_amount_check(test_engine):
    # 决策 3:每期金额选填 → 不填就不做金额校验,任何金额都认领。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, expected_amount_per_period=None,
                                        tolerance_pct=None)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="99999.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_no_candidate_rows_returns_none(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        for r in (await db.execute(select(AgreementPaymentSchedule).where(
                AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all():
            r.status = "received"
        await db.flush()
        inv = await _invoice(db, agr, vendor, user)
        assert await sched_crud.claim_next_period(db, agr, inv) is None
        await db.commit()


async def test_claimed_row_records_the_invoice_and_flips_to_received(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row.status == "received" and row.invoice_id == inv.id
        await db.commit()


async def test_milestone_claim_rejects_a_row_from_another_agreement(test_engine):
    async with _factory(test_engine)() as db:
        agr_a, vendor, user = await _seed(db, agreement_type="milestone",
                                          recurring_type=None, expected_invoice_day=None,
                                          expected_amount_per_period=None, tolerance_pct=None)
        agr_b, _, _ = await _seed(db, agreement_type="milestone", recurring_type=None,
                                  expected_invoice_day=None,
                                  expected_amount_per_period=None, tolerance_pct=None)
        foreign = AgreementPaymentSchedule(
            agreement_id=agr_b.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit", status="pending")
        db.add(foreign)
        await db.flush()
        inv = await _invoice(db, agr_a, vendor, user)
        with pytest.raises(ValueError, match="does not belong"):
            await sched_crud.claim_milestone(db, agr_a, inv, foreign.id)
        await db.commit()


async def test_already_claimed_milestone_cannot_be_claimed_again(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, agreement_type="milestone", recurring_type=None,
                                        expected_invoice_day=None,
                                        expected_amount_per_period=None, tolerance_pct=None)
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit on signing", status="pending")
        db.add(row)
        await db.flush()
        inv1 = await _invoice(db, agr, vendor, user)
        await sched_crud.claim_milestone(db, agr, inv1, row.id)
        inv2 = await _invoice(db, agr, vendor, user)
        with pytest.raises(ValueError, match="already has an invoice"):
            await sched_crud.claim_milestone(db, agr, inv2, row.id)
        await db.commit()
```

再在 `epms-api/tests/test_agreement_invoice_match.py` 里补两条走完整 `crud.invoice.match()` 路径的用例。该文件已有 `seed_vendor_and_user`（从 `tests/test_agreements.py` import）、`_make_active_agreement`、`_upload_invoice` 三个 helper——直接复用，只需给 `_make_active_agreement` 加一个 `agreement_type` / 周期参数的可选形参（默认值保持 `house_account`，既有调用不受影响）。

```python
async def test_recurring_match_does_not_flag_legacy_settlement(test_engine, admin_client):
    """1A 把**所有**协议匹配都标成无凭证付款 —— 那时协议匹配确实没有凭证。
    recurring 有排期行 + 履约确认,再统一标 legacy 会让协议详情页的
    'settled without receipt' 计数把每一张正常周期账单都算进去,那个健康度
    指标就废了。"""
    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh_agr)
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        # 注意:没有传 legacy_settlement_reason —— recurring 不该再要求它。
        await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                         matched_by=user_id)
        await db.commit()

    async with factory() as db:
        done = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert done.legacy_settlement is False
        assert done.legacy_settlement_reason is None
        assert done.schedule_id is not None
        assert done.match_route_auto is True


async def test_house_account_match_still_requires_a_reason(test_engine, admin_client):
    """1A 行为不变:house_account 仍是无凭证通道,理由仍必填。"""
    from app.crud.invoice import AgreementMatchInvalid, match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="250.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(AgreementMatchInvalid, match="reason is required"):
            await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                             matched_by=user_id)
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现两个 claim 函数**

追加到 `app/crud/agreement_schedule.py`：

```python
async def claim_next_period(
    db: AsyncSession, agr: PurchaseAgreement, invoice
) -> AgreementPaymentSchedule | None:
    """FIFO 认领。

    **按 sequence 取,不按发票日期选期次** —— 周期账单的到达日常常落在下一期
    (8 月的网络费 9/3 才开票),按 invoice_date 落在哪个期窗口去选行会系统性地
    错配一整期。周期账单本身按顺序来,FIFO 更贴合实际;乱序到达(供应商补开
    上上个月的票)由人工在 match_review 指定,这是有意留的兜底。
    """
    row = (await db.execute(
        select(AgreementPaymentSchedule)
        .where(AgreementPaymentSchedule.agreement_id == agr.id,
               AgreementPaymentSchedule.schedule_type == "period",
               AgreementPaymentSchedule.status.in_(("pending", "overdue")))
        .order_by(AgreementPaymentSchedule.sequence)
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        return None

    if row.expected_amount is not None:
        tol = row.tolerance_pct or Decimal("0")
        span = row.expected_amount * tol / Decimal("100")
        amount = Decimal(str(invoice.total_amount))
        if not (row.expected_amount - span <= amount <= row.expected_amount + span):
            return None

    row.status = "received"
    row.invoice_id = invoice.id
    await db.flush()
    return row


async def claim_milestone(
    db: AsyncSession, agr: PurchaseAgreement, invoice, row_id: uuid.UUID
) -> AgreementPaymentSchedule:
    row = (await db.execute(
        select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
    )).scalar_one_or_none()
    if row is None or row.agreement_id != agr.id or row.schedule_type != "milestone":
        raise ValueError("That milestone stage does not belong to this agreement")
    if row.invoice_id is not None:
        raise ValueError(
            f"Milestone '{row.milestone_name}' already has an invoice matched to it")
    # 不做金额校验(设计 §5.2):预期与实际并排显示给人眼判断,超支由协议 NTE 预警覆盖。
    row.status = "received"
    row.invoice_id = invoice.id
    await db.flush()
    return row
```

- [ ] **Step 4: 改 `_match_to_agreement` 分支**

`app/crud/invoice.py` 顶部按既有写法补一行 import（该文件已有 `from app.crud import agreement as agreement_crud`，紧挨着加）：

```python
from app.crud import agreement_schedule as agreement_schedule_crud
```

再把现在**无条件**要求理由的那段替换为：

```python
    # 1A 曾把**所有**协议匹配都当成"无凭证付款":那时协议匹配确实没有任何凭证。
    # 1B 之后 recurring 有排期行 + 履约确认、milestone 有阶段行,都是真凭证 ——
    # 再统一标 legacy 会把每一张正常的周期账单算进协议详情页的
    # "settled without receipt" 计数里,那个健康度指标就废了。
    claimed_row = None
    if agr.agreement_type == "house_account":
        reason = (req.legacy_settlement_reason or "").strip()
        if not reason:
            raise AgreementMatchInvalid(
                "A reason is required to settle an agreement invoice without receipt evidence")
        invoice.legacy_settlement = True
        invoice.legacy_settlement_reason = reason
    else:
        invoice.legacy_settlement = False
        invoice.legacy_settlement_reason = None
        if agr.agreement_type == "recurring":
            claimed_row = await agreement_schedule_crud.claim_next_period(db, agr, invoice)
            if claimed_row is None:
                # 认不到期次(超容差 / 无候选行)就不猜,停在复核队列由人工指定。
                require_review = True
        else:   # milestone
            if req.schedule_id is None:
                raise AgreementMatchInvalid(
                    "Pick the milestone stage this invoice pays for")
            try:
                claimed_row = await agreement_schedule_crud.claim_milestone(
                    db, agr, invoice, req.schedule_id)
            except ValueError as exc:
                raise AgreementMatchInvalid(str(exc)) from exc
```

并在下面写字段的那一段里，把原来无条件的两行 legacy 赋值删掉，`match_route_auto` 改为：

```python
    invoice.schedule_id = claimed_row.id if claimed_row else None
    invoice.match_route_auto = agr.agreement_type == "recurring" and claimed_row is not None
```

`app/schemas/invoice.py` 的 `InvoiceMatchRequest` 加：

```python
    # milestone 协议必填 —— 人工指定这张票付的是哪个阶段。
    schedule_id: uuid.UUID | None = None
```

- [ ] **Step 5: 跑测试确认通过**

Run: `... python -m pytest tests/test_agreement_schedule_claim.py tests/test_agreement_invoice_match.py -v`

Expected: 新用例全过。**`test_agreement_invoice_match.py` 里断言 house_account 必须填理由的用例仍过**；若那里有断言"任何协议匹配都 `legacy_settlement=True`"的用例，它**应该**失败——改测试并在提交信息里说明这是有意的行为变更，不要改回实现。

- [ ] **Step 6: 提交**

```bash
git add epms-api/app/crud/agreement_schedule.py epms-api/app/crud/invoice.py \
        epms-api/app/schemas/invoice.py epms-api/tests/test_agreement_schedule_claim.py \
        epms-api/tests/test_agreement_invoice_match.py
git commit -m "feat(agreement): claim schedule rows on match; stop flagging non-house-account as legacy"
```

---

## Task 7: 履约确认任务 + PA 闸门

recurring 免 GR，**履约确认是它唯一的代偿**。没有它，网络断了一个月，发票照样自动认领、自动进 PA、自动付掉，全程无人确认服务交付。

**Files:**
- Modify: `epms-api/app/crud/agreement_schedule.py`
- Modify: `epms-api/app/api/v1/agreements.py`
- Modify: `epms-api/app/api/v1/pa.py`
- Modify: `epms-api/app/models/task.py`（只加注释，登记新 task type）
- Test: `epms-api/tests/test_agreement_period_confirm.py`

**Interfaces:**
- Consumes: Task 6 的 `claim_next_period`
- Produces:
  ```python
  async def create_confirm_task(db, agr, row) -> None: ...
  async def confirm_period(db, agr, row_id, user_id) -> AgreementPaymentSchedule: ...
  # POST /agreements/{agreement_id}/schedule/{row_id}/confirm -> ScheduleRowResponse
  # task type "confirm_period"; document_type="agr", document_id=agreement.id,
  # document_number=f"{agr.number} · {row.period_label}"
  ```

> **为什么把期次塞进 `document_number` 而不是给 tasks 加一列**：`tasks` 被 approval-api、expense-api、finance-api **三个服务镜像**（`approval-api/app/models/task.py:14`、`expense-api/app/models/task_mirror.py:20`、`finance-api/app/models/mirrors.py:141`）。为一个 EPMS 局部功能给共享表加列，等于给三棵服务树都埋一个要同步的点。`document_number` 是 `String(40)`，`"AGR-202608-0001 · 2026-01"` 只有 25 字符，装得下，而且在任务箱里显示出来正好是用户需要看到的信息。

- [ ] **Step 1: 写失败测试**

Create `epms-api/tests/test_agreement_period_confirm.py`。`_seed` / `_invoice` / `_factory` 三个 helper **从 Task 6 的测试文件复制过来**（复制，不要跨测试模块 import）。

```python
async def test_claiming_a_period_creates_a_confirm_task_for_the_owner(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        agr.owner_id = user.id
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        await sched_crud.create_confirm_task(db, agr, row)
        await db.commit()
        task = (await db.execute(select(Task).where(
            Task.document_id == agr.id, Task.type == "confirm_period"))).scalar_one()
        assert task.assigned_user_id == user.id
        assert task.document_number == f"{agr.number} · 2026-01"
        assert task.is_completed is False


async def test_without_an_owner_the_task_falls_back_to_the_department_manager(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        mgr = await user_crud.create(db, RegisterRequest(
            email=f"mgr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Dept Manager", role="dept_manager"))
        dept = Department(name=f"Eng-{uuid.uuid4().hex[:6]}", is_active=True)
        db.add(dept)
        await db.flush()
        mgr.department_id = dept.id
        agr.owner_id = None
        agr.department_id = dept.id
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        await sched_crud.create_confirm_task(db, agr, row)
        await db.commit()
        task = (await db.execute(select(Task).where(
            Task.document_id == agr.id, Task.type == "confirm_period"))).scalar_one()
        assert task.assigned_role == "dept_manager"
        assert task.assigned_user_id == mgr.id


async def test_confirm_stamps_accepted_by_and_at(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        await sched_crud.create_confirm_task(db, agr, row)
        confirmed = await sched_crud.confirm_period(db, agr, row.id, user.id)
        await db.commit()
        assert confirmed.accepted_by == user.id
        assert confirmed.accepted_at is not None


async def test_confirm_completes_only_the_matching_period_task(test_engine):
    # 两期各有一个未完成任务 → 确认第一期后,只有第一期那条被关掉。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        agr.owner_id = user.id
        await sched_crud.ensure_period_rows(db, agr)
        rows = []
        for _ in range(2):
            inv = await _invoice(db, agr, vendor, user)
            r = await sched_crud.claim_next_period(db, agr, inv)
            await sched_crud.create_confirm_task(db, agr, r)
            rows.append(r)
        await sched_crud.confirm_period(db, agr, rows[0].id, user.id)
        await db.commit()
        tasks = (await db.execute(select(Task).where(
            Task.document_id == agr.id, Task.type == "confirm_period"
        ).order_by(Task.document_number))).scalars().all()
        done = {t.document_number: t.is_completed for t in tasks}
        assert done[f"{agr.number} · {rows[0].period_label}"] is True
        assert done[f"{agr.number} · {rows[1].period_label}"] is False


async def test_confirming_an_unclaimed_row_is_rejected(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id
        ).order_by(AgreementPaymentSchedule.sequence).limit(1))).scalar_one()
        with pytest.raises(ValueError, match="nothing to confirm"):
            await sched_crud.confirm_period(db, agr, row.id, user.id)
        await db.commit()


async def test_confirming_twice_is_rejected(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        await sched_crud.confirm_period(db, agr, row.id, user.id)
        with pytest.raises(ValueError, match="already been confirmed"):
            await sched_crud.confirm_period(db, agr, row.id, user.id)
        await db.commit()


async def test_milestone_rows_cannot_be_confirmed_in_this_phase(test_engine):
    # 阶段验收是 Phase 1C。本期 milestone 行走到这里必须被明确拒绝,
    # 而不是悄悄写 accepted_by —— 那会让 1C 面对一批语义不明的历史数据。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, agreement_type="milestone", recurring_type=None,
                                        expected_invoice_day=None,
                                        expected_amount_per_period=None, tolerance_pct=None)
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit", status="pending")
        db.add(row)
        await db.flush()
        with pytest.raises(ValueError, match="later phase"):
            await sched_crud.confirm_period(db, agr, row.id, user.id)
        await db.commit()
```

顶部 import 除 Task 6 那份外，另需 `from app.models.task import Task` 与 `from app.models.department import Department`。

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现 `create_confirm_task` 与 `confirm_period`**

追加到 `app/crud/agreement_schedule.py`（顶部补 `from datetime import datetime, timezone`、`from app.models.task import Task`、`from app.models.user import User`）：

```python
async def _confirm_assignee(db: AsyncSession, agr: PurchaseAgreement) -> uuid.UUID | None:
    """协议责任人优先;没设 owner 就落到该部门的在职经理。"""
    if agr.owner_id:
        return agr.owner_id
    if not agr.department_id:
        return None
    return (await db.execute(
        select(User.id).where(User.department_id == agr.department_id,
                              User.role == "dept_manager",
                              User.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()


async def create_confirm_task(
    db: AsyncSession, agr: PurchaseAgreement, row: AgreementPaymentSchedule
) -> None:
    """认领成功后派履约确认任务。

    这是普通任务,不是审批流 —— 不进 workflow_defs,不需要新的 action key。
    期次塞在 document_number 里而不是给 tasks 加列:tasks 被三个服务镜像。
    """
    db.add(Task(
        type="confirm_period",
        priority="normal",
        document_type="agr",
        document_id=agr.id,
        document_number=f"{agr.number} · {row.period_label}",
        assigned_role="dept_manager",
        assigned_user_id=await _confirm_assignee(db, agr),
        title=f"Confirm service for {row.period_label}: {agr.title}",
        description=(
            f"An invoice has been matched to {agr.number} for {row.period_label}. "
            "Confirm the service was delivered as expected — payment cannot be "
            "raised until this is confirmed."
        ),
        amount=row.expected_amount,
        vendor=agr.vendor_name,
    ))
    await db.flush()


async def confirm_period(
    db: AsyncSession, agr: PurchaseAgreement, row_id: uuid.UUID, user_id: uuid.UUID
) -> AgreementPaymentSchedule:
    row = (await db.execute(
        select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
    )).scalar_one_or_none()
    if row is None or row.agreement_id != agr.id:
        raise ValueError("That schedule row does not belong to this agreement")
    if row.schedule_type != "period":
        raise ValueError(
            "Milestone stages are accepted in a later phase, not confirmed here")
    if row.invoice_id is None:
        raise ValueError(
            f"No invoice has been matched to {row.period_label} yet — there is "
            "nothing to confirm")
    if row.accepted_at is not None:
        raise ValueError(f"{row.period_label} has already been confirmed")

    row.accepted_by = user_id
    row.accepted_at = datetime.now(timezone.utc)

    doc_number = f"{agr.number} · {row.period_label}"
    tasks = (await db.execute(
        select(Task).where(Task.document_type == "agr", Task.document_id == agr.id,
                           Task.type == "confirm_period",
                           Task.document_number == doc_number,
                           Task.is_completed.is_(False))
    )).scalars().all()
    for t in tasks:
        t.is_completed = True
        t.completed_at = row.accepted_at
        t.completed_by = user_id
    await db.flush()
    return row
```

在 Task 6 的 `_match_to_agreement` recurring 分支里，`claimed_row` 非空时补一行 `await agreement_schedule_crud.create_confirm_task(db, agr, claimed_row)`。

- [ ] **Step 4: 加确认端点**

`app/api/v1/agreements.py`：

```python
@router.post("/{agreement_id}/schedule/{row_id}/confirm", response_model=ScheduleRowResponse)
async def confirm_schedule_period(
    agreement_id: uuid.UUID, row_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload
):
    # 由持有确认任务的人执行 —— 与 PR/PO/PA 的任务型动作一致,不另设权限键。
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    try:
        row = await agr_sched_crud.confirm_period(
            db, agr, row_id, uuid.UUID(user["sub"]))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(row)
    return row
```

- [ ] **Step 5: PA 闸门**

`app/api/v1/pa.py` 的 agreement 分支，在"每张发票都必须挂在本协议上"那段校验之后加：

```python
        # recurring 免 GR,履约确认是它唯一的代偿 —— 未确认的期次不许付款。
        # milestone 本期没有验收闸门(设计 §5.3),house_account 走 slip 路径,
        # 所以这里按 agreement_type 分支,不能一刀切。
        if agr.agreement_type == "recurring":
            unconfirmed = (await db.execute(
                select(AgreementPaymentSchedule.period_label)
                .join(Invoice, Invoice.schedule_id == AgreementPaymentSchedule.id)
                .where(Invoice.id.in_(body.invoice_ids),
                       AgreementPaymentSchedule.accepted_at.is_(None))
            )).scalars().all()
            if unconfirmed:
                raise HTTPException(
                    status_code=422,
                    detail=(f"Service has not been confirmed for {', '.join(unconfirmed)}. "
                            "The department must confirm delivery before payment can be raised."))
```

- [ ] **Step 6: 在 `app/models/task.py` 的类型注释里登记 `confirm_period`**（那份注释是唯一的类型清单）

- [ ] **Step 7: 跑测试确认通过**

Run: `... python -m pytest tests/test_agreement_period_confirm.py tests/test_agreement_pa.py -v`
Expected: 新增 7 passed，`test_agreement_pa.py` 既有用例不回归（那些都是 house_account，新闸门不该碰到它们——**若碰到了，说明分支条件写错了**）

- [ ] **Step 8: 提交**

```bash
git add epms-api/app/crud/agreement_schedule.py epms-api/app/api/v1/agreements.py \
        epms-api/app/api/v1/pa.py epms-api/app/models/task.py \
        epms-api/tests/test_agreement_period_confirm.py
git commit -m "feat(agreement): service confirmation task and payment gate for recurring"
```

---

## Task 8: 缺票逾期扫描 + 通知开关

**Files:**
- Create: `epms-api/app/tasks/agreement_overdue.py`
- Modify: `epms-api/app/main.py`
- Modify: `epms-api/app/crud/config.py`
- Test: `epms-api/tests/test_agreement_overdue_sweep.py`

**Interfaces:**
- Produces:
  ```python
  async def sweep_overdue_periods(db) -> list[AgreementPaymentSchedule]:
      """把逾期的 period 行置 overdue 并返回它们。无条件跑,不看开关。"""
  async def run_agreement_overdue() -> None: ...
  async def agreement_overdue_loop() -> None: ...
  # config: notification_settings["agreement_overdue_enabled"] 默认 True
  ```

- [ ] **Step 1: 写失败测试**

Create `epms-api/tests/test_agreement_overdue_sweep.py`。helper 从 Task 6 的测试文件复制 `_seed` / `_factory`。

因为扫描用的是 `date.today()`，测试**不要 monkeypatch 时间**——直接用相对今天构造的 `expected_date`，这样测试永远不会因为跑的日期变了而漂。

```python
"""缺票逾期扫描。"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.tasks.agreement_overdue import sweep_overdue_periods

pytestmark = pytest.mark.asyncio

# _seed / _factory 从 tests/test_agreement_schedule_claim.py 复制过来


async def _row(db, agr, *, days_ago: int, grace: int = 7,
               status: str = "pending", schedule_type: str = "period"):
    row = AgreementPaymentSchedule(
        agreement_id=agr.id, schedule_type=schedule_type, sequence=1,
        expected_date=date.today() - timedelta(days=days_ago) if schedule_type == "period" else None,
        expected_timing=None if schedule_type == "period" else "After signing",
        milestone_name=None if schedule_type == "period" else "Deposit",
        period_label="2026-01" if schedule_type == "period" else None,
        overdue_after_days=grace, status=status,
    )
    db.add(row)
    await db.flush()
    return row


async def test_a_row_past_expected_date_plus_grace_becomes_overdue(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=8, grace=7)      # 到票日 + 7 < 今天
        flipped = await sweep_overdue_periods(db)
        await db.commit()
        assert [r.id for r in flipped] == [row.id]
        assert row.status == "overdue"


async def test_the_boundary_day_itself_is_not_overdue(test_engine):
    # 到票日 + grace == 今天 → **不算**逾期。宽限期的最后一天仍在宽限内。
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=7, grace=7)
        assert await sweep_overdue_periods(db) == []
        assert row.status == "pending"
        await db.commit()


async def test_received_rows_are_never_swept(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=90, status="received")
        assert await sweep_overdue_periods(db) == []
        assert row.status == "received"
        await db.commit()


async def test_milestone_rows_are_never_swept(test_engine):
    """milestone 行没有 expected_date,本就进不了扫描。但实现里的查询必须**显式**
    带 schedule_type='period' —— Phase 1C 若给阶段加了可选日期,靠
    `expected_date IS NULL` 的隐式过滤会静默失效,阶段会被当成逾期期次刷掉。"""
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db, agreement_type="milestone", recurring_type=None,
                                expected_invoice_day=None,
                                expected_amount_per_period=None, tolerance_pct=None)
        row = await _row(db, agr, days_ago=90, schedule_type="milestone")
        assert await sweep_overdue_periods(db) == []
        assert row.status == "pending"
        await db.commit()


async def test_sweep_uses_the_default_grace_when_the_row_has_none(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=8)
        row.overdue_after_days = None       # 回落默认 7 天
        await db.flush()
        assert [r.id for r in await sweep_overdue_periods(db)] == [row.id]
        await db.commit()
```

> 通知开关（`agreement_overdue_enabled`）由 `run_agreement_overdue()` 读取，而 `sweep_overdue_periods()` **不看开关**——上面每条测试都只调后者，正是为了钉住"开关只管发不发通知，状态照样扫"这条设计。

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

Create `epms-api/app/tasks/agreement_overdue.py`：

```python
"""缺票逾期扫描 —— 每日把过期未收到票的排期行置 overdue 并提醒协议责任人。

复用 daily_followup 的调度形状(同一个 followup_time),但**独立开关**:
notification_settings.agreement_overdue_enabled,默认 True。

⚠️ 与 daily_followup 一样是**单实例假设**。epms-api 若扩到多副本,扫描会重复
跑。这是既有模式的既有问题,本期沿用,不新增也不解决。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.crud.config import get_or_create as get_config
from app.db import session as session_module
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.services.notification import send_admin_alert
from app.tasks.daily_followup import _load_schedule, _seconds_until_next_run

logger = logging.getLogger(__name__)


async def sweep_overdue_periods(db) -> list[AgreementPaymentSchedule]:
    """状态扫描。无条件跑 —— 开关只管发不发通知,数据该对还是要对。

    显式限定 schedule_type='period':milestone 行本期没有 expected_date,但
    Phase 1C 若给阶段加了可选日期,靠 NULL 隐式过滤就会静默失效。
    """
    today = date.today()
    rows = (await db.execute(
        select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.schedule_type == "period",
            AgreementPaymentSchedule.status == "pending",
            AgreementPaymentSchedule.expected_date.is_not(None),
        )
    )).scalars().all()
    flipped = []
    for row in rows:
        grace = row.overdue_after_days if row.overdue_after_days is not None else 7
        if row.expected_date + timedelta(days=grace) < today:
            row.status = "overdue"
            flipped.append(row)
    await db.flush()
    return flipped


async def run_agreement_overdue() -> None:
    logger.info("Agreement overdue: starting sweep")
    try:
        async with session_module.AsyncSessionLocal() as db:
            flipped = await sweep_overdue_periods(db)
            cfg = await get_config(db)
            notify = (cfg.notification_settings or {}).get("agreement_overdue_enabled", True)
            if flipped and notify:
                agr_ids = {r.agreement_id for r in flipped}
                agreements = (await db.execute(
                    select(PurchaseAgreement).where(PurchaseAgreement.id.in_(agr_ids))
                )).scalars().all()
                by_id = {a.id: a for a in agreements}
                lines = "".join(
                    f"<li>{by_id[r.agreement_id].number} — {r.period_label} "
                    f"(expected {r.expected_date})</li>" for r in flipped)
                await send_admin_alert(
                    f"{len(flipped)} agreement invoice(s) overdue",
                    f"<p>No invoice has arrived for:</p><ul>{lines}</ul>", db)
            await db.commit()
        logger.info("Agreement overdue: %d row(s) flipped", len(flipped))
    except Exception as exc:  # noqa: BLE001 — 一次失败不能杀掉循环
        logger.error("Agreement overdue: sweep failed: %s", exc)


async def agreement_overdue_loop() -> None:
    while True:
        hour, minute = await _load_schedule()
        await asyncio.sleep(min(_seconds_until_next_run(hour, minute), 900))
        hour, minute = await _load_schedule()
        if _seconds_until_next_run(hour, minute) > 60:
            continue
        await run_agreement_overdue()
        await asyncio.sleep(90)
```

`app/crud/config.py` 的通知设置默认字典里加：

```python
    # 默认 ON 是刻意的:daily_followup_enabled 默认 OFF,结果上线后没人知道要去
    # admin 打开、提醒一直没发。开关的作用是"吵了可以关掉",不是"要用得先找到它"。
    "agreement_overdue_enabled": True,
```

`app/main.py` 的 lifespan 里，照 `followup_task` 的写法再起一个：

```python
    from app.tasks.agreement_overdue import agreement_overdue_loop
    overdue_task = asyncio.create_task(agreement_overdue_loop())
```
并在 `yield` 之后 `overdue_task.cancel()`。

- [ ] **Step 4: 跑测试确认通过** — 5 passed

- [ ] **Step 5: 提交**

```bash
git add epms-api/app/tasks/agreement_overdue.py epms-api/app/main.py \
        epms-api/app/crud/config.py epms-api/tests/test_agreement_overdue_sweep.py
git commit -m "feat(agreement): daily overdue sweep with an ON-by-default toggle"
```

---

## Task 9: 前端服务层、hooks、预算级联组件

**Files:**
- Modify: `epms/src/services/agreement.ts`
- Modify: `epms/src/hooks/useAgreements.ts`
- Create: `epms/src/services/agreementAttachments.ts`
- Create: `epms/src/components/agreements/BudgetAccountCascade.tsx`

**Interfaces:**
- Produces:
  ```ts
  // services/agreement.ts
  export type RecurringType = 'weekly' | 'monthly' | 'quarterly' | 'yearly'
  export interface ApiScheduleRow {
    id: string; agreement_id: string; schedule_type: 'period' | 'milestone'
    sequence: number; expected_amount: string | null; expected_date: string | null
    expected_timing: string | null; status: 'pending'|'received'|'overdue'|'waived'
    invoice_id: string | null; period_label: string | null
    tolerance_pct: string | null; overdue_after_days: number | null
    milestone_name: string | null; amount_pct: string | null
    accepted_by: string | null; accepted_at: string | null
  }
  export interface MilestoneRowIn {
    milestone_name: string; expected_timing?: string | null
    expected_amount?: string | null; amount_pct?: string | null
  }
  agreementService.schedule(id): Promise<{ items: ApiScheduleRow[] }>
  agreementService.confirmPeriod(agreementId, rowId): Promise<ApiScheduleRow>

  // hooks/useAgreements.ts
  useAgreementSchedule(agreementId: string)
  useConfirmPeriod(agreementId: string)

  // components/agreements/BudgetAccountCascade.tsx
  export function BudgetAccountCascade(props: {
    departmentId: string | undefined
    costCenterId: string | undefined
    budgetCode: string
    onChange: (next: { costCenterId?: string; budgetCode: string }) => void
    disabled?: boolean
  }): JSX.Element
  ```

- [ ] **Step 1: 扩 `services/agreement.ts` 的类型与方法**

`ApiAgreement` 加 7 个新字段（`cost_center_id: string | null`、`recurring_type: RecurringType | null`、`expected_invoice_day: number | null`、`anchor_month: number | null`、`expected_amount_per_period: string | null`、`tolerance_pct: string | null`、`overdue_after_days: number | null`）。**金额与百分比都是 `string`**——Pydantic 把 `Decimal` 发成字符串，用之前必须 `Number()`。

`CreateAgreementBody` / `UpdateAgreementBody` 加同名字段 + `milestones?: MilestoneRowIn[]`。加两个方法：

```ts
  schedule: (id: string) => api.get<{ items: ApiScheduleRow[] }>(`/agreements/${id}/schedule`),
  confirmPeriod: (agreementId: string, rowId: string) =>
    api.post<ApiScheduleRow>(`/agreements/${agreementId}/schedule/${rowId}/confirm`),
```

- [ ] **Step 2: 建 `services/agreementAttachments.ts`**

照抄 `src/services/prAttachments.ts`，把 `/pr/${prId}/attachments` 换成 `/agreements/${agreementId}/attachments`、导出名换成 `agreementAttachmentService`，**删掉 `regeneratePdf`**。其余（`upload` 的 FormData + Bearer 头、`download` 的 blob 取法、`delete`）一字不改。

> 下载**必须**走 `getBlob` 式的带 token 请求，不能用裸 `<a href>`：nginx 的 SPA 兜底会把无 token 的请求重定向回首页，表现为"点下载弹回主页"。

- [ ] **Step 3: 加 hooks**

```ts
export function useAgreementSchedule(agreementId: string) {
  return useQuery({
    queryKey: ['agreements', agreementId, 'schedule'],
    queryFn: () => agreementService.schedule(agreementId),
    enabled: Boolean(agreementId),
  })
}

export function useConfirmPeriod(agreementId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (rowId: string) => agreementService.confirmPeriod(agreementId, rowId),
    // await 是必须的:不 await 的话 isPending 立刻变 false,按钮在旧状态上复活,
    // 再点一次就 409。同 useAgreementAction。
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agreements'] }),
        queryClient.invalidateQueries({ queryKey: ['tasks'] }),
      ])
    },
    onError: (err: unknown) =>
      alert(err instanceof Error ? err.message : 'Failed to confirm the period'),
  })
}
```

- [ ] **Step 4: 建 `BudgetAccountCascade.tsx`**

把 PR 创建页那三级下拉抽成组件，Create/Edit 两页共用（否则两边各写一份必然漂移）。数据源与 PR 完全相同：`useBudgetOverview()` 取 `l1_groups`，`useCostCenters({ department_id, active_only: true })` 取成本中心。JSX 直接照搬 `epms/src/pages/pr/PrCreatePage.tsx:559-604` 那三个 `<select>`，把内部 state 换成 props 驱动：

- 第一级 Cost Center，`disabled={!departmentId}`
- 第二级 L1 Category，`disabled={!costCenterCode}`，选项 = `l1Groups.filter(l => l.is_active)`
- 第三级 L2 Sub-account，`disabled={!l1Code}`，选项 = `selectedL1Obj?.accounts` 过滤 `is_active || code === budgetCode`；选中即 `onChange({ costCenterId, budgetCode: code })`

**部门切换要清三级**：`departmentId` 变化时把 CC / L1 / L2 全清空并回吐 `onChange({ costCenterId: undefined, budgetCode: '' })`。旧成本中心可能不属于新部门，留着会写进一个越界的 `budget_code`。

- [ ] **Step 5: 类型门禁**

Run:
```bash
docker compose -f docker-compose.dev.yml -f docker-compose.agreement-test.yml \
  exec -T epms-frontend sh -c 'npx tsc --version && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -cE "error TS"'
```
Expected: 版本必须是 **5.9.3**（不是全局 6.0.3 回落），错误数 = 你在 Step 0 量的基线

- [ ] **Step 6: 提交**

```bash
git add epms/src/services/agreement.ts epms/src/services/agreementAttachments.ts \
        epms/src/hooks/useAgreements.ts epms/src/components/agreements/BudgetAccountCascade.tsx
git commit -m "feat(agreement/ui): schedule + attachment services, shared budget cascade"
```

---

## Task 10: Create / Edit 页 —— 预算级联、recurring 字段组、阶段编辑器、附件

**Files:**
- Create: `epms/src/components/agreements/RecurringFields.tsx`
- Create: `epms/src/components/agreements/MilestoneEditor.tsx`
- Modify: `epms/src/pages/agreements/AgreementCreatePage.tsx`
- Modify: `epms/src/pages/agreements/AgreementEditPage.tsx`

**Interfaces:**
- Consumes: Task 9 的 `BudgetAccountCascade`、`MilestoneRowIn`、`RecurringType`
- Produces:
  ```ts
  export function RecurringFields(props: {
    value: { recurringType: RecurringType | ''; expectedInvoiceDay: string
             anchorMonth: string; amountPerPeriod: string; tolerancePct: string
             overdueAfterDays: string }
    validFrom: string
    onChange: (next: RecurringFieldsValue) => void
  }): JSX.Element

  export function MilestoneEditor(props: {
    rows: MilestoneRowIn[]
    notToExceed: string
    currency: string
    onChange: (rows: MilestoneRowIn[]) => void
  }): JSX.Element
  ```

- [ ] **Step 1: 建 `RecurringFields.tsx`**

`agreement_type === 'recurring'` 时才渲染。字段：

| 控件 | 说明 |
|---|---|
| Cycle | `weekly / monthly / quarterly / yearly`，必填 |
| Expected invoice day | weekly → 星期下拉（Monday..Sunday，值 1..7）；其余 → 数字输入 1-31，帮助文本 `Clamped to the last day in shorter months` |
| Anchor month | **仅 quarterly / yearly 显示**，必填。切到这两种时**默认预填 `validFrom` 的月份**，可改。帮助文本：`Quarterly billing often runs Feb/May/Aug/Nov rather than the calendar quarters` |
| Expected amount per period | 选填。帮助文本 `Leave blank for usage-based bills — invoices will be claimed in order without an amount check` |
| Tolerance % | 选填，仅在填了金额时启用 |
| Overdue after (days) | 默认 7 |

- [ ] **Step 2: 建 `MilestoneEditor.tsx`**

`agreement_type === 'milestone'` 时才渲染。一个可增删行的表格，列：`Stage name`（必填）、`Timing`（纯文本，placeholder `e.g. Within 1 week after contract signing`）、`Amount`、`% of NTE`。

- 输入 `%` 时按 `Number(notToExceed) * pct / 100` 实时算出 Amount 并回填；输入 Amount 时反算 `%`
- **`notToExceed` 为空时 `%` 输入框 `disabled`**，并在下方给出原因 `Set a not-to-exceed ceiling on the agreement to enter percentages`。不要让用户填完才报错
- 底部显示合计与 `NTE` 的对比（只提示，不拦截）
- 删行按钮：**先取 `rows[i]` 判空再用**——`MatrixGrid` 那次 `undefined.id` 崩溃就是删行时索引越界（见 `project_uniops_mrp_forecast_remove_row_crash`）

- [ ] **Step 3: 接进 Create 页**

- `Budget Code` 文本框 → `<BudgetAccountCascade>`，提交时同时带 `cost_center_id` 与 `budget_code`
- 按 `agreementType` 条件渲染 `<RecurringFields>` / `<MilestoneEditor>`
- **切换 `agreementType` 时清空另一侧的状态**，否则 schema 的 `only apply to a recurring agreement` 会 422
- 附件：`const [attachments, setAttachments] = useState<File[]>([])`，提交成功拿到 `newAgr.id` 后 `await Promise.all(attachments.map(f => agreementAttachmentService.upload(newAgr.id, f)))`——与 `PrCreatePage.tsx:335` 同一写法
- 提交按钮 `disabled={mutation.isPending}`

- [ ] **Step 4: 接进 Edit 页**

同 Step 3，外加：进页时用协议已有值预填三级级联与阶段行（阶段行从 `useAgreementSchedule` 里 `schedule_type === 'milestone'` 的行映射回 `MilestoneRowIn`）。**`status` 不在 `draft`/`returned` 时整个表单只读**——与后端 `EDITABLE_STATUSES` 一致。

- [ ] **Step 5: 手工点验（不能只看 tsc）**

按 `feedback_uniops_reachability_in_done`：**tsc 绿不等于用户走得到**。逐条点：

1. 建 recurring 协议，Cycle 选 quarterly → Anchor month 出现且预填 `validFrom` 月份
2. 改 Anchor month 为 2 → 保存成功
3. 建 milestone 协议，NTE 留空 → `%` 输入框确实是禁用的
4. 填 NTE 后输入 `30%` → Amount 自动算出
5. 删中间一行 → **不崩**
6. 上传一个 PDF → 建档后详情页能看到

- [ ] **Step 6: 类型门禁 + 提交**

```bash
git add epms/src/components/agreements/ epms/src/pages/agreements/AgreementCreatePage.tsx \
        epms/src/pages/agreements/AgreementEditPage.tsx
git commit -m "feat(agreement/ui): budget cascade, recurring fields, milestone editor, attachments"
```

---

## Task 11: 详情页 —— 排期表 + 附件面板 + 确认按钮

**Files:**
- Create: `epms/src/components/agreements/ScheduleTable.tsx`
- Modify: `epms/src/pages/agreements/AgreementDetailPage.tsx`

**Interfaces:**
- Consumes: Task 9 的 `useAgreementSchedule` / `useConfirmPeriod` / `agreementAttachmentService`

- [ ] **Step 1: 建 `ScheduleTable.tsx`**

按 `schedule_type` 渲染两种表头：

- **period**：`#` / `Period` / `Expected date` / `Expected amount` / `Status` / `Confirmed`。`overdue` 行用 `danger` 徽章。`status === 'received' && !accepted_at` 且当前用户持有该期的 `confirm_period` 任务时，行尾出现 **Confirm** 按钮（`disabled={confirm.isPending}`，文案切 `Working…`）

  `Confirmed` 列要显示人名 + 时间，但 API 只回 `accepted_by` 这个 UUID。用既有的用户列表解析成姓名，**必须用 listAll 式的全量取法**——`GET /users` 默认 `page_size=20`，直接用第一页会让大多数人名解析不出来、静默显示成空白。解析不到时回落显示时间，不要显示裸 UUID
- **milestone**：`#` / `Stage` / `Timing` / `Expected amount` / `% of NTE` / `Status` / `Invoice`。**没有 Confirm 按钮**——阶段验收是 Phase 1C

状态徽章一律用 app 级 `StatusBadge`，不要在页面里写库存色。

- [ ] **Step 2: 接进详情页**

- `recurring` → 渲染 period 表 + 一行汇总（`已收 N / 共 M 期`、`逾期 K 期`）
- `milestone` → 渲染 milestone 表 + 一行汇总（`已开票 N / 共 M 阶段`、金额合计 vs NTE）
- `house_account` → 两张表都不渲染（它没有排期行）
- 附件面板：列表 + 上传 + 下载 + 删除，写/删按 `epms.agreement.write` 门禁
- **Milestone 表上方加一句说明**：`Stages are a record of the payment plan. Matching an invoice to a stage is manual, and no acceptance sign-off is required in this phase.` 不写这句，用户会以为建了阶段就有阶段闸门

- [ ] **Step 3: 手工点验**

1. 审批一张 recurring 协议到 `active` → 排期表出现，期数与到票日与预期一致
2. 匹配一张发票 → 对应期次变 `received`，任务箱出现 `confirm_period`
3. 点 Confirm → 该期 `Confirmed` 列出现人名与时间，任务消失
4. 未确认时去建 PA → 被 422 挡住且提示里带期次标签
5. 确认后再建 PA → 放行

- [ ] **Step 4: 类型门禁 + 提交**

```bash
git add epms/src/components/agreements/ScheduleTable.tsx \
        epms/src/pages/agreements/AgreementDetailPage.tsx
git commit -m "feat(agreement/ui): schedule table, attachments panel, period confirmation"
```

---

## Task 12: MatchPanel 期次预览 / 阶段选择 + 任务箱

**Files:**
- Modify: `epms/src/pages/invoices/MatchPanel.tsx`
- Modify: `epms/src/lib/taskTypes.ts`

- [ ] **Step 1: `taskTypes.ts` 登记 `confirm_period`**

按该文件既有写法加类型、标签（`Confirm Service Period`）与图标，跳转到 `/agreements/{document_id}`。

- [ ] **Step 2: MatchPanel 按协议类型分支**

选中协议候选后：

- **house_account** → 保持 1A 原样：必填 `legacy_settlement_reason`
- **recurring** → **不显示理由输入框**，改为显示一行预览：`This invoice will be claimed against <period_label> (expected <amount>)`。取自 `useAgreementSchedule` 里第一条 `pending`/`overdue` 行。若发票金额超出该行容差，提示 `Amount is outside the tolerance for <period_label> — this invoice will go to review for manual assignment` 且**仍允许提交**（后端会置 `match_review`）
- **milestone** → **不显示理由输入框**，显示阶段单选列表（只列 `invoice_id === null` 的行，含 `Stage / Timing / Expected amount`），选中后把 `schedule_id` 放进匹配请求。未选 → 提交按钮禁用

> `legacy_settlement_reason` 只对 house_account 显示，这是 Task 6 后端分支的正面表达。两边不一致的话，用户会在 recurring 上被要求填一个后端根本不看的理由。

- [ ] **Step 3: 手工点验**

1. recurring 协议 + 金额在容差内 → 看到期次预览，提交后直接 `matched`
2. recurring 协议 + 金额超容差 → 看到超差提示，提交后落在 `match_review`
3. milestone 协议 → 看到阶段列表，不选不能提交；选中后提交，该阶段变 `received`
4. milestone 协议已认领的阶段 → 不出现在列表里
5. house_account → 理由输入框仍在且仍必填

- [ ] **Step 4: 类型门禁 + 提交**

```bash
git add epms/src/pages/invoices/MatchPanel.tsx epms/src/lib/taskTypes.ts
git commit -m "feat(agreement/ui): period preview and milestone picker in MatchPanel"
```

---

## 收尾：全量回归

- [ ] **epms-api 全套**

Run（确认没有别的会话在跑 pytest）：
```bash
docker exec -e POSTGRES_HOST=postgres -e POSTGRES_DB=epms_test \
  -e JWT_SECRET_KEY=test-secret uniops_epms_api python -m pytest -q
```
Expected: 失败集合与你在动手前量的基线**逐条相同**（数量相同还不够——要逐条比对，新失败必须归零）

- [ ] **前端类型门禁**

```bash
docker compose -f docker-compose.dev.yml -f docker-compose.agreement-test.yml \
  exec -T epms-frontend sh -c 'npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -cE "error TS"'
```
Expected: = 基线

- [ ] **写发布清单** `docs/release-notes/2026-08-10-agreement-phase1b.md`：迁移 `ag03`（epms-api），**无部署顺序约束**，需重建 `epms-api` + `epms-web`，`agreement_overdue_enabled` 默认 ON（不需迁移，走 `notification_settings` JSONB）。
