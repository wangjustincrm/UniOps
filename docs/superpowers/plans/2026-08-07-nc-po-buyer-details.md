# NC-Imported PO Buyer Details — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an ERP PA Officer (or admin) fill in the supplier-facing detail that NC-imported POs arrive without — supplier item IDs, sample requirements, Incoterms, delivery info and buyer notes — and get a regenerated PO PDF that carries it.

**Architecture:** Four new nullable columns (`purchase_orders.buyer_notes` / `.incoterms` / `.buyer_edited_at`, `po_line_items.sample`) that the NC mirror never writes, a deliberately narrow `PATCH /po/{id}/imported-details` endpoint gated by a new Access Control Matrix key, a guard in the NC writer so a hand-set tax rate survives re-sync, and a purpose-built frontend edit page that reuses the *existing* `regenerate-pdf` endpoint.

**Tech Stack:** FastAPI + SQLAlchemy 2 (async) + Alembic + pytest/httpx (epms-api, identity-api); React 18 + TanStack Query + Tailwind + Vite (epms frontend); ReportLab (PDF).

**Spec:** `docs/superpowers/specs/2026-08-07-nc-po-buyer-details-design.md`

## Global Constraints

- **Worktree:** all work happens in `C:/Project/uniops-nc-po-edit` on branch `feature/nc-po-buyer-details`. Never `cd` into `C:/Project/uniops` (detached HEAD) or any other worktree.
- **Do not push.** Commit locally per task. Pushing and merging require explicit approval from the user.
- **UI copy is English only.** Code comments may be Chinese; every user-facing string in the frontend must be English.
- **`.env` must be copied into the worktree before running anything:** `cp /c/Project/uniops/.env /c/Project/uniops-nc-po-edit/.env`. It is gitignored and absent in a fresh worktree.
- **The host `.env` points at the PRODUCTION database.** Never run pytest or alembic without the `POSTGRES_*` overrides shown in each Verification block. A bare `pytest` run will hit `10.10.50.20`.
- **Never run two epms-api test files concurrently** — they share the single `epms_test` database.
- **Do not modify `identity-api/scripts/verify_gate_parity.py`.** It is a frozen baseline that deliberately does not track new phase-2 keys.
- **Do not modify `epms/src/components/pr/PrLineItems.tsx`** or `epms/src/types/index.ts`'s `PrLineItem`. Four pages consume them; this feature uses its own component.
- **Do not modify `_generate_po_pdf_background()` in `epms-api/app/api/v1/po.py`.** The approval-path PDF flow stays exactly as it is.
- **Editable scope is `source == 'nc'` AND `status == 'issued'`.** `closed` and `nc_milk` stay read-only. Never widen this.
- **Never `git stash`.** Baselines are recorded below; measuring one by stashing risks losing uncommitted work.
- **Never run the dev Docker stack.** Its bind mounts are relative to the invoking directory and the running containers belong to other worktrees; starting or restarting it would disrupt another branch's environment and would not show this branch's code.

### Recorded baselines

- **epms-api full suite** (`python -m pytest tests -q`): **not measured at the branch point.** An attempt was abandoned because the whole repo shares ONE `epms_test` database and the run collided with a task's own tests. The full-suite comparison therefore happens once, in Task 9 Step 7, run strictly serially with nothing else touching the database. Task-level verification uses the named test files only.
- **epms frontend** (`npx tsc -p tsconfig.app.json`): measured in Task 6 Step 1 and recorded there; every later frontend task compares against it.

**Test-database concurrency is the hard rule here:** every epms-api test run in this repo — any task, any baseline, any spot check — targets the same `epms_test` database. Only one may run at a time. Never start one in the background and then start another.

### Standard commands

**epms-api tests** (run from the worktree root; `<DB_PASSWORD>` is the `DB_PASSWORD` value in the copied `.env`):

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms \
JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/<file> -v
```

**epms frontend type gate:**

```bash
cd /c/Project/uniops-nc-po-edit/epms
npm ci                 # required once — without it tsc only prints an install hint
npx tsc -p tsconfig.app.json
```

The frontend has **no test runner** (`epms/package.json` scripts are only `dev` / `build` / `lint` / `preview`). Frontend verification is `tsc` + `npm run lint` + the manual UI check in Task 9.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `epms-api/alembic/versions/nc03_po_buyer_details.py` | **Create.** The four new columns. | 1 |
| `epms-api/app/models/po.py` | **Modify.** ORM mappings for the four columns. | 1 |
| `epms-api/app/schemas/po.py` | **Modify.** Response fields (T1) + `PoImportedDetailsUpdate` request schema (T3). | 1, 3 |
| `epms-api/tests/test_po_buyer_details.py` | **Create.** Metadata smoke (T1) + endpoint behaviour (T3). | 1, 3 |
| `epms-api/app/services/nc_purchase_sync/writer.py` | **Modify.** Preserve a hand-set tax rate across re-sync. | 2 |
| `epms-api/tests/test_nc_purchase_writer.py` | **Modify.** Two new protection tests. | 2 |
| `epms-api/app/crud/po.py` | **Modify.** `update_imported_details()`. | 3 |
| `epms-api/app/api/v1/po.py` | **Modify.** `PATCH /{po_id}/imported-details`. | 3 |
| `identity-api/scripts/seed_phase2_keys.py` | **Modify.** Register `epms.po.edit_imported`. | 4 |
| `identity-api/alembic/versions/0006_po_edit_imported.py` | **Create.** Seed the key + its two grants. | 4 |
| `epms-api/app/services/pdf_po.py` | **Modify.** Sample column, Incoterms line, Buyer Notes block. | 5 |
| `epms-api/tests/test_po_pdf_buyer_details.py` | **Create.** PDF content assertions. | 5 |
| `epms/src/services/po.ts` | **Modify.** Types + `updateImportedDetails()`. | 6 |
| `epms/src/hooks/usePos.ts` | **Modify.** `useUpdatePoImportedDetails()`. | 6 |
| `epms/src/components/po/ImportedPoLineItems.tsx` | **Create.** Locked line table with two editable fields. | 7 |
| `epms/src/pages/po/PoImportedEditPage.tsx` | **Create.** The edit page. | 8 |
| `epms/src/app/routes.tsx` | **Modify.** Register `/po/:id/edit-imported`. | 8 |
| `epms/src/pages/po/PoDetailPage.tsx` | **Modify.** Edit Details button, Sample column, Incoterms + Buyer Notes display. | 9 |

---

### Task 1: Database columns, ORM, and response schema

**Files:**
- Create: `epms-api/alembic/versions/nc03_po_buyer_details.py`
- Modify: `epms-api/app/models/po.py`
- Modify: `epms-api/app/schemas/po.py`
- Test: `epms-api/tests/test_po_buyer_details.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PurchaseOrder.buyer_notes: str | None`, `PurchaseOrder.incoterms: str | None`, `PurchaseOrder.buyer_edited_at: datetime | None`, `PoLineItem.sample: str | None`. `PoResponse` gains `buyer_notes`, `incoterms`, `buyer_edited_at`; `PoLineItemResponse` gains `sample`.

**Background:** `nc02_nc_cutover` is the single alembic head for epms-api. This was verified by reading every `revision` / `down_revision` pair in `epms-api/alembic/versions/`: the chain runs `… → z1 → z2 → a1_supervisor_director → a2_workflow_defs_optional_levels → z3 → z4 → aa → ab → ac → ad → ae → af → nc01_nc_provenance → nc02_nc_cutover`. Do not trust a script that reports several heads; re-read the files if in doubt. Alembic revision ids must be ≤ 32 characters (`alembic_version.version_num` is `varchar(32)`); `nc03_po_buyer_details` is 21.

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_po_buyer_details.py`:

```python
"""Buyer-supplied detail on NC-imported POs (columns, endpoint, authz).

The columns are human-owned: the NC mirror never sources or writes them
(see app/services/nc_purchase_sync/writer.py). This file covers the schema
shape; tests/test_nc_purchase_writer.py covers the re-sync protection.
"""
from app.db.base import Base


def test_buyer_detail_columns_exist():
    po = Base.metadata.tables["purchase_orders"]
    assert "buyer_notes" in po.c
    assert "incoterms" in po.c
    assert "buyer_edited_at" in po.c
    assert "sample" in Base.metadata.tables["po_line_items"].c
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_po_buyer_details.py -v
```

Expected: FAIL — `AssertionError` on `"buyer_notes" in po.c`.

- [ ] **Step 3: Add the ORM columns**

In `epms-api/app/models/po.py`, inside `class PurchaseOrder`, immediately after the `notes` line and before the `# NC ERP provenance` block, insert:

```python
    # Buyer-supplied detail, filled in by hand after an NC import. NC owns
    # `notes` (it rewrites it every sync with its own memo plus [NC Paid] /
    # [NC Closed] markers), so buyer text needs a column of its own.
    # nc_purchase_sync/writer.py must never add these to its UPDATE lists.
    buyer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    incoterms: Mapped[str | None] = mapped_column(String(100), nullable=True)
    buyer_edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

In the same file, inside `class PoLineItem`, immediately after the `supplier_item_id` line, insert:

```python
    # Free-text sample requirement asked of the vendor, e.g. "500 g" / "2 ea".
    sample: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

No import changes are needed: `Text`, `String`, `DateTime` and `datetime` are all already imported at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_po_buyer_details.py -v
```

Expected: PASS (1 passed).

- [ ] **Step 5: Write the migration**

Create `epms-api/alembic/versions/nc03_po_buyer_details.py`:

```python
"""add buyer-supplied detail columns for NC-imported POs

Revision ID: nc03_po_buyer_details
Revises: nc02_nc_cutover
Create Date: 2026-08-07

purchase_orders.notes belongs to the NC mirror — every incremental sync
rewrites it with NC's own memo plus [NC Paid] / [NC Closed <date>] markers
that finance reads. buyer_notes/incoterms therefore get their own columns,
which the sync writer deliberately never touches. buyer_edited_at marks a PO
whose tax rate was set by hand so the sync can keep that rate instead of
overwriting it with NC's.
"""
import sqlalchemy as sa
from alembic import op

