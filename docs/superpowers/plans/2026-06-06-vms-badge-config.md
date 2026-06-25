# VMS Badge Structured Config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the entire visitor badge editable by admins through a structured form, with a live preview that exactly matches the printed badge.

**Architecture:** Add a single global `badge_config` JSONB to `vms_config`. The backend serves it deep-merged over code defaults that reproduce today's badge. The frontend `BadgePreview` becomes a config-driven renderer (keeping the existing print-CSS skeleton) shared by the print page and a new structured admin editor. Spec: `docs/superpowers/specs/2026-06-06-vms-badge-config-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + Pydantic v2 (vms-api); React + TypeScript + React Query + Tailwind + Vite (vms). Backend tests: pytest. Frontend verification: `tsc -b` + `eslint` (no FE test runner exists — do not add one).

---

## File Structure

**Backend (`vms-api`)**
- Create `app/services/badge_config.py` — `DEFAULT_BADGE_CONFIG`, `META_FIELD_KEYS`, `deep_merge`, `load_badge_config`. Single source of defaults + merge logic.
- Create `app/schemas/badge_config.py` — `BadgeConfig` nested Pydantic models + validators.
- Modify `app/models/vms_config.py` — add `badge_config` column.
- Modify `app/api/v1/admin.py` — add `GET/PUT /admin/badge-config`.
- Create `alembic/versions/20260606_0011_badge_config.py` — add column.
- Create `tests/test_badge_config.py` — endpoint + merge + validation tests.

**Frontend (`vms`)**
- Modify `src/services/api.ts` — `BadgeConfig` types, `BADGE_CONFIG_DEFAULTS`, `useBadgeConfig`, `useUpdateBadgeConfig`; remove dead `BadgeTemplate` hooks.
- Rewrite `src/components/BadgePreview.tsx` — config-driven renderer.
- Modify `src/pages/BadgePrintPage.tsx` — load config, pass to renderer.
- Rewrite `src/pages/admin/BadgeTemplatesPage.tsx` — structured form + live preview.
- Modify `src/pages/admin/AdminPanel.tsx` — rename tab label "Badge Templates" → "Badge".

**Decisions locked in here:**
- Endpoints live in `admin.py` (`/admin/badge-config`), matching the other singleton-config endpoints — not the dead `/badge/templates` router.
- Meta-field reordering uses up/down buttons (no drag-and-drop library — zero new deps).
- The old `badge_templates` DB column and `/badge/templates` endpoints are left in place (no risky drop migration); only the UI stops using them.

---

## Task 1: Backend — defaults + merge helper

**Files:**
- Create: `vms-api/app/services/badge_config.py`
- Test: `vms-api/tests/test_badge_config.py`

- [ ] **Step 1: Write the failing test**

Create `vms-api/tests/test_badge_config.py`:

```python
"""Badge structured-config: defaults, deep-merge, schema, endpoints."""
import pytest

from app.services.badge_config import (
    DEFAULT_BADGE_CONFIG,
    META_FIELD_KEYS,
    deep_merge,
)


def test_default_config_shape():
    cfg = DEFAULT_BADGE_CONFIG
    assert cfg["band"]["title"] == "VISITOR"
    assert set(cfg["band"]["areas"].keys()) == {
        "office", "warehouse", "production_non_gmp",
        "production_gmp", "laboratory", "all",
    }
    assert [m["key"] for m in cfg["meta_fields"]][:3] == [
        "visit_date", "host", "valid_until",
    ]
    assert cfg["qr"]["show"] is True
    assert cfg["footer"]["lines"][0].startswith("Must be accompanied")
    assert cfg["style"]["name_size_pt"] == 22


def test_meta_field_keys_whitelist():
    assert META_FIELD_KEYS == {
        "visit_date", "host", "valid_until",
        "vehicle_plate", "accompanying_count", "visit_purpose",
    }


def test_deep_merge_overrides_nested_without_dropping_siblings():
    merged = deep_merge(DEFAULT_BADGE_CONFIG, {"band": {"title": "GUEST"}})
    assert merged["band"]["title"] == "GUEST"
    # Sibling keys under band survive the partial override.
    assert merged["band"]["show_zone"] is True
    assert "areas" in merged["band"]
    # Untouched top-level sections survive.
    assert merged["qr"]["label"] == "Scan to check out"


def test_deep_merge_replaces_lists_wholesale():
    merged = deep_merge(DEFAULT_BADGE_CONFIG, {"footer": {"lines": ["One line"]}})
    assert merged["footer"]["lines"] == ["One line"]


def test_deep_merge_does_not_mutate_default():
    deep_merge(DEFAULT_BADGE_CONFIG, {"band": {"title": "X"}})
    assert DEFAULT_BADGE_CONFIG["band"]["title"] == "VISITOR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vms-api && python -m pytest tests/test_badge_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.badge_config'`

- [ ] **Step 3: Write minimal implementation**

Create `vms-api/app/services/badge_config.py`:

