# UniOps Portal — Enterprise Entry Point & Administration
**Version:** 1.5  
**Date:** 2026-05-08  
**Status:** Implemented — Phase 2  
**Service:** `portal` (`:5174`)

**Architecture decisions recorded:**
- P-A: Portal is the **single entry point** for all UniOps modules. Users log in once at Portal and are handed off to modules without re-authenticating.
- P-B: Session handoff uses a **URL hash token** (`#__session=<base64json>`). Each module reads the hash on mount, hydrates its own Zustand auth store, and strips the hash from the URL.
- P-C: Global configuration (Company, Security, Departments, Users, Currency, Notifications) is managed exclusively in **Portal Admin Panel**. EPMS Admin Panel retains only EPMS-specific settings.
- P-D: Portal has no backend service of its own. It calls **EPMS API** (`:8000`) for authentication, configuration, and EPMS task data; and **expense-api** (`:8006`) for OA expense task data.

---

## 1. Overview

The **UniOps Portal** is the main entry point for all UniOps enterprise modules. Every user logs in through the Portal. After authentication, they can navigate to any licensed module without a second login.

The Portal serves three purposes:
1. **Authentication gateway** — single sign-on backed by EPMS API
2. **Module launcher** — health-aware cards linking to EPMS, OA, and future modules
3. **Unified Task Inbox** — aggregates pending approval tasks from all modules in one view

---

## 2. Authentication

### 2.1 Login Page

#### 2.1.1 Branding

The login page (`/login`) fetches company branding from the **public** endpoint `GET /api/v1/config/public/branding` (no authentication required) and displays:

| Field | Source | Fallback |
|-------|--------|---------|
| Logo | `logo_data_url` (base64 PNG/JPG set in Admin → Company Settings) | Initials badge derived from company name |
| Company name | `name` | `"UniOps"` |
| Tagline | `tagline` | `"Enterprise Management Platform"` |
| Footer text | `"{name} · Internal Platform"` | — |

When a logo is present it is rendered as an `<img>` (32×32 rounded square). When absent the initials badge (`bg-[#085E5E]`) is shown. The branding query has a 5-minute stale time and one automatic retry.

#### 2.1.2 Login Flow

```
User opens Portal (:5174)
  ↓ Not authenticated → redirect to /login
  ↓ LoginPage fetches GET /api/v1/config/public/branding (no auth)
  ↓ Displays company logo / name / tagline
LoginPage
  ↓ POST /api/v1/auth/login (EPMS API)
  ├─ MFA required → show 6-digit TOTP form
  │    ↓ POST /api/v1/auth/mfa/challenge
  └─ Token received
       ↓ GET /api/v1/auth/me (fetch user profile)
       ↓ Store in portal-auth (Zustand persist, localStorage key: portal-auth)
       ↓ Redirect to /
```

**FR IDs — Login Page Branding:**

| FR ID | Requirement |
|-------|-------------|
| **LP-001** | The login page must fetch company branding from `GET /api/v1/config/public/branding` without requiring authentication. |
| **LP-002** | If `logo_data_url` is set, the logo image must be displayed instead of the initials badge. |
| **LP-003** | Company name and tagline from the config must replace the hardcoded defaults on the login page and footer. |

**Auth store shape (portal-auth):**

```ts
{
  user: { id, email, full_name, role, department_id },
  token: string,           // access_token from EPMS API
  refreshToken: string,    // refresh_token
  mfaVerifiedAt: number,   // timestamp, valid 8 hours
  mfaPendingToken: string, // during 2-step MFA
  isAuthenticated: boolean
}
```

### 2.2 Token Lifetime

Access tokens issued by EPMS API are valid for **480 minutes (8 hours)**. This covers a full working day without requiring re-login. The Portal clears stale auth and redirects to `/login` on any 401 response from EPMS API.

### 2.3 MFA

When `mfa_required: true` is returned by `/auth/login`, the Portal shows a 6-digit TOTP input. After successful MFA challenge, the session is marked `mfaVerifiedAt`. The Portal re-uses the MFA verification for 8 hours — the same window as the access token.

### 2.4 Session Handoff to Modules

**Problem:** localStorage is scoped to origin (protocol + hostname + port). Portal at `:5174` and EPMS at `:5173` cannot share localStorage.

**Solution:** On navigation to a module, the Portal encodes the session as base64 JSON and appends it to the URL hash:

