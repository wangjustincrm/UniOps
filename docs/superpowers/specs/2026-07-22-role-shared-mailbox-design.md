# Role Shared Mailbox for Role-Pool Notifications — Design

**Date:** 2026-07-22
**Branch:** `feature/role-shared-mailbox`
**Worktree:** `c:/Project/uniops-shared-mailbox`

## Problem

EPMS notifications for tasks that are addressed to a *role* rather than a person
(e.g. `process_pa` after a PA is fully approved, invoice-matching work) are sent
individually to every active user holding that role. AP Clerk currently has 4–5
holders, so each such event produces 4–5 near-identical emails. The AP team asked
for one shared mailbox (`ap@canadaroyalmilk.com`) to receive this traffic instead.

Today's recipient resolution — `epms-api/app/services/notification.py`, in `_dispatch`:

```python
if task.assigned_user_id:
    ... single user ...
else:
    result = await db.execute(
        select(User).where(User.role == task.assigned_role, User.is_active.is_(True))
    )
    recipients = list(result.scalars().all())
```

This dispatcher is the only role-pool email sender in the platform (VMS and Booking
notify named individuals only), so the change is contained to one service.

## Goals

- Role-pool notifications for a configured role go to **one** shared mailbox.
- Configuration is generic (any role), lives in company config, editable in Admin UI.
- No behaviour change for tasks assigned to a specific person.
- No database migration.

## Non-goals

- VMS / Booking / identity notifiers.
- Multiple addresses per role (use a real distribution group for that).
- A "shared mailbox **and** individuals" dual-send mode.
- Changing which tasks are role-pool vs. personally assigned.

## Design

### 1. Configuration

New key inside the existing `company_config.notification_settings` JSONB column
(free-form `dict[str, Any]` in both the model and the Pydantic schema, so no
migration and no schema change is required):

```json
{
  "default_channel": "email_only",
  "teams_webhook_url": null,
  "followup_time": "08:00",
  "role_shared_mailboxes": { "ap_clerk": "ap@canadaroyalmilk.com" }
}
```

- Key = role key (built-in or custom role), value = a single email address.
- Missing key, `null`, or empty string = that role keeps today's per-user behaviour.
- Default value when absent: `{}`.

### 2. Dispatch logic

In `_dispatch` (`epms-api/app/services/notification.py`), the recipient-resolution
block becomes a three-way branch:

| Condition | Behaviour |
|---|---|
| `task.assigned_user_id` is set | **Unchanged** — resolve that user, honour their `notification_channel`, email and/or Teams. |
| `assigned_user_id` is `NULL` **and** `role_shared_mailboxes[task.assigned_role]` is non-empty | Send exactly **one** email to the shared mailbox — unless the company channel suppresses it (see below). No per-user email, no Teams card. Per-user `notification_channel` is not consulted. |
| `assigned_user_id` is `NULL` and no shared mailbox configured | **Unchanged** — one email (and/or Teams card) per active user holding the role. |

Details of the shared-mailbox branch:

- The company-level master switch is evaluated first and is unchanged: when
  `notification_settings.default_channel == "none"`, nothing is sent, shared
  mailbox included. (This early return already exists.)
- The shared-mailbox path is **email-only** (it never posts a Teams card), so it
  is additionally bound to the company `default_channel`: the email is
  **suppressed on the explicit value `"teams_only"`** and sent for every other
  channel (`email_only`, `both`, and any unexpected/typo'd stored value — which
  still delivers email, matching the per-user path whose per-user default is
  `email_only`). Under `teams_only` nothing at all is dispatched for that task:
  there is deliberately **no** fallback to the per-member fan-out.
- The shared-mailbox lookup happens **before** the role-member query, so a
  notification is still delivered when the role currently has zero active
  members. This is an intentional behaviour change: today an empty role pool
  silently drops the notification; with a shared mailbox configured the mailbox
  *is* the recipient, independent of who holds the role.
- Template rendering is identical to the per-user path, except
  `recipient_name` = *role display name* + `" Team"` (e.g. `"AP Clerk Team"`).
  The display name comes from the existing role-label map in
  `epms-api/app/crud/config.py` (`"ap_clerk": "AP Clerk"`), extended to look at
  `custom_roles` as well; when a role has no label, fall back to the role key
  title-cased with underscores replaced by spaces.
- Retry/backoff uses the existing `_send_with_retry` helper, extended (not
  unchanged): it now takes `User | None` plus an optional explicit
  `recipient_email`, so the shared-mailbox delivery logs through the same code.

### 3. Delivery log

`notification_logs.user_id` is already nullable (`ondelete="SET NULL"`), so a
shared-mailbox delivery is logged as `user_id = NULL`,
`recipient_email = "<shared mailbox>"`, `channel = "email"`, with the normal
`status` / `attempt` / `error_message` semantics. `_send_with_retry` currently
takes a `User`; it will take an optional user plus an explicit recipient email so
both paths log through the same code.

### 4. Admin UI

EPMS Admin → **Notifications** tab (`epms/src/pages/admin/AdminPanel.tsx`, the
notification-settings section) gains a **Role Shared Mailboxes** block:

- One labelled text input per role, listing built-in roles plus configured custom roles.
- Placeholder text makes the disabled state obvious (empty = individual delivery).
- Email format validated on save; invalid entries block the save with an inline message.
- Saved through the existing `updateConfig.mutate({ notification_settings })` call —
  no new endpoint.
- All user-facing copy in English, matching the rest of the panel.

### 5. Side effect: daily reminders

The daily pending-task reminder (`daily_pending_reminder`) runs through this same
dispatcher, so role-pool reminders for a configured role also collapse into a
single email to the shared mailbox. This is desirable and requires no extra work.

## Testing

Extending `epms-api/tests/test_notification_dispatch.py`:

1. Role-pool task, shared mailbox configured → exactly one email, addressed to the
   shared mailbox; zero emails to individual role members; zero Teams sends.
2. Role-pool task, shared mailbox configured, role has **no** active members →
   still exactly one email to the shared mailbox.
3. Role-pool task, no shared mailbox configured → one email per active role member
   (regression guard on today's behaviour).
4. Task with `assigned_user_id` set, shared mailbox configured for the same role →
   email goes to that user only.
5. `default_channel = "none"` with a shared mailbox configured → nothing is sent.
6. Rendered body of a shared-mailbox email contains `"AP Clerk Team"` as the
   recipient name.
7. A shared-mailbox delivery writes a `notification_logs` row with `user_id IS NULL`
   and the mailbox in `recipient_email`.

Frontend: Portal/EPMS typecheck must stay at its existing baseline (EPMS frontend
is TS 5.9.3 with 69 pre-existing errors — no new ones).

## Files touched

- `epms-api/app/services/notification.py` — dispatch branch, role-label helper use, log signature.
- `epms-api/app/crud/config.py` — default `role_shared_mailboxes: {}` in notification settings; export role-label lookup helper.
- `epms/src/pages/admin/AdminPanel.tsx` — Role Shared Mailboxes UI block.
- `epms/src/services/config.ts` — `NotificationSettings` type gains `role_shared_mailboxes?: Record<string, string>`.
- `epms-api/tests/test_notification_dispatch.py` — cases above.

No Alembic migration. No API contract change.

## Rollout

Config-only activation: after deploy, set AP Clerk's shared mailbox to
`ap@canadaroyalmilk.com` in Admin → Notifications. Reverting is emptying the field —
no code rollback needed.
