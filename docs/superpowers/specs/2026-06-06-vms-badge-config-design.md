# VMS Badge — Fully Editable Structured Config

**Date:** 2026-06-06
**Status:** Approved (design)
**Module:** uniops / vms-api + vms (frontend)

## Problem

Almost nothing on the printed visitor badge is editable. Two disconnected
systems exist today:

- **Admin editor** (`vms/src/pages/admin/BadgeTemplatesPage.tsx`): edits freeform
  HTML/CSS templates stored in `vms_config.badge_templates`, with 5 placeholders.
  It carries a notice admitting its edits do **not** reach the printed badge.
- **Actual badge** (`vms/src/components/BadgePreview.tsx`): a hard-coded React
  component. The print page (`vms/src/pages/BadgePrintPage.tsx`) renders it and
  ignores templates entirely (`template_used: 'standard'` is only audit metadata).

So every label and field on the real badge — band title "VISITOR", zone/risk
labels, name, company, Date/Host/Valid-until rows, QR label, the two footer
lines — is fixed in code.

## Goal

Let an admin edit **all** badge content and styling through a **structured form**
(no HTML/CSS), with a live preview that matches the printed output exactly.

## Decisions (from brainstorming)

- **Editing model:** structured field form (not HTML/CSS, not WYSIWYG-freeform).
- **Scope:** all text labels + per-field visibility toggles, **plus style control**
  (font sizes, colors, alignment, band palette).
- **Out of scope:** company logo upload; per-access-area content variation;
  multiple named templates. → a **single global** badge config.
- **Route A:** config-driven rendering that **keeps the existing print-CSS
  skeleton** of `BadgePreview` (mm sizing, `print-color-adjust`, page-break
  rules); only the written-in text/fields/styles become config-driven.
- Top color band stays **auto-selected by `access_area`**, but its per-area
  labels and palette are editable.

## Data Model

New nullable column `badge_config JSONB` on `vms_config` (`server_default '{}'`).
A single global config object:

```jsonc
{
  "version": 1,
  "band": {
    "title": "VISITOR",
    "show_zone": true,
    "show_risk": true,
    "risk_suffix": "RISK",
    "areas": {
      "office":             { "label": "OFFICE",           "bg": "#10B981", "fg": "#FFFFFF", "risk": "LOW" },
      "warehouse":          { "label": "WAREHOUSE",        "bg": "#F59E0B", "fg": "#1A2730", "risk": "MEDIUM" },
      "production_non_gmp": { "label": "PRODUCTION",       "bg": "#EA580C", "fg": "#FFFFFF", "risk": "MEDIUM" },
      "production_gmp":     { "label": "PRODUCTION (GMP)", "bg": "#DC2626", "fg": "#FFFFFF", "risk": "HIGH" },
      "laboratory":         { "label": "LABORATORY",       "bg": "#DC2626", "fg": "#FFFFFF", "risk": "HIGH" },
      "all":                { "label": "ALL ZONES",        "bg": "#DC2626", "fg": "#FFFFFF", "risk": "HIGH" }
    }
  },
  "identity": { "name_uppercase": true, "show_company": true },
  "meta_fields": [
    { "key": "visit_date",         "label": "Date",         "visible": true  },
    { "key": "host",               "label": "Host",         "visible": true  },
    { "key": "valid_until",        "label": "Valid until",  "visible": true  },
    { "key": "vehicle_plate",      "label": "Vehicle",      "visible": false },
    { "key": "accompanying_count", "label": "Accompanying", "visible": false },
    { "key": "visit_purpose",      "label": "Purpose",      "visible": false }
  ],
  "qr":     { "show": true, "label": "Scan to check out" },
  "footer": { "show": true, "lines": ["Must be accompanied by Host at all times", "Please return badge when leaving"] },
  "style":  {
    "name_size_pt": 22,
    "band_title_size_pt": 18,
    "text_color": "#1A2730",
    "company_color": "#4E6070",
    "footer_color": "#4E6070",
    "name_align": "left"
  }
}
```

### `meta_fields` whitelist

`key` must be one of a **fixed whitelist**; the renderer maps each key to a known
visit/visitor accessor. Admin may toggle visibility, edit the label, and reorder,
but cannot bind arbitrary data (that would be the HTML/CSS route).

