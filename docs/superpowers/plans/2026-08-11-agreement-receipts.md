# 协议凭证重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把写死成"柜台小票"的凭证泛化成带类型的**协议凭证**，并把"关联发票到协议"与"给发票挂凭证"拆成两件独立的事——录入有自己的菜单入口、对账在发票详情页做、协议详情页用 Document Chain 展示三者关系。

**Architecture:** 现有小票实现约七成原样平移（表 / CRUD / 附件 / 释放不变式 / PA 闸门 / 证据包 / OCR / 权限），只做重命名并加一个 `receipt_type` 判别列。真正重做的是**入口与门禁的位置**：匹配处拆除凭证门禁，发票详情新增凭证区块，新增跨协议的凭证列表页与新建页，`DocumentChainTree` 加一条协议轴心分支。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic（epms-api / identity-api）、React 18 + TanStack Query v5 + Tailwind（epms）、Anthropic vision OCR（expense-api，本计划不改）

## Global Constraints

- **UI 与通知文案一律英文**；代码注释可中文。
- **金额一律 `string`**：后端 Pydantic 把 `Decimal` 序列化成 JSON **字符串**，前端算数必须显式 `Number()`；金额比较必须**分级整数**（`Math.round(x*100)`），与后端 `Numeric(15,2)` 精确相等口径一致。OCR 端点返回的是 JSON **number**，两种形状并存，各自处理。
- **绝不自签 JWT、不回显任何密钥、不把密码写进任何被 git 跟踪的文件。**
- **绝不手工 INSERT/UPDATE 任何 `alembic_version*` 表**；迁移只能通过 `alembic upgrade/downgrade` 推进。
- **所有触发写操作的按钮必须 `disabled={mutation.isPending}`**（本项目已统一：双击 approve 能静默多批一级）。
- **不得改动 recurring / milestone / PO 路线的任何既有行为。**
- 新增 ORM 模型必须注册进 `epms-api/app/models/__init__.py`——测试库的表来自 `Base.metadata.create_all`，不走 alembic。
- **不为了让测试变绿而削弱断言。**

## 测试与门禁命令（每个任务都要用，账本里的旧姿势是错的）

**epms-api pytest**（宿主跑；容器里没有 pytest；仓库根 `.env` 指**生产库**而 conftest 会 `drop_all`）：
```bash
cd /c/Project/uniops-agreement/epms-api
PGPW=$(docker exec uniops_postgres env | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)
export POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD="$PGPW" JWT_SECRET_KEY=test-secret
python -c "from tests.conftest import _TEST_DB_URL as u; assert 'localhost' in u and u.endswith('/epms_test'), u"
python -m pytest <files> -q
```
坑：设置名是 `POSTGRES_HOST` **不是** `POSTGRES_SERVER`（后者被静默忽略）；用户是 `epms` **不是** `postgres`；`POSTGRES_DB` 完全无效（`conftest.py:229-230` 硬编码 `/epms_test`），所以断言要断在 `_TEST_DB_URL` 上。**同一时刻只能跑一个 epms 套件。**

**identity-api pytest**（变量名与 epms 完全不同）：
```bash
cd /c/Project/uniops-agreement/identity-api
PGPW=$(docker exec uniops_postgres env | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)
export TEST_PG_HOST=localhost TEST_PG_PORT=5432 TEST_PG_USER=epms TEST_PG_PASSWORD="$PGPW" TEST_IDENTITY_DB=identity_test
python -m pytest tests/test_phase2_keys.py -q
```

**前端类型门禁**（容器已挂本 worktree）：
```bash
docker exec uniops_epms_frontend sh -c 'cd /app && npx tsc --version && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -E "error TS" | wc -l'
```
必须 `Version 5.9.3`，错误数 **= 58**（基线）。版本不对或数字异常暴涨/归零 → **停下来报告**（本项目踩过两次 tsc 假绿）。

**已知既有失败**：`tests/test_invoices.py` 单独跑时 7 failed（`approval_dept_routing` 表不存在 + approval-api 401），环境原因。epms-api 全量基线 = **69 failed / 782 passed**。

---

## 命名映射表（Task 1-5 全部照此，一处不许漏）

| 旧 | 新 |
|---|---|
| 表 `agreement_pickup_slips` | `agreement_receipts` |
| 表 `agreement_slip_attachments` | `agreement_receipt_attachments` |
| 列 `slip_date` | `receipt_date` |
| 列 `slip_ref` | `receipt_ref` |
| 列 `picked_by` | `received_by` |
| 列 `missing_slip_reason` | `missing_receipt_reason` |
| 列 `invoices.slip_ids` | `invoices.receipt_ids` |
| 列 `invoices.slip_variance_reason` | `invoices.receipt_variance_reason` |
| 索引 `uq_agr_slip_ref_per_agreement` | `uq_agr_receipt_ref_per_agreement` |
| 模型 `AgreementPickupSlip` | `AgreementReceipt` |
| 模型 `AgreementSlipAttachment` | `AgreementReceiptAttachment` |
| 模块 `app/models/agreement_slip.py` | `app/models/agreement_receipt.py` |
| 模块 `app/models/agreement_slip_attachment.py` | `app/models/agreement_receipt_attachment.py` |
| 模块 `app/schemas/agreement_slip.py` | `app/schemas/agreement_receipt.py` |
| 模块 `app/crud/agreement_slip.py` | `app/crud/agreement_receipt.py` |
| 模块 `app/api/v1/agreement_slips.py` | `app/api/v1/agreement_receipts.py` |
| 模块 `app/api/v1/agreement_slip_attachments.py` | `app/api/v1/agreement_receipt_attachments.py` |
| Schema `SlipCreate/SlipUpdate/SlipResponse/SlipListResponse/SlipApReview` | `ReceiptCreate/ReceiptUpdate/ReceiptResponse/ReceiptListResponse/ReceiptApReview` |
| 路由前缀 `/agreements/{id}/slips` | `/agreements/{id}/receipts` |
| 路由 `/invoices/{iid}/agreements/{aid}/slips` | `/invoices/{iid}/agreements/{aid}/receipts` |
| 权限键 `epms.agreement.slip.write` | `epms.agreement.receipt.write` |
| 前端 `services/agreementSlips.ts` | `services/agreementReceipts.ts` |
| 前端 `services/agreementSlipAttachments.ts` | `services/agreementReceiptAttachments.ts` |
| 前端 `hooks/useAgreementSlips.ts` | `hooks/useAgreementReceipts.ts` |
| 前端类型 `ApiSlip/SlipStatus` | `ApiReceipt/ReceiptStatus` |
| 常量 `SLIP_AGING_DAYS` | `RECEIPT_AGING_DAYS` |
| 常量 `RETIRED_SLIP_STATUSES` | `RETIRED_RECEIPT_STATUSES` |
| 测试文件 `test_pickup_slip_*.py` / `test_slip_*.py` | `test_agreement_receipt_*.py` / `test_receipt_*.py` |

