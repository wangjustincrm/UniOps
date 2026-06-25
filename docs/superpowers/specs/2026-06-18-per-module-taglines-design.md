# Per-Module Taglines — Design

**Date:** 2026-06-18
**Status:** Approved (design)
**Branch:** feature/vms-gmp-checkin-order

## Problem

Company Settings has a single `tagline`. We want each module (Portal, EPMS, OA, VMS,
and any future module) to show its **own** tagline, while all modules share the **same**
company logo and company name. The current global `tagline` IS the Portal tagline.

## Decisions (locked)

- **Four taglines, one per module**: Portal, EPMS, OA, VMS. No separate "global/fallback"
  field surfaced to the admin. The module list is a frontend constant; adding a future
  module = add one entry to that array (no DB migration).
- **Editor lives in Portal AdminPanel only** (EPMS AdminPanel keeps its single field
  untouched for now).
- **Display**: each module shows its tagline on its login page (where one exists) and in
  its in-app sidebar. OA and VMS have **no login page** (Portal SSO handoff), so for them
  this is sidebar-only.
- **Shared logo**: OA and VMS currently render a text badge ("OA" / "VMS"). These are
  replaced by the shared company logo (fallback: company-name initials), with the module
  tagline beneath the shared company name.

## Data model

Reuse + extend `company_config` (epms-api):

- **`tagline`** (existing column) = **Portal's tagline**. Unchanged storage, zero migration,
  Portal behavior identical to today.
- **`module_taglines`** (new `JSONB`, default `{}`) = taglines for the *other* modules,
  keyed by module: `{"epms": "...", "oa": "...", "vms": "..."}`.

Asymmetry (Portal in `tagline`, others in the map) is a storage detail hidden behind a
symmetric 4-row admin UI. Documented in the model so it isn't surprising later.

### Resolution rule (single source of truth)

```
tagline_for(module):
    if module in (None, "portal"):
        return cfg.tagline
    return cfg.module_taglines.get(module) or cfg.tagline   # silent safety fallback
```

The `or cfg.tagline` is only to avoid a blank subtitle before an admin fills a field. It is
not a user-facing "global tagline" concept.

## Backend changes (epms-api)

1. **`app/models/config.py`** — add `module_taglines: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")`.
2. **Alembic migration** — add `module_taglines` column with `server_default='{}'`.
3. **`app/schemas/config.py`**
   - `ConfigUpdate`: add `module_taglines: dict[str, str] | None = None`.
   - `ConfigResponse`: add `module_taglines: dict[str, Any]`.
   - CRUD `update()` is generic (`model_dump(exclude_none=True)` + `setattr`), so no CRUD
     change needed.
4. **`app/api/v1/config.py`** — `GET /config/public/branding` gains optional `?module=<key>`:
   - `PublicBrandingResponse` adds `module: str | None`.
   - Resolves tagline via the rule above. `name` and `logo_data_url` returned unchanged.
   - **No `module` param → returns `cfg.tagline`** (current behavior, fully backward compatible).

## Frontend changes

Each app is a separate Vite project (no shared package), so each gets its own minimal
branding fetch. All show shared logo + shared name + per-module tagline.

| Module | Login page | Sidebar | Module key | Tagline source |
|--------|-----------|---------|-----------|----------------|
| Portal | yes (existing branding query) | yes (PortalSidebar) | `portal` | `?module=portal` |
| EPMS   | yes | yes (Sidebar reads full authed `/config`) | `epms` | login: `?module=epms`; sidebar: `config.module_taglines.epms ?? config.tagline` |
| OA     | none (SSO handoff) | yes (AppLayout Sidebar) | `oa` | `?module=oa` via existing OA→epms client |
| VMS    | none (SSO handoff) | yes (AppLayout Sidebar) | `vms` | `?module=vms` via existing VMS→epms client |

### Portal
- Extend `portal/src/hooks/useBranding.ts` to accept a `module` arg, append `?module=`,
  and key the query `['public-branding', module]`.
- `PortalSidebar.tsx` and `LoginPage.tsx` pass `'portal'`. (LoginPage may be switched to the
  shared hook; optional, not required.)

### EPMS
- `epms/src/pages/auth/LoginPage.tsx` — append `?module=epms` to its public branding fetch.
- `epms/src/components/layout/Sidebar.tsx` — resolve `companyTagline` as
  `config?.module_taglines?.epms ?? config?.tagline ?? ''`.

### OA
- Add `oa/src/hooks/useBranding.ts` calling the OA→epms client with `?module=oa`.
- `oa/src/components/layout/AppLayout.tsx` Sidebar — replace the hardcoded "OA" badge with the
  shared company logo (fallback: name initials); show company name + OA tagline.

### VMS
- Add `vms/src/hooks/useBranding.ts` calling the VMS→epms client with `?module=vms`.
- `vms/src/components/layout/AppLayout.tsx` Sidebar — same treatment as OA.

### Company Settings editor (Portal AdminPanel only)
- Add a module registry constant, e.g.
  `MODULE_TAGLINE_KEYS = [{ key: 'portal', label: 'Portal' }, { key: 'epms', label: 'EPMS' }, { key: 'oa', label: 'OA' }, { key: 'vms', label: 'VMS' }]`.
- Render one input per entry under a "Module Taglines" block. The **Portal** row binds to the
  existing `tagline` field; **EPMS/OA/VMS** rows bind to `module_taglines[key]`.
- Save sends `tagline` (from Portal row) + `module_taglines` (the other three) in one
  PATCH `/config`.
- Future module: add one entry to `MODULE_TAGLINE_KEYS` and have that app fetch
  `?module=<newkey>`.

## Compatibility & risk

- **Cross-origin**: OA and VMS already call epms-api (budget/EPMS data); CORS already allows
  their origins. The branding endpoint is public/no-auth. Low risk.
- **Backward compat**: `?module` is optional; existing callers unaffected. `module_taglines`
  defaults to `{}`, so unconfigured modules silently fall back to the Portal tagline.

## Out of scope

- EPMS AdminPanel editor changes (editor is Portal-only).
- Per-module logos (logo is shared by explicit decision).
- Localized/i18n taglines.

## Testing

- Backend: unit test `GET /config/public/branding?module=epms` returns the mapped tagline;
  no-param returns `cfg.tagline`; unknown/blank module falls back to `cfg.tagline`.
- Backend: `PATCH /config` with `module_taglines` persists and round-trips in `ConfigResponse`.
- Frontend: manual verification — set 4 distinct taglines in Portal Company Settings, then
  confirm each module's sidebar (and EPMS/Portal login) shows its own tagline with the shared
  logo.
