# EPMS — System Design
**Version:** 2.2
**Date:** 2026-04-24
**PRD Reference:** v2.11

---

## 1. Tech Stack

### 1.1 Frontend (`epms`)

| Layer | Technology | Version |
|-------|-----------|---------|
| Build | Vite | 6.x |
| UI Framework | React | 18.x |
| Language | TypeScript | 5.x |
| Styling | Tailwind CSS | v4 (CSS-first `@theme {}`) |
| Auth State | Zustand + persist middleware | 5.x |
| Server State | TanStack Query (React Query) | 5.x |
| Routing | React Router | v7 |
| Forms | React Hook Form + Zod | 7.x / 3.x |
| Component Base | shadcn/ui (customised) | — |
| Icons | Lucide React | — |
| Portal Rendering | React DOM `createPortal` | built-in |

### 1.2 Backend (`epms-api`)

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | 0.115.x |
| Language | Python | 3.12 |
| ASGI Server | Uvicorn | 0.32.x |
| ORM | SQLAlchemy (async) | 2.0.x |
| DB Driver | asyncpg | 0.30.x |
| Database | PostgreSQL | 16 |
| Migrations | Alembic | 1.14.x |
| Schema Validation | Pydantic v2 | 2.10.x |
| Auth | python-jose (JWT) + pyotp (TOTP) + passlib (bcrypt) | — |
| Cache / Sessions | Redis (asyncio) | 5.x |
| PDF Generation | ReportLab | 4.2.x |
| HTTP Client | httpx | 0.28.x |
| File Handling | python-multipart | — |
| Email (SMTP) | aiosmtplib | planned (B11) |
| Task Scheduler | APScheduler or Celery Beat | planned (B11) |

---

## 2. Architecture Overview

```
Browser (React SPA)
    │
    │  REST API (JSON) over HTTPS
    ▼
FastAPI  (/api/v1/*)
    │
    ├── Auth middleware (JWT bearer token)
    ├── RBAC middleware (role + permission checks)
    │
    ├── Routers
    │   ├── /auth              login, refresh, MFA verify
    │   ├── /users             user CRUD, profile, notification preference
    │   ├── /pr                PR CRUD + workflow actions
    │   ├── /pr/{id}/attachments   file up/download
    │   ├── /po                PO CRUD + workflow actions + place-order
    │   ├── /gr                GR CRUD + acknowledgement + collection
    │   ├── /invoices          invoice upload, OCR, matching
    │   ├── /pa                PA CRUD + workflow actions (Standard/Prepayment/Settlement)
    │   ├── /budget            L1/account CRUD + CSV import/export
    │   ├── /tasks             task inbox (read + complete)
    │   ├── /vendors           vendor master CRUD
    │   ├── /cost-centers      cost center master data
    │   ├── /departments       department master data
    │   ├── /parts             parts catalog CRUD
    │   ├── /projects          project master data CRUD
    │   ├── /config            company config (singleton) + role management
    │   ├── /dashboard         role-specific KPIs + pending approvals
    │   ├── /notifications     notification log (planned B11)
    │   └── /reports           CSV exports
    │
    ├── PostgreSQL (via SQLAlchemy async + asyncpg)
    ├── Redis (session cache, MFA token store)
    │
    └── Background Services (planned B11)
        ├── NotificationDispatcher  — immediate async dispatch on task create
        └── DailyFollowUpScheduler  — 08:00 daily job, re-notifies open tasks
```

---

## 3. Frontend Project Structure