revision = "nc03_po_buyer_details"
down_revision = "nc02_nc_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_orders", sa.Column("buyer_notes", sa.Text(), nullable=True))
    op.add_column("purchase_orders", sa.Column("incoterms", sa.String(length=100), nullable=True))
    op.add_column(
        "purchase_orders",
        sa.Column("buyer_edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("po_line_items", sa.Column("sample", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("po_line_items", "sample")
    op.drop_column("purchase_orders", "buyer_edited_at")
    op.drop_column("purchase_orders", "incoterms")
    op.drop_column("purchase_orders", "buyer_notes")
```

- [ ] **Step 6: Verify the migration chain still has exactly one head**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
grep -h "^down_revision" alembic/versions/*.py | grep -c "nc02_nc_cutover"
```

Expected: `1` — only the new migration claims `nc02_nc_cutover` as its parent. If it prints `2` or more, another migration already branched off `nc02` and this one must be re-pointed at the real tail.

- [ ] **Step 7: Extend the response schemas**

In `epms-api/app/schemas/po.py`, in `class PoLineItemResponse`, after the `supplier_item_id: str | None` line, add:

```python
    sample: str | None = None
```

In the same file, in `class PoResponse`, after the `notes: str | None` line, add:

```python
    # Buyer-supplied detail (NC-imported POs). `notes` stays NC-owned.
    buyer_notes: str | None = None
    incoterms: str | None = None
    buyer_edited_at: datetime | None = None
```

- [ ] **Step 8: Run the wider PO suite for regressions**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_po_buyer_details.py tests/test_nc_source_field.py -v
```

Expected: all PASS. Adding optional response fields cannot break existing assertions, so any failure here is a real problem — read it, don't rerun.

- [ ] **Step 9: Commit**

```bash
cd /c/Project/uniops-nc-po-edit
git add epms-api/alembic/versions/nc03_po_buyer_details.py epms-api/app/models/po.py \
        epms-api/app/schemas/po.py epms-api/tests/test_po_buyer_details.py
git commit -m "feat(epms): add buyer_notes/incoterms/buyer_edited_at + line sample columns"
```

---

### Task 2: Protect hand-edited data from NC re-sync

**Files:**
- Modify: `epms-api/app/services/nc_purchase_sync/writer.py:100-107`
- Test: `epms-api/tests/test_nc_purchase_writer.py`

**Interfaces:**
- Consumes: `PurchaseOrder.buyer_edited_at` (Task 1).
- Produces: no new symbols. Behaviour change only: `writer.upsert()` keeps the stored `tax_rate` on any PO whose `buyer_edited_at` is set, and re-derives `tax_amount` / `total` from NC's fresh `subtotal`.

**Background:** `writer.upsert()`'s PO branch currently overwrites `tax_rate`, `tax_amount` and `total` from the NC payload on every incremental run. `buyer_notes`, `incoterms`, `buyer_edited_at`, `po_line_items.sample` and `po_line_items.supplier_item_id` are already absent from both UPDATE column lists and so need no code change — only a comment so nobody adds them later. The tax columns do need a guard. Note there is a second, broader protection already in place: `_po_consumed()` skips a PO entirely once any invoice references it, so this guard only matters in the window before the first invoice arrives.

- [ ] **Step 1: Write the failing tests**

Append to `epms-api/tests/test_nc_purchase_writer.py`:

```python
# ── buyer-edited data survives re-sync ───────────────────────────────────────

def test_upsert_preserves_buyer_details_and_manual_tax(pg_cur, seeded_vendor, system_user_id):
    """A PO whose buyer_edited_at is set keeps its hand-entered columns and its
    hand-set tax rate. The money is re-derived from NC's new subtotal so the
    header still satisfies subtotal + tax_amount == total."""
    payload = _mini_payload(seeded_vendor)
    writer.upsert(pg_cur, payload, system_user_id)

    pg_cur.execute(
        "update purchase_orders set buyer_notes=%s, incoterms=%s, tax_rate=%s, "
        "buyer_edited_at=now() where nc_source_pk='O1'",
        ("Ship in one lot", "FOB Shanghai", Decimal("0.13")))
    pg_cur.execute(
        "update po_line_items set supplier_item_id=%s, sample=%s where nc_source_pk='OL1'",
        ("SKU-9", "500 g"))

    # NC re-sends the order with a bigger subtotal and its own zero tax.
    payload["orders"][0]["subtotal"] = Decimal("200.00")
    payload["orders"][0]["tax_rate"] = Decimal("0")
    payload["orders"][0]["tax_amount"] = Decimal("0")
    payload["orders"][0]["total"] = Decimal("200.00")
    writer.upsert(pg_cur, payload, system_user_id)

    pg_cur.execute(
        "select buyer_notes, incoterms, subtotal, tax_rate, tax_amount, total "
        "from purchase_orders where nc_source_pk='O1'")
    notes, inco, subtotal, rate, tax_amount, total = pg_cur.fetchone()
    assert notes == "Ship in one lot"
    assert inco == "FOB Shanghai"
    assert subtotal == Decimal("200.00")      # NC still owns the subtotal
    assert rate == Decimal("0.13")            # buyer's rate survives
    assert tax_amount == Decimal("26.00")     # re-derived off the new subtotal
    assert total == Decimal("226.00")
    assert subtotal + tax_amount == total

    pg_cur.execute(
        "select supplier_item_id, sample from po_line_items where nc_source_pk='OL1'")
    assert pg_cur.fetchone() == ("SKU-9", "500 g")


def test_upsert_takes_nc_tax_when_po_was_never_buyer_edited(pg_cur, seeded_vendor, system_user_id):
    """Guard against over-reach: with buyer_edited_at NULL the mirror must still
    take NC's tax verbatim, exactly as before this change."""
    payload = _mini_payload(seeded_vendor)
    writer.upsert(pg_cur, payload, system_user_id)

    payload["orders"][0]["subtotal"] = Decimal("200.00")
    payload["orders"][0]["tax_rate"] = Decimal("0.05")
    payload["orders"][0]["tax_amount"] = Decimal("10.00")
    payload["orders"][0]["total"] = Decimal("210.00")
    writer.upsert(pg_cur, payload, system_user_id)

    pg_cur.execute(
        "select tax_rate, tax_amount, total from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone() == (Decimal("0.05"), Decimal("10.00"), Decimal("210.00"))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_nc_purchase_writer.py -v 2>&1 | tail -5
```

Expected: `test_upsert_preserves_buyer_details_and_manual_tax` FAILS (`assert Decimal('0.0000') == Decimal('0.13')` — NC's zero rate clobbered the buyer's). `test_upsert_takes_nc_tax_when_po_was_never_buyer_edited` PASSES already (it pins current behaviour).

Record the full `N passed, M failed` line — Step 6 compares against it.

- [ ] **Step 3: Add the Decimal import**

`epms-api/app/services/nc_purchase_sync/writer.py` currently imports only `secrets` and `uuid`. Add above them so the import block stays alphabetical:

```python
from decimal import Decimal
```

- [ ] **Step 4: Implement the guard**

In `epms-api/app/services/nc_purchase_sync/writer.py`, replace the `if row:` branch of the `for po in payload["orders"]:` loop (currently lines 100-107) with:

```python
        if row:
            pid = row[0]
            # buyer_notes / incoterms / buyer_edited_at here, and sample /
            # supplier_item_id on the line UPDATE below, are human-owned: NC has
            # no source for them, so they are deliberately absent from these
            # column lists. Never add them — a sync would silently erase work a
            # buyer did by hand.
            cur.execute("select tax_rate from purchase_orders "
                        "where id=%s and buyer_edited_at is not null", (pid,))
            edited = cur.fetchone()
            if edited is None:
                tax_rate = po["tax_rate"]
                tax_amount = po["tax_amount"]
                total = po["total"]
            else:
                # A buyer set this rate by hand (PATCH /po/{id}/imported-details).
                # Keep it, but re-derive the money from NC's fresh subtotal —
                # simply skipping the columns would leave subtotal + tax != total
                # whenever NC changed the line amounts.
                tax_rate = edited[0]
                tax_amount = (Decimal(po["subtotal"]) * Decimal(tax_rate)).quantize(
                    Decimal("0.01"))
                total = Decimal(po["subtotal"]) + tax_amount
            cur.execute("update purchase_orders set number=%s,title=%s,status=%s,currency=%s,"
                        "subtotal=%s,tax_rate=%s,tax_amount=%s,total=%s,"
                        "vendor_id=%s,vendor_name=%s,notes=%s,updated_at=now() where id=%s",
                        (po["number"], po["title"], po["status"], po["currency"],
                         po["subtotal"], tax_rate, tax_amount, total,
                         po["vendor_id"], po["vendor_name"], po["notes"], pid))
```

- [ ] **Step 5: Add the same warning comment to the line UPDATE**

Directly above the `cur.execute("update po_line_items set description=%s,...` call in the `for ln in payload["order_lines"]:` loop, insert:

```python
            # supplier_item_id and sample are omitted on purpose — see the note
            # on the PO UPDATE above.
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_nc_purchase_writer.py -v
```

Expected: every test in the file PASSES, including the pre-existing idempotency and consumed-guard tests. Compared with the line recorded in Step 2, the passed count must be exactly `+1` (the previously failing test now passes) and the failed count must be `0`.

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops-nc-po-edit
git add epms-api/app/services/nc_purchase_sync/writer.py epms-api/tests/test_nc_purchase_writer.py
git commit -m "fix(epms): NC re-sync keeps hand-set tax rate and buyer-owned columns"
```

---

### Task 3: The imported-details endpoint

**Files:**
- Modify: `epms-api/app/schemas/po.py`
- Modify: `epms-api/app/crud/po.py`
- Modify: `epms-api/app/api/v1/po.py`
- Test: `epms-api/tests/test_po_buyer_details.py`

**Interfaces:**
- Consumes: the Task 1 columns and response fields.
- Produces:
  - `app.schemas.po.PoImportedLineUpdate` — `{id: uuid.UUID, supplier_item_id: str | None, sample: str | None}`
  - `app.schemas.po.PoImportedDetailsUpdate` — `{expected_delivery, delivery_address, incoterms, tax_code, tax_rate, is_prepaid, buyer_notes, lines: list[PoImportedLineUpdate]}`
  - `app.crud.po.update_imported_details(db, po, payload) -> tuple[PurchaseOrder, dict, dict]` returning `(po, before, after)`; raises `ValueError` when a line id does not belong to the PO.
  - `PATCH /api/v1/po/{po_id}/imported-details` returning `PoResponse`.

**Background:** the matrix key `epms.po.edit_imported` is registered for production in Task 4. Tests must seed it themselves — `epms-api/tests/conftest.py::_seed_default_matrix` only seeds the 19 phase-1 keys, never phase-2 ones. The precedent is `tests/test_pa_on_behalf_authz.py::_grant_pa_write`. `require_permission` reads identity's `role_permissions` table directly over the shared physical database (`app/core/deps.py:74-91`), and `system_admin` short-circuits to allowed.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `epms-api/tests/test_po_buyer_details.py` with (the Task 1 smoke test is kept as the first test):

```python
"""Buyer-supplied detail on NC-imported POs (columns, endpoint, authz).

The columns are human-owned: the NC mirror never sources or writes them
(see app/services/nc_purchase_sync/writer.py). Re-sync protection lives in
tests/test_nc_purchase_writer.py.

PATCH /po/{id}/imported-details is deliberately NOT the general PATCH /po/{id}:
that one can replace the vendor, the currency and the entire line-item set, so
widening its status gate for NC POs would let anyone holding epms.po.write
rewrite the money on an order already in the invoice/payment flow. These tests
pin that separation — especially test_cannot_touch_another_pos_line and
test_locked_fields_are_unreachable.
"""
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.db.base import Base
from app.main import create_app
from app.models.admin_audit_log import AdminAuditLog
from app.models.po import PoLineItem, PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

_KEY = "epms.po.edit_imported"


def test_buyer_detail_columns_exist():
    po = Base.metadata.tables["purchase_orders"]
    assert "buyer_notes" in po.c
    assert "incoterms" in po.c
    assert "buyer_edited_at" in po.c
    assert "sample" in Base.metadata.tables["po_line_items"].c


async def _grant_edit_imported(db):
    """conftest's default matrix seeds only phase-1 keys, so the phase-2 key this
    endpoint is gated on has to be inserted here — mirroring what identity's
    0006_po_edit_imported migration does in production."""
    await db.execute(text(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','epms','Edit Imported (NC) POs',104) ON CONFLICT (key) DO NOTHING"))
    await db.execute(text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('erp_pa_officer','{_KEY}') ON CONFLICT DO NOTHING"))


async def _user(db, role: str):
    return await user_crud.create(db, RegisterRequest(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=role.replace("_", " ").title(), role=role))


async def _nc_po(db, *, status: str = "issued", source: str | None = "nc", creator_id=None):
    """An NC-mirrored PO with one line, shaped like writer.upsert() leaves it."""
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    po = PurchaseOrder(
        number=f"PO-NC-{uuid.uuid4().hex[:8]}", title="NC order", type=1,
        status=status, currency="CAD", vendor_id=v.id, vendor_name="Acme",
        subtotal=Decimal("100.00"), tax_rate=Decimal("0"), tax_amount=Decimal("0"),
        total=Decimal("100.00"), source=source, nc_source_pk=uuid.uuid4().hex,
        notes="nc memo [NC Invoiced]", created_by=creator_id,
    )
    db.add(po)
    await db.flush()
    line = PoLineItem(
        po_id=po.id, description="Widget", material_id="MAT-1", qty=Decimal("10"),
        unit="EA", unit_price=Decimal("10.00"), line_total=Decimal("100.00"),
        sort_order=0,
    )
    db.add(line)
    await db.flush()
    return po, line


def _client_for(user):
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()),
                       base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _url(po_id):
    return f"/api/v1/po/{po_id}/imported-details"


@pytest.mark.asyncio
async def test_erp_pa_officer_fills_in_buyer_details(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id, line_id = po.id, line.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={
            "expected_delivery": "2026-09-01",
            "delivery_address": "1 Royal Way",
            "incoterms": "FOB Shanghai",
            "tax_code": "HST13",
            "tax_rate": "0.13",
            "is_prepaid": True,
            "buyer_notes": "Ship in one lot.",
            "lines": [{"id": str(line_id), "supplier_item_id": "SKU-9", "sample": "500 g"}],
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["incoterms"] == "FOB Shanghai"
    assert body["buyer_notes"] == "Ship in one lot."
    assert body["delivery_address"] == "1 Royal Way"
    assert body["buyer_edited_at"] is not None
    assert body["line_items"][0]["supplier_item_id"] == "SKU-9"
    assert body["line_items"][0]["sample"] == "500 g"
    # tax recomputed off the untouched subtotal
    assert Decimal(body["subtotal"]) == Decimal("100.00")
    assert Decimal(body["tax_amount"]) == Decimal("13.00")
    assert Decimal(body["total"]) == Decimal("113.00")
    # NC's own notes column is untouched — the [NC Invoiced] marker finance
    # reads must survive a buyer edit.
    assert body["notes"] == "nc memo [NC Invoiced]"


@pytest.mark.asyncio
async def test_role_without_the_matrix_key_is_rejected(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)          # granted to erp_pa_officer only
        officer = await _user(db, "erp_pa_officer")
        stranger = await _user(db, "requester")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(stranger) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "EXW"})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_system_admin_is_allowed(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        admin = await _user(db, "system_admin")
        po, line = await _nc_po(db, creator_id=admin.id)
        po_id = po.id
        await db.commit()

    async with _client_for(admin) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "DDP Toronto"})
    assert r.status_code == 200, r.text
    assert r.json()["incoterms"] == "DDP Toronto"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["closed", "nc_milk", "draft"])
async def test_only_issued_nc_pos_are_editable(test_engine, status):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, status=status, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "EXW"})
    assert r.status_code == 409, r.text
    assert status in r.text


@pytest.mark.asyncio
async def test_non_nc_po_is_rejected(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, source=None, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "EXW"})
    assert r.status_code == 409, r.text
    assert "NC-imported" in r.text


@pytest.mark.asyncio
async def test_cannot_touch_another_pos_line(test_engine):
    """Passing a line id that belongs to a different PO must be rejected AND must
    leave that line untouched. Without the ownership check this endpoint would be
    a cross-document write primitive."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        mine, _ = await _nc_po(db, creator_id=officer.id)
        _, victim_line = await _nc_po(db, creator_id=officer.id)
        mine_id, victim_line_id = mine.id, victim_line.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(mine_id), json={
            "lines": [{"id": str(victim_line_id), "supplier_item_id": "STOLEN"}]})
    assert r.status_code == 400, r.text

    async with factory() as db:
        got = (await db.execute(
            select(PoLineItem.supplier_item_id).where(PoLineItem.id == victim_line_id)
        )).scalar_one()
        assert got is None, "a rejected request must not have written the other PO's line"


@pytest.mark.asyncio
async def test_locked_fields_are_unreachable(test_engine):
    """vendor / currency / title / qty / unit_price are not in the request schema.
    Sending them must change nothing — pydantic drops the unknown keys."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id, line_id = po.id, line.id
        before = (po.vendor_id, po.vendor_name, po.currency, po.title, po.subtotal)
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={
            "incoterms": "EXW",
            "vendor_id": str(uuid.uuid4()),
            "currency": "USD",
            "title": "hacked",
            "lines": [{"id": str(line_id), "supplier_item_id": "SKU-1",
                       "qty": "999", "unit_price": "0.01"}],
        })
    assert r.status_code == 200, r.text

    async with factory() as db:
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        assert (po.vendor_id, po.vendor_name, po.currency, po.title, po.subtotal) == before
        ln = (await db.execute(
            select(PoLineItem).where(PoLineItem.id == line_id))).scalar_one()
        assert ln.qty == Decimal("10.0000")
        assert ln.unit_price == Decimal("10.00")
        assert ln.supplier_item_id == "SKU-1"


