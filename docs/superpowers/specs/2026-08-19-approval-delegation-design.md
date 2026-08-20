# Approval Delegation (代班) + Same-Approver Skip Fix — Design

Date: 2026-08-19
Branch: `feature/approval-delegation`
Base: `origin/main` @ `454ab4e`

## Problem

A Department Manager (Production) needs half a month of leave. The intended
stand-in is the OPM, who is already the Engineering Department Manager.

The system cannot express this. A department's manager is derived from the
users table, not stored on the department:

```sql
SELECT id FROM users
WHERE role = 'dept_manager' AND department_id = <dept> AND is_active
LIMIT 1
```

`users.role` holds one value and `users.department_id` holds one value, so one
person can manage exactly one department. `departments` has no manager column.

There is no delegation mechanism. The old one (`temp_assignments`: delegate
user, role key, start/end date) was removed in the phase 2+3 permission
restructure and its table dropped by migration `z4_drop_temp_assignments`.

`approval_backups` (Portal → Approval Routing → Backup) looks like a
solution but is not: it covers only `gm`/`opm`, and the approval engine never
reads it. It is stored, displayed, and ignored. This design does not extend it.

### Second problem, discovered while designing the first

The engine already auto-skips a following step held by the same approver
(`Auto-approved (same approver holds both roles)`), but its identity test
`_holds()` has drifted from the real authorization function
`_actor_can_approve()`:

| Step role | `_actor_can_approve` | `_holds` |
|---|---|---|
| `gm_or_opm` | `post_holder_ids` set membership | equality against `rm['gm_user_id']`, i.e. **only the first holder by sort** |
| `finance_manager`, `procurement_manager`, `vendor_manager`, `gm`, `opm` as a direct step | set membership | same collapsed first-holder equality |
| `dept_manager`, `director`, `supervisor`, `finance_bp`, `quality_manager` | resolved individually | resolved individually — correct |

Consequence: a Department Manager who also holds GM through an additional
`user_roles` role is not recognised at the `gm_or_opm` step whenever another
holder sorts first, so the skip does not fire and they must approve twice.

