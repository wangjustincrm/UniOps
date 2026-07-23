# Role Shared Mailbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route EPMS notifications for role-addressed tasks (no named assignee) to a single configurable shared mailbox per role, so the 4–5 AP Clerks stop receiving duplicate copies of every AP notification.

**Architecture:** One new key `role_shared_mailboxes` inside the existing `company_config.notification_settings` JSONB column drives a new branch in the EPMS notification dispatcher: when a task has no `assigned_user_id` and its `assigned_role` has a shared mailbox configured, exactly one email is sent to that address and the per-user fan-out (email and Teams) is skipped. Tasks assigned to a named person are untouched. Admins edit the mapping in EPMS Admin → Notifications.

**Tech Stack:** FastAPI + SQLAlchemy 2 async (epms-api), pytest-asyncio, React 18 + TypeScript 5.9.3 + Tailwind (epms frontend).

**Spec:** `docs/superpowers/specs/2026-07-22-role-shared-mailbox-design.md`

## Global Constraints

- Work happens in the worktree `c:/Project/uniops-shared-mailbox` on branch `feature/role-shared-mailbox`. Never commit to `main`, never touch another worktree.
- **No Alembic migration.** `notification_settings` is a free-form JSONB dict and `notification_logs.user_id` is already nullable. If you think you need a migration, you have misread the design.
- All user-facing frontend strings are English. Code comments may be Chinese.
- EPMS frontend is **TypeScript 5.9.3** — typecheck with `npx tsc -p tsconfig.app.json --noEmit` and **never** pass `--ignoreDeprecations 6.0` (that flag is for Portal, which is TS 6.0.3). The baseline is 69 pre-existing errors; your changes must add zero new ones.
- epms-api tests must run against the local test database, never production. Set `POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms_test POSTGRES_USER=<from container> POSTGRES_PASSWORD=<from container>` before pytest; read the credentials with `docker exec uniops_postgres printenv POSTGRES_USER POSTGRES_PASSWORD`. The bare host `.env` points at production `10.10.50.20`.
- Only one epms pytest suite may run at a time — concurrent runs `drop_all` each other's tables and produce fake `UndefinedTable` failures.
- Commit after each task with the exact message given in the task.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `epms-api/app/crud/config.py` | Company-config defaults and role metadata | Add `role_shared_mailboxes: {}` default; add `role_display_name()` helper |
| `epms-api/app/services/notification.py` | Notification dispatch | Add `_shared_mailbox_for()`; add shared-mailbox branch in `_dispatch`; widen `_send_with_retry` to log a user-less delivery |
| `epms-api/tests/test_notification_dispatch.py` | Dispatcher regression tests | Add shared-mailbox cases |
| `epms/src/services/config.ts` | Frontend config types | `NotificationSettings.role_shared_mailboxes?: Record<string, string>` |
| `epms/src/pages/admin/AdminPanel.tsx` | Admin UI | New "Role Shared Mailboxes" block in `NotificationSettingsSection` + save-time validation |

---

### Task 1: Config default and role display name

**Files:**
- Modify: `epms-api/app/crud/config.py:100-104` (defaults) and after `:525` (`_BUILTIN_ROLE_NAMES`)
- Test: `epms-api/tests/test_notification_dispatch.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `app.crud.config.role_display_name(cfg: CompanyConfig, role_code: str | None) -> str` — returns the built-in label (`"ap_clerk"` → `"AP Clerk"`), else the active custom-role name, else the role key with underscores replaced by spaces and title-cased; returns `"Team"` when `role_code` is falsy. Also `_DEFAULT_NOTIFICATION_SETTINGS["role_shared_mailboxes"] == {}`.

- [ ] **Step 1: Write the failing test**

Append to `epms-api/tests/test_notification_dispatch.py`:

```python
# ── Role shared mailbox ──────────────────────────────────────────────────────

from app.crud.config import role_display_name  # noqa: E402