@pytest.mark.asyncio
async def test_edit_is_audited(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id, po_number = po.id, po.number
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "FOB Shanghai"})
    assert r.status_code == 200, r.text

    async with factory() as db:
        row = (await db.execute(
            select(AdminAuditLog).where(AdminAuditLog.record_id == po_id))).scalar_one()
        assert row.entity == "po"
        assert row.action == "edit"
        assert row.system == "epms"
        assert row.record_number == po_number
        assert row.after["incoterms"] == "FOB Shanghai"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_po_buyer_details.py -v
```

Expected: `test_buyer_detail_columns_exist` PASSES; every HTTP test FAILS with `404` (the route does not exist yet).

- [ ] **Step 3: Add the request schemas**

In `epms-api/app/schemas/po.py`, after `class PoUpdate`, add:

```python
class PoImportedLineUpdate(BaseModel):
    """The only two line columns a buyer may fill in on an imported PO."""
    id: uuid.UUID
    supplier_item_id: str | None = Field(default=None, max_length=100)
    sample: str | None = Field(default=None, max_length=100)


class PoImportedDetailsUpdate(BaseModel):
    """Buyer-supplied detail on an NC-imported PO.

    Deliberately narrow. vendor_id, currency, title, budget_code, type and every
    line money/quantity field are absent, so no caller can reach them through
    this endpoint no matter what the frontend does or does not disable. Widening
    this model is a security change, not a convenience change.
    """
    expected_delivery: date | None = None
    delivery_address: str | None = None
    incoterms: str | None = Field(default=None, max_length=100)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    is_prepaid: bool | None = None
    buyer_notes: str | None = None
    lines: list[PoImportedLineUpdate] = Field(default_factory=list)
