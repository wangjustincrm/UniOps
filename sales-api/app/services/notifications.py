"""Notifications (FR-OB-56; design §8): the rules page (Configuration →
Notifications), the routing every raised problem goes through, the email the
rules ask for, the test notification and the history.

Routing (the tasks service calls `route` for every problem it raises):
- a task is written for an event that needs someone to act (FR-OB-06), and for
  a notify-only event whose rule says TASK or BOTH (or that has no rule); it
  goes to the rule's first user, else to the operator (X2);
- an email goes out when the active rule says EMAIL or BOTH and names someone
  — once per problem: when its task is new, never on the refresh of an open
  one (a problem failing every cycle mails once, FR-OB-06). With no rule no
  email is sent (AC-OB-52). One email per notification, every recipient in To
  (FR-OB-18: ONE email to the warehouse and the owner).
- emails are outbox rows (`notify_email`) sent through the guarded email
  gateway: with SALES_EXTERNAL_EMAIL_ENABLED off they end SUPPRESSED with the
  "would have sent" row (D-b). Plain text only — no user text is put into
  HTML.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.domain.notifications import ACTION, BY_KEY, EVENT_TYPES, GATE_EVENTS
from app.integrations.email import ExternalEmail
from app.integrations.epms_users import find_users, search_users
from app.models.config import NotificationRule
from app.models.records import NotificationRecord, OutboxRecord
from app.platform.authorization import requires
from app.platform.errors import InvalidInput, NotFound
from app.platform.outbox import Outbox, OutboxKind, retry_while_not_accepted
from app.platform.repository import Repository
from app.platform.services import CrudService, ReadOnlyService
from app.platform.views import Preset
from app.schemas.notifications import (
    EventTypeRead, GateEventRead, NotificationRead, NotificationRuleCreate, NotificationRuleRead, NotificationRuleUpdate,
    RuleTestResult, UserOption,
)
from app.services import oplog

READ = "sales.read"
CONFIG = "sales.config"
NOTIFY_EMAIL = "notify_email"
TEST_EVENT = "test"
OUTBOX = Outbox(OutboxRecord)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── routing ──────────────────────────────────────────────────────────────────
async def active_rule(session, event: str) -> NotificationRule | None:
    return (await session.execute(select(NotificationRule).where(
        NotificationRule.event_type == event, NotificationRule.active.is_(True)))).scalar_one_or_none()


async def recipients(session, rule: NotificationRule | None) -> tuple[list[uuid.UUID], list[str]]:
    """The rule's active users (in its order) and every address to mail: the
    users' own, then the external ones; each once."""
    if rule is None:
        return [], []
    ids = [uuid.UUID(u) for u in (rule.recipients or {}).get("users", [])]
    found = await find_users(session, ids)
    users = [i for i in ids if i in found and found[i].active]
    addresses: list[str] = []
    for a in [found[i].email for i in users] + list((rule.recipients or {}).get("emails", [])):
        if a and a.lower() not in {x.lower() for x in addresses}:
            addresses.append(a)
    return users, addresses


def wants_task(event: str, rule: NotificationRule | None) -> bool:
    ev = BY_KEY.get(event)
    return ev is None or ev.kind == ACTION or rule is None or rule.channel in ("TASK", "BOTH")


def wants_email(rule: NotificationRule | None) -> bool:
    return rule is not None and rule.channel in ("EMAIL", "BOTH")


def _body(title: str, description: str | None, key: str | None) -> str:
    from app.core.config import settings
    parts = [title, "", description or ""]
    if key:
        parts += ["", f"Problem: {key}"]
        base = (getattr(settings, "SALES_PUBLIC_URL", "") or "").rstrip("/")
        if base:
            parts.append(f"Open in Sales: {base}/inbox/{key}")
    parts += ["", "Sent by UniOps Sales. Who receives this is set under Configuration → Notifications."]
    return "\n".join(parts)


async def record_task(session, event: str, key: str, assignee: uuid.UUID | None, title: str, task_id,
                      *, test: bool = False) -> None:
    session.add(NotificationRecord(event_type=event, problem_key=key, channel="TASK",
                                   recipients=[str(assignee)] if assignee else [], subject=title, task_id=task_id,
                                   test=test))
    await session.flush()


async def queue_email(session, event: str, key: str | None, to: list[str], title: str, description: str | None,
                      *, test: bool = False) -> OutboxRecord:
    subject = f"Sales: {title}"[:255]
    row = OUTBOX.add(session, NOTIFY_EMAIL, {"to": to, "subject": subject, "body": _body(title, description, key)})
    await session.flush()
    session.add(NotificationRecord(event_type=event, problem_key=key, channel="EMAIL", recipients=to, subject=subject,
                                   outbox_id=row.id, test=test))
    await session.flush()
    return row


