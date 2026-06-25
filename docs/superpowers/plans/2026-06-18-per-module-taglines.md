# Per-Module Taglines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each module (Portal, EPMS, OA, VMS, and any future module) its own tagline while sharing one company logo and name.

**Architecture:** Backend `company_config` keeps `tagline` as Portal's tagline and adds a `module_taglines` JSONB map for the other modules. `GET /config/public/branding?module=<key>` resolves the right tagline. Each frontend fetches its own module key; the Portal AdminPanel edits all four in one symmetric form.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + Postgres (epms-api); React + TypeScript + Vite + TanStack Query + Zustand (portal/epms/oa/vms).

**Spec:** `docs/superpowers/specs/2026-06-18-per-module-taglines-design.md`

**Module keys:** `portal` (stored in `tagline`), `epms` / `oa` / `vms` (stored in `module_taglines`).

**Test DB note:** epms-api tests build schema from `Base.metadata.create_all` (see `tests/conftest.py:108-109`), so a new model column appears in tests automatically — the Alembic migration is only for real databases. Run tests with local Postgres per the team convention (override `POSTGRES_*` to the local docker `uniops_postgres`).

---

## File Structure

**Backend (epms-api):**
- Modify `app/models/config.py` — add `module_taglines` column.
- Create `alembic/versions/x7_add_module_taglines.py` — add column to real DBs.
- Modify `app/schemas/config.py` — `ConfigUpdate` + `ConfigResponse` gain `module_taglines`.
- Modify `app/api/v1/config.py` — `PublicBrandingResponse` + `?module=` resolution.
- Modify `tests/test_config.py` — round-trip + branding resolution tests.

**Portal:**
- Modify `portal/src/hooks/useBranding.ts` — accept a `module` arg.
- Modify `portal/src/components/layout/PortalSidebar.tsx` — pass `'portal'`.
- Modify `portal/src/pages/LoginPage.tsx` — pass `'portal'` to its branding query.
- Modify `portal/src/pages/admin/AdminPanel.tsx` — `CompanyConfig` type + module-tagline editor.

**EPMS:**
- Modify `epms/src/services/config.ts` — `CompanyConfig` gains `module_taglines`.
- Modify `epms/src/pages/auth/LoginPage.tsx` — fetch `?module=epms`.
- Modify `epms/src/components/layout/Sidebar.tsx` — resolve EPMS tagline.

**OA:**
- Create `oa/src/hooks/useBranding.ts`.
- Modify `oa/src/components/layout/AppLayout.tsx` — shared logo + OA tagline.

**VMS:**
- Create `vms/src/hooks/useBranding.ts`.
- Modify `vms/src/components/layout/AppLayout.tsx` — shared logo + VMS tagline.

---

## Task 1: Backend storage — model, migration, schema

**Files:**
- Modify: `epms-api/app/models/config.py` (after line 27, the `delivery_address` column)
- Create: `epms-api/alembic/versions/x7_add_module_taglines.py`
- Modify: `epms-api/app/schemas/config.py` (`ConfigUpdate` ~line 160, `ConfigResponse` ~line 216)
- Test: `epms-api/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `epms-api/tests/test_config.py`:

```python
# ── Per-module taglines ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_module_taglines_round_trips(admin_client):
    r = await admin_client.patch(CONFIG_URL, json={
        "tagline": "Unified Operations Hub",
        "module_taglines": {"epms": "Procurement", "oa": "Office Automation", "vms": "Visitor Management"},
    })
    assert r.status_code == 200
    data = r.json()
    assert data["tagline"] == "Unified Operations Hub"
    assert data["module_taglines"]["epms"] == "Procurement"
    assert data["module_taglines"]["oa"] == "Office Automation"
    assert data["module_taglines"]["vms"] == "Visitor Management"