```

- [ ] **Step 4: Add the CRUD function**

In `epms-api/app/crud/po.py`, add `PoImportedDetailsUpdate` to the existing `from app.schemas.po import ...` line, then add this function directly after `async def update(...)`:

```python
# ── Imported-PO buyer details (NC mirror, status='issued' only) ───────────────

async def update_imported_details(
    db: AsyncSession,
    po: PurchaseOrder,
    payload: PoImportedDetailsUpdate,
) -> tuple[PurchaseOrder, dict, dict]:
    """Apply buyer-supplied detail to an NC-imported PO.

    Only writes the columns named in PoImportedDetailsUpdate. subtotal is never
    touched: a tax-rate change re-derives tax_amount/total from the existing
    subtotal so the header stays internally consistent.

    Returns (po, before, after) holding only the fields this call actually
    changed, for the caller's audit-log entry. Raises ValueError if a line id
    does not belong to this PO.
    """
    before: dict = {}
    after: dict = {}

    def _set(field: str, value) -> None:
        old = getattr(po, field)
        if value is None or old == value:
            return
        before[field] = str(old) if old is not None else None
        after[field] = str(value)
        setattr(po, field, value)

    for field in ("expected_delivery", "delivery_address", "incoterms",
                  "tax_code", "buyer_notes", "is_prepaid"):
        _set(field, getattr(payload, field))

    if payload.tax_rate is not None and payload.tax_rate != po.tax_rate:
        before["tax_rate"] = str(po.tax_rate)
        po.tax_rate = payload.tax_rate
        po.tax_amount = (po.subtotal * payload.tax_rate).quantize(Decimal("0.01"))
        po.total = po.subtotal + po.tax_amount
        after["tax_rate"] = str(po.tax_rate)
        after["tax_amount"] = str(po.tax_amount)
        after["total"] = str(po.total)

    by_id = {line.id: line for line in po.line_items}
    line_changes: list[dict] = []
    for patch in payload.lines:
        line = by_id.get(patch.id)
        if line is None:
            # Never a 500 and never a silent no-op: a line id from another PO is
            # a caller error worth surfacing, and letting it through would make
            # this endpoint a cross-document write primitive.
            raise ValueError(f"Line {patch.id} does not belong to PO {po.number}")
        delta: dict = {}
        if patch.supplier_item_id is not None and line.supplier_item_id != patch.supplier_item_id:
            delta["supplier_item_id"] = [line.supplier_item_id, patch.supplier_item_id]
            line.supplier_item_id = patch.supplier_item_id
        if patch.sample is not None and line.sample != patch.sample:
            delta["sample"] = [line.sample, patch.sample]
            line.sample = patch.sample
        if delta:
            line_changes.append({"line_id": str(patch.id), **delta})
    if line_changes:
        after["lines"] = line_changes

    # Marks the PO for nc_purchase_sync/writer.py's tax-rate guard.
    po.buyer_edited_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(po)
    return po, before, after