**新增列**（唯一的结构性新增）：

```
receipt_type   String(20)  NOT NULL  server_default='counter_slip'   index=True
               取值 counter_slip | delivery | service
```

---

## 前置：dev 库回退（★ 由控制方在派发 Task 1 之前完成，实现者不要做）

Task 1 会**改写** `ag04` 并**删除** `ag05`。dev 库当前停在 `ag05_slip_ref_uq_active`、identity 停在 `0007_slip_write_perm`，**必须先用旧文件回退，再改写文件**——顺序反了就再也降不回去。

```bash
cd /c/Project/uniops
docker compose -f docker-compose.dev.yml -f docker-compose.agreement-test.yml \
  run --rm --no-deps epms-api alembic downgrade ag03_agreement_schedule
docker compose -f docker-compose.dev.yml \
  run --rm --no-deps -v C:/Project/uniops-agreement/identity-api:/app \
  identity-api alembic downgrade 0006_agreement_perms
```

**为什么改写而不是叠加**：`ag04`/`ag05`/identity `0007` **都没上过生产**。叠 `ag06`+`0008` 会让生产按"建小票表 → 修索引 → 改名成凭证表"三步走完一件本可以一步做完的事，且永久留在迁移史里。改写后生产只看到一条建 `agreement_receipts` 的迁移。

---

## Task 1: 迁移与 ORM 模型改名 + `receipt_type`

**Files:**
- Rewrite: `epms-api/alembic/versions/ag04_pickup_slips.py` → 重命名文件为 `ag04_agreement_receipts.py`（revision id 也改成 `ag04_agreement_receipts`）
- Delete: `epms-api/alembic/versions/ag05_slip_ref_uq_active.py`
- Rename: `epms-api/app/models/agreement_slip.py` → `agreement_receipt.py`
- Rename: `epms-api/app/models/agreement_slip_attachment.py` → `agreement_receipt_attachment.py`
- Modify: `epms-api/app/models/__init__.py`, `epms-api/app/models/invoice.py`
- Test: `epms-api/tests/test_agreement_receipt_model.py`（由 `test_pickup_slip_model.py` 改名而来）

**Interfaces:**
- Produces：`AgreementReceipt`（`app/models/agreement_receipt.py`）、`AgreementReceiptAttachment`、`Invoice.receipt_ids` / `Invoice.receipt_variance_reason`、迁移 revision `ag04_agreement_receipts`（`down_revision = "ag03_agreement_schedule"`）

- [ ] **Step 1: 改写迁移**

把 `ag04` 重写成直接建最终形态。**`ag05` 那条收窄后的索引谓词直接写进来**（它是修复"作废后小票号被永久占用"的必需品，不能丢）：

```python
"""Agreement receipts — the evidence a house-account invoice reconciles against.

Supersedes the pickup-slip shape this branch originally shipped. Receipts are
typed (counter_slip | delivery | service) because a house account is not
necessarily a counter-pickup account: it may receive deliveries or services.
Hardcoding "pickup slip" would have made this feature usable by exactly one
vendor.

Deliberately NOT reusing goods_receipts: its po_id is NOT NULL, its line items
point at po_line_id, and its acknowledgement flow assumes a PR requester.
Agreements have no PO, so widening that table would open a hole in a document
that is already live in production.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "ag04_agreement_receipts"
down_revision = "ag03_agreement_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agreement_receipts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("agreement_id", UUID(as_uuid=True),
                  sa.ForeignKey("purchase_agreements.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # counter_slip | delivery | service
        sa.Column("receipt_type", sa.String(20), nullable=False,
                  server_default="counter_slip", index=True),
        sa.Column("receipt_date", sa.Date, nullable=False),
        sa.Column("receipt_ref", sa.String(64), nullable=True, index=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("received_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("missing_receipt_reason", sa.Text, nullable=True),
        sa.Column("ap_reviewed_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("ap_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        # pending_ap_review | open | reconciled | voided | rejected
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="open", index=True),
        sa.Column("invoice_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    # Retired rows must NOT hold their reference hostage: voiding a mis-keyed
    # receipt is the normal "I typed it wrong" path, and re-recording the same
    # paper document has to be accepted. voided/rejected are absolute terminal
    # states (crud refuses to edit or void them), so without this predicate the
    # reference would be burned for the life of the agreement.
    op.create_index(
        "uq_agr_receipt_ref_per_agreement", "agreement_receipts",
        ["agreement_id", "receipt_ref"], unique=True,
        postgresql_where=sa.text(
            "receipt_ref IS NOT NULL AND status NOT IN ('voided', 'rejected')"),
    )
    op.create_table(
        "agreement_receipt_attachments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("receipt_id", UUID(as_uuid=True),
                  sa.ForeignKey("agreement_receipts.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("storage_key", UUID(as_uuid=True), nullable=True),
        sa.Column("file_data", sa.LargeBinary, nullable=True),
        sa.Column("uploaded_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.add_column("invoices", sa.Column("receipt_ids", JSONB, nullable=True))
    op.add_column("invoices", sa.Column("receipt_variance_reason", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "receipt_variance_reason")
    op.drop_column("invoices", "receipt_ids")
    op.drop_table("agreement_receipt_attachments")
    op.drop_index("uq_agr_receipt_ref_per_agreement", table_name="agreement_receipts")
    op.drop_table("agreement_receipts")
```

⚠️ **先读一遍现有的 `ag04_pickup_slips.py` 与 `ag05_slip_ref_uq_active.py`**，把上面漏掉的任何列/约束补齐（例如附件表的实际列名可能与此不同）。以现有文件为准，上面这段是形状不是圣经。

- [ ] **Step 2: 删除 ag05，确认链尾**

```bash
git rm epms-api/alembic/versions/ag05_slip_ref_uq_active.py
git mv epms-api/alembic/versions/ag04_pickup_slips.py epms-api/alembic/versions/ag04_agreement_receipts.py
cd /c/Project/uniops && docker compose -f docker-compose.dev.yml -f docker-compose.agreement-test.yml \
  run --rm --no-deps epms-api alembic heads
```
Expected: 单一 head `ag04_agreement_receipts`。若出现多个 head 或报 `Can't locate revision`，**停下来报告**。

