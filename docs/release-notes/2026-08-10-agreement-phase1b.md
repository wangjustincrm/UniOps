# Purchase Agreement Phase 1B — release checklist

## What this phase ships

Phase 1A shipped one agreement type — `house_account` — with no schedule
concept at all: every matched invoice was flagged `legacy_settlement` with a
mandatory reason, full stop. Phase 1B adds two more agreement types that
carry a real, generated payment schedule instead of a blanket "no evidence"
flag:

- **`recurring`** — a schedule of periods (weekly/monthly/quarterly/yearly)
  is generated when the agreement reaches `active`
  (`ensure_period_rows`/`build_period_rows`). Matching an invoice to a
  recurring agreement **FIFO-claims** the next `pending`/`overdue` period by
  `sequence` — deliberately not by matching the invoice date to a period
  window, because period bills routinely arrive inside the *next* period
  (see `claim_next_period`'s comment). If the invoice total falls outside
  the claimed period's `tolerance_pct`, or nothing is left to claim, the
  server does not guess: the invoice lands in `match_review` for a human to
  assign. A successful claim raises a `confirm_period` task — this is the
  only human checkpoint before a recurring invoice can be paid, since this
  route has no goods receipt at all.
- **`milestone`** — stages (`milestone_name`, `expected_timing`,
  `expected_amount`/`amount_pct` against `not_to_exceed`) are entered at
  create/edit time. Matching requires the caller to pick an unclaimed stage
  (`schedule_id`); the server does no amount validation on this route —
  expected vs. actual is shown side by side for a human to judge.
- `house_account` is otherwise unchanged from 1A: mandatory
  `legacy_settlement_reason`, no schedule row at all.

Also new: agreement attachments (a 5th near-duplicate attachment router,
accepted by user ruling rather than factored — see `progress.md`), a
Milestone Stage editor and Recurring Fields group on the agreement
create/edit pages, a schedule table + attachments panel + period-confirm
action on the agreement detail page, a daily overdue sweep
(`app/tasks/agreement_overdue.py`) that flags periods past
`overdue_after_days` and notifies the agreement's owner/department manager,
and (this task, Task 12) `MatchPanel`'s Agreements tab now branches its UI on
`agreement_type` instead of always showing the legacy-settlement reason
field.

## Migration (single, no cross-service ordering constraint)

- **epms-api**: `ag03_agreement_schedule`, chained onto `ag02_agreement_links`.
  Creates `agreement_payment_schedule` and `agreement_attachments`, adds
  `cost_center_id` / `recurring_type` / `expected_invoice_day` /
  `anchor_month` / `expected_amount_per_period` / `tolerance_pct` /
  `overdue_after_days` to `purchase_agreements`, and adds
  `invoices.schedule_id`.

Unlike `ag02_agreement_links` (Phase 1A), **this migration has no deploy-order
constraint**: `invoices` is a shared table, but `finance-api`, `expense-api`
and `approval-api` all declare an explicit column list for it and none of
them reads `schedule_id` — so bringing up the new epms-api image before or
after the migration runs does not break any other service. (Contrast with
1A's `payment_applications.agreement_id`, which those same services' models
*do* select — see the 1A release note for why that one was order-sensitive.)
Run it via `migrate-prod.sh` as usual; re-verify the epms-api head before
deploying in case another branch has landed a migration since 2026-08-10.

**Rebuild required: `epms-api` + `epms-web` only.** No other service's model
or frontend touches `agreement_payment_schedule` / `agreement_attachments` /
the new `purchase_agreements`/`invoices` columns in this phase.

## Config default — no migration needed

`agreement_overdue_enabled` defaults to **ON** in `epms-api/app/crud/config.py`
(`_CONFIG_DEFAULTS`), read out of the existing `notification_settings` JSONB
column — no new column, no migration. This is a deliberate reversal of the
`daily_followup_enabled` precedent (that one defaults OFF and, per this
branch's own memory, nobody discovered they had to turn it on after it
shipped). The switch exists so an admin who wants the sweep quiet can turn it
off, not so someone has to find it to turn it on. **Post-deploy: confirm this
is intentional in production** — the sweep will start emailing agreement
owners/department managers about overdue periods on day one unless someone in
Portal Admin → Notification Settings turns it off first.

## Known gaps found while implementing Task 12

- **`taskTypes.ts` has no per-task-type icon anywhere in the codebase.** The
  Task 12 brief asked for `confirm_period` to be registered with "a label, an
  icon consistent with its neighbours, and a link" — the label and link
  (generic `document_type: "agr"` → `/agreements/{id}` fallback, already
  correct with no code change) exist, but there is no icon field on
  `TASK_TYPE_LABELS`, and neither `TaskInboxPage.tsx` nor the dashboard
  `TaskInbox.tsx` renders a per-type icon (both use only generic
  urgent/done/clock icons). `approve_agr`/`revise_agr`, added earlier in this
  same phase, have the same gap. No icon was invented for `confirm_period`
  specifically to avoid introducing a one-off convention nothing else follows;
  flagging here in case a future task wants to add real per-type icons across
  the board.
- `confirm_period` was also left out of `TaskInboxPage.tsx`'s `GROUP_ORDER`
  (the curated display-order list), matching `approve_agr`/`revise_agr`'s
  existing precedent — unlisted types still render correctly, just appended
  after the ordered ones instead of grouped near their siblings.

## Post-deploy verification

1. **Migration applied**: `SELECT version_num FROM alembic_version;` expects
   `ag03_agreement_schedule`. `\d agreement_payment_schedule` and
   `\d agreement_attachments` should both exist; `\d invoices` should include
   `schedule_id`; `\d purchase_agreements` should include `recurring_type`,
   `expected_invoice_day`, `anchor_month`, `expected_amount_per_period`,
   `tolerance_pct`, `overdue_after_days`, `cost_center_id`.
2. **Recurring agreement end-to-end**: create a `recurring` agreement, take
   it through the `agr` approval chain to `active`, confirm the schedule
   table populates with the right number of periods for its cycle. Match an
   invoice inside a period's tolerance — confirm `matched`, the period's
   status flips to `received`, and a `confirm_period` task appears in the
   assignee's Task Inbox labelled "Confirm Service Period" (not a raw,
   unlabelled `confirm_period` row) linking to the agreement. Match an
   invoice outside tolerance — confirm it lands in `match_review` instead and
   no period is claimed.
