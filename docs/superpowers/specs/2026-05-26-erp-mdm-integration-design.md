# ERP MDM Integration — Design Spec

**Date:** 2026-05-26
**Sprint context:** S4 add-on (independent of TRV / CFM / E2E work)
**Owner:** UniOps team

---

## 1. Goal

Pull three master-data feeds from the external ERP system at `http://10.10.95.66` into UniOps so that:

- Admins can manually trigger sync of **Materials**, **Suppliers**, and **Persons** from ERP into local mirror tables.
- User Management can import users in bulk from the ERP Persons mirror.
- Vendor Management can import vendors in bulk from the ERP Suppliers mirror, automatically setting the existing `vendors.erp_id` field from the ERP supplier code.
- PR Type 1 (原料/包材采购) item lines can only pick from the ERP Materials mirror.

ERP is the source of truth for these three masters; mirror tables decouple read traffic from ERP availability and let the user curate which records become live UniOps users/vendors.

## 2. Non-goals

- No real-time sync, no webhooks, no background scheduler in v1 — manual button only.
- No write-back to ERP. Direction is ERP → UniOps only.
- No data transformation beyond field mapping. Validation/cleansing of ERP data is out of scope; raw payload is preserved for later debugging.
- No multi-tenant support — single ERP endpoint, single mirror per kind.
- No retroactive backfill of `erp_id` on vendors already created manually.

## 3. Architecture

UniOps already has a service split that this design must respect:

- **mdm-api** (:8002): directory/master-data service. Will own ERP mirror tables, sync orchestrator, and browse endpoints. Does NOT own operational user/vendor writes.
- **epms-api** (:8000): operational CRUD for `users` and `vendors`. Will host the bulk import endpoints, which fetch ERP rows from mdm-api server-to-server then write to its own `users` / `vendors` tables.
- **Portal frontend**: adds a new `mdmApi` HTTP client and a new ERP MDM admin section; existing `epmsApi` client is used for import calls.
- **EPMS frontend**: uses `mdmApi` for PR-type-1 material picker; uses `epmsApi` for the "From ERP" vendor import.

```
Portal AdminPanel ───────────────► mdm-api (:8002) ───POST───► ERP (10.10.95.66)
   (ERP MDM section)         GET /mdm/v1/erp/...        /firmusData/touch_mdm_mes/...
                                      │
                                      │ erp_materials / erp_suppliers / erp_persons
                                      │ erp_sync_state          (PostgreSQL)
                                      │
Portal/EPMS ──► epms-api (:8000) ──── reads via httpx ─► mdm-api
  "From ERP"   /users/import-from-erp
   buttons    /vendors/import-from-erp
                       │
                       ▼
                 users / vendors (epms-api PostgreSQL)

EPMS PR Type 1 ──► mdm-api GET /mdm/v1/erp/materials (picker only)
                   PrLineItem.material_id stores ERP part_NO snapshot
```

**Service boundary:** all ERP I/O lives in mdm-api. epms-api and Portal never call ERP directly — they only read mirror tables via mdm-api. This keeps ERP credentials/timeouts/retries in one place. Bulk import endpoints live in epms-api because that's where the destination tables (`users`, `vendors`) live; they pull selected rows from mdm-api over the internal network.

## 4. Data model

### 4.1 New tables (mdm-api)