```python
"""Default badge configuration + deep-merge helper.

Single source of truth for what the badge looks like out of the box. The
defaults reproduce the historical hard-coded BadgePreview exactly, so an
empty/partial stored config renders identically to the pre-config badge.
"""
from __future__ import annotations

import copy

# Whitelisted meta-field keys. Each maps to a known visit/visitor accessor in
# the frontend renderer. Admins may toggle/relabel/reorder these but cannot
# bind arbitrary data.
META_FIELD_KEYS = {
    "visit_date",
    "host",
    "valid_until",
    "vehicle_plate",
    "accompanying_count",
    "visit_purpose",
}

DEFAULT_BADGE_CONFIG: dict = {
    "version": 1,
    "band": {
        "title": "VISITOR",
        "show_zone": True,
        "show_risk": True,
        "risk_suffix": "RISK",
        "areas": {
            "office":             {"label": "OFFICE",           "bg": "#10B981", "fg": "#FFFFFF", "risk": "LOW"},
            "warehouse":          {"label": "WAREHOUSE",        "bg": "#F59E0B", "fg": "#1A2730", "risk": "MEDIUM"},
            "production_non_gmp": {"label": "PRODUCTION",       "bg": "#EA580C", "fg": "#FFFFFF", "risk": "MEDIUM"},
            "production_gmp":     {"label": "PRODUCTION (GMP)", "bg": "#DC2626", "fg": "#FFFFFF", "risk": "HIGH"},
            "laboratory":         {"label": "LABORATORY",       "bg": "#DC2626", "fg": "#FFFFFF", "risk": "HIGH"},
            "all":                {"label": "ALL ZONES",        "bg": "#DC2626", "fg": "#FFFFFF", "risk": "HIGH"},
        },
    },
    "identity": {"name_uppercase": True, "show_company": True},
    "meta_fields": [
        {"key": "visit_date",         "label": "Date",         "visible": True},
        {"key": "host",               "label": "Host",         "visible": True},
        {"key": "valid_until",        "label": "Valid until",  "visible": True},
        {"key": "vehicle_plate",      "label": "Vehicle",      "visible": False},
        {"key": "accompanying_count", "label": "Accompanying", "visible": False},
        {"key": "visit_purpose",      "label": "Purpose",      "visible": False},
    ],
    "qr": {"show": True, "label": "Scan to check out"},
    "footer": {
        "show": True,
        "lines": [
            "Must be accompanied by Host at all times",
            "Please return badge when leaving",
        ],
    },
    "style": {
        "name_size_pt": 22,
        "band_title_size_pt": 18,
        "text_color": "#1A2730",
        "company_color": "#4E6070",
        "footer_color": "#4E6070",
        "name_align": "left",
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` onto a deep copy of `base`.

    Dicts merge key-by-key; every other type (including lists) replaces the
    base value wholesale. Never mutates either argument.
    """
    result = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd vms-api && python -m pytest tests/test_badge_config.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add vms-api/app/services/badge_config.py vms-api/tests/test_badge_config.py
git commit -m "feat(vms): badge config defaults + deep-merge helper"
```

---

## Task 2: Backend — BadgeConfig Pydantic schema

**Files:**
- Create: `vms-api/app/schemas/badge_config.py`
- Test: `vms-api/tests/test_badge_config.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `vms-api/tests/test_badge_config.py`:

```python
from app.schemas.badge_config import BadgeConfig
from app.services.badge_config import deep_merge as _dm


def test_badgeconfig_accepts_default():
    cfg = BadgeConfig.model_validate(DEFAULT_BADGE_CONFIG)
    assert cfg.band.title == "VISITOR"
    assert cfg.style.name_align == "left"


def test_badgeconfig_rejects_bad_hex_color():
    bad = _dm(DEFAULT_BADGE_CONFIG, {"style": {"text_color": "blue"}})
    with pytest.raises(ValueError):
        BadgeConfig.model_validate(bad)


def test_badgeconfig_rejects_unknown_meta_key():
    bad = _dm(DEFAULT_BADGE_CONFIG, {})
    bad["meta_fields"] = [{"key": "ssn", "label": "SSN", "visible": True}]
    with pytest.raises(ValueError):
        BadgeConfig.model_validate(bad)


def test_badgeconfig_rejects_bad_align():
    bad = _dm(DEFAULT_BADGE_CONFIG, {"style": {"name_align": "justify"}})
    with pytest.raises(ValueError):
        BadgeConfig.model_validate(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vms-api && python -m pytest tests/test_badge_config.py -k badgeconfig -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.schemas.badge_config'`

- [ ] **Step 3: Write minimal implementation**

Create `vms-api/app/schemas/badge_config.py`:

```python
"""Pydantic schema for the structured badge configuration.

Validates the JSONB stored in `vms_config.badge_config`. Defaults live in
`app.services.badge_config.DEFAULT_BADGE_CONFIG`; this module enforces shape,
hex colors, the meta-field whitelist, and the alignment enum.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, field_validator

from app.services.badge_config import META_FIELD_KEYS

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _check_hex(v: str) -> str:
    if not _HEX.match(v):
        raise ValueError(f"invalid hex color: {v!r} (expected #RRGGBB)")
    return v


class AreaStyle(BaseModel):
    label: str
    bg: str
    fg: str
    risk: str

    @field_validator("bg", "fg")
    @classmethod
    def _hex(cls, v: str) -> str:
        return _check_hex(v)


class Band(BaseModel):
    title: str
    show_zone: bool = True
    show_risk: bool = True
    risk_suffix: str = "RISK"
    areas: dict[str, AreaStyle]


class Identity(BaseModel):
    name_uppercase: bool = True
    show_company: bool = True


class MetaField(BaseModel):
    key: str
    label: str
    visible: bool = True

    @field_validator("key")
    @classmethod
    def _known_key(cls, v: str) -> str:
        if v not in META_FIELD_KEYS:
            raise ValueError(f"unknown meta field key: {v!r}")
        return v


class Qr(BaseModel):
    show: bool = True
    label: str = ""


class Footer(BaseModel):
    show: bool = True
    lines: list[str] = []


class Style(BaseModel):
    name_size_pt: int = 22
    band_title_size_pt: int = 18
    text_color: str = "#1A2730"
    company_color: str = "#4E6070"
    footer_color: str = "#4E6070"
    name_align: Literal["left", "center", "right"] = "left"

    @field_validator("text_color", "company_color", "footer_color")
    @classmethod
    def _hex(cls, v: str) -> str:
        return _check_hex(v)


class BadgeConfig(BaseModel):
    version: int = 1
    band: Band
    identity: Identity
    meta_fields: list[MetaField]
    qr: Qr
    footer: Footer
    style: Style
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd vms-api && python -m pytest tests/test_badge_config.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add vms-api/app/schemas/badge_config.py vms-api/tests/test_badge_config.py
git commit -m "feat(vms): BadgeConfig pydantic schema with validation"
```

---

## Task 3: Backend — model column + migration

**Files:**
- Modify: `vms-api/app/models/vms_config.py:43`
- Create: `vms-api/alembic/versions/20260606_0011_badge_config.py`

- [ ] **Step 1: Add the model column**

In `vms-api/app/models/vms_config.py`, after the `badge_templates` column (line 43), add:

```python
    # ── Structured badge configuration (replaces badge_templates UI) ────────
    # Single global config object; shape validated by schemas.badge_config.
    # Empty dict → renderer falls back to DEFAULT_BADGE_CONFIG.
    badge_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
