# Task 8 report: sending and the send log

## Status: DONE

Commit: `d85efd8` — "feat(finance): send remittance advice with per-payee isolation and resend"
(branch `feature/batch-payment-remittance`, worktree `c:/Project/uniops-remittance`)

Note: the file at this path previously contained an unrelated report (a routing-parity
verifier task from a different feature/repo). That content has been replaced below with
the actual Task 8 (remittance send + log) report, per the task instructions for this file.

## Files changed

- **Created** `finance-api/app/crud/remittance_send.py`
  - `_upsert(db, *, scope_kind, scope_id, group, status, error, actor_id)` — looks up an
    existing `RemittanceNotification` row by the unique key
    `(scope_kind, scope_id, recipient_kind, party_id)`; inserts on first send
    (`attempts=1`), updates + increments `attempts` on resend. `sent_at` is only set/refreshed
    when `status == SENT`; a failed resend leaves the prior `sent_at` alone.
  - `send_groups(db, *, scope_kind, scope_id, groups, reference, payment_method,
    company_name, sender, actor_id) -> list[dict]` — iterates `PayeeGroup`s from
    Task 6, renders via Task 7's `render()`, sends via Task 5's `send_email()` using
    Task 5's `RemittanceSettings` for connection info and `cc_email`. Per-payee results are
    `{"recipient_kind", "party_id", "party_name", "status", "error"}` with
    `status in {"sent", "failed", "skipped"}`.
  - Blocked/emailless groups (`g.block_reasons` non-empty or `not g.email`) are refused
    before any network call or DB write — `status="skipped"`, no row written, `send_email`
    never invoked. This is the server-side enforcement point described in the design intent:
    a client posting a blocked payee cannot get `sent`, and never triggers an actual email.
  - Each payee's `send_email` call is wrapped in `try/except Exception` — a failure is
    logged, recorded as a `FAILED` row via `_upsert`, appended to results as `"failed"`, and
    the loop continues to the next payee (does not re-raise). This isolates one payee's SMTP
    error from the rest of the batch.
  - No transaction handling of its own — matches the design intent that sending is never
    inside the payment transaction; the caller is expected to have already committed the
    payment and to call this afterward. `_upsert` only `flush()`es, leaving commit to the
    caller (a later task/endpoint, out of scope here).

- **Modified** `finance-api/tests/test_remittance.py` (appended only, existing content
  untouched)
  - Added `from app.crud import remittance_send as rsend` and the Task 8 test block from
    the brief verbatim: `_sender()`, `_send()` helper, plus
    `test_send_writes_log_and_uses_cc`, `test_resend_upserts_and_increments_attempts`,
    `test_one_failure_does_not_stop_the_others`, `test_blocked_group_is_skipped_not_sent`.
    Reused the existing `_group()` builder from the Task 7 section rather than redefining
    it, per instructions.
  - Added one extra test not in the brief:
    `test_send_email_non_ascii_subject_serializes_cleanly` (placed next to the existing
    Task 5 `send_email` TLS tests). See the non-ASCII subject section below for why and what
    it proves.

## Test commands and output

Step 2 (verify failing state before writing the module):

```
$env:TEST_PG_PASSWORD="7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"
cd C:\Project\uniops-remittance\finance-api
python -m pytest tests/test_remittance.py -k "send_writes or resend or one_failure or blocked_group" -v
```
Result: collection error —
`ImportError: cannot import name 'remittance_send' from 'app.crud' (unknown location)`
(module didn't exist yet, as expected; the brief predicted `ModuleNotFoundError`, the
actual error was the equivalent `ImportError` from the `from app.crud import
remittance_send as rsend` form of the import — same root cause, module absent).

After writing `remittance_send.py`, targeted re-run:

```
python -m pytest tests/test_remittance.py -k "send_writes or resend or one_failure or blocked_group" -v
```
Result: `4 passed, 21 deselected in 25.79s` — all four new tests green on first try, no
implementation changes needed beyond what's below.