```sql
-- erp_materials  (ERP 2.1 getMaterialInfo)
CREATE TABLE erp_materials (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  erp_part_no     VARCHAR(50)  NOT NULL UNIQUE,
  description     TEXT,
  unit_meas       VARCHAR(20),
  dim_quality     VARCHAR(100),
  weight_net      NUMERIC(12,4),
  weight_gross    NUMERIC(12,4),
  volume          NUMERIC(12,6),
  part_status     VARCHAR(10),         -- 'A' active / 'B' inactive
  item_mes_type   VARCHAR(20),         -- e.g. 'A-10'
  raw_payload     JSONB    NOT NULL,
  erp_rowversion  TIMESTAMPTZ,
  synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_erp_materials_part_status ON erp_materials (part_status);
CREATE INDEX ix_erp_materials_item_mes_type ON erp_materials (item_mes_type);

-- erp_suppliers  (ERP 2.5 getSupplierInfo)
CREATE TABLE erp_suppliers (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  erp_supplier_code  VARCHAR(50)  NOT NULL UNIQUE,
  supplier_name      VARCHAR(255) NOT NULL,
  supplier_address   TEXT,
  supplier_tel       VARCHAR(50),
  supplier_fax       VARCHAR(50),
  supplier_type      VARCHAR(20),
  raw_payload        JSONB NOT NULL,
  erp_rowversion     TIMESTAMPTZ,
  synced_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- erp_persons  (ERP 2.9 getPersonInfo)
CREATE TABLE erp_persons (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  erp_person_code  VARCHAR(50)  NOT NULL UNIQUE,
  person_name      VARCHAR(255) NOT NULL,
  company_code     VARCHAR(50),
  company_name     VARCHAR(255),
  department_code  VARCHAR(50),
  department_name  VARCHAR(255),
  is_valid         BOOLEAN NOT NULL DEFAULT TRUE,
  pk_psndoc        VARCHAR(50),
  raw_payload      JSONB NOT NULL,
  erp_rowversion   TIMESTAMPTZ,
  synced_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_erp_persons_department_code ON erp_persons (department_code);

-- erp_sync_state  (one row per kind)
CREATE TABLE erp_sync_state (
  kind            VARCHAR(20) PRIMARY KEY,  -- 'material' | 'supplier' | 'person'
  last_ts         TIMESTAMPTZ,              -- for incremental ts param
  last_synced_at  TIMESTAMPTZ,
  last_status     VARCHAR(20),              -- 'success' | 'failed' | 'running'
  last_message    TEXT,
  last_row_count  INTEGER NOT NULL DEFAULT 0,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`raw_payload` is the full ERP record as JSONB — we keep it because ERP fields are loosely specified and may evolve. `erp_rowversion` is parsed from ERP's `rowversion` field for tracking `last_ts`.

### 4.2 Changes to existing tables (in **epms-api**)

```sql
-- users (epms-api): track which ERP person a user came from (prevents duplicate import)
ALTER TABLE users ADD COLUMN erp_person_code VARCHAR(50);
CREATE UNIQUE INDEX ux_users_erp_person_code ON users (erp_person_code) WHERE erp_person_code IS NOT NULL;

-- vendors (epms-api): erp_id already exists, but add partial unique index to prevent duplicate imports
CREATE UNIQUE INDEX ux_vendors_erp_id ON vendors (erp_id) WHERE erp_id IS NOT NULL;
```

`pr_line_items.material_id` already exists (VARCHAR(50)) — reuse it to store ERP `part_NO` for PR Type 1 lines. No new column needed.

No change to `parts` table — PR Type 1 lines read directly from mdm-api's `erp_materials` (cross-service GET). The `parts` table continues to serve PR Type 2/3 (non-raw-material categories).

## 5. ERP client

`mdm-api/app/services/erp_client.py`

- `httpx.AsyncClient(base_url=settings.ERP_BASE_URL, timeout=60.0)`
- `settings.ERP_BASE_URL` defaults to `http://10.10.95.66`, overridable via env.
- No auth in v1. Reserve future env vars `ERP_USERNAME` / `ERP_PASSWORD` but do not wire them.
- Three methods:
  ```python
  async def fetch_materials(ts: datetime) -> list[dict]
  async def fetch_suppliers(ts: datetime) -> list[dict]
  async def fetch_persons(ts: datetime) -> list[dict]
  ```
- All POST JSON `{"ts": ts.strftime("%Y-%m-%d %H:%M:%S")}` to the URLs in section 2.x of the ERP doc.
- Parse `{code, message, data}`. `code != 200` → raise `ErpError(code, message)`. Empty/missing `data` → return `[]`.
- Network/timeout errors → bubble up as `ErpError` with message.

## 6. Sync orchestrator

`mdm-api/app/services/erp_sync.py`

```python
async def sync_kind(db: AsyncSession, kind: Literal['material','supplier','person'], full: bool = False) -> dict
```