```
http://localhost:5173/dashboard#__session=eyJ0b2tlbiI6Ii4uLiJ9
```

The session payload:
```json
{ "token": "<access_token>", "refreshToken": "<refresh_token>", "user": { ... } }
```

Each module's `main.tsx` runs a bootstrap IIFE **before React mounts**:
1. Reads `window.location.hash`
2. Matches `/__session=([^&]+)/`
3. Decodes the base64 JSON
4. Maps fields to the module's own user schema (e.g., Portal's `full_name` → EPMS's `name`)
5. Calls `useAuthStore.setState(...)` to hydrate the in-memory store
6. Writes to the module's own localStorage key for persistence across refreshes
7. Calls `history.replaceState` to strip the hash

| Module | localStorage key | User field mapping |
|--------|----------------|--------------------|
| EPMS   | `epms-auth`    | `full_name` → `name` |
| OA     | `oa-auth`      | `full_name` kept as-is |

---

## 3. Portal Home

### 3.1 Layout

Full-page layout (no centered card):

```
┌──────────────────────────────────────────────────────────┐
│  Sidebar (desktop: 224px expanded / 64px collapsed)      │
│  bg-[#085E5E]                                            │
│  ┌─ Brand area ───────────────────────────────────────┐  │
│  │  Logo badge (32×32) + "UniOps Portal" + tagline    │  │
│  │  [ChevronLeft collapse toggle]                     │  │
│  └────────────────────────────────────────────────────┘  │
│  ├─ Home (active)                                        │
│  ├─ Procurement → EPMS (with token handoff)              │
│  ├─ OA → OA frontend (with token handoff)                │
│  └─ Admin [system_admin only] → /admin                   │
│  ─────────────────────────────────────────────────────── │
│  [ChevronRight expand button — visible when collapsed]   │
├──────────────────────────────────────────────────────────┤
│  Header (60px, white, border-b)                          │
│  ├─ [☰ Hamburger — mobile only]                          │
│  ├─ Search bar (cosmetic placeholder — desktop only)     │
│  └─ User avatar + dropdown (name, role, sign out)        │
├──────────────────────────────────────────────────────────┤
│  Main content (responsive grid)                          │
│  ├─ Left (flex-1, min-w-0):                              │
│  │   ├─ Welcome heading + pending task count             │
│  │   ├─ Module cards (2-column grid, max-w-lg)           │
│  │   └─ My Task Inbox                                    │
│  └─ Right (280px, xl screens only):                      │
│      ├─ Platform Health widget                           │
│      └─ Recent Activity widget                           │
│  (below xl: right column stacks below left column)       │
└──────────────────────────────────────────────────────────┘
```

**Sidebar — Collapse behaviour (desktop):**
- Expanded: `w-56` (224 px). Shows logo badge + name, nav labels, section headings.
- Collapsed: `w-16` (64 px). Shows logo badge only; nav items show icon only (tooltip on hover). Section heading dividers replace label text. Collapse toggle (`ChevronLeft`) is inside the brand row when expanded; an expand button (`ChevronRight`) appears at the bottom when collapsed.
- State is local (`useState`); not persisted across page reloads.
- Desktop collapse toggle is hidden on mobile (`hidden md:flex`).

**Sidebar — Mobile behaviour:**
- On screens `< md` (< 768 px) the sidebar is absolutely positioned (`fixed inset-y-0 left-0 z-30`) and starts off-screen (`-translate-x-full opacity-0`).
- The Header's hamburger button (`☰`, `md:hidden`) sets `mobileOpen = true`, sliding the sidebar in (`translate-x-0 opacity-100`).
- A semi-transparent black overlay (`bg-black/50`, `z-20`) covers the main content while the sidebar is open; clicking it closes the sidebar.
- Sidebar is always `w-56` on mobile (never collapses to icon-only on small screens).

**FR IDs — Portal Layout:**

| FR ID | Requirement |
|-------|-------------|
| **PL-001** | Sidebar must support desktop collapse (w-56 ↔ w-16) via a toggle button inside the brand row. |
| **PL-002** | Collapsed sidebar must show icons only; each nav item must have a `title` tooltip. |
| **PL-003** | On mobile (< md), the sidebar must be hidden by default and toggled via a hamburger button in the Header. |
| **PL-004** | A black/50 overlay must appear behind the mobile sidebar when open; clicking the overlay closes the sidebar. |
| **PL-005** | The Header hamburger button must be visible only on mobile (`md:hidden`). |
| **PL-006** | The main content area must use a responsive two-column grid: single column below `xl` (1280 px CSS), `[1fr_280px]` at `xl` and above. The right-column widgets (Platform Health, Recent Activity) stack below the Task Inbox on narrower viewports. No fixed `max-width` container — the content fills the available width to prevent horizontal overflow at common DPI-scaled resolutions (e.g. 1920×1080 at 125% DPI → ~1536 CSS px). |

