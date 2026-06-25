# OA Direct PA Description Field Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a multi-line "Description" field (background/reason) below the Title in Direct PA Payment Details, reusing the existing `notes` field.

**Architecture:** Frontend-only relabel + reposition across three OA pages. The PA `notes` field (already in create/update payloads and the response) is repurposed: the create wizard's Notes textarea moves under Title and is relabeled "Description"; the detail and edit pages relabel their existing Notes UI. No backend, schema, or DB change.

**Tech Stack:** React + TypeScript 6.0.3.

## Global Constraints

- No git commits / pushes / branches this round — changes stay in the working tree.
- UI strings English-only.
- Frontend typecheck (run in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`.
- Reuse the existing `notes` field — do NOT add a new column/schema/migration.
- Description replaces the Notes box (does not coexist with it). Optional field.

---

### Task 1: Relabel + reposition Notes → Description across create, detail, edit pages

**Files:**
- Modify: `uniops/oa/src/pages/pa/PaDirectCreatePage.tsx`
- Modify: `uniops/oa/src/pages/pa/PaDetailPage.tsx`
- Modify: `uniops/oa/src/pages/pa/PaDirectEditPage.tsx`

**Interfaces:**
- Consumes: existing `notes` state and `pa.notes`; no API change.
- Produces: nothing for later tasks (page-local UI change).

- [ ] **Step 1: Create wizard — add Description under Title (`PaDirectCreatePage.tsx`)**

In `Step3PaForm`, the Title block currently is:

```tsx
      {/* Title */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Title *</label>
        <input value={title} onChange={e => setTitle(e.target.value)} required
          placeholder="e.g. Direct Payment — Vendor Name Invoice #..."
          className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400" />
      </div>
```

Insert a Description block immediately after that closing `</div>` (so it sits right below Title):

```tsx

      {/* Description — background / reason for this payment */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Description</label>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3}
          placeholder="Background and reason for this payment…"
          className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 resize-none" />
      </div>
```

- [ ] **Step 2: Create wizard — remove the old bottom Notes block (`PaDirectCreatePage.tsx`)**

Delete the existing bottom Notes block (it binds to the same `notes` state, now relocated above):

```tsx
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Notes</label>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2}
          className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 resize-none" />
      </div>
```

(The `notes` / `setNotes` state and the `notes: notes || null` create payload stay unchanged.)

- [ ] **Step 3: Detail page — relabel Notes → Description (`PaDetailPage.tsx`)**

In `DetailsTab`, the meta item currently is:

```tsx
        {pa.notes && (
          <div className="col-span-2 sm:col-span-3">
            <dt className="text-neutral-400 text-xs">Notes</dt>
            <dd className="mt-0.5 text-neutral-700">{pa.notes}</dd>
          </div>
        )}
```

Change the `<dt>` text from `Notes` to `Description`:

```tsx
            <dt className="text-neutral-400 text-xs">Description</dt>
```

- [ ] **Step 4: Edit page — relabel + move Description under Title (`PaDirectEditPage.tsx`)**

Remove the existing Notes block (currently after the Cost Center & Budget Account block):

```tsx
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Notes</label>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2} className={`${input} resize-none`} />
      </div>
```

Then insert a Description block immediately after the Title block (after its closing `</div>`, before the `{/* Matched Vendor */}` block):

```tsx

      {/* Description — background / reason for this payment */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Description</label>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3}
          placeholder="Background and reason for this payment…" className={`${input} resize-none`} />
      </div>
```

(The `notes` state, prefill `setNotes(pa.notes ?? '')`, and `notes: notes || null` in the PATCH payload stay unchanged.)

- [ ] **Step 5: Typecheck**

Run (in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS, no errors (no orphaned/duplicate JSX, `notes`/`setNotes` still used in all three files).

- [ ] **Step 6: Manual verification**

- Create wizard Step 3: a "Description" textarea appears directly under Title; there is no separate Notes box at the bottom; text entered is saved onto the created PA.
- Detail page Details tab: the value shows under a "Description" label.
- Edit page: "Description" appears under Title, prefilled from the PA, and editing + saving persists it.

---

## Notes

- No commit step: leave changes in the working tree for the batch commit.
- Single task spanning 3 files because it is one cohesive relabel/reposition of the same `notes` field; a reviewer would accept/reject it as a unit.
