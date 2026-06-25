# OA PA History Actor Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the actor's user name (instead of only their role) in the Direct PA detail History tab.

**Architecture:** Frontend-only. `HistoryTab` resolves each event's `actor_id` to a full name via epms-api's existing `/api/v1/users/directory/{id}` endpoint (through the `epmsApi` client OA already uses), and renders the name with a role fallback.

**Tech Stack:** React + TypeScript 6.0.3 + @tanstack/react-query.

## Global Constraints

- No git commits / pushes / branches this round — changes stay in the working tree.
- UI strings English-only.
- Frontend typecheck (run in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`.
- No backend change — use the existing `GET /api/v1/users/directory/{user_id}` (auth-only, returns `{ id, full_name, ... }`, 404 for unknown/inactive).

---

### Task 1: Resolve and display actor names in HistoryTab

**Files:**
- Modify: `uniops/oa/src/pages/pa/PaDetailPage.tsx`

**Interfaces:**
- Consumes: `epmsApi.get<T>(path)` from `@/lib/api` (already used elsewhere in OA); `GET /api/v1/users/directory/{id}` → `{ id: string; full_name: string }`.
- Produces: nothing for later tasks (page-local change).

- [ ] **Step 1: Import `epmsApi`**

In `uniops/oa/src/pages/pa/PaDetailPage.tsx`, change the api import (line 9):

```tsx
import { api, epmsApi } from '@/lib/api'
```

- [ ] **Step 2: Add an actor-name lookup query in `HistoryTab`**

In `HistoryTab` (starts at `function HistoryTab({ paId }: { paId: string }) {`), immediately after the existing `events` `useQuery` block and BEFORE the `if (isLoading)` early return, add:

```tsx
  // Resolve actor user names via epms-api's user directory (auth-only endpoint).
  // Inactive/unknown ids (404) are skipped and fall back to the role label.
  const actorIds = [...new Set(events.map(e => e.actor_id))].sort()
  const { data: nameMap = {} } = useQuery<Record<string, string>>({
    queryKey: ['pa-history-actors', actorIds],
    queryFn: async () => {
      const map: Record<string, string> = {}
      await Promise.all(actorIds.map(async (id) => {
        try {
          const u = await epmsApi.get<{ id: string; full_name: string }>(`/api/v1/users/directory/${id}`)
          map[id] = u.full_name
        } catch {
          /* inactive or unknown user — leave unmapped, render falls back to role */
        }
      }))
      return map
    },
    enabled: actorIds.length > 0,
  })
```

(Placing this hook before the early returns keeps hook order stable; while `events` is still loading, `actorIds` is empty and the query is disabled.)

- [ ] **Step 3: Render the name with role fallback**

In the events `.map(...)`, replace the role label line (currently `<span className="text-sm font-medium text-neutral-800">{roleLabel}</span>`) with:

```tsx
                <span className="text-sm font-medium text-neutral-800">{nameMap[ev.actor_id] ?? roleLabel}</span>
```

Keep the `roleLabel` computation (`const roleLabel = ev.actor_role.replace(...)`) — it is now the fallback, so it is still used and `actor_role` remains consumed.

- [ ] **Step 4: Typecheck**

Run (in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS, no errors (no unused-variable error for `roleLabel`, which is still referenced as the fallback).

- [ ] **Step 5: Manual verification**

Open a Direct PA that has approval history. Each event shows the actor's full name in place of the role. An event performed by a since-deactivated user (directory 404) still shows its role label rather than blank.

---

## Notes

- No commit step: leave changes in the working tree for the batch commit.
- The directory endpoint requires only authentication (no role gate), so OA `requester`/owner users can resolve names.