### 3.2 Module Cards

Two active module cards displayed in a 2-column grid (`max-w-lg`):

| Module | Icon | Status | Action |
|--------|------|--------|--------|
| EPMS | Briefcase | Live (health check) | → EPMS dashboard with token |
| OA | CreditCard | Live (health check) | → OA Task Inbox with token |

Health check: Portal fetches `GET /api/v1/health` (EPMS) and `GET /health` (OA) every 60 seconds. If a service is unreachable the card shows an "Offline" badge and the link is disabled.

**Personnel** and **Audit Log** cards have been removed from the Portal home page and sidebar navigation. They will be re-introduced when the respective modules are ready for integration.

### 3.3 Unified Task Inbox

Aggregates tasks from all modules in a single sorted list:

| Source | Endpoint | Task type |
|--------|----------|-----------|
| EPMS | `GET /api/v1/tasks?is_completed=false` | Approval tasks (approve_pr, approve_po, etc.) |
| OA | `GET /api/v1/expenses/my-actions` | Expense claims needing action for current user's role |

**Task row layout:** Module badge · Time ago · Task title · Doc number · Amount · Arrow

**Module badge colours:**
- EPMS: teal (`bg-primary-50 text-primary-700`), teal left border
- OA/Expense: amber (`bg-amber-50 text-amber-700`), amber left border

**OA task logic (`/expenses/my-actions`):**

| Role | Returns |
|------|---------|
| `dept_manager` | Claims with `status=submitted`, `approval_step_idx=0` |
| `finance_bp` | Claims with `status=in_review`, `approval_step_idx=1` |
| `finance_manager` | Claims with `status=in_review, step=1 or 2` + `status=approved` |
| `system_admin` | All of the above |
| `ap_clerk` | Claims with `status=approved` (pending payment) |

Clicking a task navigates to the document in the correct module, embedding the `#__session` token in the URL.

### 3.4 Platform Health Widget

Real-time health checks run every 60 seconds:
- Services Online: X/2 with coloured progress bar
- Avg Response Time: measured as round-trip latency to health endpoint
- Per-service status dots (green = healthy, red = offline)
- Status banner: "All systems are operational" / "Some services are degraded"

### 3.5 Recent Activity Widget

Shows the 5 most recently created tasks from the combined task list, with time-ago labels and links.

---

## 4. User Menu

Clicking the avatar in the top header opens a dropdown matching EPMS's user menu:

- **Header:** Full name + role
- **Profile** → links to EPMS `/profile` with token handoff
- **Change Password** → links to EPMS `/profile` with token handoff
- **Sign Out** → calls `globalSignOut()`, clears Portal's own `portal-auth` session, redirects to `/login`

### 4.1 Global Sign-Out Rule

**Signing out from any UniOps module (Portal, EPMS, or OA) always ends with the user at the Portal login page.** There is no per-module logout — a session is valid across all modules or not at all.

#### Cross-Origin localStorage Constraint

`localStorage` is **origin-scoped** (protocol + hostname + port). The three frontends run on different origins:

| Module | Origin |
|--------|--------|
| Portal | `http://localhost:5174` (prod: `portal.uniops.com`) |
| EPMS   | `http://localhost:5173` (prod: `epms.uniops.com`) |
| OA     | `http://localhost:5175` (prod: `oa.uniops.com`) |

A module can only remove its **own** localStorage key. It cannot reach into another origin's storage. Attempting `localStorage.removeItem('portal-auth')` from EPMS's origin has no effect on Portal's `portal-auth`.

#### `/logout` Route — Central Cleanup Endpoint

To work around this constraint, Portal exposes a dedicated public route `/logout` (`LogoutPage.tsx`). When loaded:

1. Calls `useAuthStore.logout()` to clear Zustand in-memory state
2. Calls `localStorage.removeItem('portal-auth')` (same origin — works)
3. Calls `navigate('/login', { replace: true })` — user sees the login form