3. **Milestone agreement end-to-end**: create a `milestone` agreement with
   stages, approve to `active`, confirm the Milestone Stages editor round-trips
   on Edit. In `MatchPanel`, confirm the reason field is absent and a stage
   picker appears instead; confirm a claimed stage no longer appears as a
   pickable option on a second invoice.
4. **house_account unchanged**: confirm the reason field is still present and
   still mandatory — matching without one still 422s.
5. **Overdue sweep**: confirm `agreement_overdue_enabled` reads `true` by
   default (`GET` the company config, or check Portal Admin → Notification
   Settings) unless an admin has deliberately turned it off before this
   deploy.

## What was verified for Task 12 (this task's own scope)

Driven against a live dev `epms-api` (host `pytest`, and the running
`uniops_epms_api` dev container for HTTP exercises — confirmed fresh/current
via `/openapi.json` before trusting it, per Task 10's stale-container
finding) with the exact request bodies `MatchPanel.tsx` builds for each
branch:

- Recurring, invoice inside tolerance → `200`, `status: "matched"`,
  `match_route_auto: true`, the claimed period's `status` flips to
  `received`, a `confirm_period` task is created.
- Recurring, invoice outside tolerance → `200`, `status: "match_review"`,
  `match_route_auto: false`, no period claimed.
- Milestone, with `schedule_id` → `200`, `status: "matched"`, the named stage
  flips to `received`.
- Milestone, without `schedule_id` → `422`, `"Pick the milestone stage this
  invoice pays for"`.
- house_account, with `legacy_settlement_reason` → `200`,
  `legacy_settlement: true`.
- house_account, without a reason → `422`, `"A reason is required to settle
  an agreement invoice without receipt evidence"`.

Full transcript, tsc gate result, and the frontend full-regression pytest
diff: see
`.superpowers/sdd/2026-08-10-agreement-phase1b/task-12-report.md`.

## Verification steps that still need a human

No frontend test framework exists in this repo (unchanged from 1A). Task 12's
own verification was tsc + driving the real endpoints, not a browser
click-through. After deploy, a person should still:

1. Click through the recurring/milestone/house_account `MatchPanel` branches
   described above in the actual browser UI (loading states, the out-of-
   tolerance warning banner, the disabled-until-selected milestone submit
   button) — not just the API responses driving them.
2. Click the `confirm_period` task in the Task Inbox end-to-end and confirm
   it opens the right agreement and the label reads "Confirm Service Period".
3. Everything listed under Phase 1A's own "Verification steps that need a
   human" section remains applicable and has not been re-walked in this
   phase.