```

- [ ] **Step 2: Create the migration**

Create `vms-api/alembic/versions/20260606_0011_badge_config.py`:

```python
"""Add structured badge_config to vms_config.

Replaces the freeform badge_templates HTML/CSS (whose editor never reached the
printed badge) with a single structured config object the renderer consumes.
Nullable-safe via server_default '{}'; the renderer falls back to code defaults
when empty, so an unapplied migration cannot 500 the badge — it just disables
editing.

Revision ID: 20260606_0011
Revises: 20260602_0010
Create Date: 2026-06-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260606_0011"
down_revision = "20260602_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vms_config",
        sa.Column(
            "badge_config",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("vms_config", "badge_config")
```

- [ ] **Step 3: Verify model import + metadata still load**

Run: `cd vms-api && python -c "import app.models; from app.models.vms_config import VmsConfig; print('badge_config' in VmsConfig.__table__.columns)"`
Expected: prints `True`

- [ ] **Step 4: Verify the existing suite still imports/collects (create_all picks up the new column)**

Run: `cd vms-api && python -m pytest tests/test_badge_config.py -v`
Expected: PASS (9 passed) — confirms the model change didn't break collection.

- [ ] **Step 5: Commit**

```bash
git add vms-api/app/models/vms_config.py vms-api/alembic/versions/20260606_0011_badge_config.py
git commit -m "feat(vms): add badge_config column + migration"
```

---

## Task 4: Backend — GET/PUT /admin/badge-config endpoints

**Files:**
- Modify: `vms-api/app/api/v1/admin.py`
- Test: `vms-api/tests/test_badge_config.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `vms-api/tests/test_badge_config.py`:

```python
@pytest.mark.asyncio
async def test_badge_config_get_returns_merged_defaults(admin):
    _, client = admin
    resp = await client.get("/api/v1/admin/badge-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Fresh install (empty column) → full default object.
    assert body["band"]["title"] == "VISITOR"
    assert len(body["meta_fields"]) == 6
    assert body["style"]["name_align"] == "left"


@pytest.mark.asyncio
async def test_badge_config_put_persists_and_merges(admin):
    _, client = admin
    # Send a full valid object with one change.
    got = (await client.get("/api/v1/admin/badge-config")).json()
    got["band"]["title"] = "GUEST PASS"
    got["footer"]["lines"] = ["Return at front desk"]
    resp = await client.put("/api/v1/admin/badge-config", json=got)
    assert resp.status_code == 200, resp.text
    assert resp.json()["band"]["title"] == "GUEST PASS"

    again = (await client.get("/api/v1/admin/badge-config")).json()
    assert again["band"]["title"] == "GUEST PASS"
    assert again["footer"]["lines"] == ["Return at front desk"]


@pytest.mark.asyncio
async def test_badge_config_put_rejects_bad_color(admin):
    _, client = admin
    got = (await client.get("/api/v1/admin/badge-config")).json()
    got["style"]["text_color"] = "notacolor"
    resp = await client.put("/api/v1/admin/badge-config", json=got)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_badge_config_get_requires_admin(requester):
    _, client = requester
    resp = await client.get("/api/v1/admin/badge-config")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_badge_config_put_requires_admin(requester):
    _, client = requester
    resp = await client.put("/api/v1/admin/badge-config", json={})
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vms-api && python -m pytest tests/test_badge_config.py -k "endpoint or get_returns or put_persists or requires_admin or rejects_bad_color" -v`
Expected: FAIL with 404 (route not found) on the GET/PUT tests.

- [ ] **Step 3: Write minimal implementation**

In `vms-api/app/api/v1/admin.py`, add this import near the other schema imports (after line 33):

```python
from app.schemas.badge_config import BadgeConfig
from app.services.badge_config import DEFAULT_BADGE_CONFIG, deep_merge
```

Then append these endpoints to the end of the file:

```python
# ── Structured badge config (replaces badge_templates UI) ───────────────────-

@router.get("/badge-config", response_model=BadgeConfig)
async def get_badge_config(
    db: SessionDep,
    _: AdminDep,
):
    """Current badge configuration, deep-merged over code defaults so the
    response is always a complete object even on a fresh install."""
    cfg = await _load_config(db)
    merged = deep_merge(DEFAULT_BADGE_CONFIG, cfg.badge_config or {})
    return BadgeConfig.model_validate(merged)


@router.put("/badge-config", response_model=BadgeConfig)
async def set_badge_config(
    payload: BadgeConfig,
    request: Request,
    db: SessionDep,
    user: AdminDep,
):
    """Replace the badge configuration atomically. Stored as a plain dict;
    the schema has already validated colors, the meta-field whitelist, and
    the alignment enum."""
    cfg = await _load_config(db)
    before = dict(cfg.badge_config or {})
    new_config = payload.model_dump()
    cfg.badge_config = new_config
    await db.flush()

    meta = await load_request_meta(db, user, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="admin.badge_config.update",
        entity_type="vms_config",
        entity_id=cfg.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value={"badge_config": before},
        new_value={"badge_config": new_config},
    )
    return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd vms-api && python -m pytest tests/test_badge_config.py -v`
Expected: PASS (all, 14 passed)

- [ ] **Step 5: Run the full backend suite (no regressions)**

Run: `cd vms-api && python -m pytest -q`
Expected: all tests pass (no failures introduced).

- [ ] **Step 6: Commit**

```bash
git add vms-api/app/api/v1/admin.py vms-api/tests/test_badge_config.py
git commit -m "feat(vms): GET/PUT /admin/badge-config endpoints"
```

---

## Task 5: Frontend — types, defaults, hooks

**Files:**
- Modify: `vms/src/services/api.ts` (replace the `BadgeTemplate` block at lines 804-826)

- [ ] **Step 1: Replace the dead BadgeTemplate block**

In `vms/src/services/api.ts`, replace lines 804-826 (the `BadgeTemplate` interface, `useBadgeTemplates`, `useUpsertBadgeTemplate`) with:

```typescript
// ── Badge config (structured, replaces HTML/CSS templates) ──────────────────-

export interface BadgeAreaStyle { label: string; bg: string; fg: string; risk: string }
export interface BadgeBand {
  title: string
  show_zone: boolean
  show_risk: boolean
  risk_suffix: string
  areas: Record<AccessArea, BadgeAreaStyle>
}
export interface BadgeIdentity { name_uppercase: boolean; show_company: boolean }
export type BadgeMetaKey =
  | 'visit_date' | 'host' | 'valid_until'
  | 'vehicle_plate' | 'accompanying_count' | 'visit_purpose'
export interface BadgeMetaField { key: BadgeMetaKey; label: string; visible: boolean }
export interface BadgeQr { show: boolean; label: string }
export interface BadgeFooter { show: boolean; lines: string[] }
export interface BadgeStyle {
  name_size_pt: number
  band_title_size_pt: number
  text_color: string
  company_color: string
  footer_color: string
  name_align: 'left' | 'center' | 'right'
}
export interface BadgeConfig {
  version: number
  band: BadgeBand
  identity: BadgeIdentity
  meta_fields: BadgeMetaField[]
  qr: BadgeQr
  footer: BadgeFooter
  style: BadgeStyle
}

/** Mirror of vms-api DEFAULT_BADGE_CONFIG — used as the render fallback and
 *  the starting point for the admin form before the server responds. */
export const BADGE_CONFIG_DEFAULTS: BadgeConfig = {
  version: 1,
  band: {
    title: 'VISITOR',
    show_zone: true,
    show_risk: true,
    risk_suffix: 'RISK',
    areas: {
      office:             { label: 'OFFICE',           bg: '#10B981', fg: '#FFFFFF', risk: 'LOW' },
      warehouse:          { label: 'WAREHOUSE',        bg: '#F59E0B', fg: '#1A2730', risk: 'MEDIUM' },
      production_non_gmp: { label: 'PRODUCTION',       bg: '#EA580C', fg: '#FFFFFF', risk: 'MEDIUM' },
      production_gmp:     { label: 'PRODUCTION (GMP)', bg: '#DC2626', fg: '#FFFFFF', risk: 'HIGH' },
      laboratory:         { label: 'LABORATORY',       bg: '#DC2626', fg: '#FFFFFF', risk: 'HIGH' },
      all:                { label: 'ALL ZONES',        bg: '#DC2626', fg: '#FFFFFF', risk: 'HIGH' },
    },
  },
  identity: { name_uppercase: true, show_company: true },
  meta_fields: [
    { key: 'visit_date',         label: 'Date',         visible: true },
    { key: 'host',               label: 'Host',         visible: true },
    { key: 'valid_until',        label: 'Valid until',  visible: true },
    { key: 'vehicle_plate',      label: 'Vehicle',      visible: false },
    { key: 'accompanying_count', label: 'Accompanying', visible: false },
    { key: 'visit_purpose',      label: 'Purpose',      visible: false },
  ],
  qr: { show: true, label: 'Scan to check out' },
  footer: { show: true, lines: ['Must be accompanied by Host at all times', 'Please return badge when leaving'] },
  style: {
    name_size_pt: 22, band_title_size_pt: 18,
    text_color: '#1A2730', company_color: '#4E6070', footer_color: '#4E6070',
    name_align: 'left',
  },
}

export function useBadgeConfig() {
  return useQuery<BadgeConfig>({
    queryKey: ['vms-badge-config'],
    queryFn: () => api.get<BadgeConfig>('/api/v1/admin/badge-config'),
    staleTime: 30_000,
  })
}

export function useUpdateBadgeConfig() {
  const qc = useQueryClient()
  return useMutation<BadgeConfig, Error, BadgeConfig>({
    mutationFn: (body) => api.put<BadgeConfig>('/api/v1/admin/badge-config', body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['vms-badge-config'] }) },
  })
}
```

- [ ] **Step 2: Typecheck**

Run: `cd vms && npx tsc -b`
Expected: errors ONLY in `BadgePreview.tsx` / `BadgePrintPage.tsx` / `BadgeTemplatesPage.tsx` referencing the removed `BadgeTemplate` exports (fixed in Tasks 6-8). No errors in `api.ts`.

- [ ] **Step 3: Commit**

```bash
git add vms/src/services/api.ts
git commit -m "feat(vms-fe): badge config types, defaults, and hooks"
```

---

## Task 6: Frontend — config-driven BadgePreview renderer

**Files:**
- Rewrite: `vms/src/components/BadgePreview.tsx`

- [ ] **Step 1: Replace the file**

Overwrite `vms/src/components/BadgePreview.tsx` with:

```tsx
/** Printable visitor badge layout (PRD §2.3 VMS-LB-001..006).
 *
 * Config-driven: all text, field visibility, and styling come from
 * BadgeConfig (admin-editable). The print-CSS skeleton (mm sizing,
 * print-color-adjust, page-break rules) is preserved from the original
 * hard-coded layout so physical badges still cut and print correctly.
 * This component is the single renderer shared by the print page and the
 * admin live preview.
 */
import { QRCodeSVG } from 'qrcode.react'
import type {
  AccessArea, BadgeConfig, BadgeMetaKey, Visit, Visitor, UserBrief,
} from '@/services/api'
import { BADGE_CONFIG_DEFAULTS } from '@/services/api'
import { formatDate } from '@/lib/utils'

const PURPOSE_LABEL: Record<string, string> = {
  meeting: 'Meeting', maintenance: 'Maintenance', tour: 'Tour', audit: 'Audit',
  interview: 'Interview', delivery: 'Delivery', other: 'Other',
}

/** Resolve a whitelisted meta key to its display string, or null to omit. */
function metaValue(
  key: BadgeMetaKey, visit: Visit, host: UserBrief | null,
): string | null {
  switch (key) {
    case 'visit_date':
      return formatDate(visit.visit_date)
    case 'host':
      return host?.full_name ?? null
    case 'valid_until':
      return visit.planned_departure
        ? new Date(visit.planned_departure).toLocaleTimeString('en-CA',
            { hour: '2-digit', minute: '2-digit', hour12: false })
        : null
    case 'vehicle_plate':
      return visit.vehicle_plate || null
    case 'accompanying_count':
      return visit.accompanying_count != null ? String(visit.accompanying_count) : null
    case 'visit_purpose':
      return PURPOSE_LABEL[visit.visit_purpose] ?? visit.visit_purpose
    default:
      return null
  }
}

export function BadgePreview({
  visit,
  visitor,
  host,
  config,
}: {
  visit: Visit
  visitor: Visitor
  host: UserBrief | null
  config?: BadgeConfig
}) {
  const cfg = config ?? BADGE_CONFIG_DEFAULTS
  const area = cfg.band.areas[visit.access_area as AccessArea] ?? cfg.band.areas.office
  const { style } = cfg

  const name = cfg.identity.name_uppercase
    ? `${visitor.first_name} ${visitor.last_name}`.toUpperCase()
    : `${visitor.first_name} ${visitor.last_name}`

  const rows = cfg.meta_fields
    .filter((f) => f.visible)
    .map((f) => ({ label: f.label, value: metaValue(f.key, visit, host) }))
    .filter((r) => r.value != null)

  return (
    <div className="badge-card">
      {/* Top color band */}
      <div className="badge-band" style={{ background: area.bg, color: area.fg }}>
        <div className="badge-band-title">{cfg.band.title}</div>
        {cfg.band.show_zone && <div className="badge-band-zone">{area.label}</div>}
        {cfg.band.show_risk && (
          <div className="badge-band-risk">{`${area.risk} ${cfg.band.risk_suffix}`.trim()}</div>
        )}
      </div>

      {/* Body */}
      <div className="badge-body">
        <div className="badge-info">
          <p className="badge-name">{name}</p>
          {cfg.identity.show_company && <p className="badge-company">{visitor.company_name}</p>}

          {rows.length > 0 && (
            <dl className="badge-meta">
              {rows.map((r, i) => (
                <div key={i}><dt>{r.label}</dt><dd>{r.value}</dd></div>
              ))}
            </dl>
          )}
        </div>

        {cfg.qr.show && (
          <div className="badge-qr">
            <QRCodeSVG value={visit.id} size={120} includeMargin={false} />
            {cfg.qr.label && <p className="badge-qr-label">{cfg.qr.label}</p>}
          </div>
        )}
      </div>

      {/* Footer */}
      {cfg.footer.show && cfg.footer.lines.length > 0 && (
        <div className="badge-footer">
          {cfg.footer.lines.map((line, i) => <p key={i}>{line}</p>)}
        </div>
      )}

      {/* ── Styles ──────────────────────────────────────────────────────── */}
      <style>{`
        .badge-card {
          width: 140mm;
          min-height: 100mm;
          background: white;
          border: 1px solid #D9DFE3;
          border-radius: 4px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.08);
          overflow: hidden;
          font-family: 'Inter', system-ui, sans-serif;
          page-break-inside: avoid;
          color: ${style.text_color};
          -webkit-print-color-adjust: exact;
          print-color-adjust: exact;
        }
        .badge-card * {
          -webkit-print-color-adjust: exact;
          print-color-adjust: exact;
        }
        @media print {
          .badge-card { page-break-after: always; }
          .badge-card:last-child { page-break-after: auto; }
        }

        .badge-band {
          padding: 8mm 10mm 6mm;
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          gap: 4mm;
          line-height: 1;
        }
        .badge-band-title { font-size: ${style.band_title_size_pt}pt; font-weight: 700; letter-spacing: 2px; }
        .badge-band-zone  { font-size: 12pt; font-weight: 600; letter-spacing: 1px; }
        .badge-band-risk  { font-size: 9pt;  font-weight: 700; letter-spacing: 1.5px; }

        .badge-body {
          display: flex;
          gap: 6mm;
          padding: 6mm 10mm;
          align-items: flex-start;
        }
        .badge-info  { flex: 1; min-width: 0; }
        .badge-name {
          font-size: ${style.name_size_pt}pt;
          font-weight: 800;
          margin: 0;
          line-height: 1.1;
          letter-spacing: 0.5px;
          word-break: break-word;
          text-align: ${style.name_align};
        }
        .badge-company {
          font-size: 11pt;
          font-weight: 500;
          margin: 1mm 0 4mm;
          color: ${style.company_color};
        }
        .badge-meta {
          margin: 0;
          font-size: 9pt;
          color: #3A4D5C;
          display: grid;
          gap: 1.2mm;
        }
        .badge-meta > div { display: flex; gap: 2mm; }
        .badge-meta dt {
          width: 24mm;
          font-weight: 600;
          color: #667685;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          font-size: 8pt;
          margin: 0;
        }
        .badge-meta dd { font-weight: 500; margin: 0; }

        .badge-qr { width: 34mm; text-align: center; flex-shrink: 0; }
        .badge-qr-label {
          margin: 1mm 0 0;
          font-size: 7pt;
          color: #667685;
          letter-spacing: 0.5px;
          text-transform: uppercase;
        }

        .badge-footer {
          background: #F7F8F9;
          border-top: 1px solid #ECEEF0;
          padding: 3mm 10mm;
          font-size: 8pt;
          color: ${style.footer_color};
          line-height: 1.4;
        }
        .badge-footer p { margin: 0; }

        @media print {
          @page { size: 8.5in 11in; margin: 1cm; }
          body  { background: white !important; }
          .badge-card { box-shadow: none !important; border: 1px dashed #999; margin: 0; }
          .no-print { display: none !important; }
        }
      `}</style>
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd vms && npx tsc -b`
Expected: no errors in `BadgePreview.tsx`. Remaining errors only in `BadgePrintPage.tsx` (Task 7) and `BadgeTemplatesPage.tsx` (Task 8).

- [ ] **Step 3: Commit**

```bash
git add vms/src/components/BadgePreview.tsx
git commit -m "feat(vms-fe): config-driven BadgePreview renderer"
```

---

## Task 7: Frontend — wire BadgePrintPage to config

**Files:**
- Modify: `vms/src/pages/BadgePrintPage.tsx`

- [ ] **Step 1: Import the hook**

In `vms/src/pages/BadgePrintPage.tsx`, update the api import (line 16-18) to add `useBadgeConfig`:

```tsx
import {
  useVisit, useVisitor, useUserBrief, usePrintBadge, useBadgeConfig,
} from '@/services/api'
```

- [ ] **Step 2: Load the config in the component**

After the existing `const print = usePrintBadge(visitId)` line (line 30), add:

```tsx
  const { data: badgeConfig } = useBadgeConfig()
```

- [ ] **Step 3: Pass config to both renderers**

Replace the badge render block (lines 113-120) with:

```tsx
      {visit && visitor && (
        <>
          <BadgePreview visit={visit} visitor={visitor} host={host ?? null} config={badgeConfig} />
          {additionalVisitors.map((v) => (
            <BadgePreview key={v.id} visit={visit} visitor={v} host={host ?? null} config={badgeConfig} />
          ))}
        </>
      )}
```

- [ ] **Step 4: Typecheck**

Run: `cd vms && npx tsc -b`
Expected: no errors in `BadgePrintPage.tsx`. Only `BadgeTemplatesPage.tsx` errors remain.

- [ ] **Step 5: Commit**

```bash
git add vms/src/pages/BadgePrintPage.tsx
git commit -m "feat(vms-fe): print page renders from badge config"
```

---

## Task 8: Frontend — structured admin editor

**Files:**
- Rewrite: `vms/src/pages/admin/BadgeTemplatesPage.tsx`
- Modify: `vms/src/pages/admin/AdminPanel.tsx:30`

- [ ] **Step 1: Rewrite the admin page**

Overwrite `vms/src/pages/admin/BadgeTemplatesPage.tsx` with:

```tsx
/** Structured badge editor — every label, field, and style is editable here.
 *
 * Edits a single global BadgeConfig (vms_config.badge_config). The live
 * preview uses the exact same BadgePreview renderer the print page uses, so
 * what you see is what prints. No HTML/CSS — pure form controls.
 */
import { useMemo, useState } from 'react'
import { CheckCircle2, Loader2, ArrowUp, ArrowDown } from 'lucide-react'
import {
  useBadgeConfig, useUpdateBadgeConfig, BADGE_CONFIG_DEFAULTS,
  type BadgeConfig, type AccessArea,
  type Visit, type Visitor, type UserBrief,
} from '@/services/api'
import { BadgePreview } from '@/components/BadgePreview'

const AREA_ORDER: AccessArea[] = [
  'office', 'warehouse', 'production_non_gmp', 'production_gmp', 'laboratory', 'all',
]

// Sample data so the preview shows a realistic badge while editing.
const SAMPLE_VISIT = {
  id: '00000000-0000-0000-0000-000000000000',
  visit_date: new Date().toISOString().slice(0, 10),
  planned_departure: new Date(Date.now() + 2 * 3600_000).toISOString(),
  visit_purpose: 'meeting',
  access_area: 'production_gmp',
  accompanying_count: 2,
  vehicle_plate: 'ABC-123',
} as unknown as Visit
const SAMPLE_VISITOR = {
  first_name: 'Jordan', last_name: 'Lee', company_name: 'Acme Foods Ltd.',
} as unknown as Visitor
const SAMPLE_HOST = { id: 'h', full_name: 'Pat Morgan' } as unknown as UserBrief

const inputCls =
  'rounded-md border border-neutral-300 px-2 py-1 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500'
const sectionCls = 'rounded-md border border-neutral-200 bg-white p-3 space-y-2'
const headCls = 'text-xs font-semibold uppercase tracking-wider text-neutral-500'

export default function BadgeTemplatesPage() {
  const { data, isLoading } = useBadgeConfig()
  const save = useUpdateBadgeConfig()

  const server = useMemo<BadgeConfig>(() => data ?? BADGE_CONFIG_DEFAULTS, [data])
  const [draft, setDraft] = useState<BadgeConfig | null>(null)
  const cfg = draft ?? server
  const dirty = draft !== null

  // Patch helpers — always operate on a deep-ish clone of the current cfg.
  const update = (mut: (c: BadgeConfig) => void) => {
    const next: BadgeConfig = structuredClone(cfg)
    mut(next)
    setDraft(next)
  }

  const onSave = () => {
    save.mutate(cfg, { onSuccess: () => setDraft(null) })
  }

  const moveMeta = (i: number, dir: -1 | 1) => update((c) => {
    const j = i + dir
    if (j < 0 || j >= c.meta_fields.length) return
    ;[c.meta_fields[i], c.meta_fields[j]] = [c.meta_fields[j], c.meta_fields[i]]
  })

  return (
    <div className="max-w-5xl">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold text-neutral-900">Badge</h2>
          <p className="mt-0.5 text-xs text-neutral-500">
            Edit every label, field, and style on the printed visitor badge.
            The preview matches what prints.
          </p>
        </div>
        <button
          onClick={onSave}
          disabled={!dirty || save.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
        >
          {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
          Save
        </button>
      </div>

      {save.error && (
        <p className="mt-2 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">{save.error.message}</p>
      )}
      {isLoading && <p className="mt-3 text-xs text-neutral-400">Loading…</p>}

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* ── Form ── */}
        <div className="space-y-3">
          {/* Band */}
          <div className={sectionCls}>
            <p className={headCls}>Top band</p>
            <label className="block text-xs text-neutral-600">Title
              <input className={`${inputCls} mt-1 w-full`} value={cfg.band.title}
                onChange={(e) => update((c) => { c.band.title = e.target.value })} />
            </label>
            <div className="flex gap-4 text-xs text-neutral-600">
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.band.show_zone}
                  onChange={(e) => update((c) => { c.band.show_zone = e.target.checked })} /> Show zone
              </label>
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.band.show_risk}
                  onChange={(e) => update((c) => { c.band.show_risk = e.target.checked })} /> Show risk
              </label>
              <label className="inline-flex items-center gap-1.5">Risk suffix
                <input className={`${inputCls} w-20`} value={cfg.band.risk_suffix}
                  onChange={(e) => update((c) => { c.band.risk_suffix = e.target.value })} />
              </label>
            </div>
            <p className={headCls}>Zones (label + colors + risk)</p>
            <div className="space-y-1">
              {AREA_ORDER.map((a) => (
                <div key={a} className="flex items-center gap-2">
                  <span className="w-32 shrink-0 text-[11px] text-neutral-500">{a}</span>
                  <input className={`${inputCls} flex-1`} value={cfg.band.areas[a].label}
                    onChange={(e) => update((c) => { c.band.areas[a].label = e.target.value })} />
                  <input type="color" className="h-7 w-9 rounded border" value={cfg.band.areas[a].bg}
                    onChange={(e) => update((c) => { c.band.areas[a].bg = e.target.value.toUpperCase() })} title="background" />
                  <input type="color" className="h-7 w-9 rounded border" value={cfg.band.areas[a].fg}
                    onChange={(e) => update((c) => { c.band.areas[a].fg = e.target.value.toUpperCase() })} title="text" />
                  <input className={`${inputCls} w-24`} value={cfg.band.areas[a].risk}
                    onChange={(e) => update((c) => { c.band.areas[a].risk = e.target.value })} title="risk label" />
                </div>
              ))}
            </div>
          </div>

          {/* Identity */}
          <div className={sectionCls}>
            <p className={headCls}>Name & company</p>
            <div className="flex gap-4 text-xs text-neutral-600">
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.identity.name_uppercase}
                  onChange={(e) => update((c) => { c.identity.name_uppercase = e.target.checked })} /> Uppercase name
              </label>
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.identity.show_company}
                  onChange={(e) => update((c) => { c.identity.show_company = e.target.checked })} /> Show company
              </label>
            </div>
          </div>

          {/* Meta fields */}
          <div className={sectionCls}>
            <p className={headCls}>Detail rows</p>
            {cfg.meta_fields.map((f, i) => (
              <div key={f.key} className="flex items-center gap-2">
                <input type="checkbox" checked={f.visible}
                  onChange={(e) => update((c) => { c.meta_fields[i].visible = e.target.checked })} />
                <span className="w-32 shrink-0 text-[11px] text-neutral-500">{f.key}</span>
                <input className={`${inputCls} flex-1`} value={f.label}
                  onChange={(e) => update((c) => { c.meta_fields[i].label = e.target.value })} />
                <button onClick={() => moveMeta(i, -1)} disabled={i === 0}
                  className="rounded p-1 text-neutral-500 hover:bg-neutral-100 disabled:opacity-30"><ArrowUp className="h-3.5 w-3.5" /></button>
                <button onClick={() => moveMeta(i, 1)} disabled={i === cfg.meta_fields.length - 1}
                  className="rounded p-1 text-neutral-500 hover:bg-neutral-100 disabled:opacity-30"><ArrowDown className="h-3.5 w-3.5" /></button>
              </div>
            ))}
          </div>

          {/* QR */}
          <div className={sectionCls}>
            <p className={headCls}>QR code</p>
            <label className="inline-flex items-center gap-1.5 text-xs text-neutral-600">
              <input type="checkbox" checked={cfg.qr.show}
                onChange={(e) => update((c) => { c.qr.show = e.target.checked })} /> Show QR
            </label>
            {!cfg.qr.show && (
              <p className="rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-700">
                Hiding the QR disables scan-to-check-out on the printed badge.
              </p>
            )}
            <label className="block text-xs text-neutral-600">QR caption
              <input className={`${inputCls} mt-1 w-full`} value={cfg.qr.label}
                onChange={(e) => update((c) => { c.qr.label = e.target.value })} />
            </label>
          </div>

          {/* Footer */}
          <div className={sectionCls}>
            <div className="flex items-center justify-between">
              <p className={headCls}>Footer lines</p>
              <button className="text-xs text-primary-700 hover:underline"
                onClick={() => update((c) => { c.footer.lines.push('') })}>+ Add line</button>
            </div>
            <label className="inline-flex items-center gap-1.5 text-xs text-neutral-600">
              <input type="checkbox" checked={cfg.footer.show}
                onChange={(e) => update((c) => { c.footer.show = e.target.checked })} /> Show footer
            </label>
            {cfg.footer.lines.map((line, i) => (
              <div key={i} className="flex items-center gap-2">
                <input className={`${inputCls} flex-1`} value={line}
                  onChange={(e) => update((c) => { c.footer.lines[i] = e.target.value })} />
                <button className="text-xs text-danger-600 hover:underline"
                  onClick={() => update((c) => { c.footer.lines.splice(i, 1) })}>Remove</button>
              </div>
            ))}
          </div>

          {/* Style */}
          <div className={sectionCls}>
            <p className={headCls}>Style</p>
            <div className="grid grid-cols-2 gap-2 text-xs text-neutral-600">
              <label>Name size (pt)
                <input type="number" className={`${inputCls} mt-1 w-full`} value={cfg.style.name_size_pt}
                  onChange={(e) => update((c) => { c.style.name_size_pt = Number(e.target.value) })} />
              </label>
              <label>Band title size (pt)
                <input type="number" className={`${inputCls} mt-1 w-full`} value={cfg.style.band_title_size_pt}
                  onChange={(e) => update((c) => { c.style.band_title_size_pt = Number(e.target.value) })} />
              </label>
              <label className="flex items-center gap-2">Text color
                <input type="color" className="h-7 w-9 rounded border" value={cfg.style.text_color}
                  onChange={(e) => update((c) => { c.style.text_color = e.target.value.toUpperCase() })} />
              </label>
              <label className="flex items-center gap-2">Company color
                <input type="color" className="h-7 w-9 rounded border" value={cfg.style.company_color}
                  onChange={(e) => update((c) => { c.style.company_color = e.target.value.toUpperCase() })} />
              </label>
              <label className="flex items-center gap-2">Footer color
                <input type="color" className="h-7 w-9 rounded border" value={cfg.style.footer_color}
                  onChange={(e) => update((c) => { c.style.footer_color = e.target.value.toUpperCase() })} />
              </label>
              <label>Name align
                <select className={`${inputCls} mt-1 w-full`} value={cfg.style.name_align}
                  onChange={(e) => update((c) => { c.style.name_align = e.target.value as BadgeConfig['style']['name_align'] })}>
                  <option value="left">left</option>
                  <option value="center">center</option>
                  <option value="right">right</option>
                </select>
              </label>
            </div>
          </div>
        </div>

        {/* ── Live preview ── */}
        <div className="lg:sticky lg:top-4 self-start">
          <p className={`${headCls} mb-1`}>Preview</p>
          <div className="overflow-auto rounded-md border border-neutral-200 bg-neutral-50 p-3">
            <BadgePreview visit={SAMPLE_VISIT} visitor={SAMPLE_VISITOR} host={SAMPLE_HOST} config={cfg} />
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Rename the AdminPanel tab**

In `vms/src/pages/admin/AdminPanel.tsx:30`, change the badge tab label:

```tsx
  { key: 'badges',        label: 'Badge',                 icon: Image },
```

- [ ] **Step 3: Typecheck the whole frontend**

Run: `cd vms && npx tsc -b`
Expected: PASS (no errors anywhere — all references to the removed `BadgeTemplate`/`useBadgeTemplates`/`useUpsertBadgeTemplate` are gone).

- [ ] **Step 4: Lint**

Run: `cd vms && npx eslint src/pages/admin/BadgeTemplatesPage.tsx src/components/BadgePreview.tsx src/pages/BadgePrintPage.tsx src/services/api.ts`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add vms/src/pages/admin/BadgeTemplatesPage.tsx vms/src/pages/admin/AdminPanel.tsx
git commit -m "feat(vms-fe): structured badge editor with live preview"
```

---

## Task 9: Apply migration + manual verification

**Files:** none (deployment + dogfood)

- [ ] **Step 1: Apply the migration in the running vms-api container**

Run: `docker exec uniops_vms_api alembic upgrade head`
Expected: `Running upgrade 20260602_0010 -> 20260606_0011`. (Per project history, the migration MUST be applied in the running container or editing returns errors — though the badge still renders via defaults.)

- [ ] **Step 2: Verify the endpoint live**

Run: `docker exec uniops_vms_api python -c "import asyncio; from sqlalchemy import select; from app.db.session import AsyncSessionLocal; from app.models.vms_config import VmsConfig; \nasync def m():\n  async with AsyncSessionLocal() as db:\n    c=(await db.execute(select(VmsConfig).limit(1))).scalar_one();\n    print('badge_config column ok, value=', c.badge_config)\nasyncio.run(m())"`
Expected: prints `badge_config column ok, value= {}` (no AttributeError / no missing-column error).

- [ ] **Step 3: Manual dogfood (admin editor)**

1. Open the VMS app → Admin → **Badge** tab.
2. Change the band Title to `CONTRACTOR`, toggle on `Vehicle`, change Name align to `center`, edit a footer line.
3. Confirm the live preview updates immediately.
4. Click **Save** → no error, Save button disables (clean state).
5. Reload the page → changes persist.

- [ ] **Step 4: Manual dogfood (printed badge)**

1. Open a confirmed visit → Print badge.
2. Confirm the printed badge reflects the saved config (title, vehicle row, centered name, footer).
3. Open the browser print dialog → confirm the colored band prints and page sizing is intact (Letter, dashed cut border).

- [ ] **Step 5: Final commit (plan completion marker, if any working-tree changes remain)**

```bash
git add -A
git commit -m "chore(vms): badge config feature complete" --allow-empty
```

---

## Self-Review

**Spec coverage:**
- Data model (`badge_config` JSONB + schema) → Tasks 1-3. ✓
- GET/PUT `/admin/badge-config`, admin-only, audit, deep-merge → Task 4. ✓
- Config-driven renderer keeping print-CSS skeleton → Task 6. ✓
- Print page consumes config → Task 7. ✓
- Structured admin form (band/identity/meta reorder+toggle+label/QR/footer/style) + live preview + remove MVP notice + rename tab → Task 8. ✓
- meta whitelist enforced backend + typed frontend → Tasks 1-2, 5. ✓
- Defaults reproduce current badge (backend + frontend constants) → Tasks 1, 5. ✓
- Edge cases: partial config deep-merge (Task 1 tests), omit empty meta rows + hide empty footer + QR-off warning (Task 6/8), invalid hex 422 (Tasks 2, 4). ✓
- Migration safety (nullable server_default, renderer falls back) → Task 3 + Task 9. ✓
- Testing: backend pytest TDD; frontend tsc+eslint+manual (no FE runner exists). ✓ (intentional deviation from spec's "frontend snapshot tests" — documented in header).

**Placeholder scan:** none — every code step has full content.

**Type consistency:** `BadgeConfig`/`BadgeMetaField`/`BadgeStyle` names match across api.ts, BadgePreview, and the editor. Endpoint path `/api/v1/admin/badge-config` consistent across backend route, hooks, and tests. `META_FIELD_KEYS` matches the frontend `BadgeMetaKey` union and `metaValue` switch. Backend `deep_merge` + `DEFAULT_BADGE_CONFIG` referenced consistently in Task 4.

**Note (intentional deviations from spec):**
- Endpoints under `/admin/badge-config` (not a `/config/badge` router) to match existing singleton-config endpoints.
- Meta reorder via up/down buttons, not drag-and-drop (no new dependency).
- No frontend test framework added; FE verified by typecheck + lint + manual dogfood.