EPMS and OA redirect to `{PORTAL_URL}/logout` after clearing their own key, so Portal always performs its own cleanup regardless of which module initiated the sign-out.

#### Sign-Out Flow per Module

**From EPMS (Header button / 401 / idle timeout):**
```
EPMS: localStorage.removeItem('epms-auth')   ← same origin ✓
EPMS: window.location.href = PORTAL_URL/logout
  → Portal /logout: logout() + removeItem('portal-auth') + navigate('/login') ✓
```

**From OA (Header button / 401):**
```
OA:   localStorage.removeItem('oa-auth')     ← same origin ✓
OA:   window.location.href = PORTAL_URL/logout
  → Portal /logout: same as above ✓
```

**From Portal (Header button / 401):**
```
Portal: localStorage.removeItem('portal-auth')  ← same origin ✓
Portal: window.location.href = '/login'          ← direct, no /logout needed
```

#### Trigger Points

| Module | Trigger | Steps |
|--------|---------|-------|
| Portal | Header "Sign Out" button | `globalSignOut()` → removes `portal-auth` → `/login` |
| Portal | HTTP 401 from any API | `globalSignOut()` → removes `portal-auth` → `/login` |
| EPMS | Header "Sign Out" button | `globalSignOut()` → removes `epms-auth` → `PORTAL_URL/logout` |
| EPMS | HTTP 401 (after failed refresh) | `globalSignOut()` → removes `epms-auth` → `PORTAL_URL/logout` |
| EPMS | Idle timeout (15 min) | `globalSignOut()` after server token blacklist → `PORTAL_URL/logout` |
| OA | Header "Sign Out" button | `signOut()` → removes `oa-auth` → `PORTAL_URL/logout` |
| OA | HTTP 401 from expense-api | removes `oa-auth` → `PORTAL_URL/logout` |

#### Residual Sessions

When sign-out is initiated from EPMS or OA, the **other non-initiating module's** localStorage key is not cleared in that browser navigation (different origin). However:
- The JWT access token has a fixed expiry (8 hours); after expiry the module redirects to Portal login.
- The refresh token is server-side blacklisted by EPMS on idle timeout or explicit sign-out; any refresh attempt after that returns 401, triggering the module's own sign-out flow on next use.
- In practice the user arrives at the Portal login page immediately and must re-authenticate before accessing any module.

**FR IDs — Global Sign-Out:**

| FR ID | Requirement |
|-------|-------------|
| **SO-001** | Signing out from any module must result in the user landing on the Portal login page. |
| **SO-002** | Each module must remove only its own `localStorage` key (same-origin rule). Attempting cross-origin localStorage removal is ineffective and must not be relied upon. |
| **SO-003** | EPMS and OA must redirect to `{PORTAL_URL}/logout` after clearing their own key, so Portal can clear its own session. |
| **SO-004** | Portal must expose a public `/logout` route (no auth guard) that: clears `portal-auth`, resets Zustand auth state, and redirects to `/login`. |
| **SO-005** | An HTTP 401 response from any backend API must trigger the module's sign-out flow (own key cleared → Portal `/logout` or Portal `/login`). |
| **SO-006** | EPMS idle timeout (15 min) must blacklist the refresh token server-side (`POST /auth/logout`) before calling `globalSignOut()`. |
| **SO-007** | The Portal `/logout` route must be accessible without authentication (no `ProtectedRoute` wrapper) so redirects from EPMS/OA always succeed. |

---

## 5. Global Admin Panel (`/admin`)

Available only to `system_admin` role. All settings call EPMS API and apply to every module.

### 5.1 Navigation

Left sidebar with 7 sections:

| Section | Description |
|---------|-------------|
| Company Settings | Name, tagline, delivery address, logo |
| Security | MFA toggle, password expiry |
| Departments | CRUD table for all departments |
| User Management | CRUD table with pagination, CSV import/export |
| Currency Settings | Default currency, enabled currency toggles |
| Notification Settings | **SMTP (task notifications)**, default channel, Teams webhook, daily follow-up time |
| Approval Workflows | Workflow definitions and action-key bindings for all modules |

### 5.2 Company Settings

Fields: `name`, `tagline`, `delivery_address`, `logo_data_url` (base64 PNG/JPG).

Logo upload: FileReader converts to data URL. Preview shown inline. Remove button clears logo.

API: `GET /api/v1/config`, `PATCH /api/v1/config`.

### 5.3 Security