- [ ] **Step 3: 改名 ORM 模型**

`git mv` 两个模型文件，然后按命名映射表逐项改。`AgreementReceipt` 加 `receipt_type` 列：

```python
    # counter_slip | delivery | service —— 判别列。一份 house account 未必是柜台
    # 领用账户,它可能是按月送货或外包服务;把凭证写死成"小票"会让整个特性只能
    # 服务一个供应商。三种类型共用同一组列,差异只体现在 UI 文案。
    receipt_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="counter_slip", index=True)
```

`__table_args__` 的部分唯一索引**必须与迁移逐字一致**：
```python
    __table_args__ = (
        sa.Index("uq_agr_receipt_ref_per_agreement", "agreement_id", "receipt_ref",
                 unique=True,
                 postgresql_where=sa.text(
                     "receipt_ref IS NOT NULL AND status NOT IN ('voided', 'rejected')")),
    )
```
⚠️ 测试库的表来自 `Base.metadata.create_all` **不走迁移**——模型没改的话，索引相关的测试根本验证不到任何东西（本分支已踩过一次，当时写出了一个不可能失败的测试）。

`app/models/invoice.py` 的两列改名；`app/models/__init__.py` 的导入与 `__all__` 同步。

- [ ] **Step 4: 改名并扩充模型测试**

`git mv epms-api/tests/test_pickup_slip_model.py epms-api/tests/test_agreement_receipt_model.py`，按映射表改，并**新增**：

```python
async def test_receipt_type_defaults_to_counter_slip(db_session, seeded_agreement, seeded_user):
    """不传 receipt_type 时落 counter_slip —— 存量语义（柜台小票）就是默认。"""
    r = AgreementReceipt(
        agreement_id=seeded_agreement.id, receipt_date=date(2026, 8, 1),
        amount=Decimal("10.00"), tax_amount=Decimal("1.30"),
        total_amount=Decimal("11.30"), received_by=seeded_user.id,
        created_by=seeded_user.id,
    )
    db_session.add(r)
    await db_session.flush()
    await db_session.refresh(r)
    assert r.receipt_type == "counter_slip"


async def test_all_three_receipt_types_persist(db_session, seeded_agreement, seeded_user):
    """delivery / service 与 counter_slip 是并列的一等类型,不是特例。"""
    for t in ("counter_slip", "delivery", "service"):
        r = AgreementReceipt(
            agreement_id=seeded_agreement.id, receipt_type=t,
            receipt_date=date(2026, 8, 1), amount=Decimal("10.00"),
            tax_amount=Decimal("0.00"), total_amount=Decimal("10.00"),
            received_by=seeded_user.id, created_by=seeded_user.id,
        )
        db_session.add(r)
    await db_session.flush()
    rows = (await db_session.execute(
        select(AgreementReceipt.receipt_type).where(
            AgreementReceipt.agreement_id == seeded_agreement.id)
    )).scalars().all()
    assert sorted(rows) == ["counter_slip", "delivery", "service"]
```

⚠️ **`seeded_agreement` / `seeded_user` 是占位名**——去现有 `test_pickup_slip_model.py` 里看它实际用的 fixture 与建行辅助函数，照抄那套，不要自造。

- [ ] **Step 5: 跑测试 + 应用迁移**