```
src/
├── App.tsx                     # Route definitions
├── main.tsx
├── index.css                   # Tailwind @theme + global base styles
│
├── types/
│   └── index.ts                # Shared TypeScript interfaces & enums
│
├── stores/
│   ├── auth.store.ts           # Session token, MFA state (persisted)
│   └── user.store.ts           # Current user role/permissions
│
├── lib/
│   ├── api.ts                  # fetch wrapper with auth header + 401 refresh
│   ├── utils.ts                # cn(), formatCAD(), formatAmount(), etc.
│   └── email-template.ts       # TEMPLATE_VARIABLE_DOCS — variable reference for Admin Panel
│
├── services/                   # API call functions (one file per domain)
│   ├── auth.ts
│   ├── pr.ts
│   ├── po.ts
│   ├── gr.ts
│   ├── invoices.ts
│   ├── pa.ts
│   ├── budget.ts               # getSummary() — lightweight CC summaries; getL1ByCostCenter(id) — per-CC lazy detail; getOverview() — all CCs (admin panel / all-CC view)
│   ├── tasks.ts
│   ├── vendors.ts
│   ├── departments.ts
│   ├── costCenters.ts
│   ├── parts.ts
│   ├── projects.ts
│   ├── prAttachments.ts
│   ├── users.ts
│   └── config.ts
│
├── hooks/                      # TanStack Query hooks (one file per domain)
│   ├── usePrs.ts               # usePrs, usePr, usePrEvents, useCreatePr, useUpdatePr, usePrAction
│   ├── usePos.ts
│   ├── useGrs.ts
│   ├── useInvoices.ts
│   ├── usePas.ts
│   ├── useBudget.ts            # useBudgetSummary, useBudgetCCDetail, useBudgetAllDetail, useBudgetOverview, useImportBudget, useUpdateBudgetAccount
│   ├── useTasks.ts
│   ├── useVendors.ts
│   ├── useDepartments.ts
│   ├── useCostCenters.ts
│   ├── useParts.ts
│   ├── useProjects.ts
│   ├── useUsers.ts
│   ├── useConfig.ts            # useConfig, useUpdateConfig, useCreateTempAssignment, useDeleteTempAssignment
│   ├── useDashboard.ts
│   ├── usePrAttachments.ts
│   └── useIdleTimeout.ts
│
├── components/
│   ├── layout/
│   │   ├── AppLayout.tsx       # Sidebar + Header shell
│   │   ├── Sidebar.tsx         # Dark teal nav, company branding
│   │   └── Header.tsx          # Breadcrumb, notifications badge, user menu
│   ├── ui/                     # Design system primitives
│   │   ├── button.tsx
│   │   ├── input.tsx
│   │   ├── badge.tsx           # StatusBadge component
│   │   └── form-field.tsx
│   ├── pr/
│   │   ├── PrLineItems.tsx     # Line items table + PartsPicker (Type 3)
│   │   ├── ProcurementTypeSelector.tsx
│   │   ├── BudgetBalanceWidget.tsx
│   │   └── ApprovalTimeline.tsx
│   ├── po/
│   │   └── PlaceOrderComposer.tsx  # Email Composer + Online confirmation panel (§14)
│   └── dashboard/
│       ├── StatCard.tsx
│       ├── PendingApprovals.tsx    # Approve/Return/Reject buttons (useMutation → prService.action)
│       └── BudgetOverview.tsx
│
└── pages/
    ├── auth/
    │   ├── LoginPage.tsx
    │   └── MfaPage.tsx
    ├── profile/
    │   └── ProfilePage.tsx         # Display name, email, Teams, notification channel pref, change password, MFA
    ├── dashboard/
    │   ├── DashboardRouter.tsx     # Role-based routing
    │   ├── RequesterDashboard.tsx
    │   ├── ApproverDashboard.tsx
    │   ├── ProcurementDashboard.tsx
    │   ├── WarehouseDashboard.tsx
    │   ├── ApClerkDashboard.tsx
    │   ├── FinanceManagerDashboard.tsx
    │   ├── FinanceBpDashboard.tsx
    │   ├── VendorManagerDashboard.tsx
    │   └── SystemAdminDashboard.tsx
    ├── pr/
    │   ├── PrListPage.tsx
    │   ├── PrCreatePage.tsx
    │   ├── PrDetailPage.tsx
    │   └── PrEditPage.tsx
    ├── po/
    │   ├── PoListPage.tsx
    │   ├── PoCreatePage.tsx
    │   └── PoDetailPage.tsx        # Includes Place Order action + Document Tree
    ├── gr/
    │   ├── GrListPage.tsx
    │   ├── GrCreatePage.tsx
    │   ├── GrDetailPage.tsx
    │   ├── CollectionConfirmPage.tsx
    │   └── ServiceGrConfirmPage.tsx
    ├── invoices/
    │   ├── InvoiceListPage.tsx
    │   └── InvoiceDetailPage.tsx
    ├── pa/
    │   ├── PaListPage.tsx
    │   ├── PaCreatePage.tsx
    │   ├── PaDetailPage.tsx        # "returned" status support; Edit button for draft/returned; "Mark as Processed" (ap_clerk only)
    │   ├── PaEditPage.tsx          # Edit form for draft/returned PAs; PO locked; Save Draft + Save & Resubmit
    │   └── SettlementTaskPage.tsx
    ├── budget/
    │   └── BudgetDashboard.tsx
    ├── tasks/
    │   └── TaskInboxPage.tsx
    ├── parts/
    │   └── PartsListPage.tsx
    ├── vendors/
    │   └── VendorsPage.tsx
    ├── projects/
    │   └── ProjectsPage.tsx
    ├── reports/
    │   └── ReportCentrePage.tsx
    └── admin/
        └── AdminPanel.tsx          # All admin sections incl. Role Management (§3.6.7)
```

---

## 4. Backend Project Structure