def email_kind(email) -> OutboxKind:
    """The email of a notification: retried while the server has not taken it
    (§13.7), never after."""
    async def effect(payload: dict[str, Any], ctx) -> None:  # noqa: ARG001
        await email.send_external(ExternalEmail(to=tuple(payload["to"]), subject=payload["subject"],
                                                body_text=payload["body"]))

    async def failed(row, exc, uow) -> None:
        await oplog.write(uow.session, "ERROR", "notify", f"notification email {row.id} not sent: {exc}",
                          context={"outbox_id": row.id, "subject": row.payload.get("subject")})

    async def suppressed(row, exc, uow) -> None:
        await oplog.write(uow.session, "INFO", "notify", f"notification email {row.id} suppressed: {exc}",
                          context={"outbox_id": row.id, "subject": row.payload.get("subject")})

    return OutboxKind(effect=effect, policy=retry_while_not_accepted(3), on_failed=failed, on_suppressed=suppressed)


# ── the go-live gate (PRD section 11 step 3, AC-OB-71) ───────────────────────
async def gate(session) -> list[GateEventRead]:
    rules = {r.event_type: r for r in (await session.execute(
        select(NotificationRule).where(NotificationRule.event_type.in_(GATE_EVENTS)))).scalars()}
    out = []
    for key in GATE_EVENTS:
        r = rules.get(key)
        users, addresses = await recipients(session, r) if r is not None and r.active else ([], [])
        has = bool(users or addresses)
        problems = []
        if r is None or not r.active:
            problems.append("no active rule")
        elif not has:
            problems.append("no recipients")
        if r is None or r.tested_at is None:
            problems.append("no test notification sent")
        out.append(GateEventRead(event_type=key, label=BY_KEY[key].label, rule_id=r.id if r else None,
                                 recipients=len(users) + len(addresses), tested_at=r.tested_at if r else None,
                                 ok=not problems, problem="; ".join(problems) or None))
    return out


# ── Configuration → Notifications ────────────────────────────────────────────
class NotificationRuleRepository(Repository[NotificationRule]):
    model = NotificationRule
    search_fields = ("event_type",)
    list_fields = ("event_type", "channel", "active", "tested_at", "created_at", "updated_at")
    labels = {"event_type": "Event", "tested_at": "Last test"}
    enums = {"channel": ("TASK", "EMAIL", "BOTH")}
    default_sort = ("event_type",)
    groupable = ("channel", "active")
    presets = (Preset("active", "Active", {"filters": [{"field": "active", "op": "eq", "value": "true"}]}),
               Preset("all", "All"))


def _recipients(users: list[uuid.UUID] | None, emails: list[str] | None) -> dict[str, list[str]]:
    return {"users": [str(u) for u in users or []], "emails": list(emails or [])}


