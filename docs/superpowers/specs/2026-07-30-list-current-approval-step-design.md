# List View: Current Approval Step Indicator

**Date:** 2026-07-30
**Module:** UniOps EPMS (PR / PO / PA List views)
**Branch:** `feature/list-current-approval-step` (worktree `C:/Project/uniops-liststep`, base main `013bf3a`)

## Problem

In the PR / PO / PA List views, a document in **In Review** status shows only a generic
`In Review` badge. Users cannot tell, without opening each document's Detail page, *which
approval step* the document is currently waiting on, *who* is holding it, or *how long* it
has been stuck there. This forces one-by-one drill-downs to answer "what's blocking my
documents?".

## Goal

Surface the current approval step directly in each list row, for `in_review` documents:
the **step (role) name**, the **current approver's name**, and the **days waiting** at that
step — plus make the Status column sort meaningfully by this information.

Out of scope: changing the approval engine, Detail-page timeline, or any routing behavior.
This is a read-only display enrichment on the three list views.

## Approach

**Backend enrichment driven by the open approval task** (not by `approval_step_idx`).

The authoritative "real current step" for an in-flight document is the open
`approve_{doctype}` task's `assigned_role` — this is exactly what
`resync_inflight_approvals` realigns `approval_step_idx` *to*. Using the task role avoids
two known pitfalls:

- Over-budget PRs inject extra finance/GM steps at the front of the effective chain, so
  indexing a static chain by `approval_step_idx` mislabels the step.
- After a routing-config change, `approval_step_idx` can be stale until a resync runs; the
  open task role stays correct.

Rejected alternative: per-row `GET /{doc}/{id}/workflow-steps` from the frontend — N HTTP
calls per page and still indexes the chain by the fragile `approval_step_idx`.

## Backend Design (epms-api)

### Shared enrichment helper

New helper `enrich_current_step(session, doc_type, items)` used by all three list
endpoints (`list_prs`, `list_pos`, `list_pas`):

1. Collect ids of items with `status == "in_review"`.
2. **One** batched query over the `tasks` mirror table for open (`is_completed == False`)
   `approve_{doctype}` tasks whose `document_id` is in that set → build a
   `{document_id -> task}` map. (No N+1.)
3. Batch-resolve `task.assigned_user_id` → user name via the existing name-resolution path
   used for `created_by_name` / event `actor_name`.
4. For each matched item, attach a `current_step` object; all others (`null`):

```jsonc
current_step: {
  "role": "gm_or_opm",           // task.assigned_role — authoritative current step
  "label": "GM / OPM",           // static ROLE_LABELS[role] map
  "approver_name": "Zhang San",  // resolved from assigned_user_id; null if role pool w/o assignee
  "since": "2026-07-27T10:12:00Z" // task.created_at — start point for "days waiting"
}
```

### Static role→label / role→order maps

A single module-level dict in epms-api maps each role token to:
- an English **label** (matching the default workflow labels, e.g. `supervisor` →
  "Supervisor", `dept_manager` → "Dept Manager", `director` → "Director", `gm_or_opm` →
  "GM / OPM", `procurement_manager` → "Procurement Manager", `finance_bp` → "Finance BP",
  `finance_manager` → "Finance Manager", `vendor_manager` → "Vendor Manager"), and
- an **order** integer for chain-order sorting (Supervisor < Dept Manager < Director <
  GM/OPM < Finance BP < Finance Manager …), independent of any per-doc
  `approval_step_idx` so over-budget injection cannot distort grouping.

Unknown/absent role → label falls back to a humanized form of the role token; order sinks
to the end.

### Response schema

Add optional `current_step: CurrentStep | None` to `PrResponse`, `PoResponse`,
`PaResponse` (new `CurrentStep` Pydantic model: `role`, `label`, `approver_name | None`,
`since`). Defaults to `null`, so non-`in_review` items and items without an open task are
unaffected.

### Edge cases

- `in_review` but **no** open `approve_*` task (rare: task not yet created / awaiting
  resync) → `current_step = null` → row shows badge only. Graceful degradation.
- More than one open approve task for a doc → take one deterministically (e.g. earliest
  `created_at`); a role-pool task has `assigned_user_id = null` → `approver_name = null`.
- Status not `in_review` → `null` (helper skips it).

## Frontend Design (epms)

### Types & services

Add optional `current_step` to `ApiPr` (`services/pr.ts`), `ApiPo` (`services/po.ts`),
`ApiPa` (`services/pa.ts`):

```ts
current_step?: {
  role: string
  label: string
  approver_name: string | null
  since: string
} | null
```

### `CurrentStepHint` component

New shared component near `components/ui/` taking a `current_step` object. Renders a small,
muted subtext line **under** the status badge:

- With approver: `GM / OPM · Zhang San · 3d`
- Role pool (no assignee): `Finance BP · 3d`
- No `current_step`: renders nothing.

Days waiting computed client-side as `floor((now - since) / 1 day)`, shown as `{n}d`; hover
title shows the precise step-entry timestamp. All literal text (labels, `d` unit) is
English per UI convention; the approver name is data, rendered as-is.

### Status cell integration

- `PrListPage.tsx` status cell (~line 288): keep `<StatusBadge status={pr.status} />`, add
  `<CurrentStepHint current_step={pr.current_step} />` below it.
- `PoListPage.tsx` (~line 251): same, below `<StatusBadge>`.
- `PaListPage.tsx` (~line 198): same, below the local `PaStatusBadge` — visual parity
  across all three pages.

### Status column sort

Shared comparator utility reused by all three pages. Sort keys:

1. **Primary:** `status` — preserves existing status ordering semantics.
2. **Secondary (only within the `in_review` group):** current step **role**, ordered by the
   static role→order map (chain order, not alphabetical).
3. **Tertiary:** `approver_name`, alphabetical; `null` sinks to the end of its role group.

Non-`in_review` rows keep their existing relative order (secondary/tertiary are no-ops for
them). Sort direction toggle on the Status header behaves as today for the primary key.

## Testing

**Backend (`enrich_current_step`):**
- Attaches `current_step` for an `in_review` item with an open approve task.
- Returns `null` for non-`in_review` items and for `in_review` items with no open task.
- `approver_name` is `null` for a role-pool task (no `assigned_user_id`).
- `since` equals the task's `created_at`.
- Single batched query for N items (no N+1).
- Unknown role token → humanized label fallback, order sinks last.

**Frontend:**
- `CurrentStepHint` renders all three branches (with approver / pool / none).
- Comparator: status primary, role chain-order secondary within `in_review`, approver_name
  tertiary with `null` last.

## Files (anticipated)

- `epms-api/app/...` — `enrich_current_step` helper + `ROLE_LABELS`/`ROLE_ORDER` maps;
  `CurrentStep` schema; wire into `list_prs` / `list_pos` / `list_pas`.
- `epms/src/services/{pr,po,pa}.ts` — `current_step` on `ApiPr`/`ApiPo`/`ApiPa`.
- `epms/src/components/ui/CurrentStepHint.tsx` — new component.
- `epms/src/pages/{pr,po,pa}/*ListPage.tsx` — render hint + shared comparator.
- `epms/src/...` — shared status comparator utility.
- Tests under `epms-api/tests/` and the epms frontend test suite.

## Constraints / Notes

- epms frontend baseline is 59 tsc / 220 lint (gate: `tsc -p tsconfig.app.json --noEmit`
  must stay at 59). TS 5.9.3 — no `--ignoreDeprecations` flag.
- Zero DB migration (display-only; reads existing `tasks` mirror).
- Multi-session discipline: this branch only; converge at release.
