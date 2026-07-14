# VMS: Recurring Overdue Reminder + Overdue Badge — Design

**Date:** 2026-07-13
**Status:** Approved (user confirmed both features; escalation stays one-shot)

## Background

VMS-CO-010 sends the Host ONE email when a checked-in visitor is 1h+ past
`planned_departure`; VMS-CO-011 escalates ONCE to the dept manager at 4h+.
After that the system is silent — a visitor stuck "checked_in" for 3 days
(prod visit `2d59ff87…`, 2026-07-10) generated no further signal, and the UI
shows only the green "On-Site" badge. Both prod emails were verified sent
(flags set 2026-07-10 19:19 / 22:29 UTC), so the gap is by-design one-shot
semantics, not a delivery failure.

## Decisions

1. **Host reminder becomes recurring**: re-send every 24h until checkout.
2. **Dept-manager escalation stays one-shot** (avoid manager mail noise;
   daily pressure goes to the Host).
3. **UI gets a red "Overdue" badge** on visit list, visit detail, and active
   visits pages.

## Backend (vms-api)

- `app/services/scheduled_jobs.py` — reinterpret `overdue_reminder_sent_at`
  as "last sent at" instead of a one-shot flag. Query condition changes from
  `overdue_reminder_sent_at IS NULL` to
  `IS NULL OR overdue_reminder_sent_at < now - OVERDUE_REMINDER_REPEAT`.
- New module constant `OVERDUE_REMINDER_REPEAT = timedelta(hours=24)`
  alongside the existing threshold constants.
- **No migration** — the existing nullable timestamptz column already holds
  the needed value. Checkout flips status to `checked_out`, so the query
  stops matching and re-sends stop naturally.
- `app/services/notifications.py` — `notify_host_overdue` body gains one
  line: "You will receive this reminder daily until the visitor is checked
  out."
- `escalate_overdue` untouched.

## Frontend (vms)

- Overdue is a **derived state** (matches backend doc comment):
  `status === 'checked_in' && planned_departure && planned_departure < now`.
- `src/components/StatusBadge.tsx` — add exported `isVisitOverdue()` helper
  and `OverdueBadge` component (red, `bg-danger-50 text-danger-600
  ring-red-200`, same pill shape as `StatusBadge`). Label: "Overdue".
- Render next to the status badge in:
  - `src/pages/VisitListPage.tsx` (row)
  - `src/pages/VisitDetailPage.tsx` (header)
  - `src/pages/ActiveVisitsPage.tsx`
- All user-facing text English-only.

## Testing

- Extend `vms-api/tests/test_scheduled_jobs.py`:
  1. reminder already sent >24h ago → re-sent, timestamp refreshed;
  2. reminder sent <24h ago → not re-sent;
  3. checked-out visit → never re-sent.
- Frontend: typecheck via
  `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`.

## Out of scope

- Repeating the dept-manager escalation.
- Auto-checkout at end of day / anomaly flagging.
- Making the 24h interval admin-configurable (constant is enough; YAGNI).