**MFA:** Toggle enforces TOTP for all users company-wide.
**Password expiry:** Days until mandatory password change (blank = no expiry).

> SMTP / email configuration **moved to Notification Settings** as of 2026-05-28. The split is intentional: Notification Settings owns the SMTP profile used for all *internal* outbound mail (approval emails, task reminders, MFA OTP). The EPMS Admin → Email Settings page owns a separate SMTP profile for *external* PO emails sent to vendors. See PRD-EMAIL-SPLIT-001~004 below.

### 5.4 Department Management

Table: Code · Name · Active status · Edit / Delete
Create/edit via modal form.

**Backend owner:** `mdm-api` (master-data service). As of 2026-05-27 all writes target mdm-api:
`GET|POST|PATCH|DELETE /mdm/v1/departments`. The Portal Admin page calls `mdmApi` rather than `epmsApi`.

**Write access:** `system_admin | finance_manager | ap_clerk` (enforced by `require_roles` on mdm-api endpoints). The Portal Admin page itself remains gated to `system_admin` only; finance roles use the mdm-api directly via Cost Center management (§5.x — Budget Config Cost Centers).

**Delete behaviour:** `DELETE /departments/{id}` returns **HTTP 409** if the department has any linked cost centers or assigned users. The UI must surface the error message and offer "Deactivate instead" as the safe alternative (`PATCH /{id}` with `{ is_active: false }`).

### 5.4a Cost Center Management (Portal → Finance → Budget Config)

Cost Centers are managed inside the existing Portal **Budget Config** page as a top-level section.

**Backend owner:** `mdm-api`. Writes target `POST|PATCH|DELETE /mdm/v1/cost-centers`; the Budget Config page calls `mdmApi`.

**Page access:** the Budget Config page is open to `system_admin | finance_manager | ap_clerk`. System Admin sees the full page (company config, decomposition, catalog link, and Cost Centers); finance roles see only the Cost Centers section.

**Sidebar:** the Portal FINANCE → Budget Config link is visible to all three roles (sidebar entry uses `allowedRoles: ['system_admin', 'finance_manager', 'ap_clerk']`).

**Delete behaviour:** `DELETE /cost-centers/{id}` returns **HTTP 409** if the cost center is referenced by any PR or budget L1 group; UI offers Deactivate instead. Backed by a shared-DB count against `purchase_requests.cost_center_id` (mdm-api and epms-api share the `epms` Postgres database).

### 5.4b Factor Library (Portal → Finance → Factor Library)