Full file:

```
python -m pytest tests/test_remittance.py -v
```
Result (after also adding the non-ASCII subject test): `26 passed in 106.87s` — every test
in the file (Tasks 1, 4, 5, 6, 7, 8 combined) passes.

Single pytest session used throughout; no concurrent runs against the shared test DB.

## Non-ASCII subject check (the one concrete thing to verify)

Verified by hand first, then captured as a permanent regression test.

Manual check (`python3 -c ...` against the real `email.mime.multipart.MIMEMultipart` +
`email.mime.text.MIMEText`, no aiosmtplib involved):

```python
msg = MIMEMultipart('mixed')
msg['Subject'] = 'Remittance Advice — Canada Royal Milk — BP-20260722-0001'
...
b = msg.as_bytes()
```

Result: `msg.as_bytes()` does **not** raise, and the em dash is correctly RFC 2047-encoded:

```
Subject: =?utf-8?q?Remittance_Advice_=E2=80=94_Canada_Royal_Milk_=E2=80=94_B?=
 =?utf-8?q?P-20260722-0001?=
```

This works because `email.message.Message.__setitem__` stores the header as a plain str,
and `Generator`/`BytesGenerator` under the default `compat32` policy fold headers through
`email.header.Header`, which auto-detects non-ASCII content and switches to `utf-8`
Q-encoding — no explicit charset/policy needed on `send_email`'s part. `email.py` did not
need to change.

Added `test_send_email_non_ascii_subject_serializes_cleanly` in
`finance-api/tests/test_remittance.py` to make this a permanent, real (non-mocked-away)
proof rather than something only checked by hand:

- Calls the real `send_email()` with a subject containing an em dash, mocking only
  `aiosmtplib.send` (the network boundary) exactly like the other `email.py` tests already
  in the file.
- Retrieves the actual `MIMEMultipart` object passed to the mock (`m.await_args.args[0]`)
  and calls `.as_bytes()` on it — this is the real flattening step a mocked
  `send_email(...)` test would never exercise, since a mock of `send_email` itself (as used
  by `test_send_writes_log_and_uses_cc` etc.) never touches the `MIMEMultipart` internals.
- Asserts the raw em-dash UTF-8 bytes (`\xe2\x80\x94`) are **not** present verbatim in the
  serialized output (i.e. it went through header encoding, not a raw pass-through that
  would produce an invalid 8-bit header).
- Re-parses the serialized bytes with `email.message_from_bytes` and decodes the `Subject`
  header back with `email.header.decode_header` / `make_header`, asserting round-trip
  equality with the original subject string — proving a real mail client/MTA would recover
  the exact original text, not a mangled one.

Conclusion: **no fix was needed in `email.py`.** The subject serializes cleanly under the
default compat32 policy as-is; the concern in the brief was valid to check but did not
materialize as a bug in this codebase's Python version (3.12).

## Discrepancies vs. the brief

- The brief's Step 2 said to expect `ModuleNotFoundError`; the actual failure was
  `ImportError` (same underlying cause — `app.crud.remittance_send` doesn't exist — just a
  different exception class for a `from X import Y` statement vs. a bare `import X`).
  Cosmetic only, no action needed.
- The brief's `send_groups`/`_upsert` code in Step 3 matched the real signatures of
  `PayeeGroup`, `RemittanceSettings`, `render()`, `send_email()`, and
  `RemittanceNotification` exactly — no changes were needed against what's actually in
  `app/crud/remittance.py`, `app/services/remittance_template.py`,
  `app/services/remittance_config.py`, `app/services/email.py`, or `app/models/remittance.py`.
  The module was implemented essentially verbatim from the brief.
- No other contradictions found.

## Concerns

