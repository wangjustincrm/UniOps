# OA PA History — show actor user name

**Date:** 2026-06-24
**Status:** Approved, pending implementation
**Scope:** OA frontend only — `oa/src/pages/pa/PaDetailPage.tsx` (`HistoryTab`). No backend change.

## Goal

On the Direct PA detail page → History tab, each event currently shows only the
actor's **role** (e.g. "Requester", "Gm Or Opm"). Show the actor's **user name**
instead.

## Approach

Resolve `actor_id → full_name` on the frontend using epms-api's existing
directory endpoint (the same `epmsApi` client OA already uses for vendors /
cost centers). No backend change.

- `GET /api/v1/pa/{id}/history` already returns each event's `actor_id` (UUID)
  and `actor_role`. (`ApprovalEventOut` in `expense-api/app/api/v1/pa.py`.)
- `epms-api` exposes `GET /api/v1/users/directory/{user_id}` → `UserBriefResponse`
  (`{ id, full_name, ... }`), auth-only (no role gate), so OA users can call it.
  It returns 404 for unknown/inactive users.

### HistoryTab changes

1. After loading `events`, compute the unique `actor_id`s.
2. Fetch names with a single `useQuery` keyed on the sorted unique ids: for each
   id call `epmsApi.get<{ id: string; full_name: string }>('/api/v1/users/directory/{id}')`,
   tolerate per-id failure (404 / inactive) by skipping it, and build an
   `id → full_name` map (`Record<string, string>`). `enabled` only when there is
   at least one event.
3. Render the **full name** as the event's primary label, replacing the role
   label. If the id is not in the map (unresolved), fall back to the existing
   role label so the row is never blank.

### Rendering (Name only)

- Keep the action badge (Submit/Approve/etc.) and the timeline spine/date as-is.
- Replace `{roleLabel}` (`<span class="text-sm font-medium text-neutral-800">`)
  with `{nameMap[ev.actor_id] ?? roleLabel}`.
- The `roleLabel` computation stays (used only as the fallback). `actor_role` is
  still consumed, so no unused-variable issues.

## Out of scope

- Backend enrichment of the history response (no users mirror in expense-api;
  rejected to avoid new coupling).
- Showing the role alongside the name (user chose name-only).
- Any other tab or page.

## Verification

- `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` in `oa/`.
- Manual: open a Direct PA with approval history; each event shows the actor's
  full name; an event by a since-deactivated user still shows its role (no blank).

## Constraints

- UI strings English-only.
- No git commits this round — working tree only.