```
app/
├── main.py                     # FastAPI app factory, router registration, CORS
├── db/
│   └── base.py                 # Base, UUIDPrimaryKey, TimestampMixin
│
├── models/                     # SQLAlchemy ORM models
│   ├── user.py                 # User + notification_channel field
│   ├── department.py
│   ├── cost_center.py
│   ├── vendor.py               # Vendor + poid field (unique, 1–3 chars)
│   ├── pr.py                   # PurchaseRequest + PrLineItem
│   ├── pr_attachment.py        # PrAttachment (binary blob in DB)
│   ├── po.py                   # PurchaseOrder + PoLineItem + PoAttachment
│   ├── gr.py                   # GoodsReceipt + GrLineItem
│   ├── invoice.py              # Invoice + InvoiceLineItem
│   ├── pa.py                   # PaymentApplication + PaLineItem
│   ├── budget.py               # BudgetL1 + BudgetAccount
│   ├── task.py                 # Task (unified task inbox)
│   ├── approval.py             # ApprovalEvent (immutable audit log)
│   ├── config.py               # CompanyConfig (singleton) + TempAssignment
│   ├── part.py                 # Part (Parts Catalog)
│   ├── project.py              # Project + status lifecycle
│   └── notification_log.py     # NotificationLog — delivery attempts (planned B11)
│
├── schemas/                    # Pydantic v2 schemas (request/response)
│   ├── user.py
│   ├── pr.py                   # PrCreate, PrUpdate, PrResponse, PrActionRequest, ApprovalEventResponse
│   ├── po.py                   # PoCreate, PoUpdate, PoResponse, PoActionRequest, PlaceOrderRequest
│   ├── gr.py
│   ├── invoice.py
│   ├── pa.py
│   ├── budget.py               # BudgetL1Response, BudgetAccountResponse, CsvImportRow, ImportResult
│   ├── task.py
│   ├── config.py               # CompanyConfigResponse, WorkflowNodeDef, PdfTemplateSettings, RolePermissionMatrix
│   └── vendor.py
│
├── crud/                       # Database operations + domain logic
│   ├── pr.py                   # workflow engine, _resolve_names, _attach_pr_pdf, _create_task, _complete_tasks
│   ├── po.py                   # workflow engine, _attach_po_pdf (planned), _generate_po_number
│   ├── gr.py
│   ├── invoice.py              # OCR extraction, auto-match, 3-way match
│   ├── pa.py                   # Standard/Prepayment/Settlement PA logic
│   ├── budget.py               # get_l1_all, import_from_csv, export_to_csv, get_summary
│   ├── user.py
│   ├── vendor.py
│   ├── project.py
│   ├── task.py
│   └── config.py
│
├── api/v1/                     # FastAPI routers
│   ├── auth.py
│   ├── users.py
│   ├── pr.py
│   ├── pr_attachments.py
│   ├── po.py                   # includes POST /po/{id}/place-order
│   ├── gr.py
│   ├── invoices.py
│   ├── pa.py
│   ├── budget.py
│   ├── tasks.py
│   ├── config.py
│   ├── dashboard.py
│   ├── departments.py
│   ├── cost_centers.py
│   ├── vendors.py
│   ├── parts.py
│   ├── projects.py
│   ├── notifications.py        # planned B11
│   └── reports.py
│
├── services/
│   ├── pdf_pr.py               # ReportLab PDF — approved PR
│   ├── pdf_po.py               # ReportLab PDF — approved PO (planned)
│   ├── pdf_gr.py               # ReportLab PDF — confirmed GR (planned)
│   ├── pdf_pa.py               # ReportLab PDF — approved PA (planned)
│   ├── email.py                # SMTP dispatch via aiosmtplib (planned B11)
│   ├── teams.py                # Teams Adaptive Card via Webhook (planned B11)
│   └── notification.py         # Unified dispatcher — routes to email/teams per user pref (planned B11)
│
└── tasks/                      # Background / scheduled jobs
    └── daily_followup.py       # APScheduler job — 08:00 daily, re-notifies open tasks (planned B11)

alembic/
└── versions/                   # Migration files (chronological)
    ├── ...initial schema...
    └── e5f6a7b8c9d1_add_cost_center_department_name_to_pr.py
```

---

## 5. Key Data Models

### 5.1 `PurchaseRequest` (backend ORM)
```python
class PurchaseRequest(UUIDPrimaryKey, TimestampMixin, Base):
    number: str                    # PR-YYYYMMDD-NNNN (assigned at submit)
    title: str
    type: int                      # 1–6 (procurement type)
    status: str                    # draft|submitted|in_review|approved|returned|rejected|cancelled
    currency: str
    amount: Decimal                # sum of line_item.line_total

    vendor_id: UUID | None
    vendor_name: str | None        # denormalised at creation time via _resolve_names()

    cost_center_id: UUID | None
    cost_center_name: str | None   # denormalised at creation time
    department_name: str | None    # denormalised at creation time

    budget_code: str | None        # L2 budget account code
    over_budget: bool              # True if projected balance < 0 at submission
    over_budget_justification: str | None

    required_by: date | None
    delivery_address: str | None
    notes: str | None
    submitted_at: datetime | None
    approval_step_idx: int         # current workflow step (0-indexed)
    po_id: UUID | None
    po_number: str | None
    created_by: UUID               # FK to users

    line_items: list[PrLineItem]   # selectin loaded
```

### 5.2 `PrLineItem` (backend ORM)
```python
class PrLineItem(UUIDPrimaryKey, Base):
    pr_id: UUID
    description: str
    material_id: str | None
    supplier_item_id: str | None   # e.g. Amazon ASIN, supplier catalog #
    qty: Decimal
    unit: str
    unit_price: Decimal
    line_total: Decimal
    notes: str | None
    sort_order: int
```

### 5.3 `PrAttachment` / `PoAttachment` (backend ORM pattern)
```python
class PrAttachment(UUIDPrimaryKey, TimestampMixin, Base):
    pr_id: UUID                    # FK → purchase_requests (CASCADE delete)
    filename: str                  # e.g. "PR-20260101-0001.pdf"
    content_type: str              # "application/pdf" | "image/png" | ...
    file_size: int                 # bytes
    file_data: bytes               # LargeBinary — stored in PostgreSQL

# PoAttachment, GrAttachment, PaAttachment follow the same pattern
```

### 5.4 `ApprovalEvent` (backend ORM — immutable audit log)
```python
class ApprovalEvent(UUIDPrimaryKey, Base):
    document_type: str             # "pr" | "po" | "pa"
    document_id: UUID
    document_number: str
    step_idx: int
    action: str                    # submit|approve|return|reject|recall|cancel
    actor_id: UUID
    actor_role: str
    comment: str | None
    created_at: datetime           # server UTC, never editable
```

### 5.5 `Task` (backend ORM — unified task inbox)
```python
class Task(UUIDPrimaryKey, TimestampMixin, Base):
    type: str                      # approve_pr|approve_po|approve_pa|process_pa|
                                   # revise_pr|revise_pa|revise_po|
                                   # gr_acknowledgement|goods_collection|service_gr_confirm|
                                   # settle_prepayment|link_invoice|create_pa|
                                   # create_po|place_order|collection_discrepancy
    priority: str                  # normal | urgent
    document_type: str             # pr|po|gr|invoice|pa
    document_id: UUID
    document_number: str
    assigned_role: str | None      # route by role (if no specific user)
    assigned_user_id: UUID | None  # route to specific user
    title: str
    description: str | None
    amount: Decimal | None
    vendor: str | None
    is_completed: bool
    completed_at: datetime | None
    due_date: datetime | None      # SLA deadline
```