- `git status` showed `.superpowers/sdd/task-4-report.md` and
  `.superpowers/sdd/task-7-report.md` as modified in the working tree before I started, and
  `.superpowers/sdd/task-8-report.md` itself contained a stale, unrelated report (see note
  at top). These pre-existing modifications/content are unrelated to Task 8 and were
  deliberately excluded from the commit — only `finance-api/app/crud/remittance_send.py`
  and `finance-api/tests/test_remittance.py` were staged and committed.
- `send_groups`/`_upsert` only `flush()`, never `commit()`. This is correct per the design
  intent (sending happens after the caller already committed the payment; the endpoint
  layer that wires this in is responsible for committing the notification-log writes
  afterward) but is worth double-checking when that endpoint is built, so a send that
  updates the log doesn't get silently rolled back by an uncommitted session.

## Fix: durability and upsert race

Follow-up to the review findings above — the "worth double-checking" concern in the last
bullet turned out to be exactly right and is fixed here. Commit: see the commit that
introduces this section (`fix(finance): make each remittance send durable and upsert
race-safe`, branch `feature/batch-payment-remittance`).

### The problem

Sending an email is an irreversible side effect, but the record of it lived only in this
session's uncommitted transaction. If a later payee (or the caller) raised before that
transaction committed, the email had already gone out with no durable log row — an operator
seeing "not sent" would press Send again and double-send the vendor. Two smaller issues fed
the same root cause: `render()` sat outside the per-payee `try`, so a template error on payee
N aborted the loop for every payee after N (never reaching the log write for them at all);
and `_upsert`'s SELECT-then-insert had a TOCTOU window where two overlapping sends for the
same payee could both miss the SELECT and both attempt an INSERT, the second raising
`IntegrityError` after its email had already gone out.

### Fix 1 — widened isolation boundary + per-payee commit

Moved `render(...)` inside the same per-payee `try/except Exception` as `send_email(...)`,
so a render failure on one payee is caught, logged against that payee, and does not stop the
rest of the loop (previously it would have propagated straight out of `send_groups`).

Added `await db.commit()` immediately after each payee's `_upsert` — both on the success path
and the failure path — instead of leaving the commit to the caller or to the end of the loop.
This is documented as safe *by design* in the module docstring and inline above each commit
call: remittance sending always runs after the payment transaction has already been committed
by the caller, so there is no unrelated payment work sitting uncommitted in this session that
a per-payee commit could flush early. The comment explicitly warns a future reader not to
"optimize" this back into a single trailing commit.

### Fix 2 — atomic upsert (chosen strategy: `ON CONFLICT DO UPDATE`)

Chose the atomic PostgreSQL upsert over the "catch IntegrityError + savepoint + re-select"
alternative, because:
- It fits the existing code cleanly — `_upsert` already had one write path to replace, not
  two (a try path and a catch path) to maintain in parallel.
- It's a single round trip to the database and pushes the conflict resolution down to the row
  lock PostgreSQL already takes for the unique index, rather than depending on session-level
  exception handling and a manual savepoint dance to get the same guarantee.
- `attempts` increments correctly straight off the existing row
  (`RemittanceNotification.attempts + 1` in the `SET` clause) without a second read.

Implementation: `sqlalchemy.dialects.postgresql.insert` building an INSERT with all columns
(as before), then `.on_conflict_do_update(constraint="uq_remittance_scope_party", set_=...)`
— matching the real constraint name declared on the model. The `SET` clause takes
`party_name`/`email`/`payment_record_ids`/`amount`/`currency`/`status`/`error` from
`excluded` (the row that would have been inserted) and `attempts` from
`RemittanceNotification.attempts + 1` (the row already on disk) — reproducing the old
SELECT-then-update semantics exactly, just atomically. `sent_at` is only included in the SET
clause when `status == SENT`, matching the old code's "a failed resend leaves the prior
sent_at alone" behavior.

### Fix 3 — dropped the duplicate error log