Standalone page at `/budget/factors` that hosts CRUD for **reusable decomposition factor templates** (see budget-api PRD §4.2.1, BFAC-LIB-001 through BFAC-LIB-008). Selecting a template from the EPMS Account Catalog (`From Library` button on a Budget Account's factor section) copies the template's factor code/name + active values into the per-Account tables — there is no live link after the copy.

**Backend owner:** `budget-api`. Page calls `budgetApi` directly (`GET|POST|PATCH|DELETE /factor-templates`, `POST /factor-templates/{id}/values`, etc.).

**Page access:** `system_admin | finance_manager | finance_bp` (mirrors the existing factor write roles in budget-api). Read endpoints are open to any authenticated user so the EPMS picker can list templates.

**Sidebar:** Portal FINANCE → Factor Library entry uses `allowedRoles: ['system_admin', 'finance_manager', 'finance_bp']`.

**Behaviour notes:**
- Template `factor_code` is **immutable** after creation; it acts as the default copy key on attach. Admins can override the code at attach time inside EPMS if it collides on a given account.
- Template values support hard delete (no per-Account ripple) plus an active/inactive toggle for soft hiding.
- Deleting a template is always safe — per-Account factors that were cloned from it are not affected.

### 5.5 User Management

**Table:** Name / Email · Role badge · Department · Active status · Edit / Delete

**Filters:** Search (name or email, ilike), Role dropdown.

**Pagination:** 20 rows per page. Prev/Next + numbered page buttons with ellipsis. Shows "X–Y of Z users" count.

**Export CSV:** `GET /api/v1/users/export` → downloads `users.csv`.  
**CSV columns:** `email, full_name, role, department_code, is_active, teams_account, password`

**Import CSV:** Upload → `POST /api/v1/users/import` (multipart).  
- Matches users by `email`; creates new or updates existing  
- Auto-generates password for new users if `password` column is blank  
- Returns `{ created, updated, errors[] }`; inline result banner shows outcome  
**Template:** Download a pre-filled example CSV for reference.

**API:** `GET /api/v1/users?page=&page_size=&search=&role=`, `POST /api/v1/users`, `PATCH /api/v1/users/{id}`, `DELETE /api/v1/users/{id}` (soft-delete: `is_active = false`).

### 5.6 Currency Settings

Default currency selector (CAD, USD, EUR, GBP, CNY, JPY, AUD, CHF).  
Enabled currencies: toggle grid (only enabled currencies appear in transaction forms in all modules).

### 5.7 Notification Settings

Houses **two coupled blocks**: the SMTP profile used for internal task notifications, and the delivery preferences (channel, Teams webhook, follow-up time). The Save button persists both blocks in a single `PATCH /api/v1/config`.

**SMTP — Task Notifications** *(used for all internal outbound mail)*

| Field | Stored in CompanyConfig | Notes |
|-------|-------------------------|-------|
| SMTP Host | `smtp_host` | Falls back to `epms-api` `SMTP_HOST` env var when null |
| Port | `smtp_port` | |
| Username | `smtp_user` | |
| Password | `smtp_password` | Masked in UI; show/hide toggle |
| From Address | `smtp_from` | |
| TLS | `smtp_use_tls` | STARTTLS toggle |

**Delivery Preferences**

| Field | Description |
|-------|-------------|
| Default Channel | `email_only` / `teams_only` / `email + teams` / `disabled` |
| Teams Webhook URL | Incoming webhook for Microsoft Teams notifications |
| Daily Follow-up Time (UTC) | Time to send pending task reminders |
| Test | `POST /api/v1/config/test-smtp` with `{ to, kind: "task" }` — sends a test email through the SMTP profile above |

#### PRD-EMAIL-SPLIT FR IDs

| FR ID | Requirement |
|-------|-------------|
| **PRD-EMAIL-SPLIT-001** | CompanyConfig must carry two SMTP profiles: `smtp_*` (task notifications) and `po_smtp_*` (PO-to-vendor). Both are NULLable. PO send falls back to `smtp_*` per-field when `po_smtp_*` is unset, so existing single-mailbox installs keep working. |
| **PRD-EMAIL-SPLIT-002** | Portal Admin → Notification Settings owns `smtp_*`. Portal Admin → Security MUST NOT show SMTP fields; the Security page only manages MFA + password expiry. |
| **PRD-EMAIL-SPLIT-003** | EPMS Admin → Email Settings (renamed from "Email Templates" 2026-05-28) owns `po_smtp_*` alongside the PO email template (`po_email_subject`, `po_email_body`). |
| **PRD-EMAIL-SPLIT-004** | `POST /api/v1/config/test-smtp` accepts a `kind` field (`"task"` default, or `"po"`). The PO test exercises the same per-field fallback as the real send path so admins can confirm their hybrid setup before issuing a real PO. |

### 5.8 Approval Workflows

Central configuration for all approval workflows across every UniOps module. Only `system_admin` can edit. Changes take effect immediately on the next document submission — no service restart required.

**Architecture (see PRD.md §3.3):**

- Each submittable document/form type has a unique **action key**.
- Each action key is bound to exactly one **workflow** (ordered list of approval steps).
- Workflows are stored in `CompanyConfig.workflow_defs[action_key]` (EPMS API `PATCH /api/v1/config`).
- The shared `approval-api` (`:8003`) reads this config at runtime for every submission.

**UI layout:**

```
┌─────────────────────────────────────────────────────────────┐
│  Action Key selector (tab row):                              │
│  [PR] [PO] [PA] [PA-DIR] [EXP] [TRV] [MIL] [CFM]          │
├─────────────────────────────────────────────────────────────┤
│  Step list for selected action key                           │
│  ┌──────────────────────────────────────────────────┐       │
│  │  Step 1  [Label input          ] [Role ▼] [↑][↓][×] │    │
│  │  Step 2  [Label input          ] [Role ▼] [↑][↓][×] │    │
│  └──────────────────────────────────────────────────┘       │
│  [+ Add Step]                                                │
│  [Save Workflow]                                             │
└─────────────────────────────────────────────────────────────┘
```

**Supported action keys and shipped defaults:**

| Action Key | Document / Form | Default Steps |
|------------|----------------|--------------|
| `pr` | Purchase Request | `dept_manager → gm_or_opm` |
| `po` | Purchase Order | `procurement_manager → gm_or_opm` |
| `pa` | PA (PO-linked) | `dept_manager → gm_or_opm → finance_bp → finance_manager` |
| `pa_dir` | PA (Direct, no PO) | `finance_bp → finance_manager` |
| `exp` | General Expense | `dept_manager → finance_bp` |
| `trv` | Travel Expense | `dept_manager → finance_bp` |
| `mil` | Mileage Claim | `dept_manager → finance_bp` |
| `cfm` | Custom Form (default) | `dept_manager` |

Custom Form types (`cfm_<code>`) appear automatically in the tab list when created. They inherit the `cfm` workflow unless given their own override.

**Available role tokens (step role selector):**

| Token | Resolves to |
|-------|-------------|
| `dept_manager` | Dept manager of the document creator's department |
| `gm_or_opm` | GM or OPM, resolved at runtime via Dept-GM/OPM Mapping |
| `gm` | Assigned GM user |
| `opm` | Assigned OPM user |
| `procurement_manager` | Assigned Procurement Manager user |
| `finance_manager` | Assigned Finance Manager user |
| `finance_bp` | All users with Finance BP role |
| `ap_clerk` | All users with AP Clerk role |

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **WF-P-001** | System Admin can add, remove, and reorder steps for any action key without code changes or service restart. |
| **WF-P-002** | Changes to workflow definitions apply to new submissions only; in-flight documents continue on the workflow that was active at time of submission. |
| **WF-P-003** | If `workflow_defs[action_key]` is missing or empty, the engine falls back to a single `dept_manager` step and logs a warning. |
| **WF-P-004** | The UI prevents saving a workflow with zero steps; at least one step is required per action key. |
| **WF-P-005** | Workflow definitions are seeded with the defaults listed above on first-boot if `CompanyConfig.workflow_defs` is empty. |

**API:** `GET /api/v1/config` (reads `workflow_defs`), `PATCH /api/v1/config` (writes `workflow_defs`). Both calls are made to **EPMS API** (`:8000`); `approval-api` reads from the same `CompanyConfig` row.

---

## 6. Routing

| Path | Access | Component |
|------|--------|-----------|
| `/login` | Public (redirects if authenticated) | `LoginPage` |
| `/` | Protected | `PortalHome` |
| `/admin` | Protected + `system_admin` | `AdminPanel` |

---

## 7. Tech Stack

| Layer | Choice |
|-------|--------|
| Framework | React 19 + Vite |
| State | Zustand 5 + persist middleware |
| Server state | TanStack Query v5 |
| Styling | Tailwind CSS v4 (same token set as EPMS) |
| Icons | Lucide React |
| Auth backend | EPMS API (`:8000`) |
| Task backend | EPMS API + expense-api (`:8006`) |

---

## 8. EPMS Admin Panel — Moved Sections

The following sections were removed from EPMS Admin Panel and replaced with a redirect notice pointing to Portal Admin:

| Moved section | Portal path |
|--------------|-------------|
| Company Settings | `/admin` → Company Settings |
| Security | `/admin` → Security |
| Departments | `/admin` → Departments |
| User Management | `/admin` → User Management |
| Currency Settings | `/admin` → Currency Settings |
| Notification Settings | `/admin` → Notification Settings |
| **Approval Workflows** | **`/admin` → Approval Workflows (§5.8)** |

**Rationale for moving Approval Workflows:** `approval-api` is a shared service consumed by both EPMS (PR, PO, PA) and OA (EXP, TRV, MIL, PA-DIR). Workflow configuration belongs in the unified Portal Admin — not in EPMS Admin which only one module owns. EPMS Admin retains a read-only summary panel with a "Configure in Portal Admin →" link.

EPMS Admin Panel retains: Email Templates, PDF Templates, Dept GM/OPM Mapping, Vendor Settings, Prepayment Config, Budget Config, Collection Config, Role Management, Custom Roles, Access Matrix.

---

## 9. Future Work

| Item | Target Phase |
|------|-------------|
| Token refresh (silent re-login when access token nears expiry) | P3 |
| Personnel module integration (re-add sidebar nav + module card when ready) | P3 |
| Audit Log module (re-add sidebar nav + module card when ready) | P3 |
| Portal-level notification bell (real-time task count) | P3 |
| Role-based module visibility (hide modules user has no access to) | P3 |
| `VITE_PORTAL_URL` deep-link back from all modules | P3 |