```

`datetime`, `timezone`, `Decimal` and `AsyncSession` are already imported at the top of `crud/po.py`.

- [ ] **Step 5: Add the endpoint**

In `epms-api/app/api/v1/po.py`:

1. Add `PoImportedDetailsUpdate` to the existing `from app.schemas.po import ...` line.
2. Add `from app.models.admin_audit_log import AdminAuditLog` to the imports.
3. Next to the existing `PoWriteDep` on line 27, add:

```python
PoEditImportedDep = Annotated[dict, Depends(require_permission("epms.po.edit_imported"))]
```

4. Directly after the existing `update_po` endpoint, add:

```python
@router.patch("/{po_id}/imported-details", response_model=PoResponse)
async def update_imported_details(
    po_id: uuid.UUID, body: PoImportedDetailsUpdate, db: SessionDep, user: PoEditImportedDep,
):
    """Fill in buyer-supplied detail on an NC-imported PO.

    Deliberately separate from PATCH /po/{po_id}. That endpoint accepts a new
    vendor_id, a new currency and a full replacement line_items list (crud.update
    deletes and rebuilds the lines), so relaxing its draft/returned status gate
    for NC POs would hand anyone holding epms.po.write the ability to rewrite the
    money on an order already in the invoice/payment flow. Here the request model
    itself makes those fields unreachable.
    """
    po = await po_crud.get_by_id(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")
    if po.source != "nc":
        raise HTTPException(
            status_code=409, detail="Only NC-imported POs can be edited here")
    if po.status != "issued":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot edit an NC PO in status '{po.status}' — only 'issued' is editable",
        )

    try:
        po, before, after = await po_crud.update_imported_details(db, po, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if after:
        db.add(AdminAuditLog(
            actor_id=uuid.UUID(user["sub"]),
            actor_email=user.get("email", ""),
            action="edit",
            system="epms",
            entity="po",
            record_id=po.id,
            record_number=po.number,
            before=before,
            after=after,
        ))
        await db.flush()
    return po
```

`user.get("email", "")` mirrors `api/v1/admin.py::_actor` — the access token carries only `sub`, `role` and `type`, so the column (NOT NULL) gets an empty string rather than a crash.

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_po_buyer_details.py -v
```

Expected: 10 passed (1 smoke + 3 status params + 6 behaviour tests).

- [ ] **Step 7: Check for regressions in the general PO endpoints**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_po.py tests/test_po_search.py tests/test_po_pa_filters.py \
                 tests/test_nc_source_field.py tests/test_pa_on_behalf_authz.py -v
```

Expected: same pass/fail counts as the branch-point baseline recorded in **Global Constraints → Recorded baselines**. Do not assume zero failures — this suite carries a pre-existing failure population. **Never `git stash` to measure a baseline**; the recorded numbers exist precisely so you never have to.

- [ ] **Step 8: Commit**

```bash
cd /c/Project/uniops-nc-po-edit
git add epms-api/app/schemas/po.py epms-api/app/crud/po.py epms-api/app/api/v1/po.py \
        epms-api/tests/test_po_buyer_details.py
git commit -m "feat(epms): PATCH /po/{id}/imported-details for NC-imported PO buyer detail"
```

---

### Task 4: Register the `epms.po.edit_imported` matrix key

**Files:**
- Modify: `identity-api/scripts/seed_phase2_keys.py:15-50`
- Create: `identity-api/alembic/versions/0006_po_edit_imported.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (the endpoint's tests seed the key themselves).
- Produces: permission key `epms.po.edit_imported` granted to `system_admin` and `erp_pa_officer` on a real database.

**Background:** identity-api's single alembic head is `0005_procurement_officer_pa`. Revision ids are capped at 32 characters by `alembic_version_identity.version_num`; `0006_po_edit_imported` is 20. `erp_pa_officer` already exists as a role (`identity-api/alembic/versions/0004_erp_pa_officer_role.py`), so unlike migration 0005 this one does **not** need to insert a `role_defs` row. Do not touch `verify_gate_parity.py`.

- [ ] **Step 1: Register the key in the seed script**

In `identity-api/scripts/seed_phase2_keys.py`, add to `PHASE2_KEYS` directly after the `epms.gr.receive` entry:

```python
    "epms.po.edit_imported": ("epms",    "Edit Imported (NC) POs",  104),
```

and to `PHASE2_DEFAULTS` directly after the `epms.gr.receive` entry:

```python
    "epms.po.edit_imported": ("system_admin", "erp_pa_officer"),
```

- [ ] **Step 2: Write the migration**

Create `identity-api/alembic/versions/0006_po_edit_imported.py`:

```python
"""Add epms.po.edit_imported and grant it to erp_pa_officer.

NC-imported POs land as status='issued' carrying only what NC holds — no
supplier item IDs, no sample requirements, no Incoterms, no delivery detail.
Filling those in is gated by this key rather than by epms.po.write, because
epms.po.write also opens PATCH /po/{id}, which can replace the vendor, the
currency and the whole line-item set.

Seeding the grant here rather than leaving it for an admin to tick in
Portal -> Access Control is deliberate: the Create-GR cutover shipped a
matrix-driven gate with no seeded grant and 403'd everyone who previously had
the button.

system_admin is NOT granted explicitly — require_permission short-circuits it.

Idempotent (ON CONFLICT DO NOTHING), so it is safe on a database the seed
scripts already touched and self-sufficient on a fresh one. Unlike migration
0005 it does not insert a role_defs row: erp_pa_officer was created by
0004_erp_pa_officer_role, which this migration transitively follows.

Revision id length: alembic_version_identity.version_num is varchar(32);
"0006_po_edit_imported" is 20 characters.
"""
from alembic import op

revision = "0006_po_edit_imported"
down_revision = "0005_procurement_officer_pa"
branch_labels = None
depends_on = None

_ROLE = "erp_pa_officer"
_KEY = "epms.po.edit_imported"


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','epms','Edit Imported (NC) POs',104) "
        "ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('{_ROLE}','{_KEY}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grant and the key this migration introduced. role_defs is shared
    # with the seed scripts and other roles' grants — leave it alone.
    op.execute(
        f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}'")
    op.execute(f"DELETE FROM permission_defs WHERE key = '{_KEY}'")
```

- [ ] **Step 3: Verify the chain has exactly one head**

```bash
cd /c/Project/uniops-nc-po-edit/identity-api
grep -h "^down_revision" alembic/versions/*.py | grep -c "0005_procurement_officer_pa"
```

Expected: `1`.

- [ ] **Step 4: Verify the key length and the label/sort agree across both files**

```bash
cd /c/Project/uniops-nc-po-edit
python -c "print(len('0006_po_edit_imported'))"
grep -n "edit_imported" identity-api/scripts/seed_phase2_keys.py \
                        identity-api/alembic/versions/0006_po_edit_imported.py \
                        epms-api/tests/test_po_buyer_details.py
```

Expected: `20`, then four lines — the two seed-script entries, the migration's INSERT, and the test helper. The label `Edit Imported (NC) POs` and sort `104` must read identically in all three places; a mismatch pins the key at the wrong position in the Portal matrix depending on which ran first.

- [ ] **Step 5: Confirm the frozen baseline was not touched**

```bash
cd /c/Project/uniops-nc-po-edit
git status --short identity-api/scripts/verify_gate_parity.py
```

Expected: no output. If it shows as modified, revert it — it is deliberately frozen.

- [ ] **Step 6: Commit**

```bash
cd /c/Project/uniops-nc-po-edit
git add identity-api/scripts/seed_phase2_keys.py \
        identity-api/alembic/versions/0006_po_edit_imported.py
git commit -m "feat(authz): add epms.po.edit_imported matrix key for erp_pa_officer"
```

---

### Task 5: PDF — Sample column, Incoterms, Buyer Notes

**Files:**
- Modify: `epms-api/app/services/pdf_po.py`
- Test: `epms-api/tests/test_po_pdf_buyer_details.py`

**Interfaces:**
- Consumes: `PurchaseOrder.buyer_notes` / `.incoterms` / `.source`, `PoLineItem.sample` (Task 1).
- Produces: no new symbols. `generate_po_pdf()` keeps its signature.

**Background:** the PDF is (re)generated by the *existing* `POST /po/{po_id}/attachments/regenerate-pdf` endpoint (`app/api/v1/po_attachments.py:84-133`), whose `_PDF_STATUSES` already includes `issued`. That endpoint already deletes the old `<number>.pdf` attachment (including the file-server object) before re-uploading. **Nothing in Task 5 changes any endpoint** — only the renderer.

Column-width arithmetic, since this is easy to get wrong: `W = A4 width - 40mm = 170mm`. The fixed columns are `8 + 26 + 15 + 14 + 28 + 28 = 119mm`. With Description at `W*0.18 = 30.6mm` and Sample at `20mm` the total is `169.6mm`, just inside `W`. At `W*0.20` it would be `173mm` and overflow the page.

- [ ] **Step 1: Write the failing tests**

Create `epms-api/tests/test_po_pdf_buyer_details.py`:

```python
"""PO PDF carries Incoterms, Buyer Notes and the Sample column.

Follows tests/test_pr_pdf_department.py: build ORM rows in memory, call
generate_po_pdf() synchronously, and assert on the decompressed content
streams. All fixture text is ASCII so reportlab writes it literally into the
Tj/TJ operators (non-ASCII would go through font subsetting and defeat a raw
substring check).
"""
import base64
import re
import uuid
import zlib
from decimal import Decimal

from app.models.po import PoLineItem, PurchaseOrder
from app.services.pdf_po import generate_po_pdf

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _text_of(pdf_bytes: bytes) -> str:
    out = bytearray()
    for match in _STREAM_RE.finditer(pdf_bytes):
        raw = match.group(1).strip(b"\r\n")
        try:
            data = raw
            if data.rstrip().endswith(b"~>"):
                data = data.rstrip()[:-2]
            out += zlib.decompress(base64.a85decode(data))
        except Exception:
            continue
    return out.decode("latin-1")


def _po(**kw) -> PurchaseOrder:
    line_kw = kw.pop("line_kw", {})
    po = PurchaseOrder(
        id=uuid.uuid4(), number="PO-NC-0001", title="NC order", type=1,
        status="issued", currency="CAD", vendor_id=uuid.uuid4(), vendor_name="Acme",
        subtotal=Decimal("100.00"), tax_rate=Decimal("0"), tax_amount=Decimal("0"),
        total=Decimal("100.00"), created_by=uuid.uuid4(), **kw,
    )
    po.line_items = [PoLineItem(
        id=uuid.uuid4(), po_id=po.id, description="Widget", qty=Decimal("10"),
        unit="EA", unit_price=Decimal("10.00"), line_total=Decimal("100.00"),
        received_qty=Decimal("0"), sort_order=0, **line_kw,
    )]
    return po


def test_incoterms_and_buyer_notes_render_in_order():
    po = _po(source="nc", incoterms="FOB Shanghai",
             buyer_notes="Ship in one lot.", notes="nc memo [NC Paid]")
    text = _text_of(generate_po_pdf(po))
    assert "FOB Shanghai" in text
    assert "Ship in one lot." in text
    # Incoterms must sit BEFORE Buyer Notes, per the spec's PDF ordering.
    assert text.index("FOB Shanghai") < text.index("Ship in one lot.")


def test_nc_notes_never_leak_into_the_vendor_facing_pdf():
    """An NC PO with no buyer_notes must NOT fall back to `notes` — that column
    holds NC's internal [NC Paid] / [NC Closed] markers."""
    po = _po(source="nc", buyer_notes=None, notes="nc memo [NC Paid]")
    text = _text_of(generate_po_pdf(po))
    assert "NC Paid" not in text
    assert "nc memo" not in text
    assert "BUYER NOTES" not in text, "no empty Buyer Notes heading"


def test_empty_incoterms_renders_no_label():
    """A PO without Incoterms must not carry a stranded 'Incoterms' label."""
    text = _text_of(generate_po_pdf(_po(incoterms=None)))
    assert "Incoterms" not in text


def test_non_nc_po_falls_back_to_notes():
    """Ordinary POs collect buyer text in `notes` via the Create PO page's
    'Buyer Notes / Terms & Conditions' box, which never reached the PDF before."""
    po = _po(source=None, buyer_notes=None, notes="Deliver to dock 3.")
    text = _text_of(generate_po_pdf(po))
    assert "Deliver to dock 3." in text


def test_sample_column_appears_only_when_a_line_has_one():
    with_sample = _text_of(generate_po_pdf(_po(line_kw={"sample": "500 g"})))
    assert "Sample" in with_sample
    assert "500 g" in with_sample

    without = _text_of(generate_po_pdf(_po(line_kw={"sample": None})))
    assert "Sample" not in without, "existing POs must keep their original layout"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_po_pdf_buyer_details.py -v
```

Expected: `test_incoterms_and_buyer_notes_render_in_order`, `test_non_nc_po_falls_back_to_notes` and `test_sample_column_appears_only_when_a_line_has_one` FAIL. `test_nc_notes_never_leak_into_the_vendor_facing_pdf` and `test_empty_incoterms_renders_no_label` pass vacuously (nothing renders those yet) — that is fine, they are regression guards for the change below.

If a *positive* assertion fails after Step 3/4 are implemented, first check whether reportlab split the string across two text-showing operators before assuming the render is wrong: `print(_text_of(pdf)[-3000:])` and look for the fragments. The existing `tests/test_pr_pdf_department.py` relies on the same whole-string behaviour for ASCII, so a split is unlikely but is the one plausible false negative here.

- [ ] **Step 3: Make the line table sample-aware**

In `epms-api/app/services/pdf_po.py`, replace the `col_w` / `headers` / row-building block in the `# ── Line items ──` section with:

```python
    # Sample is a buyer-supplied, NC-import-only field. Rendering the column
    # only when some line actually carries one keeps every pre-existing PO's
    # layout byte-identical. Width check (W = 170mm): fixed columns
    # 8+26+15+14+28+28 = 119mm, Description 0.18*W = 30.6mm, Sample 20mm
    # -> 169.6mm, inside W. Description at 0.20*W would overflow at 173mm.
    show_sample = any(getattr(item, "sample", None) for item in po.line_items)
    if show_sample:
        col_w = [8 * mm, W * 0.18, 26 * mm, 15 * mm, 14 * mm, 20 * mm, 28 * mm, 28 * mm]
        headers = ["#", "Description", "Supplier ID", "Qty", "Unit", "Sample",
                   "Unit Price", "Line Total"]
    else:
        col_w = [8 * mm, W * 0.28, 26 * mm, 15 * mm, 14 * mm, 28 * mm, 28 * mm]
        headers = ["#", "Description", "Supplier ID", "Qty", "Unit",
                   "Unit Price", "Line Total"]

    rows: list = [[Paragraph(h, th_style) for h in headers]]
    for i, item in enumerate(po.line_items, 1):
        row = [
            Paragraph(str(i),                              td_style),
            Paragraph(item.description,                    td_style),
            Paragraph(item.supplier_item_id or "",         td_style),
            Paragraph(str(item.qty),                       td_r_style),
            Paragraph(item.unit or "",                     td_style),
        ]
        if show_sample:
            row.append(Paragraph(getattr(item, "sample", None) or "", td_style))
        row += [
            Paragraph(f"{float(item.unit_price):,.2f}",    td_r_style),
            Paragraph(f"{float(item.line_total):,.2f}",    td_r_style),
        ]
        rows.append(row)
```

- [ ] **Step 4: Render Incoterms and Buyer Notes**

In the same file, between the `story += [Spacer(1, 3 * mm), totals, Spacer(1, 6 * mm)]` line and the `# ── Terms & Conditions (from template) ──` block, insert:

```python
    # ── Incoterms ─────────────────────────────────────────────────────────────
    if po.incoterms:
        story += [
            Table([[Paragraph("Incoterms", lbl_style), Paragraph(po.incoterms, val_style)]],
                  colWidths=[25 * mm, W - 25 * mm]),
            Spacer(1, 4 * mm),
        ]

    # ── Buyer Notes ───────────────────────────────────────────────────────────
    # NC owns purchase_orders.notes: every sync rewrites it with NC's memo plus
    # [NC Paid] / [NC Closed] markers meant for finance. Falling back to it on an
    # NC PO would print those internal markers on the vendor's copy, so only
    # non-NC POs fall back (that is where the Create PO page's "Buyer Notes /
    # Terms & Conditions" box lands — it never reached the PDF before).
    buyer_text = po.buyer_notes or (po.notes if po.source != "nc" else None)
    if buyer_text and buyer_text.strip():
        story += [
            Paragraph("BUYER NOTES", sec_style),
            Paragraph(buyer_text.strip(), val_style),
            Spacer(1, 4 * mm),
        ]
```

`lbl_style`, `val_style` and `sec_style` are all already defined near the top of `generate_po_pdf`; `Table` and `Spacer` are already imported.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests/test_po_pdf_buyer_details.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
cd /c/Project/uniops-nc-po-edit
git add epms-api/app/services/pdf_po.py epms-api/tests/test_po_pdf_buyer_details.py
git commit -m "feat(epms): PO PDF renders Sample column, Incoterms and Buyer Notes"
```

---

### Task 6: Frontend service and hook plumbing

**Files:**
- Modify: `epms/src/services/po.ts`
- Modify: `epms/src/hooks/usePos.ts`

**Interfaces:**
- Consumes: the Task 3 endpoint.
- Produces:
  - `ApiPo` gains `buyer_notes?: string | null`, `incoterms?: string | null`, `buyer_edited_at?: string | null`; `ApiPoLineItem` gains `sample?: string | null`.
  - `export interface ImportedDetailsBody` and `poService.updateImportedDetails(id, body): Promise<ApiPo>`
  - `export function useUpdatePoImportedDetails(id: string)` — a mutation taking `ImportedDetailsBody`.

**Background:** the frontend has no test runner. The gate for Tasks 6-9 is `npx tsc -p tsconfig.app.json` plus `npm run lint`. **Measure the pre-existing error count yourself before changing anything** — the baseline drifts with the code, and historical numbers from other branches do not apply here.

- [ ] **Step 1: Measure the type-check baseline**

```bash
cd /c/Project/uniops-nc-po-edit/epms
npm ci
npx tsc -p tsconfig.app.json 2>&1 | tee /tmp/tsc-baseline.txt | tail -5
grep -c "error TS" /tmp/tsc-baseline.txt || echo 0
```

Record that number. If the output instead reads like an npm install hint, `npm ci` did not finish — rerun it. A count of `0` obtained from an unbuilt tree is a false negative.

- [ ] **Step 2: Extend the API types**

In `epms/src/services/po.ts`, in `interface ApiPoLineItem`, after the `supplier_item_id?: string` line add:

```ts
  // Buyer-supplied sample requirement, e.g. "500 g" / "2 ea" (NC-imported POs).
  sample?: string | null
```

In `interface ApiPo`, after the `notes?: string` line add:

```ts
  // Buyer-supplied detail on NC-imported POs. `notes` stays NC-owned and may
  // contain internal [NC Paid] / [NC Closed] markers — never show it as
  // buyer text on an NC PO.
  buyer_notes?: string | null
  incoterms?: string | null
  buyer_edited_at?: string | null
```

- [ ] **Step 3: Add the service call**

In `epms/src/services/po.ts`, add the body type next to the other body interfaces and the call to the exported `poService` object:

```ts
export interface ImportedDetailsLineBody {
  id: string
  supplier_item_id?: string | null
  sample?: string | null
}

export interface ImportedDetailsBody {
  expected_delivery?: string | null
  delivery_address?: string | null
  incoterms?: string | null
  tax_code?: string | null
  tax_rate?: number | null
  is_prepaid?: boolean | null
  buyer_notes?: string | null
  lines: ImportedDetailsLineBody[]
}
```

and inside `poService`:

```ts
  updateImportedDetails: (id: string, body: ImportedDetailsBody) =>
    api.patch<ApiPo>(`/po/${id}/imported-details`, body),
```

Match the surrounding style — check whether the neighbouring members use `api.patch<T>(...)` directly or wrap it, and follow whichever is there.

- [ ] **Step 4: Add the mutation hook**

In `epms/src/hooks/usePos.ts`, directly after `useUpdatePo()`, add:

```ts
/** Buyer-detail edit for NC-imported POs (PATCH /po/{id}/imported-details).
 *  Separate from useUpdatePo: that one drives the general draft/returned edit
 *  form and can change the vendor, the currency and the whole line set. */
export function useUpdatePoImportedDetails(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ImportedDetailsBody) => poService.updateImportedDetails(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pos'] })
      queryClient.invalidateQueries({ queryKey: ['pos', id] })
    },
  })
}
```

Add `ImportedDetailsBody` to the existing `import type { ... } from '@/services/po'` line (create the import if the file imports `poService` without types).

- [ ] **Step 5: Verify the type check is clean**

```bash
cd /c/Project/uniops-nc-po-edit/epms
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS" || echo 0
```

Expected: the same number recorded in Step 1. Any increase is caused by this task — read the errors, do not rerun.

- [ ] **Step 6: Commit**

```bash
cd /c/Project/uniops-nc-po-edit
git add epms/src/services/po.ts epms/src/hooks/usePos.ts
git commit -m "feat(epms-ui): API types and mutation for imported-PO buyer details"
```

---

### Task 7: The locked line-items component

**Files:**
- Create: `epms/src/components/po/ImportedPoLineItems.tsx`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure presentation).
- Produces:
  - `export interface ImportedPoLine { id: string; description: string; materialId?: string | null; qty: number; unit: string; unitPrice: number; lineTotal: number; supplierItemId: string; sample: string }`
  - `export function ImportedPoLineItems({ items, onChange, currency }: { items: ImportedPoLine[]; onChange: (items: ImportedPoLine[]) => void; currency: string })`

**Background:** this deliberately does **not** extend `epms/src/components/pr/PrLineItems.tsx`. That component is 768 lines, has four consumer pages (`PrCreatePage`, `PrEditPage`, `PoCreatePage`, `PoEditPage`) and two renderings (desktop table + mobile cards), and carries parts pickers, an ERP materials picker, drag-to-reorder, add/remove-row and per-row validation — none of which apply here. Threading a lock flag through it would buy four pages of regression surface for nothing.

- [ ] **Step 1: Create the component**

Create `epms/src/components/po/ImportedPoLineItems.tsx`:

```tsx
import { formatAmount } from '@/lib/utils'