class NotificationRuleService(CrudService[NotificationRule, NotificationRuleRead, NotificationRuleCreate,
                                          NotificationRuleUpdate]):
    repo_cls = NotificationRuleRepository
    read_schema = NotificationRuleRead
    create_schema = NotificationRuleCreate
    update_schema = NotificationRuleUpdate
    read_permission = READ
    write_permission = CONFIG
    unique_constraints = {"sales_notification_rules_event_type_key": ("notification.event_taken", "event_type")}

    def to_read(self, entity: NotificationRule) -> NotificationRuleRead:
        ev = BY_KEY.get(entity.event_type)
        r = entity.recipients or {}
        return NotificationRuleRead(
            id=entity.id, event_type=entity.event_type, event_label=ev.label if ev else entity.event_type,
            kind=ev.kind if ev else ACTION, gate=bool(ev and ev.gate), channel=entity.channel,
            users=r.get("users", []), emails=r.get("emails", []), active=entity.active, tested_at=entity.tested_at,
            test_result=entity.test_result, version=entity.version, created_at=entity.created_at,
            updated_at=entity.updated_at)

    def build(self, values: dict[str, Any]) -> NotificationRule:
        users, emails = values.pop("users", None), values.pop("emails", None)
        return NotificationRule(**values, recipients=_recipients(users, emails))

    async def apply_changes(self, entity: NotificationRule, changes: dict[str, Any]) -> None:
        if "users" in changes or "emails" in changes:
            cur = entity.recipients or {}
            entity.recipients = _recipients(changes.pop("users", cur.get("users")), changes.pop("emails", cur.get("emails")))
        await super().apply_changes(entity, changes)

    async def _check(self, channel: str, users: list[uuid.UUID], emails: list[str]) -> None:
        found = await find_users(self.uow.session, users)
        missing = [str(u) for u in users if u not in found or not found[u].active]
        if missing:
            raise InvalidInput("notification.unknown_user", f"no active user {', '.join(missing)}", {"field": "users"})
        bad = [e for e in emails if not _EMAIL.match(e)]
        if bad:
            raise InvalidInput("notification.bad_email", f"not an email address: {', '.join(bad)}", {"field": "emails"})
        if channel in ("EMAIL", "BOTH") and not (users or emails):
            raise InvalidInput("notification.no_recipients", "an email rule needs at least one user or address",
                               {"field": "emails"})

    async def validate_create(self, data: NotificationRuleCreate) -> None:
        await self._check(data.channel, data.users, data.emails)

    async def before_update(self, entity: NotificationRule, changes: dict[str, Any]) -> None:
        cur = entity.recipients or {}
        await self._check(changes.get("channel", entity.channel),
                          changes.get("users", [uuid.UUID(u) for u in cur.get("users", [])]),
                          changes.get("emails", cur.get("emails", [])))

    @requires(READ)
    async def events(self) -> list[EventTypeRead]:
        ruled = {r.event_type: r for r in (await self.uow.session.execute(select(NotificationRule))).scalars()}
        return [EventTypeRead(key=e.key, label=e.label, kind=e.kind, gate=e.gate,
                              rule_id=ruled[e.key].id if e.key in ruled else None) for e in EVENT_TYPES]

    @requires(READ)
    async def users(self, q: str = "") -> list[UserOption]:
        """Active users for the rule's picker (all of them, by name; a search narrows)."""
        return [UserOption(id=u.id, name=u.name, email=u.email) for u in await search_users(self.uow.session, q, limit=500)]

    @requires(READ)
    async def gate(self) -> list[GateEventRead]:
        return await gate(self.uow.session)

    @requires(CONFIG)
    async def send_test(self, id: int, tasks) -> RuleTestResult:
        """A test notification through the rule as it is (AC-OB-71): a task
        and / or an email, whatever the shadow run. Recorded on the rule."""
        s = self.uow.session
        rule = await self.repo.get(id, for_update=True)
        if not rule.active:
            raise InvalidInput("notification.inactive", "activate the rule before testing it")
        ev = BY_KEY.get(rule.event_type)
        label = ev.label if ev else rule.event_type
        users, addresses = await recipients(s, rule)
        title = f"Test notification: {label}"
        text = (f"This is a test of the Sales notification rule for \"{label}\". When the event happens, "
                "this is where its notification arrives. Complete this task when you have seen it.")
        done = []
        if rule.channel in ("TASK", "BOTH"):
            from app.services.problems import problem
            key = tasks.problem_key(TEST_EVENT, rule.id)
            task_id = await tasks.write_task(s, TEST_EVENT, rule.id, problem(TEST_EVENT, title, text),
                                             assignee_id=users[0] if users else None)
            if task_id is not None:
                await record_task(s, rule.event_type, key, users[0] if users else await tasks.assignee(s), title,
                                  task_id, test=True)
                done.append("task written")
            else:
                done.append("task NOT written (see the log)")
        if rule.channel in ("EMAIL", "BOTH") and addresses:
            row = await queue_email(s, rule.event_type, None, addresses, title, text, test=True)
            done.append(f"email queued to {len(addresses)} address(es) (outbox #{row.id})")
        self.uow.set_reason("test notification")
        rule.tested_at = datetime.now(timezone.utc)
        rule.test_result = "; ".join(done) or "nothing to send"
        await s.flush()
        return RuleTestResult(rule=self.to_read(rule), result=rule.test_result)


class NotificationRepository(Repository[NotificationRecord]):
    model = NotificationRecord
    search_fields = ("subject", "problem_key", "event_type")
    list_fields = ("id", "at", "event_type", "problem_key", "channel", "subject", "test")
    labels = {"at": "When", "event_type": "Event", "problem_key": "Problem"}
    enums = {"channel": ("TASK", "EMAIL")}
    default_sort = ("-at",)
    groupable = ("event_type", "channel", "test")
    presets = (Preset("all", "All"),)


class NotificationService(ReadOnlyService[NotificationRecord, NotificationRead]):
    """The history of notifications made (FR-OB-56); an email's delivery is its outbox row."""
    repo_cls = NotificationRepository
    read_schema = NotificationRead
    read_permission = READ

    async def _delivery(self, ids: list[int]) -> dict[int, tuple[str, str | None]]:
        if not ids:
            return {}
        rows = (await self.uow.session.execute(select(OutboxRecord.id, OutboxRecord.status, OutboxRecord.last_error)
                                               .where(OutboxRecord.id.in_(ids)))).all()
        return {r.id: (r.status, r.last_error) for r in rows}

    async def list(self, spec):  # type: ignore[override]
        page = await super().list(spec)
        delivery = await self._delivery([i.outbox_id for i in page.items if i.outbox_id])
        for i in page.items:
            if i.outbox_id in delivery:
                i.delivery, i.delivery_error = delivery[i.outbox_id]
            elif i.channel == "TASK":
                i.delivery = "Task written"
        return page

    def to_read(self, entity: NotificationRecord) -> NotificationRead:
        ev = BY_KEY.get(entity.event_type)
        return NotificationRead(id=entity.id, at=entity.at, event_type=entity.event_type,
                                event_label=ev.label if ev else entity.event_type, problem_key=entity.problem_key,
                                channel=entity.channel, recipients=entity.recipients, subject=entity.subject,
                                outbox_id=entity.outbox_id, test=entity.test)


async def require_rule(session, id: int) -> NotificationRule:
    r = await session.get(NotificationRule, id)
    if r is None:
        raise NotFound("notification_rule", id)
    return r