| key                  | source                                   | formatting                          |
|----------------------|------------------------------------------|-------------------------------------|
| `visit_date`         | `visit.visit_date`                       | `formatDate`                        |
| `host`               | `host.full_name`                         | as-is                               |
| `valid_until`        | `visit.planned_departure`                | `HH:mm` 24h; omit if null           |
| `vehicle_plate`      | `visit.vehicle_plate`                    | as-is; omit if null                 |
| `accompanying_count` | `visit.accompanying_count`               | number; omit if null                |
| `visit_purpose`      | `visit.visit_purpose`                    | label-cased                         |

The whitelist is the single source for both backend validation and the frontend
field picker. Adding a future field = one entry in this table + an accessor.

### Defaults

Default values (above) reproduce today's badge **exactly**. Defaults live in code:
- backend: `BadgeConfig` Pydantic field defaults,
- frontend: a `BADGE_CONFIG_DEFAULTS` constant.

Stored config is **deep-merged over defaults** on read, so a null/partial config
renders identically to today, and future fields never break old configs.

## Backend (vms-api)

- **Migration:** add `badge_config JSONB NULL DEFAULT '{}'` to `vms_config`.
  Nullable + server_default so existing rows are unaffected.
- **Schema:** `BadgeConfig` nested Pydantic models (`Band`, `AreaStyle`,
  `MetaField`, `Qr`, `Footer`, `Style`). Validate colors as hex (`#RRGGBB`),
  `meta_fields[].key` ∈ whitelist, `name_align ∈ {left,center,right}`.
- **Endpoints** (new `config` router or existing config area):
  - `GET /config/badge` → returns stored config deep-merged with defaults
    (always a complete object). Any authenticated user (print page needs it).
  - `PUT /config/badge` → validate + persist; **`system_admin` only**;
    audit log `action_type="badge_config.update"`.
- **Dead code:** the `badge_templates` HTML/CSS endpoints remain but are no longer
  used by the UI. The DB column `badge_templates` is **left in place** (no risky
  drop migration).

## Frontend (vms)

- **Renderer:** rewrite `BadgePreview.tsx` into a pure
  `(config, visit, visitor, host) => JSX` component that keeps the existing
  `@media print` CSS skeleton and maps config → slots. Single source of truth for
  preview and print.
- **Admin page:** replace `BadgeTemplatesPage.tsx` content with a structured form:
  sections for Band (title, toggles, per-area label + color pickers), Identity,
  Meta fields (drag-reorder + visibility toggle + label input, add-from-whitelist),
  QR (toggle + label), Footer (multi-line list), Style (size/color/alignment).
  Right pane: live preview via the renderer with sample data. Remove the MVP notice.
- **Print page:** `BadgePrintPage.tsx` adds `useBadgeConfig()` and passes config to
  the renderer. QR always encodes `visit.id`; `qr.show=false` only hides display
  and surfaces an admin-side warning that check-out scanning is disabled.
- **api.ts:** `BadgeConfig` type + `useBadgeConfig()` / `useUpdateBadgeConfig()`.

## Data Flow

1. Admin edits form → live preview re-renders from in-memory draft.
2. Save → `PUT /config/badge` → audit row written.
3. Print page loads visit + `GET /config/badge` → renderer → `window.print()`.
4. Multi-visitor: renderer invoked per visitor (unchanged).

## Edge Cases & Error Handling

- Missing/partial stored config → deep-merge with defaults.
- `meta_fields` entry `visible:true` but value absent on this visit → omit the row
  (no empty rows).
- `footer.lines` empty → hide footer block.
- Invalid hex color → backend 422; frontend uses color pickers to avoid it.
- Unknown `meta_fields[].key` (e.g. removed from whitelist later) → renderer skips
  it; backend PUT rejects it.

## Testing

**Backend**
- `BadgeConfig` validation: hex colors, whitelist keys, align enum.
- `GET /config/badge` returns merged defaults when column null/partial.
- `PUT /config/badge` persists and writes an audit row.
- `PUT` as non-admin → 403.

**Frontend**
- Default config renders a badge equal to the current hard-coded layout (snapshot).
- Visibility toggles remove the corresponding element.
- Meta-field reorder changes row order.

## Migration Sensitivity (gotcha prevention)

Per project history, vms-api migrations have caused 500s when not applied in the
running container, and `alembic_version` stamping is dangerous. Mitigations:

- Column is **nullable with `server_default '{}'`**, and the renderer falls back to
  defaults for a null config — so even if the migration is **not** yet applied in
  the running container, the badge still renders (only editing is unavailable),
  **no 500**.
- After deploy, run `alembic upgrade head` inside the vms-api container.

## Out of Scope (YAGNI)

- Company logo / image upload.
- Per-access-area content variation (only band label/palette is per-area).
- Multiple named templates.
- Freeform HTML/CSS layout control.
