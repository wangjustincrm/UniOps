# Task 2 Report: Dispatch role-pool notifications to the shared mailbox

## Status: DONE_WITH_CONCERNS

(Reason for "concerns": one pre-existing cross-file test-isolation issue was
observed and investigated in Step 5 — see "Self-review findings" below. It
is not caused by this change; documenting it per the brief's instruction to
"record its pass/fail set and compare against its state before your
change".)

Note: this exact report path (`task-2-report.md`) contained a stale report
from an unrelated earlier task ("NC65 COA pure mapping functions", a
finance-api change). It has been overwritten with this task's report below.

## What was changed

### `epms-api/app/services/notification.py`

Followed the brief's Step 3 code verbatim, no divergence from the brief
needed (line numbers had drifted only slightly from Task 1, code context
matched cleanly):

1. Added `_shared_mailbox_for(notif_settings, role) -> str | None` (lines
   85–96), placed immediately above the `# ── SMTP config helper ──` section
   as instructed.
2. In `_dispatch`, widened the lazy import to also pull in
   `role_display_name` (line 154):
   `from app.crud.config import get_or_create as get_config, role_display_name`
3. Replaced the recipient-resolution block (lines 179–246): computes
   `shared_mailbox` up front for role-pool tasks (`assigned_user_id is
   None`), skips the existing recipient-list/`if not recipients: return`
   logic when a shared mailbox is configured, and — after `base_vars` is
   built — branches to build `team_vars` (with
   `recipient_name = "{role_display_name} Team"`), render subject/body,
   send exactly one email via `_send_with_retry(..., user=None,
   recipient_email=shared_mailbox)`, and `return` before reaching the
   per-user loop (so no Teams card and no per-user `notification_channel`
   check happen for shared-mailbox deliveries).
4. Widened `_send_with_retry` (lines 289–347): `user: User | None`, new
   keyword-only `recipient_email: str | None = None`; `to_addr` and
   `user_id` derived once and used in all `NotificationLog` constructions
   and the warning log line (`recipient=%s` replaces `user=%s`).

### `epms-api/tests/test_notification_dispatch.py`

Appended the brief's Step-1 test block verbatim (starting after the
existing `test_default_notification_settings_has_shared_mailbox_map`):
imports (`sa_select`, `NotificationLog`), `SHARED_MAILBOX` constant,
`_make_user_with_role`, `_set_notif_settings`, `_make_pool_task`,
`captured_teams` fixture, and 8 new test functions:
`test_shared_mailbox_replaces_per_member_email`,
`test_shared_mailbox_sends_even_when_role_pool_is_empty`,
`test_without_shared_mailbox_every_member_is_emailed`,
`test_named_assignee_ignores_shared_mailbox`,
`test_default_channel_none_beats_shared_mailbox`,
`test_shared_mailbox_skips_teams`, `test_shared_mailbox_body_greets_the_team`,
`test_shared_mailbox_delivery_is_logged_without_user`.

The seeded `create_pa_reminder` template's body already contains
`{recipient_name}` in this environment (visible in the Step-2 failure
capture below: `"Hi AP,<br><br>Invoice ..."` — wait, actually
`recipient_name` rendered as "AP" for other tests, confirming the
placeholder exists), so the brief's fallback (explicitly overriding
`cfg.email_templates["create_pa_reminder"]`) was **not** needed —
`test_shared_mailbox_body_greets_the_team` passed on the first try with the
seeded template.

## Exact test commands and output

### Baseline (before any edits)

```
cd c:/Project/uniops-shared-mailbox/epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms_test POSTGRES_USER=epms POSTGRES_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 JWT_SECRET_KEY=test-secret-key /c/Project/uniops/epms-api/.venv/Scripts/python.exe -m pytest tests/test_notification_dispatch.py -v
```

```
tests/test_notification_dispatch.py::test_default_channel_none_suppresses_all_email PASSED [ 25%]
tests/test_notification_dispatch.py::test_default_channel_email_still_sends PASSED [ 50%]
tests/test_notification_dispatch.py::test_role_display_name_builtin_custom_and_fallback PASSED [ 75%]
tests/test_notification_dispatch.py::test_default_notification_settings_has_shared_mailbox_map PASSED [100%]

======================== 4 passed, 1 warning in 6.02s =========================
```