async def test_role_display_name_builtin_custom_and_fallback():
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        cfg.custom_roles = [{"code": "ap_lead", "name": "AP Lead", "is_active": True}]
        await db.commit()

        assert role_display_name(cfg, "ap_clerk") == "AP Clerk"
        assert role_display_name(cfg, "ap_lead") == "AP Lead"
        assert role_display_name(cfg, "some_new_role") == "Some New Role"
        assert role_display_name(cfg, None) == "Team"


async def test_default_notification_settings_has_shared_mailbox_map():
    from app.crud.config import _DEFAULT_NOTIFICATION_SETTINGS

    assert _DEFAULT_NOTIFICATION_SETTINGS["role_shared_mailboxes"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notification_dispatch.py -v -k "role_display_name or shared_mailbox_map"`
Expected: FAIL — `ImportError: cannot import name 'role_display_name'`

- [ ] **Step 3: Write minimal implementation**

In `epms-api/app/crud/config.py`, extend the defaults (currently lines 100-104):

```python
_DEFAULT_NOTIFICATION_SETTINGS = {
    "default_channel": "email_only",   # email_only | teams_only | both | none
    "teams_webhook_url": None,
    "followup_time": "08:00",
    # 角色 → 共享邮箱。配了地址的角色,其“角色池”任务只发这一个邮箱,
    # 不再逐个通知该角色成员。空 = 维持逐人发送。
    "role_shared_mailboxes": {},
}
```

Add below `_BUILTIN_ROLE_NAMES` (after line 525):

```python
def role_display_name(cfg: CompanyConfig, role_code: str | None) -> str:
    """Human-readable name for a role code (built-in, custom, or unknown)."""
    if not role_code:
        return "Team"
    if role_code in _BUILTIN_ROLE_NAMES:
        return _BUILTIN_ROLE_NAMES[role_code]
    for cr in (cfg.custom_roles or []):
        if cr.get("code") == role_code:
            return cr.get("name") or role_code
    return role_code.replace("_", " ").title()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notification_dispatch.py -v`
Expected: PASS — the two new tests plus the two pre-existing `default_channel` tests.

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/crud/config.py epms-api/tests/test_notification_dispatch.py
git commit -m "feat(epms): add role_shared_mailboxes config default and role_display_name helper"
```

---

### Task 2: Dispatch role-pool notifications to the shared mailbox

**Files:**
- Modify: `epms-api/app/services/notification.py:130-241` (`_dispatch`) and `:244-288` (`_send_with_retry`)
- Test: `epms-api/tests/test_notification_dispatch.py`

**Interfaces:**
- Consumes: `app.crud.config.role_display_name` and the `role_shared_mailboxes` default from Task 1.
- Produces: `_shared_mailbox_for(notif_settings: dict, role: str | None) -> str | None` (module-private) and a widened `_send_with_retry(channel, task, user: User | None, template_key, db, *, send_fn, max_retries, recipient_email: str | None = None)`.

- [ ] **Step 1: Write the failing tests**

Append to `epms-api/tests/test_notification_dispatch.py`:

```python
from sqlalchemy import select as sa_select  # noqa: E402

from app.models.notification_log import NotificationLog  # noqa: E402

SHARED_MAILBOX = "ap@canadaroyalmilk.com"


async def _make_user_with_role(db, role: str):
    user = await user_crud.create(db, RegisterRequest(
        email=f"notif-{uuid.uuid4().hex[:8]}@example.com",
        password="TestPass1!",
        full_name="AP Person",
        role=role,
    ))
    await db.commit()
    return user


async def _set_notif_settings(db, **kv):
    cfg = await get_config(db)
    cfg.notification_settings = {**(cfg.notification_settings or {}), **kv}
    await db.commit()


def _make_pool_task(role: str = "ap_clerk") -> Task:
    """Role-addressed task: no assigned_user_id."""
    return Task(
        type="create_pa",
        document_type="pa",
        document_id=uuid.uuid4(),
        document_number="PA-TEST-1",
        assigned_role=role,
        title="Process Payment: PA-TEST-1",
        description="Hi {recipient_name}, PA-TEST-1 needs processing.",
    )


@pytest.fixture
def captured_teams(monkeypatch):
    sent: list[tuple] = []

    async def _fake_send_teams_card(webhook, title, body, **kwargs):
        sent.append((webhook, title))

    monkeypatch.setattr("app.services.teams.send_teams_card", _fake_send_teams_card)
    return sent


async def test_shared_mailbox_replaces_per_member_email(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        for _ in range(3):
            await _make_user_with_role(db, "ap_clerk")
        await notification.dispatch_task_notification(_make_pool_task(), db)

    assert len(captured_emails) == 1, "role-pool task must produce exactly one email"
    assert captured_emails[0][0] == SHARED_MAILBOX


async def test_shared_mailbox_sends_even_when_role_pool_is_empty(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"vendor_manager": SHARED_MAILBOX},
        )
        await notification.dispatch_task_notification(_make_pool_task("vendor_manager"), db)

    assert len(captured_emails) == 1
    assert captured_emails[0][0] == SHARED_MAILBOX


async def test_without_shared_mailbox_every_member_is_emailed(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only", role_shared_mailboxes={})
        users = [await _make_user_with_role(db, "ap_clerk") for _ in range(3)]
        await notification.dispatch_task_notification(_make_pool_task(), db)

    sent_to = {to for to, _ in captured_emails}
    # Other tests may have left ap_clerk users behind, so assert containment,
    # not an exact count: every member is mailed and nothing goes to the mailbox.
    assert {u.email for u in users} <= sent_to
    assert SHARED_MAILBOX not in sent_to


async def test_named_assignee_ignores_shared_mailbox(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        user = await _make_user_with_role(db, "ap_clerk")
        task = _make_pool_task()
        task.assigned_user_id = user.id
        await notification.dispatch_task_notification(task, db)

    assert len(captured_emails) == 1
    assert captured_emails[0][0] == user.email


async def test_default_channel_none_beats_shared_mailbox(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="none",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        await _make_user_with_role(db, "ap_clerk")
        await notification.dispatch_task_notification(_make_pool_task(), db)

    assert captured_emails == []


async def test_shared_mailbox_skips_teams(captured_emails, captured_teams):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="both",
            teams_webhook_url="https://example.com/webhook",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        user = await _make_user_with_role(db, "ap_clerk")
        user.teams_account = "ap.person@example.com"
        await db.commit()
        await notification.dispatch_task_notification(_make_pool_task(), db)

    assert len(captured_emails) == 1
    assert captured_teams == [], "shared-mailbox delivery must not also post a Teams card"


async def test_shared_mailbox_body_greets_the_team(monkeypatch):
    bodies: list[str] = []

    async def _capture(to, subject, html, **kwargs):
        bodies.append(html)

    monkeypatch.setattr("app.services.email.send_email", _capture)

    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        await notification.dispatch_task_notification(_make_pool_task(), db)

    assert len(bodies) == 1
    assert "AP Clerk Team" in bodies[0]


async def test_shared_mailbox_delivery_is_logged_without_user(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        task = _make_pool_task()
        db.add(task)
        await db.flush()
        await notification.dispatch_task_notification(task, db)
        await db.commit()

        logs = (await db.execute(
            sa_select(NotificationLog).where(NotificationLog.task_id == task.id)
        )).scalars().all()

    assert len(logs) == 1
    assert logs[0].user_id is None
    assert logs[0].recipient_email == SHARED_MAILBOX
    assert logs[0].status == "ok"
```

Note on `test_shared_mailbox_body_greets_the_team`: the seeded `create_pa_reminder` template may or may not contain `{recipient_name}`. If the assertion fails because the template body has no such placeholder, do **not** weaken the assertion — instead set the template explicitly inside the test before dispatching:

```python
        cfg = await get_config(db)
        cfg.email_templates = {
            **(cfg.email_templates or {}),
            "create_pa_reminder": {"subject": "PA {pa_number}", "body": "Hi {recipient_name}, please process it."},
        }
        await db.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_notification_dispatch.py -v -k shared_mailbox`
Expected: FAIL — the shared-mailbox tests each send 0 or N emails instead of 1 (no branch exists yet); `test_shared_mailbox_sends_even_when_role_pool_is_empty` fails with 0 emails.

- [ ] **Step 3: Write the implementation**

In `epms-api/app/services/notification.py`, add the resolver next to `_smtp_kwargs`:

```python
def _shared_mailbox_for(notif_settings: dict, role: str | None) -> str | None:
    """共享邮箱地址(角色池任务专用);未配置/空串返回 None。"""
    if not role:
        return None
    mapping = notif_settings.get("role_shared_mailboxes") or {}
    if not isinstance(mapping, dict):
        return None
    addr = mapping.get(role)
    if isinstance(addr, str) and addr.strip():
        return addr.strip()
    return None
```

In `_dispatch`, import the helper alongside the other lazy imports at the top of the function:

```python
    from app.crud.config import get_or_create as get_config, role_display_name
```

Replace the recipient-resolution block (currently lines 166-203, from `# ── Resolve recipients ──` through the end of `base_vars`) with:

```python
    # ── Resolve recipients ──────────────────────────────────────────────────
    # 角色池任务(无具体指派人)若为该角色配了共享邮箱,则整封只发共享邮箱:
    # 不再逐人发邮件、不发 Teams、也不看个人 notification_channel。
    shared_mailbox = (
        _shared_mailbox_for(notif_settings, task.assigned_role)
        if task.assigned_user_id is None
        else None
    )

    recipients: list[User] = []
    if shared_mailbox is None:
        if task.assigned_user_id:
            user = await db.get(User, task.assigned_user_id)
            if user and user.is_active:
                recipients.append(user)
        else:
            # All active users with matching role
            result = await db.execute(
                select(User).where(User.role == task.assigned_role, User.is_active.is_(True))
            )
            recipients = list(result.scalars().all())

        if not recipients:
            logger.debug("No recipients for task %s (role=%s)", task.id, task.assigned_role)
            return

    # ── Common template variables ───────────────────────────────────────────
    # Deep-link resolves per module from the document_type (this notifier serves
    # every module, not just EPMS — see _task_link).
    link = _task_link(task.document_type, task.document_id)

    base_vars: dict[str, Any] = {
        "company_name": cfg.name,
        "document_type": task.document_type.upper(),
        "document_number": task.document_number,
        "link": link,
        # Convenience aliases so templates can use natural names
        "pr_number": task.document_number,
        "po_number": task.document_number,
        "pa_number": task.document_number,
        "gr_number": task.document_number,
        "vendor": task.vendor or "",
        "vendor_name": task.vendor or "",
        "amount": str(task.amount) if task.amount else "",
        "task_title": task.title,
        **extra_vars,
    }

    if shared_mailbox:
        team_vars = {
            **base_vars,
            "recipient_name": f"{role_display_name(cfg, task.assigned_role)} Team",
        }
        if tpl:
            subject = _render(tpl.get("subject", task.title), team_vars)
            html_body = _render(tpl.get("body", task.description or ""), team_vars)
        else:
            subject = task.title
            html_body = _render(task.description or task.title, team_vars)

        html = _build_email_html(html_body)
        await _send_with_retry(
            "email", task, None, tpl_key, db,
            send_fn=lambda: send_email(shared_mailbox, subject, html, **_smtp_kwargs(cfg)),
            max_retries=max_retries,
            recipient_email=shared_mailbox,
        )
        return
```

The existing `for user in recipients:` loop below stays exactly as it is.

Then widen `_send_with_retry` — change its signature and the two `NotificationLog(...)` constructions:

```python
async def _send_with_retry(
    channel: str,
    task: Task,
    user: User | None,
    template_key: str,
    db: AsyncSession,
    *,
    send_fn,
    max_retries: int,
    recipient_email: str | None = None,
) -> None:
    """Try send_fn up to max_retries times with exponential backoff. Log each attempt.

    ``user`` is None for shared-mailbox deliveries — the log row then carries only
    the recipient address (notification_logs.user_id is nullable).
    """
    to_addr = recipient_email if recipient_email is not None else (user.email if user else None)
    user_id = user.id if user else None
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            await send_fn()
            db.add(NotificationLog(
                task_id=task.id,
                user_id=user_id,
                recipient_email=to_addr,
                channel=channel,
                template_key=template_key,
                status="ok",
                attempt=attempt,
            ))
            await db.flush()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning("Notification attempt %d/%d failed (channel=%s, recipient=%s): %s",
                           attempt, max_retries, channel, to_addr, exc)
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)   # 2s, 4s backoff

    # All attempts failed — log failure
    db.add(NotificationLog(
        task_id=task.id,
        user_id=user_id,
        recipient_email=to_addr,
        channel=channel,
        template_key=template_key,
        status="failed",
        error_message=last_error,
        attempt=max_retries,
    ))
    await db.flush()
```

- [ ] **Step 4: Run the whole dispatcher suite**

Run: `pytest tests/test_notification_dispatch.py -v`
Expected: PASS — all 12 tests (2 pre-existing + 2 from Task 1 + 8 new).

- [ ] **Step 5: Run the notification-adjacent suites for regressions**

Run: `pytest tests/test_invoice_assign.py tests/test_notification_dispatch.py -v`
Expected: same pass/fail set as before your change — `test_invoice_assign.py` must not gain new failures (it exercises the named-assignee path through `_send_with_retry`).

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/services/notification.py epms-api/tests/test_notification_dispatch.py
git commit -m "feat(epms): route role-pool notifications to a per-role shared mailbox"
```

---

### Task 3: Admin UI for role shared mailboxes

**Files:**
- Modify: `epms/src/services/config.ts:162-167` (`NotificationSettings`)
- Modify: `epms/src/pages/admin/AdminPanel.tsx:1821-1892` (`DEFAULT_NOTIF_SETTINGS`, `NotificationSettingsSection`)

**Interfaces:**
- Consumes: the backend key `notification_settings.role_shared_mailboxes` from Tasks 1–2.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Extend the type**

In `epms/src/services/config.ts`, `NotificationSettings` becomes:

```ts
export interface NotificationSettings {
  default_channel: 'email_only' | 'teams_only' | 'both' | 'none'
  teams_webhook_url: string | null
  followup_time: string  // "HH:MM" UTC
  system_url?: string
  // Role code → shared mailbox address. A role listed here receives ONE email
  // for role-addressed tasks instead of one per member. Absent/empty = per-member.
  role_shared_mailboxes?: Record<string, string>
}
```

- [ ] **Step 2: Seed the default in the panel**

In `epms/src/pages/admin/AdminPanel.tsx`, extend `DEFAULT_NOTIF_SETTINGS` (line 1821):

```tsx
const DEFAULT_NOTIF_SETTINGS: NotificationSettings = {
  default_channel: 'email_only',
  teams_webhook_url: null,
  followup_time: '08:00',
  role_shared_mailboxes: {},
}
```

- [ ] **Step 3: Add validation state and the save guard**

Inside `NotificationSettingsSection`, add the state next to the other `useState` calls (after line 1835):

```tsx
  const [mailboxError, setMailboxError] = useState<string | null>(null)
```

Add the role list (place it just after the `useEffect`, and add `useMemo` to the existing `react` import at the top of the file if it is not already imported):

```tsx
  const roleOptions = useMemo<[string, string][]>(() => [
    ...(Object.entries(ROLE_LABELS) as [string, string][]),
    ...((config?.custom_roles ?? [])
      .filter((r) => r.is_active !== false)
      .map((r) => [r.code, r.name] as [string, string])),
  ], [config?.custom_roles])
```

Replace `handleSave` (lines 1842-1845) with:

```tsx
  const handleSave = () => {
    const invalid = Object.entries(settings.role_shared_mailboxes ?? {})
      .find(([, addr]) => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(addr))
    if (invalid) {
      setMailboxError(`Invalid shared mailbox address for ${ROLE_LABELS[invalid[0] as UserRole] ?? invalid[0]}: "${invalid[1]}"`)
      return
    }
    setMailboxError(null)
    updateConfig.mutate({ notification_settings: settings, email_templates: templates })
    setSaved(true); setTimeout(() => setSaved(false), 2500)
  }
```

- [ ] **Step 4: Add the UI block**

Insert this section immediately after the "Teams webhook" `<section>` (i.e. after line 1892) and before "Follow-up time":

```tsx
      {/* Role shared mailboxes */}
      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Role Shared Mailboxes</h3>
        <p className="text-sm text-neutral-500">
          Tasks addressed to a role rather than a person are emailed to this single mailbox instead of
          every holder of the role. Leave empty to notify each member individually. Tasks assigned to a
          named person always go to that person.
        </p>
        <div className="flex flex-col gap-2">
          {roleOptions.map(([code, label]) => (
            <div key={code} className="flex items-center gap-3">
              <span className="w-52 shrink-0 text-sm text-neutral-700">{label}</span>
              <input
                className={cn(inCls, 'flex-1')}
                type="email"
                placeholder="Notify each member individually"
                value={settings.role_shared_mailboxes?.[code] ?? ''}
                onChange={(e) => setSettings((p) => {
                  const next = { ...(p.role_shared_mailboxes ?? {}) }
                  const value = e.target.value.trim()
                  if (value) next[code] = value
                  else delete next[code]
                  return { ...p, role_shared_mailboxes: next }
                })}
              />
            </div>
          ))}
        </div>
        {mailboxError && <p className="text-xs text-danger-600">{mailboxError}</p>}
      </section>
```

- [ ] **Step 5: Typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit`
Expected: the same 69 pre-existing errors as before the change, none of them in `AdminPanel.tsx` or `config.ts`. Capture the count before and after — an increase means you introduced an error. (Do **not** add `--ignoreDeprecations 6.0`; that is Portal-only.)

- [ ] **Step 6: Commit**

```bash
git add epms/src/services/config.ts epms/src/pages/admin/AdminPanel.tsx
git commit -m "feat(epms): admin UI for per-role shared notification mailboxes"
```

---

### Task 4: Manual verification

**Files:** none (verification only)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: nothing.

- [ ] **Step 1: Rebuild the dev stack for the changed services**

The dev containers mount the **main checkout**, not this worktree — do not `docker cp` into them (it writes through and pollutes other branches). Instead run the verification against a stack built from this worktree, or ask the user to point the dev stack at `feature/role-shared-mailbox` first. Do not skip this decision silently.

- [ ] **Step 2: Configure the mailbox**

In EPMS Admin → Notifications, set **AP Clerk** = `ap@canadaroyalmilk.com`, save, reload the page, and confirm the value persisted (this proves the JSONB round-trip through `ConfigUpdate`).

- [ ] **Step 3: Trigger a role-pool notification**

Fully approve a test PA so `_create_process_pa_task` fires (`epms-api/app/crud/pa.py:368-380`, `assigned_role="ap_clerk"`, no assignee). Confirm in `notification_logs` that exactly one row was written for that task with `user_id IS NULL` and `recipient_email = 'ap@canadaroyalmilk.com'`:

```sql
SELECT recipient_email, user_id, status FROM notification_logs
WHERE task_id = '<task uuid>';
```

- [ ] **Step 4: Confirm the named-assignee path is untouched**

Assign an invoice match to a specific AP Clerk and confirm the resulting `review_match` / `match_invoice` notification still lands on that person's address, not the shared mailbox.

- [ ] **Step 5: Report**

Summarise for the user: what was verified, what was not (e.g. real SMTP delivery to the shared mailbox if the mailbox does not exist yet), and the branch/commit list ready for the release merge.

---

## Release notes for the merge session

- Branch `feature/role-shared-mailbox` (worktree `c:/Project/uniops-shared-mailbox`), based on `main` = `9aa3449`.
- No migration. Services rebuilt: `epms-api` and the `epms` frontend — but per the standard release workflow, **all 15 images must be built and pushed at the same sha**.
- Activation is config-only after deploy: Admin → Notifications → Role Shared Mailboxes → AP Clerk = `ap@canadaroyalmilk.com`. Rollback is clearing the field.
- Before releasing, verify the **current production TAG** rather than assuming it equals `origin/main`.