```bash
python -m pytest tests/test_agreement_receipt_model.py -q
cd /c/Project/uniops && docker compose -f docker-compose.dev.yml -f docker-compose.agreement-test.yml \
  run --rm --no-deps epms-api alembic upgrade head
```
Expected: 测试全绿；迁移输出 `Running upgrade ag03_agreement_schedule -> ag04_agreement_receipts`。

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor(agreement): generalise pickup slips into typed agreement receipts"
```

---

## Task 2: schemas 与 crud 改名

**Files:**
- Rename: `epms-api/app/schemas/agreement_slip.py` → `agreement_receipt.py`
- Rename: `epms-api/app/crud/agreement_slip.py` → `agreement_receipt.py`
- Test: `epms-api/tests/test_agreement_receipt_api.py`（由 `test_pickup_slip_api.py` 改名）

**Interfaces:**
- Consumes: Task 1 的 `AgreementReceipt`
- Produces：`ReceiptCreate`（含 `receipt_type: str = "counter_slip"`）、`ReceiptUpdate`、`ReceiptResponse`（含 `receipt_type`）、`ReceiptListResponse`、`ReceiptApReview`、`validate_totals`；crud 的 `create / list_for_agreement / update / void / ap_review / claim`、常量 `EDITABLE` / `VOIDABLE`

- [ ] **Step 1: 改名 schemas，加 `receipt_type`**

`ReceiptCreate` 加：
```python
    # counter_slip | delivery | service。默认 counter_slip —— 绝大多数 house
    # account 仍是柜台领用,让最常见的情形免于每次都选。
    receipt_type: str = "counter_slip"

    @field_validator("receipt_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in ("counter_slip", "delivery", "service"):
            raise ValueError(
                "receipt_type must be one of: counter_slip, delivery, service")
        return v
```
`ReceiptUpdate` 加 `receipt_type: str | None = None`（同样校验，`None` 放行）；`ReceiptResponse` 加 `receipt_type: str`。

其余按映射表改名，**注释里的解释性文字一并更新**（例如 `SlipCreate` 那段讲两遍校验的注释，把"小票"改成"凭证"）。

- [ ] **Step 2: 改名 crud**

按映射表逐项改。`claim()` 的签名变成：
```python
async def claim(db, agr, receipt_ids: list[uuid.UUID], invoice) -> list[AgreementReceipt]
```
⚠️ **`update()` 里那条状态路由规则必须保留**：`missing_receipt_reason` 由空变非空且当前 `status == "open"` 时转 `pending_ap_review`；反向（理由清空）**不**自动改状态。这条是与 `create()` 对齐的规则，不是给某条路径开的后门。

- [ ] **Step 3: 改名并跑测试**

```bash
git mv epms-api/tests/test_pickup_slip_api.py epms-api/tests/test_agreement_receipt_api.py
git mv epms-api/tests/test_pickup_slip_attachments.py epms-api/tests/test_agreement_receipt_attachments.py
```
按映射表改测试内容，**新增一条**类型校验用例：

```python
async def test_create_rejects_an_unknown_receipt_type(admin_client, agreement_id):
    res = await admin_client.post(
        f"{AGR_URL}/{agreement_id}/receipts",
        json={**_receipt_payload(), "receipt_type": "carrier_pigeon"},
    )
    assert res.status_code == 422
    assert "counter_slip" in res.text
```

Run: `python -m pytest tests/test_agreement_receipt_api.py tests/test_agreement_receipt_attachments.py -q`

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "refactor(agreement): rename slip schemas and crud to receipts"
```

---

## Task 3: 路由与发票/PA 集成改名

**Files:**
- Rename: `epms-api/app/api/v1/agreement_slips.py` → `agreement_receipts.py`
- Rename: `epms-api/app/api/v1/agreement_slip_attachments.py` → `agreement_receipt_attachments.py`
- Modify: `epms-api/app/api/v1/__init__.py`, `epms-api/app/api/v1/invoices.py`, `epms-api/app/api/v1/pa.py`, `epms-api/app/crud/invoice.py`, `epms-api/app/schemas/invoice.py`, `epms-api/app/admin/registry.py`, `epms-api/app/crud/task.py`
- Test: `epms-api/tests/test_receipt_match.py`, `test_receipt_pa_gate.py`, `test_receipt_release.py`（三个改名）

**Interfaces:**
- Consumes: Task 2 的 crud 与 schemas
- Produces：路由 `/agreements/{agreement_id}/receipts*`、`/invoices/{invoice_id}/agreements/{agreement_id}/receipts`；`InvoiceMatchRequest.receipt_ids` / `.receipt_variance_reason`；`InvoiceResponse.receipt_ids` / `.receipt_variance_reason`；`_release_agreement_evidence`

- [ ] **Step 1: 改名两个路由模块**

按映射表改前缀、依赖名（`SlipRecordDep` → `ReceiptRecordDep`、`SlipReadDep` → `ReceiptReadDep`）、常量 `RETIRED_SLIP_STATUSES` → `RETIRED_RECEIPT_STATUSES`、以及 409 冲突文案里的措辞（"pickup slip" → "receipt"）。`app/api/v1/__init__.py` 的 include_router 同步。

⚠️ **权限键这一步先不动**（仍是 `epms.agreement.slip.write`），Task 4 统一改——否则 Task 3 的测试会因为找不到键而红。

- [ ] **Step 2: 改名发票/PA 侧的集成点**

- `crud/invoice.py`：`_release_agreement_evidence` 内的字段与查询、`_match_to_agreement` 的 house_account 分支、`delete()` 里的释放调用
- `schemas/invoice.py`：`InvoiceMatchRequest` 与 `InvoiceResponse` 的两个字段
- `api/v1/invoices.py`：invoice-scoped 凭证列表端点、`review_match` 与 `assign_match` 的文案（"pickup slip" → "receipt"）
- `api/v1/pa.py`：闸门判据 `not (r.receipt_ids or r.legacy_settlement)` 与 422 文案
- `admin/registry.py`、`crud/task.py`：各一处引用

- [ ] **Step 3: 改名并跑测试**

```bash
git mv epms-api/tests/test_slip_match.py epms-api/tests/test_receipt_match.py
git mv epms-api/tests/test_slip_pa_gate.py epms-api/tests/test_receipt_pa_gate.py
git mv epms-api/tests/test_slip_release.py epms-api/tests/test_receipt_release.py
```

Run:
```bash
python -m pytest tests/test_receipt_match.py tests/test_receipt_pa_gate.py \
  tests/test_receipt_release.py tests/test_agreement_receipt_api.py \
  tests/test_agreement_receipt_attachments.py tests/test_agreements.py -q
```
Expected: 全绿。

- [ ] **Step 4: 全仓搜残留**

```bash
cd /c/Project/uniops-agreement
grep -rn "slip" --include=*.py epms-api/app | grep -vi "receipt" | head -20
```
Expected: **零行**。有残留就是漏改（迁移文件里的历史注释除外，那些属于 ag01-ag03，不该动）。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "refactor(agreement): rename slip routes and invoice/PA integration to receipts"
```

---

## Task 4: identity 权限键改名

**Files:**
- Rewrite: `identity-api/alembic/versions/0007_slip_write_perm.py` → 文件与 revision 改名为 `0007_receipt_write_perm`
- Modify: `identity-api/scripts/seed_phase2_keys.py`
- Modify: `epms-api/app/api/v1/agreement_receipts.py`, `epms-api/app/api/v1/agreement_receipt_attachments.py`
- Modify: `epms/src/pages/agreements/AgreementDetailPage.tsx`（前端门禁键名）

**Interfaces:**
- Produces：权限键 `epms.agreement.receipt.write`，label `Record Agreement Receipts (needs View Agreements)`，sort 106，种子角色 `system_admin` / `ap_clerk` / `dept_admin`

- [ ] **Step 1: 改写 0007**

⚠️ **控制方已在派发前把 dev 的 identity 回退到 `0006_agreement_perms`**，所以直接改写文件即可，不要新开 0008。

- `revision = "0007_receipt_write_perm"`（19 字符，`alembic_version_identity.version_num` 是 varchar(32)）
- `down_revision = "0006_agreement_perms"`
- `_KEYS`：`"epms.agreement.receipt.write": ("epms", "Record Agreement Receipts (needs View Agreements)", 106)`
- `_GRANTS`：`"epms.agreement.receipt.write": ("system_admin", "ap_clerk", "dept_admin")` **以及** `"epms.agreement.read": ("dept_admin", "cfo", "erp_pa_officer")`
- `downgrade()` 里 `epms.agreement.read` 的删除**只能删这三个角色的授权行**，不许删整个键或 0006 种的其它角色

⚠️ 只能出现 `role_defs` 里真实存在的角色码（`role_permissions.role_code` 是 FK，写错会让迁移在**生产** ForeignKeyViolation 中止）：`ap_clerk auditor cfo dept_admin dept_manager director erp_pa_officer finance_bp finance_manager gm opm procurement_manager procurement_officer requester supervisor system_admin vendor_manager warehouse_staff`

- [ ] **Step 2: 同步 seed 脚本**

`seed_phase2_keys.py` 的 `_KEYS` / `_GRANTS` 必须与迁移**逐字一致**（键名、label、sort、角色集合、顺序）。

- [ ] **Step 3: 改后端与前端的键名引用**

`ReceiptRecordDep = Annotated[dict, Depends(require_permission("epms.agreement.receipt.write"))]`；前端 `AgreementDetailPage.tsx` 的 `canRecordSlip` → `canRecordReceipt`，读 `perms?.['epms.agreement.receipt.write']`。

- [ ] **Step 4: 跑测试并应用迁移**

```bash
cd /c/Project/uniops-agreement/identity-api
# env 见顶部"identity-api pytest"
python -m pytest tests/test_phase2_keys.py -q

cd /c/Project/uniops-agreement/epms-api
python -m pytest tests/test_agreement_receipt_api.py -q

cd /c/Project/uniops
docker compose -f docker-compose.dev.yml run --rm --no-deps \
  -v C:/Project/uniops-agreement/identity-api:/app identity-api alembic upgrade head
```
Expected: identity 4 passed；epms 凭证 API 测试全绿（其中那条非 admin 的 403→201 用例现在打的是新键）；迁移输出 `0006_agreement_perms -> 0007_receipt_write_perm`。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "refactor(agreement): rename the slip-write permission key to receipt-write"
```

---

## Task 5: 前端服务层与 hooks 改名

**Files:**
- Rename: `epms/src/services/agreementSlips.ts` → `agreementReceipts.ts`
- Rename: `epms/src/services/agreementSlipAttachments.ts` → `agreementReceiptAttachments.ts`
- Rename: `epms/src/hooks/useAgreementSlips.ts` → `useAgreementReceipts.ts`
- Modify: `epms/src/services/invoices.ts`, `epms/src/hooks/useInvoices.ts`, `epms/src/hooks/useChainAttachments.ts`, `epms/src/types/index.ts`, `epms/src/components/ui/badge.tsx`, `epms/src/pages/pa/PaCreatePage.tsx`, `epms/src/components/shared/ChainAttachmentsPanel.tsx`
- Rename: `epms/src/components/agreements/SlipEntryForm.tsx` → `ReceiptEntryForm.tsx`
- Rename: `epms/src/components/agreements/SlipTable.tsx` → `ReceiptTable.tsx`
- Modify: `epms/src/pages/agreements/AgreementDetailPage.tsx`, `epms/src/pages/invoices/MatchPanel.tsx`, `epms/src/pages/invoices/InvoiceDetailPage.tsx`

**Interfaces:**
- Produces：`ApiReceipt` / `ReceiptStatus` / `ReceiptType`、`agreementReceiptService`、`agreementReceiptAttachmentService`、`ocrService.receipt`（沿用 expense-api 的 `slip` OCR 模式，**接口名改、后端模式名不改**）、`useAgreementReceipts` / `useCreateReceipt` / `useVoidReceipt` / `useApReviewReceipt` / `useInvoiceAgreementReceipts`、`RECEIPT_AGING_DAYS`

- [ ] **Step 1: 改名并加类型**

`agreementReceipts.ts` 加：
```ts
export type ReceiptType = 'counter_slip' | 'delivery' | 'service'

export const RECEIPT_TYPE_LABELS: Record<ReceiptType, string> = {
  counter_slip: 'Counter slip',
  delivery:     'Delivery note',
  service:      'Service sign-off',
}
```
`ApiReceipt` 加 `receipt_type: ReceiptType`；`CreateReceiptBody` 加 `receipt_type?: ReceiptType`。

⚠️ 金额三个字段仍是 **`string`**（Pydantic Decimal → JSON 字符串），不要"顺手"改成 `number`。

- [ ] **Step 2: 按映射表改其余文件**

`ReceiptEntryForm` / `ReceiptTable` 本任务只做**机械改名**（含 UI 文案 "Pickup Slip" → "Receipt"），行为不动——它们的搬迁在 Task 9/10。

- [ ] **Step 3: 类型门禁**

```bash
docker exec uniops_epms_frontend sh -c 'cd /app && npx tsc --version && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -E "error TS" | wc -l'
```
Expected: `Version 5.9.3`，**58**。

- [ ] **Step 4: 全仓搜残留**

```bash
grep -rn "[Ss]lip" epms/src --include=*.ts --include=*.tsx | grep -v "counter_slip" | grep -vi "receipt" | head -20
```
Expected: 零行（`counter_slip` 是合法的类型字面量，排除掉）。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "refactor(agreement/ui): rename slip services, hooks and components to receipts"
```

---

## Task 6: 拆除匹配处的凭证门禁

**Files:**
- Modify: `epms-api/app/crud/invoice.py`（`_match_to_agreement` 的 house_account 分支）
- Modify: `epms-api/app/schemas/invoice.py`（`InvoiceMatchRequest`）
- Modify: `epms/src/pages/invoices/MatchPanel.tsx`
- Test: `epms-api/tests/test_receipt_match.py`

**Interfaces:**
- Produces：`_match_to_agreement` 对 house_account **只做关联**；`InvoiceMatchRequest` 不再有 `receipt_ids` / `receipt_variance_reason` / `legacy_settlement_reason`

- [ ] **Step 1: 写失败测试**

```python
async def test_house_account_match_needs_no_evidence_and_no_reason(admin_client, ...):
    """匹配就是关联。1A 把这一步做成了"必须交代凭证",而当时系统里根本没有
    任何地方能提供凭证 —— 用户的原始抱怨就是这个。现在它与 recurring /
    milestone 一样:选中协议、提交、结束。"""
    res = await admin_client.post(f"/api/v1/invoices/{inv_id}/match",
                                  json={"agreement_id": str(agr_id)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["agreement_id"] == str(agr_id)
    assert body["legacy_settlement"] is False
    assert body["legacy_settlement_reason"] is None
    assert body["receipt_ids"] is None
    assert body["receipt_variance_reason"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_receipt_match.py -q`
Expected: FAIL —— 当前实现会 422 要求理由。

- [ ] **Step 3: 拆除后端门禁**

`_match_to_agreement` 里 house_account 分支整段替换为：

```python
    if agr.agreement_type == "house_account":
        # 匹配 = 只做关联。凭证挂载是发票详情页上的另一件事,无凭证结算的声明
        # 也在那里 —— 两者都不该卡住"这张票属于这份协议"这个独立事实。
        # 唯一的硬约束在 PA 闸门(api/v1/pa.py):起付款时才要求要么有凭证、
        # 要么有显式声明。这里既不设 legacy_settlement 也不清它 —— 一张
        # 已经声明过无凭证的发票改挂到另一份协议时,那个声明依然成立。
        pass
    else:
        invoice.legacy_settlement = False
        invoice.legacy_settlement_reason = None
        ...  # recurring / milestone 既有逻辑一行不动
```

`InvoiceMatchRequest` 删掉三个字段（`receipt_ids` / `receipt_variance_reason` / `legacy_settlement_reason`）与相关注释。

⚠️ 函数开头那句 `if invoice.receipt_ids or invoice.schedule_id: await _release_agreement_evidence(...)` **必须保留**——改挂协议时旧凭证仍要释放。

- [ ] **Step 4: 拆除 MatchPanel 的凭证区块**

删掉：`ReceiptCandidateRow` 组件、`selectedReceiptIds` / `receiptVarianceReason` / `receiptRefInput` / `receiptRefCommitted` / `legacyReason` 等 state、`useInvoiceAgreementReceipts` 查询与两条加速路径的 effect、`autoSelectedReceiptIdRef` / `receiptPreselectAppliedRef`、以及所有相关 JSX（候选列表、错误横幅、合计/差额块、无凭证理由框）。

house_account 的 `canSubmitAgreement` 变成与 recurring 相同：选中协议即可提交。提交 payload 只留 `{ agreement_id }`。

⚠️ **`centsEqual` 若在 MatchPanel 里已无其它调用方就一并删除**；`ReceiptCandidateRow` 里那套渲染在 Task 8 会以新形态重建，不要"留着以后用"。

- [ ] **Step 5: 跑测试与类型门禁**

```bash
python -m pytest tests/test_receipt_match.py tests/test_receipt_pa_gate.py tests/test_receipt_release.py -q
docker exec uniops_epms_frontend sh -c 'cd /app && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -cE "error TS"'
```
Expected: 后端全绿（**预期有既有用例失败**：断言"house_account 匹配必须填理由"的那些现在应当改成断言"不再要求"，改测试并在提交信息里说明；断言"选了凭证就转 reconciled"的用例移到 Task 7）；tsc = 58。

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(agreement): matching an invoice to an agreement no longer asks about evidence"
```

---

## Task 7: 发票↔凭证的挂载端点

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py`
- Modify: `epms-api/app/crud/invoice.py`
- Modify: `epms-api/app/schemas/invoice.py`
- Test: `epms-api/tests/test_invoice_receipts.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `agreement_receipt_crud.claim`
- Produces：
  - `PUT /api/v1/invoices/{invoice_id}/receipts`，body `InvoiceReceiptsRequest { receipt_ids: list[UUID], variance_reason: str | None }` → `InvoiceResponse`
  - `POST /api/v1/invoices/{invoice_id}/settle-without-receipt`，body `SettleWithoutReceiptRequest { reason: str }` → `InvoiceResponse`

- [ ] **Step 1: 写失败测试**

```python
async def test_put_receipts_claims_them_and_clears_legacy(...):
    """挂上凭证 → 每份转 reconciled 并记 invoice_id、发票落 receipt_ids、
    legacy_settlement 被清掉(挂了凭证就不再是无凭证结算)。"""

async def test_put_receipts_with_fewer_ids_releases_the_dropped_ones(...):
    """★ 全量覆盖语义:先挂 A+B,再 PUT 只有 A → B 必须回到 open 且 invoice_id
    为空。这是本项目反复被咬的释放不变式,少了它 B 会永久卡在 reconciled,
    而 update()/void() 都拒绝该状态,没有任何界面能救它。"""

async def test_put_empty_list_releases_everything(...):

async def test_put_rejects_a_receipt_from_another_agreement(...):

async def test_put_rejects_a_receipt_that_is_not_open(...):
    """pending_ap_review / rejected / voided 都不可挂;已被本发票认领的
    reconciled 凭证除外 —— 重挂时必须能把自己已持有的那几份再选上。"""

async def test_variance_reason_is_stored_and_does_not_block(...):
    """差额非零照样 200 —— 柜台采购与发票金额对不上是常态(运费/折扣/税差),
    拦死只会把人推回无凭证通道。"""

async def test_settle_without_receipt_sets_the_flag_and_reason(...):

async def test_settle_without_receipt_requires_a_reason(...):
    """空白理由 → 422。"""

async def test_settle_without_receipt_releases_any_held_receipts(...):
    """声明"无凭证"时若发票还挂着凭证,那些凭证必须释放 —— 否则它们会
    永久卡在 reconciled 并支撑一张自称无凭证的发票。"""
```

⚠️ 上面全部是**签名与意图**，实际的 fixture / 建行辅助函数照抄 `tests/test_receipt_match.py` 里现成的那套。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_invoice_receipts.py -q`
Expected: FAIL（404，端点不存在）。

- [ ] **Step 3: 实现**

`schemas/invoice.py`：
```python
class InvoiceReceiptsRequest(BaseModel):
    # 全量覆盖语义:这个列表就是这张发票最终持有的凭证集合。没列出的会被释放。
    receipt_ids: list[uuid.UUID]
    variance_reason: str | None = None


class SettleWithoutReceiptRequest(BaseModel):
    reason: str = Field(min_length=1)
```

`crud/invoice.py` 新增：
```python
async def set_receipts(
    db: AsyncSession, invoice: Invoice, receipt_ids: list[uuid.UUID],
    variance_reason: str | None,
) -> Invoice:
    """全量覆盖一张发票持有的凭证集合。

    先无条件释放当前持有的全部凭证,再认领入参里的 —— 而不是做增量 diff。
    理由:diff 要同时维护"新增"和"移除"两条路径,而本项目在释放这件事上
    已经被咬过多次(每次都是某一条路径漏了释放)。释放-再认领只有一条路径,
    多余的写入换来一个不可能漏的不变式。
    """
    agr = (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == invoice.agreement_id)
    )).scalar_one_or_none()
    if agr is None:
        raise ValueError("Invoice is not matched to an agreement")

    await _release_agreement_evidence(db, invoice)

    if receipt_ids:
        claimed = await agreement_receipt_crud.claim(db, agr, receipt_ids, invoice)
        invoice.receipt_ids = [str(r.id) for r in claimed]
        invoice.receipt_variance_reason = (variance_reason or "").strip() or None
        # 挂上了凭证就不再是无凭证结算 —— 这两个状态互斥,协议详情页那个
        # 健康度计数依赖它们互斥才有意义。
        invoice.legacy_settlement = False
        invoice.legacy_settlement_reason = None
    await db.flush()
    return invoice


async def settle_without_receipt(
    db: AsyncSession, invoice: Invoice, reason: str,
) -> Invoice:
    """显式声明这张发票没有任何签收凭证。

    先释放它可能还持有的凭证:一张自称无凭证的发票不该继续锁着几份真凭证,
    那些凭证会永久卡在 reconciled 且没有任何界面能放它们回来。
    """
    await _release_agreement_evidence(db, invoice)
    invoice.legacy_settlement = True
    invoice.legacy_settlement_reason = reason.strip()
    await db.flush()
    return invoice
```

`api/v1/invoices.py` 两个端点，授权**复用 `_require_invoice_match_access`**（与 invoice-scoped 凭证列表同源，不要另抄一份判定——本项目吃过复制授权判定然后两边漂移的亏）。`ValueError` → 422。

⚠️ `agreement_receipt_crud.claim` 现在只接受 `status == "open"`。**要放宽成"open 或已被本发票认领的 reconciled"**，否则重挂时选不回自己已持有的那几份。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_invoice_receipts.py tests/test_receipt_release.py tests/test_receipt_pa_gate.py -q`

- [ ] **Step 5: 提交**

```bash
git add epms-api/app/api/v1/invoices.py epms-api/app/crud/invoice.py \
        epms-api/app/schemas/invoice.py epms-api/app/crud/agreement_receipt.py \
        epms-api/tests/test_invoice_receipts.py
git commit -m "feat(agreement): attach receipts to an invoice as its own action"
```

---

## Task 8: 发票详情页的凭证区块

**Files:**
- Create: `epms/src/components/invoices/InvoiceReceiptsPanel.tsx`
- Modify: `epms/src/pages/invoices/InvoiceDetailPage.tsx`
- Modify: `epms/src/services/invoices.ts`, `epms/src/hooks/useInvoices.ts`

**Interfaces:**
- Consumes: Task 5 的 `useInvoiceAgreementReceipts` / `ApiReceipt` / `RECEIPT_TYPE_LABELS`；Task 7 的两个端点
- Produces：`invoiceService.setReceipts(invoiceId, body)`、`invoiceService.settleWithoutReceipt(invoiceId, reason)`、`useSetInvoiceReceipts` / `useSettleWithoutReceipt`

- [ ] **Step 1: 服务层与 hooks**

```ts
export interface SetReceiptsBody { receipt_ids: string[]; variance_reason?: string | null }
```
两个 mutation 的 `onSuccess` 必须 **`await`** 失效（TanStack Query v5 的 `invalidateQueries` 只排一次后台重取，不 await 会用旧数据渲染一帧）：
```ts
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['invoices', invoiceId] }),
        queryClient.invalidateQueries({ queryKey: ['invoices', invoiceId, 'agreements'] }),
      ])
    },
```

- [ ] **Step 2: 组件**

只在 `invoice.agreement_id` 非空且协议类型为 `house_account` 时渲染。内容：

- 候选凭证多选列表（该协议下 `open` 的，加上本发票已持有的），每行显示 `RECEIPT_TYPE_LABELS[r.receipt_type]` 徽章、`receipt_ref ?? '—'`、日期、`Number(r.total_amount)`
- 实时"已选合计 / 发票金额 / 差额"，全部 `Number()`；差额判零用分级整数 `Math.round(x*100)`
- 差额 ≠ 0 时显示 variance reason 输入框，标 `(recommended)`，**不阻断提交**
- 两条加速路径**从 MatchPanel 平移过来**（Task 6 删掉的那套）：
  1. "Reference on the invoice" 输入框，**失焦或回车**才匹配（不是逐字符——中途值会误命中一个更短的编号）
  2. 金额相等且日期在发票日期前 14 天内、且**唯一命中**时预选；命中多张一张都不选
  加速器**至多持有一份**凭证，换建议时先摘旧的；且**只认领当前不在选中集合里的**那份——操作员手动勾的凭证永远不因加速器换建议而消失
- 拉取失败走**独立错误态 + Retry**，绝不渲染成"没有凭证"的空列表；失败时若显示"无凭证结算"入口，必须警示列表未加载
- 一份未挂时给 "Settle without receipt evidence" 动作 + 必填理由
- 所有按钮 `disabled={mutation.isPending}`

- [ ] **Step 3: 接进发票详情页**

放在协议区块之下。⚠️ **同一页有两处协议区块**（Linked Documents 与 3-Way Match），凭证区块只加一处（3-Way Match 那侧），但两处显示 `legacy_settlement_reason` / `receipt_variance_reason` 的文案都要跟着新语义走。

- [ ] **Step 4: 类型门禁**

Expected: `Version 5.9.3`，**58**。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat(agreement/ui): reconcile receipts against an invoice on its detail page"
```

---

## Task 9: 跨协议凭证列表（端点 + 列表页 + 侧边栏）

**Files:**
- Modify: `epms-api/app/api/v1/agreement_receipts.py`
- Create: `epms/src/pages/receipts/ReceiptListPage.tsx`
- Modify: `epms/src/services/agreementReceipts.ts`, `epms/src/hooks/useAgreementReceipts.ts`
- Modify: `epms/src/components/layout/Sidebar.tsx`, `epms/src/App.tsx`（路由）
- Test: `epms-api/tests/test_agreement_receipt_api.py`

**Interfaces:**
- Produces：`GET /api/v1/agreement-receipts`（query: `agreement_id` / `receipt_type` / `status` / `search` / `page` / `page_size`）→ `ReceiptListResponse`；`agreementReceiptService.listAll(filters)`；`useAllReceipts(filters)`

- [ ] **Step 1: 写失败测试**

```python
async def test_list_all_receipts_across_agreements(admin_client, ...):
    """两份不同协议各一份凭证 → 不带筛选时两份都在。"""

async def test_list_all_filters_by_receipt_type(admin_client, ...):

async def test_list_all_filters_by_agreement(admin_client, ...):

async def test_list_all_requires_agreement_read(non_admin_client, ...):
    """★ 用非 admin client 写 —— admin_client 是 system_admin 而 uniops_authz
    对该角色短路,用它写等于没测。"""
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现端点**

新建一个独立 router（前缀 `/agreement-receipts`，与 `/agreements/{id}/receipts` 并存），读权限 `epms.agreement.read`，分页 `page_size` 上限 200，排序 `receipt_date DESC`。

- [ ] **Step 4: 列表页 + 侧边栏**

`ReceiptListPage.tsx` **照抄 `epms/src/pages/gr/GrListPage.tsx` 的结构与样式**（筛选栏、表格、分页、空状态、`StatusBadge`）。列：日期 / 类型 / 参考号 / 协议 / 金额 / 收货人 / 状态 / 已挂发票。

侧边栏在 "Agreements" 之后插入（`Receipt` 图标要从 `lucide-react` 补进该文件的 import）：
```tsx
{ label: 'Agreement Receipts', href: '/receipts', icon: <Receipt className="h-4 w-4" />, permission: 'epms.agreement.read' },
```
⚠️ 门禁用 **`epms.agreement.read`**（能看协议就能看凭证），不是写权限——否则只读角色看不到入口。

- [ ] **Step 5: 跑测试与类型门禁**

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(agreement): cross-agreement receipt list with its own menu entry"
```

---

## Task 10: 凭证新建页 + 协议详情页改只读

**Files:**
- Create: `epms/src/pages/receipts/ReceiptCreatePage.tsx`
- Modify: `epms/src/components/agreements/ReceiptEntryForm.tsx`
- Modify: `epms/src/components/agreements/ReceiptTable.tsx`
- Modify: `epms/src/pages/agreements/AgreementDetailPage.tsx`, `epms/src/App.tsx`

**Interfaces:**
- Consumes: Task 5 的 `ReceiptEntryForm` / `ReceiptTable`、Task 9 的路由
- Produces：`/receipts/new` 页面；`ReceiptTable` 新增 `readOnly?: boolean`

- [ ] **Step 1: 新建页**

`ReceiptCreatePage.tsx` **照抄 `epms/src/pages/gr/GrCreatePage.tsx` 的页面骨架**（面包屑、卡片、提交/取消按钮位置）。内容顺序：

1. **协议选择器** —— 只列 `agreement_type === 'house_account'` 且 `is_admissible` 的协议（draft/cancelled/过期的不许录，否则那些凭证永远无法被认领却会计入老化告警）
2. **类型选择器** —— `counter_slip` / `delivery` / `service`，默认 `counter_slip`
3. 复用 `ReceiptEntryForm` 的其余部分（照片 → OCR 预填 → 全字段可编辑 → 提交建行 → 上传附件两步）

⚠️ `ReceiptEntryForm` 现在假定 `agreementId` 由父组件给定，把协议与类型提升为 props 即可，**不要重写表单**。

- [ ] **Step 2: 协议详情页改只读**

- 删掉 `ReceiptEntryForm` 的引用与 "Record a Pickup Slip" 区块
- `ReceiptTable` 传 `readOnly`，隐藏 Void 与 AP Approve/Reject（那些动作在列表页/详情页做）
- 区块标题旁加一个跳到 `/receipts/new?agreement_id=<id>` 的按钮
- ⚠️ 保留 `agreement_type === 'house_account'` 的渲染条件

- [ ] **Step 3: 类型门禁 + 手工可达性自查**

除 tsc 外，在报告里逐条说明：新建页调的每个服务函数的 method + URL 与后端路由**逐字对得上**（本分支已有三次"建对了但用户走不到"的记录，tsc 数字不是功能证据）。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "feat(agreement): receipts get their own create page; agreement detail goes read-only"
```

---

## Task 11: DocumentChainTree 的协议轴心

**Files:**
- Modify: `epms/src/components/shared/DocumentChainTree.tsx`
- Modify: `epms/src/pages/agreements/AgreementDetailPage.tsx`
- Modify: `epms/src/services/invoices.ts`（`InvoiceFilters` 加 `agreement_id`）

**Interfaces:**
- Consumes: Task 5 的 `ApiReceipt`
- Produces：`DocumentChainTreeProps.currentType` 增加 `'agr'`

- [ ] **Step 1: 加协议轴心分支**

`currentType: 'pr' | 'po' | 'pa' | 'agr'`。新增解析：

```ts
  // 协议轴心。PO 轴心那条对协议路线完全失效 —— 协议 PA 的 po_id 是 NULL,
  // 于是 poId 解析成空串、所有子查询被禁用,今天打开一张协议 PA 的详情页
  // 文档链是空的。这条分支同时修掉那个既有缺陷。
  const agreementId: string =
    currentType === 'agr' ? id :
    (currentPa?.agreement_id ?? '')
```

层级：
```
[Agreement card — 当前高亮]
     └─ Invoice row(s)          ← GET /invoices?agreement_id=
          ├─ Receipt row(s)     ← 按 invoice.receipt_ids 展开
          └─ PA row(s)          ← 现成的 usePas,按发票归组
```
与 PO 轴心排布一致：**PA 嵌在它所源自的发票之下**。

recurring / milestone 协议的发票没有凭证行，那一层自然为空，**发票与 PA 两层照常显示**——这个链对三种协议类型都有价值。

⚠️ `GET /invoices?agreement_id=` 后端与 crud **已支持**（`api/v1/invoices.py:204`、`crud/invoice.py:81`），前端 `InvoiceFilters` 要加 `agreement_id?: string`。

- [ ] **Step 2: 接进协议详情页**

照 `PoDetailPage.tsx:1003` 的写法：`<DocumentChainTree currentType="agr" id={agreement.id} />`，放在与 PO/PR 详情页相同的侧栏位置。

- [ ] **Step 3: 类型门禁**

Expected: `Version 5.9.3`，**58**。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "feat(agreement): document chain on the agreement detail page"
```

---

## 收尾

- [ ] **epms-api 全量**（确认没有别的会话在跑 pytest，**一次跑完不要分段**）
  Expected: 失败集合与基线**逐条相同**（基线 69 failed / 782 passed；本次新增用例会让 passed 上升）
- [ ] **expense-api 全量**：Expected 155 passed / 0 failed（本计划不改 expense-api）
- [ ] **identity-api** `tests/test_phase2_keys.py`：4 passed
- [ ] **前端类型门禁**：TS 5.9.3，58 = 基线
- [ ] **全仓搜残留**：`grep -rn "[Ss]lip" epms-api/app epms/src identity-api/alembic identity-api/scripts | grep -v counter_slip | grep -vi receipt` → 零行
- [ ] **改写发布清单** `docs/release-notes/2026-08-11-agreement-pickup-slips.md` → 新建 `2026-08-11-agreement-receipts.md`：迁移 `ag04_agreement_receipts`（epms-api）+ `0007_receipt_write_perm`（identity-api），**无部署顺序约束**，需重建 `epms-api` / `epms-web` / `expense-api` / `identity-api`，无新环境变量；权限键名与 label 变更要写清楚
- [ ] **把旧的 pickup-slip 规格与计划标记为已被取代**（在两份文档顶部加一行指向新规格）
