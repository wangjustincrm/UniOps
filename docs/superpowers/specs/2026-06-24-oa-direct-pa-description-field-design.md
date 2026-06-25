# OA Direct PA — Description field

**Date:** 2026-06-24
**Status:** Approved, pending implementation
**Scope:** OA frontend only (3 files). No backend, schema, or DB change.

## Goal

Add a multi-line **Description** field to the Direct PA Payment Details, below
the Title, for entering the background / reason of the payment.

## Decision

Reuse the existing `notes` field (the PA model already has `notes: Text`, and it
is already in `PaDirectCreate`, `PaDirectUpdate`, and `PaResponse`). The change
is a frontend relabel + reposition — **no new column, no migration, no backend
change**. Description is optional (matches current Notes behavior).

## Changes

### 1. Create wizard — `oa/src/pages/pa/PaDirectCreatePage.tsx` (`Step3PaForm`)

- Move the existing Notes textarea so it renders **immediately below the Title**
  field (currently it sits near the bottom of the form).
- Relabel it **"Description"**, placeholder *"Background and reason for this
  payment…"*, and make it `rows={3}`.
- Remove the old Notes block from the bottom of the form (it is the same `notes`
  state — relocated, not duplicated).
- The create payload is unchanged: it still sends `notes: notes || null`.

### 2. Detail page — `oa/src/pages/pa/PaDetailPage.tsx` (`DetailsTab`)

- Relabel the existing meta item whose `<dt>` reads "Notes" to **"Description"**.
  It still renders `pa.notes` and only shows when `pa.notes` is present.

### 3. Edit page — `oa/src/pages/pa/PaDirectEditPage.tsx`

- Relabel the Notes textarea to **"Description"** and move it to directly under
  the Title field. It still binds to the `notes` state and is sent as `notes` in
  the `PATCH` payload.

## Out of scope

- Any backend/schema/DB change (reusing `notes`).
- The Line Items overflow fix (tracked separately).
- Adding a *second* free-text field — Description replaces the Notes box, it does
  not coexist with it.

## Verification

- `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` in `oa/`.
- Manual:
  - Create wizard Step 3: a "Description" textarea appears directly under Title;
    no separate Notes box remains; entered text persists onto the created PA.
  - Detail page Details tab shows the text under a "Description" label.
  - Edit page shows "Description" under Title; editing and saving persists it.

## Constraints

- UI strings English-only.
- No git commits this round — working tree only.