This is the same bug class as the already-fixed production issue recorded in
`approval-api/tests/test_engine_multiholder_approve.py` ("hanchenggang:
Department Manager + GM"). That fix repaired `_actor_can_approve` and left
`_holds` behind.

## Decisions

Settled with the product owner before design:

| Question | Decision |
|---|---|
| Which approval roles can be delegated | All of them |
| Does the delegator keep approving during the window | Yes — both can approve, first click wins |
| Who receives the notification email | The delegate only |
| Who administers delegations | `system_admin` only, in Portal Admin |
| Which tasks are delegated | Approval tasks (`approve_*`) only |
| Delegation shape | One-to-one, non-transitive, no overlapping windows per delegator |
| Audit trail | Record the delegate as actor, annotated `on behalf of <delegator>`, including on PDFs |
| Skip when the delegate is also the next step's approver | Yes — dedupe on who actually clicked |

Note on the last decision: it means a document that two people approve while
the manager is present is approved by one person while they are away. This was
raised as an internal-control trade-off and accepted.

## Approach

**Predicate widening.** The delegation table is the single source of truth and
the date range is evaluated inside the query. Read paths widen
`assigned_user_id == me` to `assigned_user_id IN (me ∪ people delegating to me
today)`; `_actor_can_approve` gains a delegation branch; the notification
recipient is substituted rather than widened. **The `tasks` table is never
rewritten.**

Two rejected alternatives:

- *Substitute the assignee when the task is created.* One change point, but
  documents already in flight when the window opens are not covered, and the
  window closing requires a second manual `resync-inflight`. The date stops
  being self-enforcing and becomes an action someone must remember to take.
  It also destroys the record of who the step really belonged to.
- *Issue a shadow task for the delegate.* No read-path changes, but it needs
  the same start/end batch mutation, fights `resync-inflight`, and pollutes
  every task-derived statistic (Dashboard, current-step, Mark Done).

Predicate widening is the only option where an expiry date expires by itself,
and the only one that cannot conflict with `resync-inflight` — because it does
not touch what `resync-inflight` rewrites.

## Data model

`approval_delegations` is owned by **approval-api**, which already owns
`approval_dept_routing` and `approval_backups`. epms-api and expense-api read
it read-only — the same cross-service pattern `access_scope.py` already uses to
read `approval_dept_routing`.

Migration `0002_approval_delegations`, `down_revision = "0001_approval_routing"`
(verified: that is the only head in approval-api).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `delegator_user_id` | UUID | the person going away |
| `delegate_user_id` | UUID | the stand-in |
| `start_date` | DATE | inclusive |
| `end_date` | DATE | inclusive |
| `note` | TEXT NULL | free text, e.g. "annual leave" |
| `revoked_at` | TIMESTAMPTZ NULL | early termination; rows are never deleted |
| `revoked_by` | UUID NULL | |
| `created_by` | UUID | |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

Constraints:

1. `CHECK (delegator_user_id <> delegate_user_id)`
2. `CHECK (end_date >= start_date)`
3. ```sql
   EXCLUDE USING gist (
     delegator_user_id WITH =,
     daterange(start_date, end_date, '[]') WITH &&
   ) WHERE (revoked_at IS NULL)
   ```
   No overlapping live windows per delegator — this is what enforces the
   one-to-one decision at the database level. `btree_gist` is already installed
   in production (created by `booking-api/alembic/versions/20260707_0001_booking_initial.py`,
   which deliberately does not drop it so other services can rely on it).

Non-transitivity is not a constraint; it follows from every query joining
exactly one level (`delegate_user_id = :me`), never recursively.

### Date semantics

Inclusive on both ends, evaluated against **today in the plant's local
timezone** (`America/Toronto`, following vms-api's existing `REPORT_TIMEZONE`
setting).

`today` is computed in Python and passed as a **bind parameter**, never written
as `CURRENT_DATE` inside the SQL. Two reasons: the containers do not set `TZ`,
so a database-side date would silently be the UTC date and expire windows four
hours early; and a bind parameter lets tests inject any date to assert the
boundaries without touching a clock.

The active-delegation predicate is exactly:

```
revoked_at IS NULL
AND start_date <= :today
AND end_date   >= :today
AND EXISTS (SELECT 1 FROM users u
            WHERE u.id = approval_delegations.delegate_user_id AND u.is_active)
```

A delegate may hold delegations from **several** delegators at once (the
exclusion constraint is per delegator, not per delegate) — one person covering
two colleagues on leave is legitimate and supported.

## Change points

### approval-api

1. **Extract `_actor_is_step_holder(...)`** — the identity half of
   `_actor_can_approve`, *without* the `actor_role == "system_admin"` bypass.
   Both `_actor_can_approve` (which keeps the bypass on top) and the
   same-approver skip walk call it. This retires `_holds()` and makes the
   drift described above structurally impossible to reintroduce.
   **This alone fixes the double-approval problem and is independently
   shippable — see Phasing.**
2. **`_actor_can_approve` gains a delegation branch.** Concretely: if
   `_actor_is_step_holder(actor)` is false, load the actor's active
   delegations and return true when `_actor_is_step_holder(delegator)` holds
   for any of them. The delegator that matched is carried out of the check so
   the caller can annotate the event — when more than one matches (a delegate
   covering two people who both approve this step, e.g. one is the Department
   Manager and the other the GM), take the one belonging to the **current
   step**, and if still ambiguous the lowest `delegator_user_id`, so the
   annotation is deterministic.
3. **Approval events** — when the actor acted as a delegate, append
   `on behalf of <delegator full name>` to the event comment.
4. **CRUD + REST** for delegations: list / create / update / revoke.
   `system_admin` only. An overlapping window returns 409 (the exclusion
   constraint violation is translated, not surfaced as a 500).

### epms-api (read-only consumers)

5. `crud/task.py` `get_for_role` — task inbox predicate.
6. `core/access_scope.py` `_open_task_doc_ids` — document visibility.
7. `services/notification.py` — recipient **substitution** (the single place
   that substitutes rather than widens, implementing "email the delegate only").
8. `crud/dashboard.py` — two places: pending-approval count and overdue-task
   count.
9. `crud/current_step.py` — display as `<delegator> (delegated: <delegate>)`.
10. `crud/signatories.py` `approval_signatories` — PDF signature block. One
    change point covers all four generators (`pdf_pr`, `pdf_po`, `pdf_pa`,
    `pdf_gr`), which all call this helper.

### expense-api (read-only consumers)

11. `_can_act_on_claim` — OA approve gate. `pa.py` imports it too; its
    docstring flags it as load-bearing for both expense claims and PAs.
12. my-actions query — OA inbox.

### portal

13. New Admin page "Approval Delegation", alongside Approval Routing.

### Frontend, otherwise unchanged

Every detail page computes its Approve button from the task list the backend
returns (`hasApproveTask`). Widening the backend predicate carries through
automatically.

## Security boundaries

- **`system_admin` is never inherited.** Delegation authorizes through
  `_actor_is_step_holder`, which has no admin bypass. Making an admin a
  delegator must not hand their unconditional approval rights to the delegate.
  Explicitly tested.
- **Delegation grants no non-approval permission.** It covers `approve_*`
  tasks only; it never widens the Access Control matrix (creating documents,
  paying, etc.). Explicitly tested.
- **Delegation is not transitive.** One-level join only.

## Edge cases

| Case | Behaviour |
|---|---|
| Delegate deactivated / leaves | The predicate joins `users.is_active`, so the delegation stops matching and approval falls back to the delegator, who never lost the right (the "both can approve" decision). No document can strand. |
| Notification recipient is a deactivated delegate | Substitution falls back to the delegator, so the mail does not disappear into a disabled account. |
| Delegator deactivated / leaves | The row is orphaned but harmless: no task points at them, so nothing matches. |
| Department Manager replaced mid-window | Delegation binds a *person*, not a post. Once the delegator is no longer the manager, new tasks do not point at them and the delegation stops applying to new documents. |
| Early return from leave | `revoked_at` takes effect immediately, without waiting for `end_date`. |
| Delegate is also a later approver on the same document | One task in the inbox; after they approve, the same-approver skip clears the later step. |
| Both people click approve at once | No new mechanism: the frontend's two-layer `isPending` gate plus the engine's state machine (409). |
| Interaction with `resync-inflight` | Orthogonal. Delegation never touches `tasks`; after a resync reissues a task to the real manager, the delegation predicate still matches it. |

## Testing

TDD: each change point gets a failing test first.

**approval-api**
- `_actor_is_step_holder` unit tests per role kind, including the multi-holder
  cases that `_holds` currently fails.
- Same-approver skip regression: Department Manager who also holds GM as an
  additional role, where another holder sorts first. Red before the fix.
- Delegation authorization: inside window, outside window, revoked, delegate
  deactivated, `system_admin` not inherited, non-approval permissions not
  granted.
- Overlapping window returns 409, not 500.

**epms-api** — inbox, document visibility, dashboard counts, current-step
label, notification recipient substitution (including the deactivated-delegate
fallback), PDF signatories annotation.

**expense-api** — `_can_act_on_claim`, my-actions.

**Drift protection for the three date predicates.** approval-api, epms-api and
expense-api each carry their own copy, because cross-service imports are not
possible in this architecture. Each service gets the *same* four boundary
assertions over the same fixture data: `start − 1` inactive, `start` active,
`end` active, `end + 1` inactive. A copy that drifts turns its own boundary
test red. No cross-service contract test — this repo has no infrastructure for
one, and the three test suites use different database env knobs.

**Baselines.** Measure the current test baseline of approval-api, epms-api and
expense-api before changing anything. Do not reuse remembered numbers: they
were taken on other commits, epms-api has three flaky tests that require
comparing the failing *set* rather than the count, and a full epms-api run
takes about an hour — budget for it.

## Phasing

**Phase 0 — same-approver skip fix (change point 1).** Independent of
delegation, low risk, no migration, and it is biting production today. Can ship
on its own branch ahead of the rest.

**Phase 1 — delegation** (change points 2-13).

## Deployment notes

- One migration, in approval-api (`0002_approval_delegations`). Applied via
  `migrate-prod.sh` before the containers roll.
- Services needing a rebuilt image: approval-api, epms-api, expense-api,
  portal-web.
- No new build args, so no `VITE_*` exposure — but the standard pre-build
  assertion over `.env.prod.example` still applies to the portal-web build.
- Nothing to backfill: an empty delegation table means every predicate behaves
  exactly as it does today.