Behavior:
1. Read `erp_sync_state[kind]`. If `full=True` or no row → `ts = datetime(1900,1,1)`. Else `ts = last_ts or datetime(1900,1,1)`.
2. Mark state as `last_status='running'` in its own committed transaction so concurrent calls can detect (optional v1 — okay to skip if we add a simple in-process lock).
3. Call ERP client to fetch records since `ts`.
4. For each record: upsert into the mirror table keyed by the unique ERP business code, replacing all mapped columns and `raw_payload`. Parse `rowversion` into `erp_rowversion`.
5. Compute `max_rowversion = max(parsed rowversions)`; on success update `erp_sync_state[kind]` with `last_ts=max_rowversion or now()`, `last_synced_at=now()`, `last_status='success'`, `last_row_count=len(records)`, `last_message='ok'`.
6. On `ErpError` or DB error: rollback the data tx, write a state row with `last_status='failed'`, `last_message=str(err)` in a fresh tx, re-raise.
7. Return `{kind, mode: 'full'|'incremental', total, inserted, updated, last_ts, status, message}`.

`inserted` vs `updated` is determined per-record by checking `INSERT … ON CONFLICT` outcome (use SQLAlchemy `insert(...).on_conflict_do_update(...).returning(xmax)` — `xmax = 0` means insert).

## 7. REST endpoints

### 7.0 mdm-api endpoints (new, prefix `/mdm/v1/erp/`)

Require `CurrentUser`. Sync mutations require `role == 'system_admin'`. Browse GETs additionally allow roles that consume the data downstream (see §14).

| Method | Path | Description |
|---|---|---|
| POST | `/mdm/v1/erp/sync/{kind}?full=<bool>` | Trigger sync. `kind ∈ {material, supplier, person}`. Returns the sync result dict from §6. 502 on `ErpError`. |
| GET  | `/mdm/v1/erp/sync/status` | Returns `{material: state, supplier: state, person: state}` for the AdminPanel header. |
| GET  | `/mdm/v1/erp/materials?page=&page_size=&search=&part_status=&item_mes_type=` | Paginated browse. |
| GET  | `/mdm/v1/erp/suppliers?page=&page_size=&search=&exclude_codes=<csv>` | `exclude_codes` accepts a comma-separated list of supplier codes already in use; the caller (epms-api) supplies this from its own `vendors.erp_id` set. |
| GET  | `/mdm/v1/erp/persons?page=&page_size=&search=&exclude_codes=<csv>` | Same pattern as suppliers, for persons. |

### 7.1 epms-api endpoints (new, under existing `/api/v1/`)