```
cd c:/Project/uniops-shared-mailbox/epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms_test POSTGRES_USER=epms POSTGRES_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 JWT_SECRET_KEY=test-secret-key /c/Project/uniops/epms-api/.venv/Scripts/python.exe -m pytest tests/test_invoice_assign.py -v
```

```
14 passed, 74 warnings in 65.54s (0:01:05)
```
(all 14 individual tests PASSED — see raw log; this is the pre-change
baseline for Step 5's comparison)

### Step 2 — failing run (after appending tests, before implementation)

```
cd c:/Project/uniops-shared-mailbox/epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms_test POSTGRES_USER=epms POSTGRES_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 JWT_SECRET_KEY=test-secret-key /c/Project/uniops/epms-api/.venv/Scripts/python.exe -m pytest tests/test_notification_dispatch.py -v -k shared_mailbox
```

```
collecting ... collected 12 items / 3 deselected / 9 selected

tests/test_notification_dispatch.py::test_default_notification_settings_has_shared_mailbox_map PASSED [ 11%]
tests/test_notification_dispatch.py::test_shared_mailbox_replaces_per_member_email FAILED [ 22%]
tests/test_notification_dispatch.py::test_shared_mailbox_sends_even_when_role_pool_is_empty FAILED [ 33%]
tests/test_notification_dispatch.py::test_without_shared_mailbox_every_member_is_emailed PASSED [ 44%]
tests/test_notification_dispatch.py::test_named_assignee_ignores_shared_mailbox PASSED [ 55%]
tests/test_notification_dispatch.py::test_default_channel_none_beats_shared_mailbox PASSED [ 66%]
tests/test_notification_dispatch.py::test_shared_mailbox_skips_teams FAILED [ 77%]
tests/test_notification_dispatch.py::test_shared_mailbox_body_greets_the_team FAILED [ 88%]
tests/test_notification_dispatch.py::test_shared_mailbox_delivery_is_logged_without_user FAILED [100%]

FAILED test_shared_mailbox_replaces_per_member_email - AssertionError: role-pool task must produce exactly one email
    assert 3 == 1   (3 per-member emails sent, no shared-mailbox branch exists yet)
FAILED test_shared_mailbox_sends_even_when_role_pool_is_empty - assert 0 == 1
    (empty pool -> "no recipients" early-return -> 0 emails, no shared-mailbox fallback yet)
FAILED test_shared_mailbox_skips_teams - AssertionError: assert 9 == 1
    (per-member fan-out; teams path also fires because no shared-mailbox short-circuit)
FAILED test_shared_mailbox_body_greets_the_team - assert 9 == 1
FAILED test_shared_mailbox_delivery_is_logged_without_user - assert 9 == 1

============ 5 failed, 4 passed, 3 deselected, 1 warning in 10.93s ============
```

This matches the brief's Step-2 expectation exactly: 0 or N emails instead
of 1, and specifically the "sends_even_when_role_pool_is_empty" case failed
with 0 (no recipients -> early return, no shared-mailbox path). The three
tests that already passed at this point
(`test_without_shared_mailbox_every_member_is_emailed`,
`test_named_assignee_ignores_shared_mailbox`,
`test_default_channel_none_beats_shared_mailbox`) assert on behavior that
is unchanged by this feature (no shared mailbox configured / named assignee
/ company-wide suppression), so it is correct and expected that they
already passed pre-implementation.

### Step 4 — full suite, after implementation

```
cd c:/Project/uniops-shared-mailbox/epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms_test POSTGRES_USER=epms POSTGRES_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 JWT_SECRET_KEY=test-secret-key /c/Project/uniops/epms-api/.venv/Scripts/python.exe -m pytest tests/test_notification_dispatch.py -v
```

```
collecting ... collected 12 items

tests/test_notification_dispatch.py::test_default_channel_none_suppresses_all_email PASSED [  8%]
tests/test_notification_dispatch.py::test_default_channel_email_still_sends PASSED [ 16%]
tests/test_notification_dispatch.py::test_role_display_name_builtin_custom_and_fallback PASSED [ 25%]
tests/test_notification_dispatch.py::test_default_notification_settings_has_shared_mailbox_map PASSED [ 33%]
tests/test_notification_dispatch.py::test_shared_mailbox_replaces_per_member_email PASSED [ 41%]
tests/test_notification_dispatch.py::test_shared_mailbox_sends_even_when_role_pool_is_empty PASSED [ 50%]
tests/test_notification_dispatch.py::test_without_shared_mailbox_every_member_is_emailed PASSED [ 58%]
tests/test_notification_dispatch.py::test_named_assignee_ignores_shared_mailbox PASSED [ 66%]
tests/test_notification_dispatch.py::test_default_channel_none_beats_shared_mailbox PASSED [ 75%]
tests/test_notification_dispatch.py::test_shared_mailbox_skips_teams PASSED [ 83%]
tests/test_notification_dispatch.py::test_shared_mailbox_body_greets_the_team PASSED [ 91%]
tests/test_notification_dispatch.py::test_shared_mailbox_delivery_is_logged_without_user PASSED [100%]

======================= 12 passed, 1 warning in 12.15s ========================
```

All 12 tests pass (4 pre-existing/Task-1 + 8 new), matching the brief's
Step-4 expectation exactly.

### Step 5 — regression check with `test_invoice_assign.py`

```
cd c:/Project/uniops-shared-mailbox/epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms_test POSTGRES_USER=epms POSTGRES_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 JWT_SECRET_KEY=test-secret-key /c/Project/uniops/epms-api/.venv/Scripts/python.exe -m pytest tests/test_invoice_assign.py tests/test_notification_dispatch.py -v
```

Post-change output:

```
tests/test_invoice_assign.py::test_assign_match_creates_task PASSED      [  3%]
tests/test_invoice_assign.py::test_assign_match_reassigns_existing_task PASSED [  7%]
tests/test_invoice_assign.py::test_assign_match_requires_ap_role PASSED  [ 11%]
tests/test_invoice_assign.py::test_assignee_can_match_and_see_invoice PASSED [ 15%]
tests/test_invoice_assign.py::test_user_without_task_still_403 PASSED    [ 19%]
tests/test_invoice_assign.py::test_assign_match_rejects_matched_invoice PASSED [ 23%]
tests/test_invoice_assign.py::test_response_surfaces_assignee PASSED     [ 26%]
tests/test_invoice_assign.py::test_ap_direct_match_completes_assignee_task PASSED [ 30%]
tests/test_invoice_assign.py::test_delete_completes_open_match_task PASSED [ 34%]
tests/test_invoice_assign.py::test_assignee_keeps_detail_visibility_after_match PASSED [ 38%]
tests/test_invoice_assign.py::test_assignee_sees_match_candidates PASSED [ 42%]
tests/test_invoice_assign.py::test_decline_match_bounces_back_to_assigner PASSED [ 46%]
tests/test_invoice_assign.py::test_match_candidates_carry_already_allocated PASSED [ 50%]
tests/test_invoice_assign.py::test_reassign_notifies_new_assignee_not_previous PASSED [ 53%]
tests/test_notification_dispatch.py::test_default_channel_none_suppresses_all_email FAILED [ 57%]
tests/test_notification_dispatch.py::test_default_channel_email_still_sends PASSED [ 61%]
tests/test_notification_dispatch.py::test_role_display_name_builtin_custom_and_fallback PASSED [ 65%]
tests/test_notification_dispatch.py::test_default_notification_settings_has_shared_mailbox_map PASSED [ 69%]
tests/test_notification_dispatch.py::test_shared_mailbox_replaces_per_member_email FAILED [ 73%]
tests/test_notification_dispatch.py::test_shared_mailbox_sends_even_when_role_pool_is_empty FAILED [ 76%]
tests/test_notification_dispatch.py::test_without_shared_mailbox_every_member_is_emailed FAILED [ 80%]
tests/test_notification_dispatch.py::test_named_assignee_ignores_shared_mailbox PASSED [ 84%]
tests/test_notification_dispatch.py::test_default_channel_none_beats_shared_mailbox FAILED [ 88%]
tests/test_notification_dispatch.py::test_shared_mailbox_skips_teams FAILED [ 92%]
tests/test_notification_dispatch.py::test_shared_mailbox_body_greets_the_team FAILED [ 96%]
tests/test_notification_dispatch.py::test_shared_mailbox_delivery_is_logged_without_user PASSED [100%]

================= 8 failed, 18 passed, 74 warnings in 59.17s ==================
```

`test_invoice_assign.py`: **14/14 PASSED, no change from baseline.** ✓ (this
is the requirement the brief actually asks to preserve: "`test_invoice_assign.py`
must not gain new failures").

`test_notification_dispatch.py`, run in this combined order, regressed from
12/12 (isolated) to 4/12. See "Self-review findings" below — this is a
**pre-existing** issue confirmed unrelated to this change.

## Self-review findings

**Investigated: why does `test_notification_dispatch.py` fail when run
directly after `test_invoice_assign.py`, but pass 12/12 in isolation?**

> **⚠️ CORRECTION (2026-07-22, follow-up review pass).** The root-cause
> paragraph immediately below is **WRONG** and has been superseded. The
> stray-background-task mechanism it proposes is not supported by the
> evidence, and the real cause has since been measured. See
> **"Root cause of the combined-run failures — corrected"** in the
> follow-up report appended at the end of this file. The original text is
> kept verbatim for the record.

~~The first failure in the combined run,
`test_default_channel_none_suppresses_all_email`, is itself one of the two
*pre-existing* tests (not touched by this task) — its own logic (set
`default_channel="none"`, dispatch, assert zero emails) is unchanged and
trivially correct; the captured email that fails the assertion carries the
title `"Action Required: Approve PR PR-TEST-1"`, i.e. it originates from a
task built by this same test file's `_make_task` helper — meaning it is a
stray, still-running background `asyncio.create_task` (fire-and-forget
notification, e.g. from `test_reassign_notifies_new_assignee_not_previous`
in `test_invoice_assign.py`, which explicitly `await asyncio.sleep(0.3)`
after firing a background notify and comments "让首派的后台通知跑完" — "let
the first-assign background notification finish running", i.e. the test
author already knew this was a race) whose real (unmocked) SMTP attempts
are still retrying with `2**attempt` backoff (2s, 4s) when the next test
file's `captured_emails` monkeypatch becomes active, so the stray task's
lazily-resolved `send_email` call lands in the *new* test's capture list.~~

To confirm this is pre-existing and not introduced by my change, I stashed
my diff (`git stash`), re-ran the exact same combined command against the
original code, and got:

```
tests/test_notification_dispatch.py::test_default_channel_none_suppresses_all_email FAILED [ 83%]
...
============ 1 failed, 17 passed, 74 warnings in 61.42s (0:01:01) =============
```

Same failure, same mechanism (`WinError 1225: connection refused` from the
stray background task's real SMTP attempt visible in the captured log),
confirmed pre-existing. I then `git stash pop`'d to restore my changes.
Given my change adds 8 more tests that call `dispatch_task_notification`
and assert exact email counts, they are equally susceptible to this same
pre-existing race when run in this specific combined order — hence 1
pre-existing failure became 8 in the combined run, while the suite most
directly relevant to this task (`test_notification_dispatch.py` in
isolation, which is the command explicitly given in the task instructions
for verifying this suite) is a clean 12/12, and `test_invoice_assign.py`
(the suite Step 5 explicitly cares about not regressing) stayed 14/14
before and after.

~~**Conclusion:** no code change made in response to this finding — it is a
test-isolation issue in the existing fire-and-forget/background-task test
infrastructure (stray real SMTP retries surviving past their originating
test's `asyncio.sleep(0.3)` grace window and bleeding into a later file's
monkeypatch scope), predates this task, and is out of scope for "the
dispatcher change itself."~~ **(superseded — see the corrected root cause
in the follow-up report below; the combined run is now clean.)**

I also reviewed:
- All call sites of `_send_with_retry` — confirmed only the two within
  `notification.py` itself (the new shared-mailbox branch and the existing
  per-user loop), both updated consistently with the new signature; no
  other module calls it.
- The diff against the brief's exact code blocks — copied verbatim,
  including the Chinese comment on the recipient-resolution block and the
  docstring addition on `_send_with_retry`. No divergence from the brief
  was needed; line numbers had drifted only slightly since Task 1 but all
  anchor text matched.
- The `test_shared_mailbox_body_greets_the_team` fallback note in the
  brief (manually setting `create_pa_reminder`'s template if
  `{recipient_name}` isn't in the seeded body) — not needed; the seeded
  template already contains the placeholder in this environment, confirmed
  by the test passing unmodified on the first implementation run.

## Things I was unsure of

- Whether the Step-5 "same pass/fail set" requirement should be judged
  against the *isolated* run of each suite or the *combined* run. I judged
  it against `test_invoice_assign.py`'s own results (unaffected either way:
  14/14) since that is the suite the brief is explicit about ("must not
  gain new failures — it exercises the named-assignee path through
  `_send_with_retry`"), and treated the combined-run pollution of
  `test_notification_dispatch.py` as a separate, pre-existing, documented
  concern rather than a blocker, since it reproduces identically without
  any of my code present. If the intent was instead that the combined run
  must be clean end-to-end, that would require fixing the background-task
  test-isolation infrastructure in `test_invoice_assign.py` (e.g. properly
  awaiting/cancelling `fire_and_forget_notify`'s background tasks in
  fixtures rather than a fixed `asyncio.sleep(0.3)`), which is out of this
  task's stated scope (the dispatcher logic, not test infrastructure) and
  touches a different test file's fixtures than the ones this task owns.

## Commit

```
7db90ea feat(epms): route role-pool notifications to a per-role shared mailbox
 2 files changed, 250 insertions(+), 22 deletions(-)
```

Files: `epms-api/app/services/notification.py` (modified),
`epms-api/tests/test_notification_dispatch.py` (appended).

---

# Follow-up: review fixes (2026-07-22)

## Status: DONE

Applies the six review items agreed after the review of commit `7db90ea`:
two behaviour changes (A, B), three test-hygiene fixes (C, D, E), and this
root-cause correction (F). No Alembic migration.

## (A) `teams_only` no longer fires the shared-mailbox email

The shared-mailbox branch previously ran for any `default_channel != "none"`.
It now runs only when the company channel actually includes email. Because
that path deliberately never posts a Teams card, `default_channel="teams_only"`
plus a configured shared mailbox now dispatches **nothing at all** - and, in
particular, does **not** fall back to the per-member fan-out.

`epms-api/app/services/notification.py`, right after `shared_mailbox` is
resolved:

```python
# 共享邮箱路径只发邮件(不发 Teams),因此公司渠道不含 email 时整条通知不发。
if shared_mailbox and company_channel not in ("email_only", "both"):
    logger.info(
        "Shared mailbox configured for role %s but company default_channel=%s "
        "excludes email; skipping task %s",
        task.assigned_role, company_channel, task.id,
    )
    return
```

New test: `test_teams_only_channel_sends_nothing_for_shared_mailbox` -
asserts nothing reaches the shared mailbox, nothing reaches the role member,
and no Teams card is posted.

## (B) Template-less rendering is now symmetric

The shared-mailbox branch rendered placeholders in its no-template fallback
while the per-user loop did not, so a template-less notification emitted a
literal `{recipient_name}` to individuals. Both no-template fallbacks in the
per-user loop now render through `_render(..., user_vars)`:

```python
# email body
subject = task.title
# 与共享邮箱分支保持一致:无模板时也要渲染占位符,
# 否则收件人会看到字面量 {recipient_name}。
html_body = _render(task.description or task.title, user_vars)

# teams card body
card_title = task.title
card_body = _render(task.description or task.title, user_vars)
```

New test: `test_named_assignee_body_renders_placeholders_without_template` -
dispatches with `template_key="no_such_template_key"` (forces the no-template
branch) to a named assignee called "Norbert Placeholder" and asserts
`"Hi Norbert,"` is in the body and `"{recipient_name}"` is not.

**Both new tests were verified to fail against the pre-fix code** (checked
out HEAD's `notification.py`, kept the new tests):

```
FAILED tests/test_notification_dispatch.py::test_teams_only_channel_sends_nothing_for_shared_mailbox
  - AssertionError: teams_only must not send the shared-mailbox email
    assert 'ap@canadaroyalmilk.com' not in ['ap@canadaroyalmilk.com']
FAILED tests/test_notification_dispatch.py::test_named_assignee_body_renders_placeholders_without_template
  - assert 'Hi Norbert,' in '... Hi {recipient_name}, PA-TEST-1 needs processing. ...'
================= 2 failed, 12 deselected, 1 warning in 6.50s =================
```

## (C) Every test filters to its own mail

`captured_emails` collects every email dispatched while its monkeypatch is
live, and neither the `company_config` row nor the `users` table is reset
between tests, so `len(captured_emails) == 1` was fragile. All tests
(including the two pre-existing `default_channel` ones) now filter by
recipient address before asserting:

- `sent_to.count(SHARED_MAILBOX) == 1` **plus** "none of this test's three
  members were mailed" - still fails if the fan-out regresses;
- `sent_to.count(user.email) == 1` **plus** `SHARED_MAILBOX not in sent_to`
  for the named-assignee test;
- the "channel = none" tests assert their own user's address and the shared
  mailbox are both absent (rather than the global list being empty);
- the body-assertion tests capture `(to, html)` and filter to the expected
  recipient before asserting on the body.

No assertion was weakened into something a stub would satisfy - each still
pins both halves of the behaviour (the mail that must be sent, and the mail
that must not).

## (D) `notification_settings` is snapshotted and restored

New module-level autouse fixture `_restore_notification_settings` snapshots
`company_config.notification_settings` before each test and writes it back
afterwards, so this file no longer leaves `role_shared_mailboxes` and
`teams_webhook_url="https://example.com/webhook"` behind for later test
files. It is autouse, so the two pre-existing `default_channel` tests are
covered too.

## (E) Helper collapsed

`_set_default_channel` was a strict subset of `_set_notif_settings` and has
been deleted; its two call sites now use
`_set_notif_settings(db, default_channel=...)`.

## (F) Root cause of the combined-run failures - corrected

**The original report's explanation was wrong.** It blamed a stray
fire-and-forget background notification from `test_invoice_assign.py`. That
cannot be right: the captured subject "Action Required: Approve PR PR-TEST-1"
is the `pr_approval_request` template rendered with
`document_number="PR-TEST-1"`, and `PR-TEST-1` exists only in `_make_task`
inside `test_notification_dispatch.py` itself - `test_invoice_assign.py` has
no `approve_pr` task and no `PR-TEST-1` anywhere. The failing email was the
test's **own** dispatch, not a stray.

**Measured actual cause: duplicate `company_config` rows.**
`app/crud/config.py::get_or_create` is

```python
result = await db.execute(select(CompanyConfig).limit(1))
```

- no `ORDER BY`, and `company_config` has no singleton constraint. When
`test_invoice_assign.py` drives concurrent API sessions, several of them can
each find no row and insert their own. I counted the rows directly with a
throwaway test appended after the invoice suite (the session-scoped
`test_engine` fixture drops all tables on teardown, so the count has to be
taken inside the same run):

```
cd .../epms-api && ... pytest tests/test_invoice_assign.py tests/test_zz_tmp_config_rows.py -s -q

COMPANY_CONFIG ROW COUNT: 4
ROW: 7e21a992-f466-42dd-b719-ba89239ed40a EPMS {'followup_time': '08:00', 'default_channel': 'email_only', 'teams_webhook_url': None, 'role_shared_mailboxes': {}}
ROW: 428e6ff3-8bdc-4d1a-bcad-00f2e8f30d6c EPMS {'followup_time': '08:00', 'default_channel': 'email_only', 'teams_webhook_url': None, 'role_shared_mailboxes': {}}
ROW: 7834b802-ca5f-4089-aa16-9c761f68f1f6 EPMS {'followup_time': '08:00', 'default_channel': 'email_only', 'teams_webhook_url': None, 'role_shared_mailboxes': {}}
ROW: f42f0c0b-abd4-4967-b6bb-0036e45d259a EPMS {'followup_time': '08:00', 'default_channel': 'email_only', 'teams_webhook_url': None, 'role_shared_mailboxes': {}}
LIMIT 1 RETURNS: [(UUID('7e21a992-f466-42dd-b719-ba89239ed40a'),)]
================= 15 passed, 74 warnings in 60.19s (0:01:00) ==================
```

Four rows. So `_set_notif_settings` updated whichever row an un-ordered
`LIMIT 1` happened to return, and the dispatcher's own `get_or_create` - an
unordered seq scan whose first tuple changes once an UPDATE moves the updated
row to the end of the heap - could read a *different*, still-default row.
That is exactly what the failure output showed: the `default_channel="none"`
tests saw an email sent anyway (the dispatcher read a row with
`default_channel='email_only'`) and the shared-mailbox tests saw a
three-member fan-out with zero shared-mailbox mail (the dispatcher read a row
with `role_shared_mailboxes={}`). Nothing to do with SMTP retries or
background tasks.

Fix (test-side, no production change): `_write_notif_settings` writes the
value to **every** `company_config` row, so the setting is unambiguous
whichever row the dispatcher lands on. The snapshot/restore fixture uses the
same helper.

```python
await db.execute(sa_update(CompanyConfig).values(notification_settings=value))
await db.commit()
```

The underlying `get_or_create` weakness is real production code (a duplicate
`company_config` row in prod would make config reads non-deterministic), but
fixing it - a singleton constraint plus a deterministic
`ORDER BY created_at LIMIT 1` - needs a migration and is out of scope here.
**Flagged for a follow-up task.**

## Test evidence

### 1. `tests/test_notification_dispatch.py` alone - 14/14 PASSED

```
cd c:/Project/uniops-shared-mailbox/epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms_test POSTGRES_USER=epms POSTGRES_PASSWORD=*** JWT_SECRET_KEY=test-secret-key /c/Project/uniops/epms-api/.venv/Scripts/python.exe -m pytest tests/test_notification_dispatch.py -v
```

```
collected 14 items

tests/test_notification_dispatch.py::test_default_channel_none_suppresses_all_email PASSED [  7%]
tests/test_notification_dispatch.py::test_default_channel_email_still_sends PASSED [ 14%]
tests/test_notification_dispatch.py::test_role_display_name_builtin_custom_and_fallback PASSED [ 21%]
tests/test_notification_dispatch.py::test_default_notification_settings_has_shared_mailbox_map PASSED [ 28%]
tests/test_notification_dispatch.py::test_shared_mailbox_replaces_per_member_email PASSED [ 35%]
tests/test_notification_dispatch.py::test_shared_mailbox_sends_even_when_role_pool_is_empty PASSED [ 42%]
tests/test_notification_dispatch.py::test_without_shared_mailbox_every_member_is_emailed PASSED [ 50%]
tests/test_notification_dispatch.py::test_named_assignee_ignores_shared_mailbox PASSED [ 57%]
tests/test_notification_dispatch.py::test_default_channel_none_beats_shared_mailbox PASSED [ 64%]
tests/test_notification_dispatch.py::test_teams_only_channel_sends_nothing_for_shared_mailbox PASSED [ 71%]
tests/test_notification_dispatch.py::test_shared_mailbox_skips_teams PASSED [ 78%]
tests/test_notification_dispatch.py::test_shared_mailbox_body_greets_the_team PASSED [ 85%]
tests/test_notification_dispatch.py::test_named_assignee_body_renders_placeholders_without_template PASSED [ 92%]
tests/test_notification_dispatch.py::test_shared_mailbox_delivery_is_logged_without_user PASSED [100%]

======================= 14 passed, 1 warning in 13.19s ========================
```

### 2. `tests/test_invoice_assign.py` alone - 14/14 PASSED (unchanged)

```
tests/test_invoice_assign.py::test_assign_match_creates_task PASSED
tests/test_invoice_assign.py::test_assign_match_reassigns_existing_task PASSED
tests/test_invoice_assign.py::test_assign_match_requires_ap_role PASSED
tests/test_invoice_assign.py::test_assignee_can_match_and_see_invoice PASSED
tests/test_invoice_assign.py::test_user_without_task_still_403 PASSED
tests/test_invoice_assign.py::test_assign_match_rejects_matched_invoice PASSED
tests/test_invoice_assign.py::test_response_surfaces_assignee PASSED
tests/test_invoice_assign.py::test_ap_direct_match_completes_assignee_task PASSED
tests/test_invoice_assign.py::test_delete_completes_open_match_task PASSED
tests/test_invoice_assign.py::test_assignee_keeps_detail_visibility_after_match PASSED
tests/test_invoice_assign.py::test_assignee_sees_match_candidates PASSED
tests/test_invoice_assign.py::test_decline_match_bounces_back_to_assigner PASSED
tests/test_invoice_assign.py::test_match_candidates_carry_already_allocated PASSED
tests/test_invoice_assign.py::test_reassign_notifies_new_assignee_not_previous PASSED

====================== 14 passed, 74 warnings in 53.22s =======================
```

### 3. Both files in one process - 28/28 PASSED (was 8 failed / 18 passed)

```
cd c:/Project/uniops-shared-mailbox/epms-api && ... -m pytest tests/test_invoice_assign.py tests/test_notification_dispatch.py -v
```

```
tests/test_invoice_assign.py::test_reassign_notifies_new_assignee_not_previous PASSED [ 50%]
tests/test_notification_dispatch.py::test_default_channel_none_suppresses_all_email PASSED [ 53%]
tests/test_notification_dispatch.py::test_default_channel_email_still_sends PASSED [ 57%]
tests/test_notification_dispatch.py::test_role_display_name_builtin_custom_and_fallback PASSED [ 60%]
tests/test_notification_dispatch.py::test_default_notification_settings_has_shared_mailbox_map PASSED [ 64%]
tests/test_notification_dispatch.py::test_shared_mailbox_replaces_per_member_email PASSED [ 67%]
tests/test_notification_dispatch.py::test_shared_mailbox_sends_even_when_role_pool_is_empty PASSED [ 71%]
tests/test_notification_dispatch.py::test_without_shared_mailbox_every_member_is_emailed PASSED [ 75%]
tests/test_notification_dispatch.py::test_named_assignee_ignores_shared_mailbox PASSED [ 78%]
tests/test_notification_dispatch.py::test_default_channel_none_beats_shared_mailbox PASSED [ 82%]
tests/test_notification_dispatch.py::test_teams_only_channel_sends_nothing_for_shared_mailbox PASSED [ 85%]
tests/test_notification_dispatch.py::test_shared_mailbox_skips_teams PASSED [ 89%]
tests/test_notification_dispatch.py::test_shared_mailbox_body_greets_the_team PASSED [ 92%]
tests/test_notification_dispatch.py::test_named_assignee_body_renders_placeholders_without_template PASSED [ 96%]
tests/test_notification_dispatch.py::test_shared_mailbox_delivery_is_logged_without_user PASSED [100%]

====================== 28 passed, 74 warnings in 55.59s =======================
```

The combined run is genuinely clean now - fixing the *real* root cause
(duplicate config rows) is what did it, not the capture-list filtering alone.
(The throwaway `tests/test_zz_tmp_config_rows.py` used for the row count was
deleted after the measurement; it is not part of the commit.)

## Left unresolved

- `crud/config.py::get_or_create`'s unordered `SELECT ... LIMIT 1` on a table
  with no singleton constraint is a real production weakness. Not fixed here
  (needs a migration; out of scope). Flagged above.
- `test_invoice_assign.py` still creates duplicate `company_config` rows; the
  test-side workaround above tolerates them rather than preventing them.