### 5.6 `Vendor` (backend ORM)
```python
class Vendor(UUIDPrimaryKey, TimestampMixin, Base):
    poid: str                      # 1–3 uppercase alphanumeric, unique — used in PO numbering
    name: str
    category: str
    contact_email: str | None      # used by Place Order via Email (§14)
    contact_name: str | None
    contact_phone: str | None
    payment_terms: str | None
    bank_details: str | None       # encrypted at rest (AES-256)
    max_prepayment_pct: Decimal | None
    is_active: bool
```

### 5.7 `CompanyConfig` (backend ORM — singleton)
```python
class CompanyConfig(Base):
    # Company identity
    name: str
    tagline: str
    logo_data_url: str | None
    delivery_address: str
    default_currency: str          # e.g. "CAD"
    enabled_currencies: list[str]  # JSONB — ["CAD", "USD", "EUR", "CNY", ...]
    custom_currencies: list[dict]  # JSONB — [{"value": "GBP", "label": "British Pound", "symbol": "£"}, ...]

    # Auth / security
    mfa_enabled: bool
    smtp_config: dict              # JSONB — host, port, username, password, from_addr, use_tls
    password_policy: dict          # JSONB — expiry_days (0 = never)

    # Workflow & config
    workflow_defs: dict            # JSONB — {"pr": [...nodes], "po": [...], "pa": [...]}
    pdf_templates: dict            # JSONB — {"pr": {show_logo, header_note, ...}, "po": {...}, ...}
    email_templates: dict          # JSONB — {"pr_approval_request": {subject, body}, ...}
    budget_admin_config: dict      # JSONB — fiscal_year_start/end, yellow_pct, red_pct
    service_gr_sla: dict           # JSONB — reminder/escalation day thresholds
    gr_notification_sla: dict      # JSONB — ack reminder/escalation days
    collection_config: dict        # JSONB — enabled, reminder/escalation days
    prepayment_config: dict        # JSONB — max_pct, settlement_sla_days, ...
    notification_settings: dict    # JSONB — default_channel, teams_webhook_url
    role_permissions: dict         # JSONB — Access Control Matrix per role (§16)
    dept_gm_opm_mapping: dict      # JSONB — {dept_id: "gm"|"opm", ...}
```

### 5.8 `BudgetL1` + `BudgetAccount` (backend ORM)
```python
class BudgetL1(UUIDPrimaryKey, Base):
    # Unique constraint: (code, cost_center_id) — same L1 code can exist in different CCs
    code: str                      # L1 code (e.g. "CRM001 TRAVEL"); unique per cost center
    name: str
    is_active: bool
    cost_center_id: UUID           # FK to cost_centers
    accounts: list[BudgetAccount]  # selectin loaded, ordered by code ASC

class BudgetAccount(UUIDPrimaryKey, Base):
    # Unique constraint: (code, l1_id) — same account code can exist across different L1s (different CCs)
    code: str                      # L2 account code (e.g. "CRM00101"); unique per L1 group
    name: str
    is_active: bool
    l1_id: UUID                    # FK to budget_l1
    annual_budget: Decimal
    committed: Decimal             # updated on PR approval / PA payment
    # actual_spent can be negative (credit notes, refunds)
    actual_spent: Decimal          # updated on PA paid
```

### 5.9 Frontend `ApiPr` type
```ts
interface ApiPr {
  id: string
  number: string
  title: string
  type: number                     // 1–6
  status: PrStatus                 // draft|submitted|in_review|approved|returned|rejected|cancelled
  currency: string
  amount: number
  vendor_id?: string
  vendor_name?: string
  cost_center_id?: string
  cost_center_name?: string        // denormalised
  department_name?: string         // denormalised
  budget_code?: string
  over_budget?: boolean
  over_budget_justification?: string
  required_by?: string
  delivery_address?: string
  notes?: string
  submitted_at?: string
  approval_step_idx: number
  line_items: ApiPrLineItem[]
  po_id?: string
  po_number?: string
  created_at: string
  updated_at: string
}
```

### 5.10 Frontend `ApiBudgetL1` / `ApiBudgetAccount` types
```ts
interface ApiBudgetL1 {
  id: string
  code: string
  name: string
  is_active: boolean
  cost_center_id: string
  cost_center_code: string         // resolved via model_validator in BudgetL1Response
  accounts: ApiBudgetAccount[]
}

interface ApiBudgetAccount {
  id: string
  code: string
  l1_id: string
  l1_code: string                  // injected by frontend service when flattening
  name: string
  annual_budget: number            // converted from Decimal string via Number()
  committed: number
  actual_spent: number
  is_active: boolean
}
```

---

## 6. Document Approval Workflow Engine

Shared by PR, PO, and PA. Each document type has its own workflow definition stored in `CompanyConfig.workflow_defs[doc_type]`.

### 6.1 Workflow Definition
```json
// Example: PO workflow
[
  { "id": "procurement_manager", "role": "procurement_manager", "label": "Procurement Manager" },
  { "id": "gm_or_opm",           "role": "gm_or_opm",           "label": "GM / OPM" }
]

// Example: PR workflow
[
  { "id": "dept_manager", "role": "dept_manager", "label": "Department Manager" },
  { "id": "gm_or_opm",   "role": "gm_or_opm",   "label": "GM / OPM" }
]
```

Fallback (empty workflow): `[{ "id": "dept_manager", "role": "dept_manager", "label": "Department Manager" }]`

### 6.2 Role Token Resolution