Require `system_admin` (for `/users/import-from-erp`) or `system_admin | vendor_manager` (for `/vendors/import-from-erp`). These endpoints internally call mdm-api over HTTP using a service token (or — v1 acceptable — using the caller's bearer token forwarded).

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/users/import-from-erp` | Body: `{items: [{erp_person_code, email, role?, department_id?, is_active?}]}`. Server fetches the corresponding `erp_persons` row from mdm-api, then creates users in epms-api's `users` table; password is random (returned in response as `created: [{email, temp_password}]`). Errors per-row in `errors`. |
| POST | `/api/v1/vendors/import-from-erp` | Body: `{erp_supplier_codes: [...], defaults: {category, payment_terms, currency}}`. Server fetches each `erp_suppliers` row from mdm-api, creates one vendor per code with `vendors.erp_id = erp_supplier_code`. |

### 7.1 Import endpoint details

**Users import** (`POST /api/v1/users/import-from-erp` on epms-api):
- Request validation: every item must have non-empty `email` and `erp_person_code`. 422 if any item violates.
- For each item:
  - Fetch the ERP person via mdm-api: `GET /mdm/v1/erp/persons?search=<erp_person_code>` (or a dedicated by-code endpoint added in mdm-api). Skip-with-error if missing.
  - Resolve `department_id`: prefer client-provided value; if absent, look up epms-api's `departments` where `code == erp_person.department_code`. If no match, leave NULL.
  - Generate `password = secrets.token_urlsafe(12)`. Hash and store. Return plaintext in response payload.
  - Insert user with `email=item.email`, `full_name=erp_persons.person_name`, `role=item.role or 'requester'`, `is_active=item.is_active ?? True`, `erp_person_code=item.erp_person_code`.
  - On unique conflict (email or erp_person_code) → add to `errors` and continue.
- Response: `{created: [{email, full_name, temp_password}], errors: [{erp_person_code, reason}]}`.

**Vendors import** (`POST /api/v1/vendors/import-from-erp` on epms-api):
- For each `erp_supplier_code`:
  - Fetch the supplier from mdm-api (`GET /mdm/v1/erp/suppliers?search=<erp_supplier_code>` or by-code endpoint). Skip-with-error if missing.
  - Build vendor: `code = erp_supplier_code` (also used as `vendors.code` per existing schema), `erp_id = erp_supplier_code`, `name = supplier_name`, `address = supplier_address`, `phone = supplier_tel`, `contact_name = supplier_name` (placeholder — `contact_name` is NOT NULL in current schema), `contact_email = f"{erp_supplier_code}@erp.local"` (placeholder, admin edits later), `category = defaults.category` (default `'other'`), `payment_terms = defaults.payment_terms` (default `'net30'`), `currency = defaults.currency` (default `'CAD'`), `is_active = True`.
  - On unique conflict on `erp_id` or `code` (e.g. a vendor with the same `code` was created manually before) → skip with error `"code conflict: vendor with code=<X> already exists"`.
- Response: `{created: int, errors: [{erp_supplier_code, reason}]}`.

## 8. Portal AdminPanel UI

### 8.1 Sidebar entry

In `portal/src/pages/admin/AdminPanel.tsx`, append to `SECTIONS`:
```ts
{ key: 'erp_mdm', label: 'ERP MDM', icon: Database }
```
(Use `Database` from `lucide-react`, already in dep tree.)

Add `{section === 'erp_mdm' && <ErpMdm />}` to the main switch.

### 8.2 `<ErpMdm/>` component

Three tabs at top: **Materials | Suppliers | Persons**. State held in URL hash so reload preserves tab.

Header bar above the table:
```
Last synced: 2026-05-26 10:30:12 · success · 3,452 rows
[ Sync Incremental ] [ Full Resync ]                [ Search… ] [ filters ]
```

- `Sync Incremental` → `POST /mdm/v1/erp/sync/{kind}` with no `full`. Spinner during request. On success, toast with `inserted` + `updated` counts and refetch the table query.
- `Full Resync` → confirmation modal "Pull all records from ERP from the beginning. This may take several minutes." → `POST … ?full=true`.
- Status from `GET /mdm/v1/erp/sync/status` polled on tab mount.

Tab tables — columns:
- **Materials**: ERP Part No · Description · Unit · Spec · MES Type · Status · ERP rowversion. Filters: `part_status` (A/B/all), `item_mes_type` (free text). 20 per page.
- **Suppliers**: Code · Name · Type · Tel · Address · Imported (badge if found in `vendors.erp_id`). 20 per page.
- **Persons**: Code · Name · Company · Department · Valid · Imported (badge if found in `users.erp_person_code`). 20 per page.

No edit/delete actions on the mirror tables — they're read-only reflections of ERP.

## 9. User Management integration

In `UserManagement` (`portal/src/pages/admin/AdminPanel.tsx`), add a button next to "+ Add User":

```
[ + From ERP ]   [ + Add User ]
```

Clicking opens a Drawer:

```
┌ Import users from ERP ─────────────────────────────────────────┐
│ Defaults: Role [requester ▾]                                   │
│           Department  ◉ Auto-map from ERP departmentcode        │
│                       ○ Override → [select department ▾]        │
│                                                                │
│ [ Search code/name… ]  [✓] Only show not-imported              │
│                                                                │
│ ┌──┬─────────┬──────────┬───────────┬──────────────────────┐  │
│ │☑ │ Code    │ Name     │ Dept      │ Email *              │  │
│ ├──┼─────────┼──────────┼───────────┼──────────────────────┤  │
│ │☑ │ 100100  │ Ayan A.  │ Producti..│ ayan@royalmilk.com   │  │
│ │☐ │ 100101  │ ...      │           │                      │  │
│ └──┴─────────┴──────────┴───────────┴──────────────────────┘  │
│                                                                │
│ 2 selected · Email required for each selected row              │
│                              [ Cancel ]  [ Import 2 users ]    │
└────────────────────────────────────────────────────────────────┘
```

- Email is **required** per selected row (ERP doesn't return email).
- On submit: client validates all selected rows have email. POST `/mdm/v1/erp/users/import` with the array.
- Response renders a success dialog listing created users + their temporary passwords with a "Copy all" button.
- On per-row error, the dialog shows the failing rows with reason; successful rows are not rolled back.

## 10. Vendor Management integration

In EPMS `VendorsPage.tsx`, add `[ + From ERP ]` next to existing "+ Add Vendor".

Drawer is analogous to user import but simpler — no per-row inputs required:

```
┌ Import vendors from ERP ───────────────────────────────────────┐
│ Defaults applied to all selected:                              │
│   Category [other ▾]  Payment [net30 ▾]  Currency [CAD ▾]      │
│                                                                │
│ [ Search code/name… ]  [✓] Only show not-imported              │
│                                                                │
│ ┌──┬─────────┬─────────────────────────┬─────────────────────┐ │
│ │☑ │ Code    │ Name                    │ Tel                 │ │
│ │☑ │ CRM027  │ JiangSu Debang Duoling..│ ...                 │ │
│ └──┴─────────┴─────────────────────────┴─────────────────────┘ │
│                                                                │
│ 5 selected   contact_email will be placeholder; edit after.    │
│                              [ Cancel ]  [ Import 5 vendors ]  │
└────────────────────────────────────────────────────────────────┘
```

POST `/mdm/v1/erp/vendors/import`. Toast on success "5 created, 0 errors. Update contact email in vendor list." Then refetch vendor query so new rows show with `erp_id` populated.

## 11. EPMS PR Type 1 item line integration

PR Type 1 = 原料/包材采购 (raw material / packaging procurement). PR `type` column is `Integer`; `type=1` marks this category.

When the PR creation form has `type === 1`:

- Replace the existing parts picker data source with mdm-api `GET /mdm/v1/erp/materials?part_status=A`.
- The picker shows: ERP Part No · Description · Unit · Spec (dim_quality).
- Selected item populates the PR line (`pr_line_items` already has `material_id` VARCHAR(50)):
  - `material_id` ← `erp_materials.erp_part_no` (snapshot — stored as a free-text reference)
  - `description` ← `erp_materials.description`
  - `unit` ← `erp_materials.unit_meas`
  - `notes` ← `erp_materials.dim_quality` (spec)
- Other PR types continue to use existing `/parts` picker — no behavior change.

**Schema impact:** none. `pr_line_items.material_id` already exists; it's repurposed as the ERP part_NO snapshot for type=1 lines.

## 12. Configuration

mdm-api env vars (with defaults):
```
ERP_BASE_URL=http://10.10.95.66
ERP_TIMEOUT_SECONDS=60
# Reserved for future, unused in v1:
# ERP_USERNAME=
# ERP_PASSWORD=
```

epms-api env vars:
```
MDM_API_URL=http://mdm-api:8002
MDM_API_TIMEOUT_SECONDS=30
```

Portal env var (`portal/.env` or `import.meta.env`):
```
VITE_MDM_API_URL=http://localhost:8002
```

EPMS env var (same):
```
VITE_MDM_API_URL=http://localhost:8002
```

Add all to the respective `core/config.py` (where applicable) and `docker-compose.dev.yml`.

## 13. Error handling

- **ERP unreachable / 5xx:** sync endpoint returns 502 with the error message. Mirror tables untouched. Sync state row updated with `failed` status. Admin sees toast "ERP unreachable: <message>".
- **ERP returns `code != 200`:** treated as ERP error, same 502 path with `code` and `message` in the response.
- **Partial fetch (e.g., 1000 records returned, DB upsert fails on one):** entire data tx rolls back. Sync state row marked failed. Admin retries.
- **Concurrent sync of same kind:** acceptable to allow in v1; idempotent upserts mean a race produces no corruption. Postpone in-process lock unless we see issues.
- **ERP returns rowversion in a different format than expected:** parse defensively (try strict ISO, then `"%Y-%m-%d %H:%M:%S"`, else `None`). Record with unparseable rowversion still imports; `last_ts` falls back to `now()`.

## 14. Authorization summary

| Endpoint | Service | Required role(s) |
|---|---|---|
| `POST /mdm/v1/erp/sync/*` | mdm-api | system_admin |
| `GET  /mdm/v1/erp/sync/status` | mdm-api | system_admin |
| `GET  /mdm/v1/erp/materials` | mdm-api | any authenticated user (PR creators need this) |
| `GET  /mdm/v1/erp/suppliers` | mdm-api | system_admin, vendor_manager |
| `GET  /mdm/v1/erp/persons` | mdm-api | system_admin |
| `POST /api/v1/users/import-from-erp` | epms-api | system_admin |
| `POST /api/v1/vendors/import-from-erp` | epms-api | system_admin, vendor_manager |

## 15. Testing

- Unit tests for `erp_client` against a mocked httpx transport — covers code=200, code=40004, network error, malformed payload.
- Unit tests for `erp_sync` orchestration — incremental vs full, upsert insert/update counting, failure path writes failed state.
- Integration tests against mdm-api with a fixture ERP stub (FastAPI app fixtured into httpx mock) — round-trip sync → browse → import.
- Frontend: smoke test that AdminPanel ERP MDM tab renders, sync button hits the right endpoint with `full=` flag, import dialog validates per-row email.

## 16. Rollout

Single PR, behind no flag. The feature is admin-only and additive — it does not change existing flows until a user clicks one of the new buttons or sets `pr_type=1`. PR Type 1 line change requires DB migration on `epms-api` side and is the only change visible to non-admin users; ensure migration runs before frontend deploy.

## 17. Out-of-scope follow-ups (not in this spec)

- Scheduled background sync (cron) — only if manual proves insufficient.
- ERP credential rotation / Basic-Auth or token support — wire env vars when ERP team confirms.
- Two-way sync, ERP write-back.
- Bulk re-mapping of historical vendors to `erp_id` post-hoc.
- ERP Factory / Inventory / Brand feeds — defer until business asks.
- Materials picker for non-PR1 flows.

---

## Appendix A — ERP endpoints used

| Mirror | ERP URL | Returned fields used |
|---|---|---|
| material | `POST /firmusData/touch_mdm_mes/material/getMaterialInfo` | `part_NO, description, unit_MEAS, dim_QUALITY, weight_NET, weight_GROSS, volume, part_STATUS, itemMESType, rowversion` |
| supplier | `POST /firmusData/touch_mdm_mes/supplier/getSupplierInfo` | `suppliercode, suppliername, supplieraddress, suppliertel, supplierfax, suppliertype, rowversion` |
| person   | `POST /firmusData/touch_mdm_mes/person/getPersonInfo` | `personcode, personname, companycode, companyname, departmentcode, departmentname, isvalid, pk_psndoc, modifiedtime` |

All accept `{"ts": "YYYY-MM-DD HH:MM:SS"}` and return `{code, message, data: [...]}`.

## Appendix B — File touch list (preview)

**mdm-api:**
- `app/models/erp_material.py` (new)
- `app/models/erp_supplier.py` (new)
- `app/models/erp_person.py` (new)
- `app/models/erp_sync_state.py` (new)
- `app/schemas/erp.py` (new)
- `app/crud/erp.py` (new)
- `app/services/erp_client.py` (new)
- `app/services/erp_sync.py` (new)
- `app/api/v1/erp_mdm.py` (new) + register in `app/api/v1/__init__.py`
- `app/core/config.py` (add ERP_BASE_URL, ERP_TIMEOUT_SECONDS)
- `app/main.py` (import new models for metadata)
- `alembic/versions/0002_create_erp_mirrors.py` (new migration)
- `requirements.txt` (add httpx if not present)
- `tests/...` (new test tree, mdm-api currently has none)

**epms-api:**
- `app/models/user.py` (add `erp_person_code` column)
- `app/schemas/user.py` (allow setting `erp_person_code`)
- `app/services/mdm_client.py` (new — httpx client calling mdm-api)
- `app/api/v1/users.py` (add `/users/import-from-erp` endpoint)
- `app/api/v1/vendors.py` (add `/vendors/import-from-erp` endpoint)
- `app/core/config.py` (add `MDM_API_URL`, `MDM_API_TIMEOUT_SECONDS`)
- `alembic/versions/<next>_users_erp_person_code.py` (new migration)

**portal:**
- `src/lib/api.ts` (add `mdmApi` client)
- `src/pages/admin/AdminPanel.tsx` (sidebar entry, `<ErpMdm/>` section, UserManagement "From ERP" drawer)

**epms:**
- `src/lib/api.ts` (add `mdmApi` client)
- `src/pages/vendors/VendorsPage.tsx` ("From ERP" button + drawer)
- `src/pages/pr/PrCreatePage.tsx` (or wherever the line picker lives — implementation will locate it) (swap picker source for `type=1`)

**ops:**
- `docker-compose.dev.yml` (env vars for all three services + frontends)