@pytest.mark.asyncio
async def test_module_taglines_default_empty(admin_client):
    r = await admin_client.get(CONFIG_URL)
    assert r.status_code == 200
    assert r.json()["module_taglines"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_update_module_taglines_round_trips tests/test_config.py::test_module_taglines_default_empty -v`
Expected: FAIL — `KeyError: 'module_taglines'` (field not in response yet).

- [ ] **Step 3: Add the model column**

In `epms-api/app/models/config.py`, immediately after the `delivery_address` column (line 27):

```python
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Per-module taglines ─────────────────────────────────────────────────
    # `tagline` above is the Portal tagline. This map holds the OTHER modules:
    # {"epms": "...", "oa": "...", "vms": "..."}. Adding a future module = a new
    # key here (no migration). Blank/missing → caller falls back to `tagline`.
    module_taglines: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
```

(`JSONB` is already imported at the top of the file.)

- [ ] **Step 4: Add `module_taglines` to the schemas**

In `epms-api/app/schemas/config.py`, in `class ConfigUpdate`, after the `tagline` line (~line 159):

```python
    tagline: str | None = None
    module_taglines: dict[str, str] | None = None
```

In `class ConfigResponse`, after its `tagline` line (~line 213):

```python
    tagline: str
    module_taglines: dict[str, Any]
```

(`Any` is already imported.)

- [ ] **Step 5: Create the Alembic migration**

Create `epms-api/alembic/versions/x7_add_module_taglines.py`:

```python
"""company_config.module_taglines — per-module taglines

Revision ID: x7_add_module_taglines
Revises: x6_match_tolerance
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "x7_add_module_taglines"
down_revision = "x6_match_tolerance"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("company_config",
                  sa.Column("module_taglines", JSONB(),
                            nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("company_config", "module_taglines")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py::test_update_module_taglines_round_trips tests/test_config.py::test_module_taglines_default_empty -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add epms-api/app/models/config.py epms-api/app/schemas/config.py epms-api/alembic/versions/x7_add_module_taglines.py epms-api/tests/test_config.py
git commit -m "feat(epms-api): add module_taglines to company config"
```

---

## Task 2: Backend — public branding resolves per module

**Files:**
- Modify: `epms-api/app/api/v1/config.py:32-63` (`PublicBrandingResponse` + endpoint)
- Test: `epms-api/tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `epms-api/tests/test_config.py`:

```python
BRANDING_URL = "/api/v1/config/public/branding"


@pytest.mark.asyncio
async def test_branding_no_module_returns_portal_tagline(admin_client, client):
    await admin_client.patch(CONFIG_URL, json={
        "tagline": "Portal Hub",
        "module_taglines": {"epms": "Procurement"},
    })
    r = await client.get(BRANDING_URL)
    assert r.status_code == 200
    assert r.json()["tagline"] == "Portal Hub"


@pytest.mark.asyncio
async def test_branding_module_returns_mapped_tagline(admin_client, client):
    await admin_client.patch(CONFIG_URL, json={
        "tagline": "Portal Hub",
        "module_taglines": {"epms": "Procurement"},
    })
    r = await client.get(BRANDING_URL, params={"module": "epms"})
    assert r.status_code == 200
    body = r.json()
    assert body["tagline"] == "Procurement"
    assert body["module"] == "epms"


@pytest.mark.asyncio
async def test_branding_blank_module_falls_back_to_portal(admin_client, client):
    await admin_client.patch(CONFIG_URL, json={
        "tagline": "Portal Hub",
        "module_taglines": {"epms": "Procurement"},
    })
    # oa has no entry → falls back to Portal tagline
    r = await client.get(BRANDING_URL, params={"module": "oa"})
    assert r.status_code == 200
    assert r.json()["tagline"] == "Portal Hub"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -k branding -v`
Expected: FAIL — `KeyError: 'module'` and `assert 'Portal Hub' == 'Procurement'` (endpoint ignores module).

- [ ] **Step 3: Update the response model and endpoint**

In `epms-api/app/api/v1/config.py`, replace `class PublicBrandingResponse` (lines 32-35):

```python
class PublicBrandingResponse(BaseModel):
    name: str
    tagline: str
    logo_data_url: str | None
    module: str | None = None
```

Replace the endpoint (lines 55-63):

```python
@router.get("/public/branding", response_model=PublicBrandingResponse)
async def get_public_branding(db: SessionDep, module: str | None = None):
    """Public endpoint — company name, logo, and the tagline for `module`.

    `tagline` column is the Portal tagline; other modules live in
    `module_taglines`. A missing/blank module entry falls back to the Portal
    tagline so a module never renders an empty subtitle. No `module` param =
    Portal tagline (backward compatible).
    """
    cfg = await config_crud.get_or_create(db)
    if module and module != "portal":
        tagline = (cfg.module_taglines or {}).get(module) or cfg.tagline
    else:
        tagline = cfg.tagline
    return PublicBrandingResponse(
        name=cfg.name,
        tagline=tagline,
        logo_data_url=cfg.logo_data_url,
        module=module,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -k branding -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full config test file (no regressions)**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/api/v1/config.py epms-api/tests/test_config.py
git commit -m "feat(epms-api): resolve public branding tagline per module"
```

---

## Task 3: Portal — fetch and show the Portal tagline

**Files:**
- Modify: `portal/src/hooks/useBranding.ts`
- Modify: `portal/src/components/layout/PortalSidebar.tsx:36` (the `useBranding()` call)
- Modify: `portal/src/pages/LoginPage.tsx:15-22` (its `useBranding` query)

- [ ] **Step 1: Make the hook module-aware**

Replace the body of `portal/src/hooks/useBranding.ts`:

```typescript
import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface PublicBranding {
  name: string
  tagline: string | null
  logo_data_url: string | null
  module?: string | null
}

/**
 * Company branding (name / tagline / logo) for a specific module.
 * `module` selects which tagline the backend resolves (default 'portal').
 * Name and logo are shared across all modules.
 */
export function useBranding(module = 'portal') {
  return useQuery<PublicBranding>({
    queryKey: ['public-branding', module],
    queryFn: () => epmsApi.get<PublicBranding>(`/config/public/branding?module=${module}`),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
```

- [ ] **Step 2: Pass the module key in PortalSidebar**

In `portal/src/components/layout/PortalSidebar.tsx`, change the hook call (line 36):

```typescript
  const { data: branding } = useBranding('portal')
```

- [ ] **Step 3: Switch LoginPage to the shared hook**

In `portal/src/pages/LoginPage.tsx`, delete the local `PublicBranding` interface and `useBranding` function (lines 9-22) and import the shared hook. Add to the imports near the top:

```typescript
import { useBranding } from '@/hooks/useBranding'
```

The existing `const { data: branding } = useBranding()` call inside `LoginPage` now resolves to the shared hook with the default `'portal'` module — no further change needed. Remove the now-unused `useQuery` and `epmsApi` imports **only if** nothing else in the file uses them (grep first).

- [ ] **Step 4: Typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0, no errors.

- [ ] **Step 5: Commit**

```bash
git add portal/src/hooks/useBranding.ts portal/src/components/layout/PortalSidebar.tsx portal/src/pages/LoginPage.tsx
git commit -m "feat(portal): fetch Portal tagline via module-aware branding hook"
```

---

## Task 4: Portal AdminPanel — edit all four taglines

**Files:**
- Modify: `portal/src/pages/admin/AdminPanel.tsx:25-30` (`CompanyConfig` interface)
- Modify: `portal/src/pages/admin/AdminPanel.tsx:136-236` (`CompanySettings` component)

- [ ] **Step 1: Add `module_taglines` to the local config type**

In `portal/src/pages/admin/AdminPanel.tsx`, inside `interface CompanyConfig`, after the `tagline` line (line 27):

```typescript
  tagline: string
  module_taglines: Record<string, string>
```

- [ ] **Step 2: Add the module registry constant**

Directly above `function CompanySettings()` (line 138), add:

```typescript
// Modules that have their own tagline. Add a row here when a new module ships;
// the module's own app fetches `/config/public/branding?module=<key>`.
// 'portal' is backed by the existing top-level `tagline` field.
const MODULE_TAGLINE_KEYS: { key: string; label: string }[] = [
  { key: 'portal', label: 'Portal' },
  { key: 'epms', label: 'EPMS' },
  { key: 'oa', label: 'OA' },
  { key: 'vms', label: 'VMS' },
]
```

- [ ] **Step 3: Track per-module tagline edits in state**

In `CompanySettings()`, after the `logoFileName` state (line 143), add:

```typescript
  // Per-module tagline overrides for epms/oa/vms (portal uses `tagline`).
  // undefined = not touched this session; falls back to cfg.module_taglines.
  const [moduleTaglines, setModuleTaglines] = useState<Record<string, string> | undefined>(undefined)

  const currentModuleTaglines = moduleTaglines ?? cfg?.module_taglines ?? {}

  const taglineFor = (key: string): string =>
    key === 'portal' ? values.tagline : (currentModuleTaglines[key] ?? '')

  const setTaglineFor = (key: string, val: string) => {
    if (key === 'portal') { setForm((p) => ({ ...p, tagline: val })); return }
    setModuleTaglines({ ...currentModuleTaglines, [key]: val })
  }
```

- [ ] **Step 4: Include `module_taglines` in the save payload**

In `handleSubmit`, replace the `body` construction (lines 177-179):

```typescript
      const body: any = { name: values.name, tagline: values.tagline, delivery_address: values.delivery_address }
      if (moduleTaglines !== undefined) body.module_taglines = moduleTaglines
      if (logoDataUrl !== undefined) body.logo_data_url = logoDataUrl
      if (logoFileName !== undefined) body.logo_file_name = logoFileName
```

- [ ] **Step 5: Replace the single Tagline field with the module editor**

In the returned form, replace the existing Tagline `Field` (lines 224-226):

```tsx
      <Field label="Company Name"><Input value={values.name} onChange={f('name')} required /></Field>

      {/* Per-module taglines — shared logo + name, but each module shows its own tagline */}
      <div className="flex flex-col gap-3">
        <p className="text-sm font-semibold text-neutral-700">Module Taglines</p>
        <p className="text-xs text-neutral-400 -mt-2">
          Each module shows the shared logo and company name, with its own tagline below.
          Leave a module blank to reuse the Portal tagline.
        </p>
        {MODULE_TAGLINE_KEYS.map((m) => (
          <Field key={m.key} label={`${m.label} Tagline`}>
            <Input value={taglineFor(m.key)} onChange={(e) => setTaglineFor(m.key, e.target.value)} />
          </Field>
        ))}
      </div>
```

- [ ] **Step 6: Typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0, no errors.

- [ ] **Step 7: Commit**

```bash
git add portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(portal): edit per-module taglines in Company Settings"
```

---

## Task 5: EPMS — show the EPMS tagline (login + sidebar)

**Files:**
- Modify: `epms/src/services/config.ts:131` (`CompanyConfig` interface)
- Modify: `epms/src/pages/auth/LoginPage.tsx:16-24` (`fetchBranding`)
- Modify: `epms/src/components/layout/Sidebar.tsx:119` (`companyTagline`)

- [ ] **Step 1: Add `module_taglines` to the EPMS config type**

In `epms/src/services/config.ts`, inside `interface CompanyConfig`, after the `tagline` line (line 131):

```typescript
  tagline: string
  module_taglines: Record<string, string>
```

- [ ] **Step 2: Fetch the EPMS tagline on the login page**

In `epms/src/pages/auth/LoginPage.tsx`, change the fetch URL in `fetchBranding` (line 19):

```typescript
    const res = await fetch(`${base}/config/public/branding?module=epms`)
```

- [ ] **Step 3: Resolve the EPMS tagline in the sidebar**

In `epms/src/components/layout/Sidebar.tsx`, change `companyTagline` (line 119):

```typescript
  const companyTagline = config?.module_taglines?.epms ?? config?.tagline ?? ''
```

- [ ] **Step 4: Typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0. (If the project is not TS 6, drop the `--ignoreDeprecations 6.0` flag.)

- [ ] **Step 5: Commit**

```bash
git add epms/src/services/config.ts epms/src/pages/auth/LoginPage.tsx epms/src/components/layout/Sidebar.tsx
git commit -m "feat(epms): show EPMS-specific tagline on login and sidebar"
```

---

## Task 6: OA — shared logo + OA tagline in the sidebar

**Files:**
- Create: `oa/src/hooks/useBranding.ts`
- Modify: `oa/src/components/layout/AppLayout.tsx:61-104` (Sidebar brand area)

- [ ] **Step 1: Create the OA branding hook**

Create `oa/src/hooks/useBranding.ts`:

```typescript
import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface PublicBranding {
  name: string
  tagline: string | null
  logo_data_url: string | null
  module?: string | null
}

/** OA branding — shared company name/logo from epms-api, OA-specific tagline. */
export function useBranding() {
  return useQuery<PublicBranding>({
    queryKey: ['public-branding', 'oa'],
    queryFn: () => epmsApi.get<PublicBranding>('/config/public/branding?module=oa'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
```

- [ ] **Step 2: Use shared logo + tagline in the OA sidebar**

In `oa/src/components/layout/AppLayout.tsx`, add the import near the other imports at the top of the file:

```typescript
import { useBranding } from '@/hooks/useBranding'
```

Inside `function Sidebar(...)`, after `const actionCount = useActionCount()` (line 72), add:

```typescript
  const { data: branding } = useBranding()
  const brandName = branding?.name || 'UniOps'
  const brandTagline = branding?.tagline || 'Expense & Payment'
  const brandLogo = branding?.logo_data_url || null
  const brandInitials = brandName.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'OA'
```

Replace the expanded brand block (lines 89-99):

```tsx
        {!collapsed && (
          <div className="flex items-center gap-2.5 min-w-0">
            {brandLogo ? (
              <img src={brandLogo} alt={brandName} className="h-8 w-8 shrink-0 rounded-md bg-white object-contain p-0.5" />
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/15">
                <span className="text-xs font-bold text-white">{brandInitials}</span>
              </div>
            )}
            <div className="min-w-0">
              <p className="text-sm font-bold text-white/95 leading-tight truncate">{brandName}</p>
              <p className="text-[10px] text-white/45 leading-tight truncate">{brandTagline}</p>
            </div>
          </div>
        )}
```

Replace the collapsed brand block (lines 100-104):

```tsx
        {collapsed && (
          brandLogo ? (
            <img src={brandLogo} alt={brandName} className="mx-auto h-8 w-8 rounded-md bg-white object-contain p-0.5" />
          ) : (
            <div className="mx-auto flex h-8 w-8 items-center justify-center rounded-md bg-white/15">
              <span className="text-xs font-bold text-white">{brandInitials}</span>
            </div>
          )
        )}
```

- [ ] **Step 3: Typecheck**

Run: `cd oa && npx tsc -p tsconfig.app.json --noEmit`
Expected: exit 0. (Add `--ignoreDeprecations 6.0` if it reports a baseUrl deprecation error.)

- [ ] **Step 4: Commit**

```bash
git add oa/src/hooks/useBranding.ts oa/src/components/layout/AppLayout.tsx
git commit -m "feat(oa): show shared logo and OA tagline in sidebar"
```

---

## Task 7: VMS — shared logo + VMS tagline in the sidebar

**Files:**
- Create: `vms/src/hooks/useBranding.ts`
- Modify: `vms/src/components/layout/AppLayout.tsx:54-103` (Sidebar brand area)

- [ ] **Step 1: Create the VMS branding hook**

Create `vms/src/hooks/useBranding.ts`:

```typescript
import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export interface PublicBranding {
  name: string
  tagline: string | null
  logo_data_url: string | null
  module?: string | null
}

/** VMS branding — shared company name/logo from epms-api, VMS-specific tagline. */
export function useBranding() {
  return useQuery<PublicBranding>({
    queryKey: ['public-branding', 'vms'],
    queryFn: () => epmsApi.get<PublicBranding>('/config/public/branding?module=vms'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
```

- [ ] **Step 2: Use shared logo + tagline in the VMS sidebar**

In `vms/src/components/layout/AppLayout.tsx`, add the import near the other imports at the top:

```typescript
import { useBranding } from '@/hooks/useBranding'
```

Inside `function Sidebar(...)`, after `const taskCount = tasks?.length ?? 0` (line 67), add:

```typescript
  const { data: branding } = useBranding()
  const brandName = branding?.name || 'UniOps'
  const brandTagline = branding?.tagline || 'Visitor Management'
  const brandLogo = branding?.logo_data_url || null
  const brandInitials = brandName.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'VMS'
```

Replace the expanded brand block (lines 88-98):

```tsx
        {!collapsed && (
          <div className="flex items-center gap-2.5 min-w-0">
            {brandLogo ? (
              <img src={brandLogo} alt={brandName} className="h-8 w-8 shrink-0 rounded-md bg-white object-contain p-0.5" />
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/15">
                <span className="text-xs font-bold text-white">{brandInitials}</span>
              </div>
            )}
            <div className="min-w-0">
              <p className="text-sm font-bold text-white/95 leading-tight truncate">{brandName}</p>
              <p className="text-[10px] text-white/45 leading-tight truncate">{brandTagline}</p>
            </div>
          </div>
        )}
```

Replace the collapsed brand block (lines 99-103):

```tsx
        {collapsed && (
          brandLogo ? (
            <img src={brandLogo} alt={brandName} className="mx-auto h-8 w-8 rounded-md bg-white object-contain p-0.5" />
          ) : (
            <div className="mx-auto flex h-8 w-8 items-center justify-center rounded-md bg-white/15">
              <span className="text-[10px] font-bold text-white">{brandInitials}</span>
            </div>
          )
        )}
```

- [ ] **Step 3: Typecheck**

Run: `cd vms && npx tsc -p tsconfig.app.json --noEmit`
Expected: exit 0. (Add `--ignoreDeprecations 6.0` if it reports a baseUrl deprecation error.)

- [ ] **Step 4: Commit**

```bash
git add vms/src/hooks/useBranding.ts vms/src/components/layout/AppLayout.tsx
git commit -m "feat(vms): show shared logo and VMS tagline in sidebar"
```

---

## Task 8: Apply the migration & end-to-end verification

**Files:** none (operational).

- [ ] **Step 1: Apply the migration to the dev DB**

Run: `cd epms-api && python -m alembic upgrade head`
Expected: applies `x7_add_module_taglines`; `python -m alembic current` shows `x7_add_module_taglines`.

- [ ] **Step 2: Manual end-to-end check**

1. Start epms-api + all four frontends.
2. Portal → Admin → Company Settings: upload a logo and set four distinct taglines (Portal / EPMS / OA / VMS). Save.
3. Verify each surface shows the shared logo + its own tagline:
   - Portal sidebar + Portal login page → Portal tagline.
   - EPMS sidebar + EPMS login page → EPMS tagline.
   - OA sidebar → OA tagline.
   - VMS sidebar → VMS tagline.
4. Clear the OA tagline in Company Settings, save, reload OA → its subtitle falls back to the Portal tagline (no blank).

- [ ] **Step 3: Final commit (if any docs/notes changed)**

```bash
git add -A
git commit -m "chore: per-module taglines verification notes" --allow-empty
```

---

## Self-Review

**Spec coverage:**
- Data model (`tagline` = Portal, `module_taglines` for others) → Task 1. ✓
- Resolution rule + `?module=` + backward-compat no-param → Task 2. ✓
- Portal display (login + sidebar) → Task 3. ✓
- Editor in Portal AdminPanel, 4 symmetric rows, registry constant → Task 4. ✓
- EPMS display (login + sidebar, reads full config) → Task 5. ✓
- OA shared logo + tagline (sidebar only) → Task 6. ✓
- VMS shared logo + tagline (sidebar only) → Task 7. ✓
- Migration + cross-origin already-allowed + manual verify → Tasks 1 & 8. ✓
- Tests (mapped/no-param/fallback/round-trip) → Tasks 1 & 2. ✓

**Type consistency:** `module_taglines: Record<string, string>` used in portal AdminPanel and epms config type; `dict[str, str]` (update) / `dict[str, Any]` (response) in backend; `PublicBranding` interface adds optional `module` in every frontend hook; resolver reads `cfg.module_taglines` (guarded with `or {}`). Hook is `useBranding(module='portal')` in Portal and zero-arg `useBranding()` in OA/VMS (each pins its own key). Consistent.

**Placeholder scan:** No TBD/TODO; every code step has complete code. ✓