| Token | Resolution Logic |
|-------|-----------------|
| `dept_manager` | `Task.assigned_user_id` = None; `Task.assigned_role = "dept_manager"` — frontend filters by `user.department_id` |
| `gm` | Lookup `CompanyConfig.role_management.gm_user_id` |
| `opm` | Lookup `CompanyConfig.role_management.opm_user_id` |
| `gm_or_opm` | At runtime: look up `document.department_id` in `CompanyConfig.dept_gm_opm_mapping` → resolve to `"gm"` or `"opm"` → then apply that token's lookup |
| `procurement_manager` | Lookup assigned procurement_manager user |
| `finance_manager` | Lookup assigned finance_manager user |
| `finance_bp` | `assigned_role = "finance_bp"` — any user with finance_bp role |

### 6.3 State Machine (all document types)
```
draft ──submit──► submitted ──approve──► in_review ──approve──► ... ──approve(final)──► approved
                     │                      │
                     ├──return──► returned ─┘  (creates revise task for requester)
                     ├──reject──► rejected
                     ├──cancel──► cancelled
                     └──recall──► draft        (clears tasks, resets step_idx)
```

### 6.4 On Final Approval — Per Document

| Document | Backend Action |
|----------|---------------|
| PR | `_attach_pr_pdf()` — ReportLab in thread executor → `PrAttachment`; `_create_create_po_task()` → `create_po` Task for all Procurement Officers |
| PO | `_create_place_order_task()` → `place_order` Task for Procurement Officer |
| PA | `_attach_pa_pdf()` → `PaAttachment`; `_create_process_pa_task()` → `process_pa` Task for `ap_clerk` role |

### 6.5 Task Generation
```python
# On submit / approve (non-final):
Task(type="approve_{doc_type}", assigned_role=workflow_node["role"], ...)

# On return:
Task(type="revise_{doc_type}", assigned_user_id=doc.created_by, assigned_role="requester", ...)

# On PO final approval:
Task(type="place_order", assigned_role="procurement_officer", ...)

# On PA final approval:
Task(type="process_pa", assigned_role="ap_clerk", ...)

# On PA "process" action (AP Clerk marks payment done):
# - completes the process_pa task
# - marks all linked invoices as "paid"

# Previous open tasks completed before creating new ones:
for task in open_tasks: task.is_completed = True; task.completed_at = now
```

### 6.6 PR Over-Budget Pre-Approval Steps

When a PR has `over_budget=True` (set by `epms-api/app/crud/pr.py:_compute_over_budget()` whenever a PR is created or updated and its projected balance `annual_budget − committed − actual_spent − amount` < 0), the shared approval engine reads `CompanyConfig.budget_admin_config.over_budget_mode` and applies one of three behaviours:

| Mode | Steps prepended to the configured `workflow_defs["pr"]` chain |
|------|---------------------------------------------------------------|
| `fm_gm_opm` (default) | `OB-1: finance_manager` → `OB-2: gm_or_opm` |
| `fm_only` | `OB-1: finance_manager` |
| `hard_block` | None — submission is rejected at `submit` time with HTTP 4xx (frontend disables Submit and shows "Reduce budget code or split PR"). |