/** One line of an NC-imported PO as the buyer-detail form sees it.
 *  Everything except supplierItemId and sample is display-only: NC owns those
 *  values and the backend endpoint cannot write them. */
export interface ImportedPoLine {
  id: string
  description: string
  materialId?: string | null
  qty: number
  unit: string
  unitPrice: number
  lineTotal: number
  supplierItemId: string
  sample: string
}

interface ImportedPoLineItemsProps {
  items: ImportedPoLine[]
  onChange: (items: ImportedPoLine[]) => void
  currency: string
}

/** Line table for the imported-PO buyer-detail form.
 *
 *  Intentionally NOT PrLineItems: that component serves four pages and carries
 *  pickers, reordering, add/remove and validation that do not apply to a
 *  mirrored order whose quantities and prices come from NC.
 */
export function ImportedPoLineItems({ items, onChange, currency }: ImportedPoLineItemsProps) {
  const update = (index: number, patch: Partial<ImportedPoLine>) => {
    onChange(items.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200">
      <table className="w-full min-w-[880px] text-sm">
        <thead>
          <tr className="border-b border-neutral-200 bg-neutral-50 text-left text-xs text-neutral-500">
            <th className="w-10 px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">Description</th>
            <th className="w-32 px-3 py-2 font-medium">Material ID</th>
            <th className="w-24 px-3 py-2 text-right font-medium">Qty</th>
            <th className="w-20 px-3 py-2 font-medium">Unit</th>
            <th className="w-28 px-3 py-2 text-right font-medium">Unit Price</th>
            <th className="w-28 px-3 py-2 text-right font-medium">Line Total</th>
            <th className="w-40 px-3 py-2 font-medium">Supplier Item ID</th>
            <th className="w-32 px-3 py-2 font-medium">Sample (g or ea)</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => (
            <tr key={item.id} className="border-b border-neutral-100 last:border-0">
              <td className="px-3 py-2 text-neutral-400">{i + 1}</td>
              <td className="px-3 py-2 text-neutral-900">{item.description}</td>
              <td className="px-3 py-2 font-mono text-xs text-neutral-500">
                {item.materialId || '—'}
              </td>
              <td className="px-3 py-2 text-right font-mono text-neutral-700">{item.qty}</td>
              <td className="px-3 py-2 text-neutral-700">{item.unit}</td>
              <td className="px-3 py-2 text-right font-mono text-neutral-700">
                {formatAmount(item.unitPrice)}
              </td>
              <td className="px-3 py-2 text-right font-mono text-neutral-900">
                {currency} {formatAmount(item.lineTotal)}
              </td>
              <td className="px-3 py-2">
                <input
                  type="text"
                  value={item.supplierItemId}
                  onChange={(e) => update(i, { supplierItemId: e.target.value })}
                  placeholder="SKU / catalog #"
                  aria-label={`Supplier item ID for line ${i + 1}`}
                  className="h-8 w-full rounded border border-neutral-300 bg-white px-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary-600"
                />
              </td>
              <td className="px-3 py-2">
                <input
                  type="text"
                  value={item.sample}
                  onChange={(e) => update(i, { sample: e.target.value })}
                  placeholder="e.g. 500 g"
                  aria-label={`Sample requirement for line ${i + 1}`}
                  className="h-8 w-full rounded border border-neutral-300 bg-white px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600"
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: Confirm `formatAmount` exists with that signature**

```bash
cd /c/Project/uniops-nc-po-edit/epms
grep -n "export function formatAmount\|export const formatAmount" src/lib/utils.ts
```

Expected: one match taking a number. If its signature differs, adjust the two call sites above to match rather than changing `utils.ts`.

- [ ] **Step 3: Verify the type check is clean**

```bash
cd /c/Project/uniops-nc-po-edit/epms
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS" || echo 0
```

Expected: the Task 6 Step 1 baseline. (An unreferenced component still type-checks.)

- [ ] **Step 4: Commit**

```bash
cd /c/Project/uniops-nc-po-edit
git add epms/src/components/po/ImportedPoLineItems.tsx
git commit -m "feat(epms-ui): ImportedPoLineItems — locked line table with supplier ID and sample"
```

---

### Task 8: The imported-PO edit page

**Files:**
- Create: `epms/src/pages/po/PoImportedEditPage.tsx`
- Modify: `epms/src/app/routes.tsx`

**Interfaces:**
- Consumes: `useUpdatePoImportedDetails` and `ImportedDetailsBody` (Task 6), `ImportedPoLineItems` / `ImportedPoLine` (Task 7), the existing `usePo`, `useTaxCodes`, `useRegeneratePoPdf`.
- Produces: route `/po/:id/edit-imported`.

**Background:** after a successful save the page chains the **existing** `useRegeneratePoPdf(id)` mutation (`hooks/usePos.ts:106-112`) — `POST /po/{id}/attachments/regenerate-pdf` already deletes the stale `<number>.pdf` and re-uploads, and its `_PDF_STATUSES` already admits `issued`. Do not add a backend PDF trigger.

Tax rate follows the rule the other two PO forms use — `PoEditPage.tsx:105` computes `effectiveTaxRate = currency === 'CAD' ? taxRate : 0`. Keep that here so the three pages agree.

All user-facing copy is English.

- [ ] **Step 1: Create the page**

Create `epms/src/pages/po/PoImportedEditPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { ArrowLeft } from 'lucide-react'
import { epmsRoutes } from '@/app/routes'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { ImportedPoLineItems, type ImportedPoLine } from '@/components/po/ImportedPoLineItems'
import { usePo, useUpdatePoImportedDetails, useRegeneratePoPdf } from '@/hooks/usePos'
import { useTaxCodes } from '@/hooks/useTaxCodes'
import { formatAmount } from '@/lib/utils'

/** Buyer-detail form for an NC-imported PO.
 *
 *  Only reachable for source='nc' + status='issued'. Vendor, currency, title,
 *  type, budget code and every line quantity/price are display-only: NC owns
 *  them, and the backend endpoint has no field for them at all.
 */
export default function PoImportedEditPage() {
  const { id } = useParams<{ id: string }>()
  const replaceTab = useReplaceTab(epmsRoutes)
  const { data: po, isLoading } = usePo(id ?? '')
  const taxCodes = useTaxCodes()
  const saveDetails = useUpdatePoImportedDetails(id ?? '')
  const regeneratePdf = useRegeneratePoPdf(id ?? '')

  const [expectedDelivery, setExpectedDelivery] = useState('')
  const [deliveryAddress, setDeliveryAddress] = useState('')
  const [incoterms, setIncoterms] = useState('')
  const [taxCode, setTaxCode] = useState<string | null>(null)
  const [taxRate, setTaxRate] = useState(0)
  const [isPrepaid, setIsPrepaid] = useState(false)
  const [buyerNotes, setBuyerNotes] = useState('')
  const [lines, setLines] = useState<ImportedPoLine[]>([])
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!po) return
    setExpectedDelivery(po.expected_delivery ?? '')
    setDeliveryAddress(po.delivery_address ?? '')
    setIncoterms(po.incoterms ?? '')
    setTaxCode(po.tax_code ?? null)
    setTaxRate(Number(po.tax_rate))
    setIsPrepaid(po.is_prepaid ?? false)
    setBuyerNotes(po.buyer_notes ?? '')
    setLines(po.line_items.map((li) => ({
      id: li.id,
      description: li.description,
      materialId: li.material_id ?? null,
      qty: Number(li.qty),
      unit: li.unit,
      unitPrice: Number(li.unit_price),
      lineTotal: Number(li.line_total),
      supplierItemId: li.supplier_item_id ?? '',
      sample: li.sample ?? '',
    })))
  }, [po?.id])

  const editable = po?.source === 'nc' && po?.status === 'issued'
  // Same rule as PoCreatePage / PoEditPage — tax only applies to CAD orders.
  const effectiveTaxRate = po?.currency === 'CAD' ? taxRate : 0
  const subtotal = Number(po?.subtotal ?? 0)
  const taxAmount = Math.round(subtotal * effectiveTaxRate * 100) / 100
  const total = subtotal + taxAmount

  const handleSave = async () => {
    if (!id) return
    setIsSubmitting(true)
    setError(null)
    try {
      await saveDetails.mutateAsync({
        expected_delivery: expectedDelivery || null,
        delivery_address: deliveryAddress || null,
        incoterms: incoterms || null,
        tax_code: taxCode,
        tax_rate: po?.currency === 'CAD' ? taxRate : 0,
        is_prepaid: isPrepaid,
        buyer_notes: buyerNotes || null,
        lines: lines.map((l) => ({
          id: l.id,
          supplier_item_id: l.supplierItemId,
          sample: l.sample,
        })),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save changes')
      setIsSubmitting(false)
      return
    }
    // The PO data is saved at this point. A PDF failure is recoverable via the
    // Regenerate PDF button on the detail page, so it must not read as a
    // failed save.
    try {
      await regeneratePdf.mutateAsync()
    } catch {
      setError('Details saved, but the PO PDF could not be regenerated. '
        + 'Use "Regenerate PDF" on the PO page to retry.')
      setIsSubmitting(false)
      return
    }
    replaceTab(`/po/${id}`)
  }

  if (isLoading) return <div className="p-6 text-sm text-neutral-500">Loading…</div>
  if (!po) return <div className="p-6 text-sm text-neutral-500">PO not found.</div>

  if (!editable) {
    return (
      <div className="p-6">
        <p className="text-sm text-neutral-700">
          Only imported POs in status “issued” can be edited here.
        </p>
        <Link to={`/po/${po.id}`} className="mt-3 inline-flex items-center gap-1 text-sm text-primary-600">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to PO
        </Link>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div>
        <Link to={`/po/${po.id}`} className="inline-flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-900">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to PO
        </Link>
        <h1 className="mt-2 text-xl font-semibold text-neutral-900">
          Edit Details — {po.number}
        </h1>
        <p className="text-sm text-neutral-500">
          Imported from NC. Vendor, currency and line quantities/prices are read-only.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {error}
        </div>
      )}

      <section className="space-y-4 rounded-lg border border-neutral-200 p-4">
        <h2 className="text-base font-semibold text-neutral-900">Order</h2>
        <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
          <div><dt className="text-xs text-neutral-500">Vendor</dt><dd className="text-neutral-900">{po.vendor_name}</dd></div>
          <div><dt className="text-xs text-neutral-500">Currency</dt><dd className="text-neutral-900">{po.currency}</dd></div>
          <div><dt className="text-xs text-neutral-500">Title</dt><dd className="text-neutral-900">{po.title}</dd></div>
          <div><dt className="text-xs text-neutral-500">Budget Code</dt><dd className="text-neutral-900">{po.budget_code || '—'}</dd></div>
        </dl>

        <div className="grid gap-4 md:grid-cols-2">
          <FormField label="Expected Delivery" htmlFor="expectedDelivery">
            <Input id="expectedDelivery" type="date" value={expectedDelivery}
                   onChange={(e) => setExpectedDelivery(e.target.value)} />
          </FormField>
          <FormField label="Incoterms" htmlFor="incoterms">
            <Input id="incoterms" type="text" value={incoterms} maxLength={100}
                   placeholder="e.g. FOB Shanghai"
                   onChange={(e) => setIncoterms(e.target.value)} />
          </FormField>
        </div>

        <FormField label="Delivery Address" htmlFor="deliveryAddress">
          <textarea id="deliveryAddress" rows={2} value={deliveryAddress}
                    onChange={(e) => setDeliveryAddress(e.target.value)}
                    className="w-full rounded border border-neutral-300 bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600" />
        </FormField>

        <div className="grid gap-4 md:grid-cols-2">
          <FormField label="Tax Code" htmlFor="taxCode">
            <select
              id="taxCode"
              value={taxCode ?? ''}
              disabled={po.currency !== 'CAD'}
              onChange={(e) => {
                const chosen = (taxCodes.data ?? []).find((t) => t.code === e.target.value)
                setTaxCode(chosen?.code ?? null)
                setTaxRate(chosen ? Number(chosen.rate) : 0)
              }}
              className="h-9 w-full rounded border border-neutral-300 bg-white px-2 text-sm disabled:bg-neutral-50 disabled:text-neutral-400"
            >
              <option value="">No tax</option>
              {(taxCodes.data ?? []).map((t) => (
                <option key={t.code} value={t.code}>{t.code} — {(Number(t.rate) * 100).toFixed(2)}%</option>
              ))}
            </select>
          </FormField>
          <label className="flex items-end gap-2 pb-2 text-sm text-neutral-700">
            <input type="checkbox" checked={isPrepaid}
                   onChange={(e) => setIsPrepaid(e.target.checked)} />
            Prepaid order
          </label>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-neutral-200 p-4">
        <h2 className="text-base font-semibold text-neutral-900">Line Items</h2>
        <ImportedPoLineItems items={lines} onChange={setLines} currency={po.currency} />
        <div className="flex justify-end gap-6 text-sm">
          <span className="text-neutral-500">Subtotal <span className="ml-2 font-mono text-neutral-900">{formatAmount(subtotal)}</span></span>
          <span className="text-neutral-500">Tax <span className="ml-2 font-mono text-neutral-900">{formatAmount(taxAmount)}</span></span>
          <span className="font-semibold text-neutral-900">Total <span className="ml-2 font-mono">{po.currency} {formatAmount(total)}</span></span>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-neutral-200 p-4">
        <h2 className="text-base font-semibold text-neutral-900">Buyer Notes</h2>
        <FormField label="Buyer Notes / Terms &amp; Conditions" htmlFor="buyerNotes">
          <textarea id="buyerNotes" rows={4} value={buyerNotes}
                    onChange={(e) => setBuyerNotes(e.target.value)}
                    placeholder="Printed on the PO sent to the vendor."
                    className="w-full rounded border border-neutral-300 bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600" />
        </FormField>
      </section>

      <div className="flex justify-end gap-2">
        <Link to={`/po/${po.id}`}>
          <Button variant="secondary" type="button">Cancel</Button>
        </Link>
        <Button type="button" onClick={handleSave} disabled={isSubmitting}>
          {isSubmitting ? 'Saving…' : 'Save & Regenerate PDF'}
        </Button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Check the shapes this page assumes**

```bash
cd /c/Project/uniops-nc-po-edit/epms
grep -n "export function useTaxCodes" -A 12 src/hooks/useTaxCodes.ts
grep -n "useReplaceTab" src/pages/po/PoEditPage.tsx
grep -n "export" src/components/ui/form-field.tsx src/components/ui/input.tsx
```

Adjust the page to whatever these actually expose: in particular the tax-code item field names (`code` / `rate`) and whether `replaceTab` takes a path string. Follow `PoEditPage.tsx` for the `useReplaceTab` call convention — it is the closest sibling.

- [ ] **Step 3: Register the route**

In `epms/src/app/routes.tsx`, add the import next to the other PO page imports:

```tsx
import PoImportedEditPage from '@/pages/po/PoImportedEditPage'
```

and add this entry **above** the `/po/:id` entry (more specific paths first, matching how `/po/:id/edit` is already placed):

```tsx
  { path: '/po/:id/edit-imported', element: <PoImportedEditPage />, tab: { title: (p) => `Edit PO ${short(p.id)}`, icon: 'Package', keyStrategy: 'param', paramName: 'id' } },
```

- [ ] **Step 4: Verify the type check is clean**

```bash
cd /c/Project/uniops-nc-po-edit/epms
npx tsc -p tsconfig.app.json 2>&1 | grep "error TS" | head -20
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS" || echo 0
```

Expected: the Task 6 Step 1 baseline. Fix any new error in this page, not by loosening `tsconfig`.

- [ ] **Step 5: Lint**

```bash
cd /c/Project/uniops-nc-po-edit/epms
npm run lint 2>&1 | tail -20
```

Expected: no new errors relative to the baseline you saw in Task 6.

- [ ] **Step 6: Commit**

```bash
cd /c/Project/uniops-nc-po-edit
git add epms/src/pages/po/PoImportedEditPage.tsx epms/src/app/routes.tsx
git commit -m "feat(epms-ui): PoImportedEditPage for NC-imported PO buyer details"
```

---

### Task 9: PO detail page integration

**Files:**
- Modify: `epms/src/pages/po/PoDetailPage.tsx`

**Interfaces:**
- Consumes: `ApiPo.incoterms` / `.buyer_notes`, `ApiPoLineItem.sample` (Task 6), the route from Task 8, the matrix key from Task 4.
- Produces: nothing downstream. This is the last task.

**Background:** the existing gate is `const canEdit = isProcurementOfficer && po && ['draft', 'returned'].includes(po.status)` at `PoDetailPage.tsx:551`, and its button sits at `:643-649`. Permission reads follow `PaListPage.tsx:122-123`: `useRolePermissions().data?.permissions` plus an explicit `system_admin` short-circuit.

- [ ] **Step 1: Add the gate**

In `epms/src/pages/po/PoDetailPage.tsx`, immediately after the existing `canEdit` line, add:

```tsx
  // NC-imported POs never reach draft/returned, so canEdit above can never fire
  // for them. Buyer detail (supplier item IDs, samples, Incoterms, delivery,
  // notes) is filled in through a separate, deliberately narrow endpoint —
  // gated by the Access Control Matrix, not a hardcoded role list, so the
  // button and PATCH /po/{id}/imported-details cannot disagree.
  const perms = useRolePermissions().data?.permissions
  const canEditImported =
    !!po &&
    po.source === 'nc' &&
    po.status === 'issued' &&
    (user?.role === 'system_admin' || !!perms?.['epms.po.edit_imported'])
```

Add `useRolePermissions` to the existing `@/hooks/useConfig` import in this file (create the import if absent).

- [ ] **Step 2: Add the button**

Directly after the existing `{canEdit && (...)}` block (around `:643-649`), add:

```tsx
            {canEditImported && (
              <Button variant="secondary" size="sm" onClick={() => navigate(`/po/${po.id}/edit-imported`)}>
                <Pencil className="h-3.5 w-3.5" />
                Edit Details
              </Button>
            )}
```

- [ ] **Step 3: Show Incoterms and Buyer Notes**

In the PO header/meta area (the same block that renders Delivery Address and other header fields — find it with `grep -n "delivery_address" src/pages/po/PoDetailPage.tsx`), add, in this order so the page matches the PDF:

```tsx
            {po.incoterms && (
              <div>
                <dt className="text-xs text-neutral-500">Incoterms</dt>
                <dd className="text-sm text-neutral-900">{po.incoterms}</dd>
              </div>
            )}
            {po.buyer_notes && (
              <div className="col-span-full">
                <dt className="text-xs text-neutral-500">Buyer Notes</dt>
                <dd className="whitespace-pre-wrap text-sm text-neutral-900">{po.buyer_notes}</dd>
              </div>
            )}
```

Match the surrounding markup — if that area uses `div`/`span` rather than `dl`/`dt`/`dd`, follow what is already there instead of introducing a second pattern.

- [ ] **Step 4: Add the Sample column to the line table**

Find the line-items table with `grep -n "Supplier ID\|supplier_item_id" src/pages/po/PoDetailPage.tsx`. Add, above the JSX that renders the table:

```tsx
  // Mirrors the PDF: the Sample column only appears when some line carries one,
  // so POs without samples keep their existing layout.
  const showSample = !!po?.line_items.some((li) => li.sample)
```

Then add a conditional header cell after the Unit column:

```tsx
                {showSample && <th className="px-3 py-2 text-left font-medium">Sample</th>}
```

and the matching body cell in the same position inside the row map:

```tsx
                  {showSample && <td className="px-3 py-2 text-neutral-700">{li.sample || '—'}</td>}
```

Use the exact class names already on the neighbouring `th` / `td` elements in that table rather than the ones above.

- [ ] **Step 5: Verify the type check and lint are clean**

```bash
cd /c/Project/uniops-nc-po-edit/epms
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS" || echo 0
npm run lint 2>&1 | tail -20
```

Expected: the Task 6 Step 1 baseline, no new lint errors.

- [ ] **Step 6: Hand the manual UI check to the human partner**

**Do not attempt to run the dev stack.** `docker-compose.dev.yml` bind-mounts `./epms-api` and `./epms` **relative to whatever directory compose was invoked from**, and the containers currently running were started from other checkouts (`uniops_epms_api` ← `C:\Project\uniops-mrp-phase0`, `uniops_epms_frontend` ← `C:\Project\uniops`). Pointing a browser at `localhost:5173` therefore exercises *someone else's code*, and re-pointing the stack at this worktree would disrupt another branch's running environment.

Instead, write the checklist below into the task report verbatim so the human partner can run it after the branch is merged or deployed to a dev environment built from this branch. Mark this step complete once the checklist is in the report — and state plainly in the report that the UI was **not** functionally verified by you.

Checklist to hand over:

1. Log in as a user holding `erp_pa_officer` (or `system_admin`).
2. Open an NC-imported PO with `status = 'issued'` — the **Edit Details** button must be visible.
3. Open an NC PO with `status = 'closed'` or `'nc_milk'` — the button must be **absent**.
4. Log in as a plain `requester` and open the same issued NC PO — the button must be **absent**.
5. From Edit Details: confirm Vendor, Currency, Title, Budget Code and every line quantity/price render as text with no input; only Supplier Item ID, Sample and the header fields accept input.
6. Fill in Incoterms, Buyer Notes, one Supplier Item ID and one Sample, then Save. Confirm it returns to the detail page, the values show there, and the attachment list has a fresh `<PO number>.pdf`.
7. Download that PDF and confirm it shows the Sample column, an Incoterms line, and the Buyer Notes block — with Incoterms **above** Buyer Notes, and no `[NC …]` marker anywhere.

- [ ] **Step 7: Full backend regression sweep**

```bash
cd /c/Project/uniops-nc-po-edit/epms-api
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
POSTGRES_PASSWORD=<DB_PASSWORD> POSTGRES_DB=epms JWT_SECRET_KEY=test-secret-key \
python -m pytest tests -q 2>&1 | tail -15
```

Compare against **Global Constraints → Recorded baselines**. This suite carries a large pre-existing failure population and takes over 10 minutes; run it once, in the foreground, and read the summary line. **Never `git stash`.**

- [ ] **Step 8: Commit**

```bash
cd /c/Project/uniops-nc-po-edit
git add epms/src/pages/po/PoDetailPage.tsx
git commit -m "feat(epms-ui): PO detail shows Edit Details, Incoterms, Buyer Notes and Sample"
```

---

## Deployment notes (not part of any task)

- Migrations: epms-api `nc03_po_buyer_details`, identity-api `0006_po_edit_imported` → the release **must** run `migrate-prod.sh`.
- Images to rebuild: `epms-api`, `epms`, `identity-api`.
- After deploying, confirm in Portal Admin → Access Control that `Edit Imported (NC) POs` is ticked for **ERP PA Officer**. The migration seeds it, but verify rather than assume.