Removed the `logger.error("Remittance send failed for %s: %s", ...)` call (and the now-unused
`logging` import/`logger` object) from the `except` block in `send_groups`. `send_email()` in
`app/services/email.py` already logs and re-raises SMTP failures, so that call was a second
log line for the same event. A `render()` failure (which never reaches `send_email`) isn't
logged a second time either now — its durable record is the `FAILED` row itself, which is the
whole point of this fix.

### Fix 4 — removed the unreachable fallback

Simplified `", ".join(g.block_reasons) or "missing_email"` to `", ".join(g.block_reasons)`.
Confirmed by reading `app/crud/remittance.py`'s `_vendor_groups`/`_employee_groups`: both
always append `BLOCK_MISSING_EMAIL` to `block_reasons` whenever `email` is blank, so
`block_reasons` can never be empty at this point outside a hand-built test fixture. Checked
every test that builds a blocked group by hand (`test_blocked_group_is_skipped_not_sent`) —
it already sets `g.block_reasons = [rem.BLOCK_MISSING_EMAIL]` explicitly and doesn't assert on
the literal `error` string, so no test needed adjusting for this simplification.

### How durability was proved

`test_send_durably_commits_not_just_flushes`: calls `send_groups` for one payee, then calls
`await db_session.rollback()` on the *same* session before reading the row back. A
trailing-commit-only implementation (the pre-fix state — `_upsert` only `flush()`ed, commit
was left to a caller that doesn't exist yet) would still have the insert sitting in an open,
uncommitted transaction at that point; the explicit `rollback()` would discard it and the
subsequent `scalar_one()` would raise `NoResultFound`. Against the fix, the row is already
hard-committed by the time `send_groups` returns, so the later `rollback()` has nothing to
undo and the row reads back fine. Ran this test against the pre-fix module (git stash) to
confirm it does fail there before finalizing — it raised `NoResultFound` as expected, then
passed clean against the fixed code.

`test_render_failure_does_not_stop_the_others`: patches `app.crud.remittance_send.render` to
raise only on its first call, and `send_email` with a plain `AsyncMock` (not asserting a
side_effect on it). Asserts both a `"failed"` and a `"sent"` outcome come back, the mock for
`send_email` was actually awaited once for the second payee's address, and both rows exist in
the log — proving the loop's isolation boundary now covers `render()`, not just `send_email()`.

`test_concurrent_double_send_lands_on_update_not_integrityerror`: inserts a
`RemittanceNotification` row directly for the same
`(scope_kind, scope_id, recipient_kind, party_id)` key (`attempts=1`, `status=SENT`) and
commits it, simulating an in-flight send that already landed. Then calls `send_groups` for
that same payee and asserts it returns `"sent"` (no `IntegrityError` propagates), and the row
in the DB now has `attempts == 2` — the second call updated the existing row instead of
crashing on the unique constraint.

### Fixture note (`db_session`)

`tests/conftest.py`'s `db_session` fixture does not wrap the test in an outer transaction that
gets rolled back at teardown — it hands out a plain `AsyncSession` against a freshly migrated
per-test database (each test calls `_migrate()`, which drops and recreates the schema). So a
real `await db.commit()` inside `send_groups` commits for real against that test database, and
nothing in the fixture needed to change to support per-payee commits or the explicit
`db_session.rollback()` durability check in the new test — the fixture's semantics for every
other test in the file are untouched.

### Final test run

```
cd c:/Project/uniops-remittance/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 python -m pytest tests/test_remittance.py -v
```

Result: **29 passed in 123.79s** — every test in the file passes, including the three new
Fix-2/Fix-1 tests added above the existing Task 8 block.

## Fix: race test honesty and success-path guard

Follow-up to a second review pass on the code above. Three findings, all in
`finance-api/tests/test_remittance.py` and `finance-api/app/crud/remittance_send.py`.

### Finding 1 — the "concurrency" test wasn't concurrent

`test_concurrent_double_send_lands_on_update_not_integrityerror` inserted a colliding row and
**committed it before calling `send_groups`**. That's sequential setup, not a race — the old
select-then-write `_upsert` would just find the already-committed row on its SELECT and take
the update branch cleanly. The test passed unchanged against the pre-fix code and proved
nothing about the race it's named for; it was a duplicate of
`test_resend_upserts_and_increments_attempts` with different setup dressing.

**Route taken: (a) — a real interleaving, achieved.** `tests/conftest.py`'s `db_session`
fixture is a plain `AsyncSession` against a freshly migrated database with no outer
transaction/rollback wrapper (confirmed by re-reading it), and `ASYNC_URL` is a plain
module-level constant — so a second, fully independent `AsyncSession` on its own
`create_async_engine(ASYNC_URL)` connection could be constructed directly inside the test,
without calling `_migrate()` again (which would drop the schema out from under the first
session).

Rewrote the test to:
1. Open `session2` (separate engine, separate connection) and `add()` + `flush()` (not
   commit) a `RemittanceNotification` row for the exact same
   `(scope_kind, scope_id, recipient_kind, party_id)` key — the row now exists only inside
   `session2`'s still-open transaction, invisible to any other session.
2. `asyncio.create_task(_send(db_session, [g], scope_id))` — starts `send_groups` on the
   primary session concurrently.
3. `await asyncio.sleep(0.3)`, then `assert not task.done()` — asserts the task actually
   blocked on the database (on the row's unique-index key lock), not that it merely hasn't
   been scheduled yet. This is the load-bearing assertion that makes the test's concurrency
   claim falsifiable instead of decorative.
4. `await session2.commit()` — releases the lock the other session was holding.
5. `await asyncio.wait_for(task, timeout=10)` — the now-unblocked `send_groups` call
   completes; assert it landed on `"sent"`, one row, `attempts == 2`.

**Proved both ways, as instructed:**

- *Against the fixed code* (`ON CONFLICT DO UPDATE`): ran `-k concurrent` alone —
  `1 passed in 8.46s`. The short, non-timeout-bound runtime is itself evidence the test isn't
  just waiting out a `wait_for` timeout: it blocked on the real lock for a fraction of a
  second and unblocked immediately once `session2.commit()` ran.

- *Against a temporary revert of `_upsert` to select-then-write* (SELECT for the existing
  row; `UPDATE` the ORM object if found, else `db.add()` a new one; no `ON CONFLICT`) — same
  test, same `-k concurrent`:

  ```
  AssertionError: assert ['failed'] == ['sent']
  ...
  ERROR app.crud.remittance_send:remittance_send.py:167 Remittance send SUCCEEDED but the log
  write FAILED for vendor ACME (388e03eb-...) — email was already sent to ap@acme.test:
  (sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class
  'asyncpg.exceptions.UniqueViolationError'>: duplicate key value violates unique constraint
  "uq_remittance_scope_party"
  DETAIL:  Key (scope_kind, scope_id, recipient_kind, party_id)=(batch, 199778a2-...) already
  exists.
  ```

  This is exactly the failure mode the docstring describes: the reverted `_upsert`'s SELECT
  ran before `session2` committed, found nothing, and its subsequent `INSERT` blocked on
  `session2`'s uncommitted row; once `session2` committed, the blocked `INSERT` unblocked and
  raised a real `UniqueViolationError`/`IntegrityError` — the second sender's email had
  already gone out (mocked `send_email` was still called) and the write to record it then
  crashed. It shows up here as `"failed"` rather than an uncaught exception only because this
  same round of fixes also wraps the success-path `_upsert`+`commit()` in a `try/except`
  (Finding 2, below) — that guard is what turned the raw `IntegrityError` into a caught,
  logged, reported failure instead of an unhandled exception aborting the whole test process.
  Either way the test fails against the old code and passes against the new code, which is
  what matters. Restored `_upsert` to the atomic `ON CONFLICT DO UPDATE` form immediately
  after capturing this output; re-ran the full file to confirm 29/29 green again.

Renamed nothing — the original name
(`test_concurrent_double_send_lands_on_update_not_integrityerror`) already accurately
describes what the rewritten test proves, now that it actually proves it. Kept the docstring
but rewrote it to describe the real second-session mechanics and to record how it was
verified both ways.

### Finding 2 — success path was unguarded

The final `_upsert(..., status=SENT, ...)` + `db.commit()` in `send_groups` sat outside any
`try/except`. A transient DB error there (the email already sent) would propagate straight
out of `send_groups`, aborting the loop for every remaining payee with no result recorded for
the one that failed — and, worse, no way for a caller/operator to tell "the email went out but
we lost the record" apart from "the email never went out".

Fix: wrapped that `_upsert` + `commit()` in its own `try/except Exception`. On failure:
- `await db.rollback()` — the session must be usable for the next payee; a failed statement
  in Postgres poisons the current transaction until it's rolled back.
- `logger.error(...)` naming the payee (`recipient_kind`, `party_name`, `party_id`) and the
  vendor's email, explicitly stating the email was **already sent** and only the log write
  failed — worded differently from a plain "failed" so an operator doesn't read this as "the
  send never happened" and press Send again (which would double-send the vendor).
- `results.append({..., "status": "failed", "error": f"email sent but not logged: {exc}"})` —
  the result list still says `"failed"` (no third status value existed in the vocabulary and
  nothing downstream consumes one yet), but the `error` string is explicit about what actually
  failed, matching the log line.
- `continue` — the loop proceeds to the next payee instead of aborting the whole batch.

Not covered by a dedicated new test (none was requested for this finding, and forcing a
`_upsert`/`commit()` failure independent of the render/send_email failures would need fault
injection at the SQL layer); verified by inspection and by confirming the existing
`test_send_writes_log_and_uses_cc` / `test_resend_upserts_and_increments_attempts` /
`test_one_failure_does_not_stop_the_others` still pass unchanged (the success path's happy
case is unaffected — the `try` just wraps what was already there).

Note: the failure-path `_upsert`+`commit()` (the one that records a `render()`/`send_email()`
failure as a `FAILED` row) has the same latent unguarded-write shape, but the review finding
was scoped specifically to "the success path" — left as-is, flagging it here rather than
fixing unrequested scope.

### Finding 3 — render() failures had no log line at all

The prior fix round removed the single shared `logger.error(...)` call from the combined
`render()`/`send_email()` except block, reasoning that `send_email()` (in
`app/services/email.py`) already logs SMTP failures and this was a duplicate. That reasoning
was correct for the `send_email()` case but had a side effect: a `render()` failure never
reaches `send_email()`, so removing that line left `render()` failures with **no** log line
anywhere — only the `FAILED` database row recorded them.

Fix: split the single `try/except` around `render()` + `send_email()` into two separate
`try/except` blocks:
- `render()`'s except block now has its own `logger.error(...)` — naming the payee and
  stating the send was never attempted. This is genuinely new information no other code path
  logs.
- `send_email()`'s except block still has **no** added log call — the comment there notes
  explicitly that `send_email()` already logs and re-raises, so nothing is added on top of it.

This satisfies "does not duplicate what `email.py` already logs" precisely: the new log line
only ever fires on the code path `email.py` never sees (`render()` raising before
`send_email()` is even called), not on the path `email.py` already logs.

Confirmed `test_render_failure_does_not_stop_the_others` (which patches
`app.crud.remittance_send.render` to raise on its first call) still passes against the split
try/except — it doesn't assert on log output, only on `results` and the DB rows, both
unaffected by the split.

### Final test run

```
cd c:/Project/uniops-remittance/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 python -m pytest tests/test_remittance.py -v
```

Result: **29 passed in 124.27s** — every test in the file passes.