**Implementation location:** the over-budget injection lives in `approval-api/app/crud/engine.py:execute_action()`, applied right after `_get_workflow(db, "pr")` so every branch (`submit`, `approve`, `return`, …) walks the same step list. The injected steps carry the ids `ob_finance_manager` / `ob_gm_or_opm` (used by `_create_approve_task` to enrich the task description with the requester's justification — OBG-002).

The injected pre-approval steps are **not** persisted in `CompanyConfig.workflow_defs["pr"]` — they are runtime-only and depend on the PR's current `over_budget` flag and the current mode.

The `over_budget_mode` configuration is the **single source of truth** stored at `BudgetAdminConfig.over_budget_mode` (configured in EPMS Admin → Budget Config → Over-Budget Approval Mode). The duplicate `WorkflowConfig.over_budget_mode` field was removed in 2026-05.

See PRD §3.2.6 (OBG-001~009) for the functional spec.

### 6.7 Multi-Role Auto-Skip

When a single user is assigned to multiple consecutive steps (per `CompanyConfig.role_management`), the engine cascades through all such steps automatically after one approval action.

**Algorithm (`crud/pr.py`, `crud/po.py`, `crud/pa.py` — `approve` branch):**
```python
# After recording the primary ApprovalEvent for step N:
next_step = step + 1
while next_step < len(workflow) and _actor_holds_role(workflow[next_step]["role"]):
    db.add(ApprovalEvent(
        step_idx=next_step,
        action="approve",
        actor_id=actor_id,
        actor_role=workflow[next_step]["role"],
        comment="Auto-approved (same approver holds both roles)",
    ))
    next_step += 1

# Then advance to next_step (or set status="approved" if past final step)
```

**`_actor_holds_role(role)` resolution per document type:**

| Workflow | `dept_manager` | other roles |
|----------|---------------|-------------|
| PR | `actor_id == _get_dept_manager_id(db, pr.created_by, "dept_manager")` | Lookup in `role_management` |
| PO | N/A (no dept_manager step in default PO workflow) | Lookup in `role_management` |
| PA | N/A | Lookup in `role_management` |

`role_management` lookup: checks `gm_user_id`, `opm_user_id`, `finance_manager_user_id`, `procurement_manager_user_id`, `vendor_manager_user_id`, and `finance_bp_user_ids` from `CompanyConfig.role_management`.

**Audit:** every auto-skipped step produces its own `ApprovalEvent` row — full history preserved and shown in Approval Timeline with "Auto-approved" indicator.

**System Admin exclusion:** `system_admin` is never assigned as a workflow approver and is excluded from `canApprove` on both frontend and backend.

---

## 7. PDF Generation

**Library:** ReportLab 4.2.x (pure Python, no system dependencies)
**Pattern:** Synchronous function called in `asyncio.get_event_loop().run_in_executor(None, fn, args)` to avoid blocking the async event loop.

### 7.1 PR PDF (`app/services/pdf_pr.py`)

**Trigger:** `crud/pr.py:action()` on final `approve` action.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│  {Company Name}              {PR-YYYYMMDD-NNNN}      │
│  Purchase Request                PURCHASE REQUEST    │
├─────────────────────────────────────────────────────┤
│  PR Number │ Date  │ Type      │ Currency            │
│  Title     │ Vendor│ Dept      │ Req. By             │
│  Cost Ctr  │ Budget Code                             │
├─────────────────────────────────────────────────────┤
│  LINE ITEMS TABLE                                    │
│  # │ Description │ [Material ID] │ [Supplier ID]    │
│    │ Qty │ Unit   │ Unit Price   │ Total             │
├─────────────────────────────────────────────────────┤
│                       Total: {currency} {amount}    │
├─────────────────────────────────────────────────────┤
│  Notes (if any)                                     │
│  Delivery Address (if any)                          │
├─────────────────────────────────────────────────────┤
│  Generated {timestamp} · Status: Approved           │
└─────────────────────────────────────────────────────┘
```

- Conditional columns: Material ID and Supplier ID shown only when ≥1 line item has a value
- Template settings read from `CompanyConfig.pdf_templates["pr"]` (show_logo, header_note, footer_note, show_terms, terms_text)
- Stored as `LargeBinary` in `PrAttachment`; served via `GET /pr/{id}/attachments/{att_id}/download`

### 7.2 PO / GR / PA PDFs (planned — B11 / B12)

`pdf_po.py`, `pdf_gr.py`, `pdf_pa.py` — same ReportLab pattern. Template settings from `CompanyConfig.pdf_templates["po"|"gr"|"pa"]`.

- **PO PDF**: saved as `PoAttachment`; Procurement Officer attaches to Place Order email (§14)
- **GR PDF**: saved on GR confirmation
- **PA PDF**: saved on PA final approval

---

## 8. Budget Dashboard Data Flow

### 8.1 Performance-First Loading Strategy

Loading all L1 groups + accounts at once (21 CCs × 20 L1s × 20 accounts ≈ 8,000+ objects) caused page freeze. Replaced with a two-tier approach:

```
Page Load
    │
    ├─► GET /budget/summary          ← lightweight: ~21 CC-level aggregates
    │       │
    │       ▼
    │   useBudgetSummary()           staleTime: 60s
    │   → summary cards (Total Budget / Committed / Spent / Available)
    │   → L1-level over-budget / near-budget alerts
    │   → Cost Center filter dropdown options
    │
    └─► (lazy, triggered by user selecting a CC)
        │
        ├─► filterCcCode === 'all'
        │       GET /budget/l1       ← full dataset, fetched once, cached 60s
        │       useBudgetAllDetail(enabled=true)
        │       → frontend aggregates accounts by code across CCs
        │         (Number() conversion required — Decimal → string in JSON)
        │
        └─► filterCcCode === 'GA-0100' (specific CC)
                GET /budget/l1?cost_center_id=xxx
                useBudgetCCDetail(ccId)   staleTime: 30s, cached per CC
                → account detail for that CC only
```

### 8.2 Role-Based Access

```
isFullAccess = FULL_ACCESS_ROLES.has(user.role)
             OR user.id matches any special role in CompanyConfig.role_management

FULL_ACCESS_ROLES = {gm, opm, finance_manager, finance_bp, ap_clerk, system_admin, cfo, auditor}

isFullAccess → all cost centers visible, no CC/dept filter required
otherwise   → filter by user.department_id (UUID match, not name string)
```

### 8.3 All-CC Account Aggregation

When "All Cost Centers" is selected, accounts with the same `code` across different CCs are summed:

```typescript
// Pydantic Decimal → JSON string → must convert with Number() before arithmetic
for (const l1 of allDetail.l1_groups) {
  for (const acct of l1.accounts) {
    const budget = Number(acct.annual_budget)  // "500.00" → 500
    const existing = map.get(acct.code)
    if (existing) {
      existing.annual_budget += budget         // safe: both are JS numbers
    } else {
      map.set(acct.code, { ...acct, annual_budget: budget, ... })
    }
  }
}
```

**Key design decisions:**
- Pydantic v2 serialises `Decimal` → string in JSON. JS `+=` on strings gives NaN or concatenation. Fix: always `Number()` at point of use.
- `BudgetL1.code` is unique per `cost_center_id` (not globally). Same L1 code (e.g. `CRM001 TRAVEL`) exists independently for each CC.
- `BudgetAccount.code` is unique per `l1_id`. Same account code (e.g. `CRM00101`) can exist in CRM001 TRAVEL for GA-0100 and also for GA-0101.

### 8.4 Budget CSV Import Rules

- **Account code uniqueness:** per `(code, l1_id)` — same code allowed across different cost centers
- **L1 code uniqueness:** per `(code, cost_center_id)` — same L1 code allowed across different cost centers
- **Negative `actual_spent`:** permitted (credit notes, refunds); `ge=0` constraint removed from `CsvImportRow` and `BudgetAccountUpdate`
- **Duplicate detection in CSV:** keyed on `(acct_code, l1_code, cc_code)` — true duplicates within the same L1/CC are skipped with an error message

---

## 9. Admin Panel — Reset Password Design

**Problem:** Inline password input in a table row caused browser password manager to autofill the user search box.

**Solution:** `createPortal(modal, document.body)` renders the reset dialog outside the table DOM:
- `autoComplete="new-password"` on the password input (prevents autofill association)
- Supports Enter key to confirm
- `resetTargetUser` derived from `resetTarget` state + users list (avoids stale closure)

---

## 10. Routing (`src/App.tsx`)

```
/login                          LoginPage
/mfa                            MfaPage
/ (AppLayout shell)
  /profile                      ProfilePage  (notification pref, password, MFA)
  /dashboard                    DashboardRouter → role-specific component
  /tasks                        TaskInboxPage
  /pr                           PrListPage
  /pr/new                       PrCreatePage
  /pr/:id                       PrDetailPage
  /pr/:id/edit                  PrEditPage
  /po                           PoListPage
  /po/new                       PoCreatePage
  /po/:id                       PoDetailPage  (includes Place Order action)
  /gr                           GrListPage
  /gr/new                       GrCreatePage
  /gr/:id                       GrDetailPage
  /gr/:id/collect               CollectionConfirmPage
  /gr/:id/service-confirm       ServiceGrConfirmPage
  /invoices                     InvoiceListPage
  /invoices/:id                 InvoiceDetailPage
  /pa                           PaListPage
  /pa/new                       PaCreatePage
  /pa/:id                       PaDetailPage
  /pa/:id/edit                  PaEditPage  (draft/returned PAs only)
  /pa/:id/settle                SettlementTaskPage
  /budget                       BudgetDashboard
  /reports                      ReportCentrePage
  /vendors                      VendorsPage
  /projects                     ProjectsPage
  /parts                        PartsListPage
  /admin                        AdminPanel
```

---

## 11. Authentication Flow

```
POST /auth/login  {email, password}
    │
    ├── Returns {access_token, refresh_token, user, mfa_required}
    │
    ├── if mfa_required → navigate('/mfa')
    │   POST /auth/mfa/verify {mfa_token, code}
    │   → Returns same token structure + sets mfa_verified=true in auth store
    │
    └── setUser(user, access_token, refresh_token)
        navigate('/dashboard')

Token refresh:
    api.ts interceptor catches 401
    → POST /auth/refresh {refresh_token}
    → Updates tokens in auth store
    → Retries original request
```

---

## 12. CSS Design System (`src/index.css`)

### Tailwind v4 CSS-first theme
```css
--color-primary-600: #0A7C7C;   /* Buttons, active nav, links */
--color-primary-700: #085E5E;   /* Sidebar background */
--color-primary-50:  #E5F2F2;   /* Hover states, selected rows */
--color-neutral-900: #1A2730;   /* Primary text (cool-tinted) */
--color-neutral-200: #D9DFE3;   /* Borders */
body background: #FAFBFC
```

### Elevation tokens
```css
.shadow-teal-sm  { box-shadow: 0 1px 3px  rgba(10,124,124,0.08); }
.shadow-teal-md  { box-shadow: 0 2px 8px  rgba(10,124,124,0.10); }
.shadow-teal-lg  { box-shadow: 0 4px 16px rgba(10,124,124,0.12); }
```

### Amount display
```css
.amount { font-family: var(--font-mono); text-align: right; white-space: nowrap; }
```

### Number input spinners hidden globally
```css
input[type="number"]::-webkit-outer-spin-button,
input[type="number"]::-webkit-inner-spin-button { -webkit-appearance: none; }
input[type="number"] { -moz-appearance: textfield; }
```

---

## 13. PO Order Placement Flow (§3.12.3 PRD)

**Trigger:** PO reaches `approved` status (final workflow step).

**Backend actions on PO final approval:**
1. `_attach_po_pdf(db, po, company_name)` — generates PO PDF, stores as `PoAttachment`
2. `Task(type="po_place_order", assigned_role="procurement_officer", document_id=po.id, ...)` created

**Frontend flow on PoDetailPage:**

```
PoDetailPage (status = "approved")
    │
    └── "Place Order" button visible to procurement_officer / procurement_manager
            │
            ▼
    PlaceOrderComposer component (inline panel or modal)
            │
    ┌───────┴───────────────────────────┐
    │  Option A: Place Order via Email  │  Option B: Place Order Online
    │                                   │
    │  1. Fetch vendor.contact_email    │  1. Confirmation dialog:
    │     from Vendor Master            │     "Confirm you placed the order
    │  2. Pre-fill EmailComposer:       │      with [Vendor] via their portal"
    │     - To: vendor email (editable) │  2. Optional portal reference #
    │     - Subject: from PO Order      │  3. Confirm → POST /po/{id}/place-order
    │       Email template (editable)   │     { method: "online", reference: "..." }
    │     - Body: template + variables  │  4. PO status → "issued"
    │       (editable)                  │
    │     - Attachment: PO PDF          │
    │       (auto-attached, locked)     │
    │  3. Send → POST /po/{id}/place-order
    │     { method: "email", to: "...",  │
    │       subject: "...", body: "..." }│
    │  4. PO status → "issued"          │
    └───────────────────────────────────┘
```

**API endpoint:**
```
POST /po/{id}/place-order
Body: {
  method: "email" | "online",
  to?: string,          # email method only
  subject?: string,     # email method only
  body?: string,        # email method only
  reference?: string    # online method only
}
Response: PoResponse (status = "issued")
```

**If vendor has no contact email:** warning displayed; Procurement Officer must add email to Vendor Master before using email method.

---

## 14. Notification Service Architecture (Planned — B11)

```
Task created (any type)
    │
    ├── NotificationDispatcher.dispatch(task)  [async, non-blocking]
    │       │
    │       ├── Resolve recipient: task.assigned_user_id OR all users with task.assigned_role
    │       │
    │       ├── For each recipient:
    │       │       read user.notification_channel  (Email | Teams | Both | None)
    │       │       if channel includes Email → email.send(smtp_config, template, recipient)
    │       │       if channel includes Teams → teams.send_card(webhook_url, card_data)
    │       │       log to NotificationLog (status, attempt, error)
    │       │
    │       └── Retry: up to 3 attempts with exponential backoff on delivery failure
    │
    └── DailyFollowUpScheduler (APScheduler, 08:00 daily)
            │
            ├── SELECT tasks WHERE is_completed=False AND created_at < today
            └── For each open task: NotificationDispatcher.dispatch(task, is_followup=True)
```

**Email dispatch (`app/services/email.py`):**
- SMTP settings from `CompanyConfig.smtp_config`
- Subject + body from `CompanyConfig.email_templates[event_key]` with variable substitution
- Library: `aiosmtplib` (async SMTP)

**Teams dispatch (`app/services/teams.py`):**
- Adaptive Card JSON posted to `CompanyConfig.notification_settings.teams_webhook_url` via `httpx`
- Card includes: document number, title, amount (if applicable), action button (deep-link to EPMS)

---

## 15. Role Management & Access Control Matrix (§3.6.7 PRD)

### 15.1 Storage

The Access Control Matrix is stored in `CompanyConfig.role_permissions` as JSONB:
```json
{
  "dept_manager":         { "create_pr": true,  "approve_pr": true,  "create_po": false, ... },
  "procurement_officer":  { "create_pr": false, "approve_pr": false, "create_po": true,  ... },
  "finance_manager":      { "create_pr": true,  "approve_pr": true,  "approve_pa": true, ... },
  "quality_auditor":      { "create_pr": true,  "approve_pr": false, ... }
}
```

Built-in role defaults are seeded by Alembic migration. Custom roles added by System Admin are appended to this dict.

### 15.2 Enforcement

RBAC middleware on every API endpoint:
```python
# Pseudocode
def require_permission(permission: str):
    def decorator(func):
        async def wrapper(current_user, db, ...):
            role_perms = config.role_permissions.get(current_user.role, {})
            if not role_perms.get(permission, False):
                raise HTTPException(403)
            return await func(...)
```

For multi-role users (§1.2 PRD), permission check is a **union** across all active roles:
```python
all_roles = [user.role] + user.additional_roles  # e.g. ["dept_manager", "finance_manager"]
allowed = any(config.role_permissions.get(r, {}).get(permission) for r in all_roles)
```

### 15.3 Locked Permissions

Built-in role core permissions that cannot be disabled via the UI are enforced by a server-side allowlist:
```python
LOCKED_PERMISSIONS = {
    "procurement_manager": {"approve_po"},
    "finance_bp":          {"approve_pa"},
    "system_admin":        {"admin_panel", "budget_admin"},
    # etc.
}
```
The Admin Panel frontend reads this list from a `GET /config/locked-permissions` endpoint and renders lock icons on the corresponding matrix cells.

---

## 16. Document Numbering

| Document | Format | Sequence Scope | Assigned At |
|----------|--------|---------------|-------------|
| PR | `PR-YYYYMMDD-XXXX` | Daily (resets each UTC day) | `submit` action |
| PO | `PO-[POID]-YYMM-NN` | Per vendor per month | `submit` action |
| GR | `GR-YYYYMMDD-XXXX` | Daily | GR `save` |
| PA | `PA-YYYYMMDD-XXXX` | Daily | `submit` action |

**PO number construction:**
```python
# POID = vendor.poid (1–3 uppercase alphanumeric, e.g. "ABC")
# YYMM = two-digit year + two-digit month of submission
# NN   = zero-padded sequential count per vendor per month
prefix = f"PO-{vendor.poid}-{now:%y%m}-"
count  = SELECT COUNT(*) FROM purchase_orders WHERE number LIKE '{prefix}%'
number = f"{prefix}{count + 1:02d}"
```

---

## 17. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Denormalised `vendor_name`, `cost_center_name`, `department_name` on PR | Avoids joins on every PR read; names resolved at creation/update time via `_resolve_names()` |
| Files stored as `LargeBinary` in PostgreSQL | No external file storage needed; max file size enforced at API layer |
| PDF generated server-side (ReportLab) in thread executor | No system dependencies; `run_in_executor` keeps async event loop free |
| `Number()` conversion in budget service | Pydantic v2 serialises `Decimal` as strings; JS arithmetic on strings produces NaN |
| `l1_code` injected when flattening budget accounts | Accounts nested inside L1 response; code needed for downstream `filter(a => filteredL1Codes.has(a.l1_code))` |
| Budget dept filter uses `d.id === user.department_id` (UUID) | Name matching (`d.name === user.department`) is fragile — nulls, casing, rename drift |
| `createPortal` for reset-password modal | Separates password input from table DOM; prevents browser autofill association |
| `createPortal` for PartsPicker dropdown | `overflow-x-auto` on line items table clips `position:absolute`; `position:fixed` portal avoids this |
| Recall action (submitted/in_review → draft) | Allows requester to pull back submitted document without going through Return; clears tasks, resets `approval_step_idx` |
| `gm_or_opm` token resolved at runtime | Admin configures one token; engine resolves GM vs OPM from `dept_gm_opm_mapping` at action time — no per-department branching in workflow definition |
| PO order placement is manual (not auto-email on approval) | Procurement Officer controls when and how to contact vendor (email or online portal); system prepares email but human sends it |
| Multi-role permission = union of all active roles | Finance Manager holds both `finance_manager` (over-budget approval) and `dept_manager` (Finance dept PRs); union ensures both scopes active simultaneously |
| Access Control Matrix stored in `CompanyConfig.role_permissions` JSONB | Allows runtime permission changes without code deployment; custom roles appended dynamically |
| Notification dispatch is async and non-blocking | Document state transitions must not fail due to email/Teams delivery issues; NotificationLog tracks failures for retry |
