"""Operations Health and the Order dashboard (FR-OB-52 / 52a / 52b).

Every count is a list query: a card or check names its resource and filters,
and the number is that list's total — the same definition the list it opens
shows (FR-OB-52: "never from a condition of its own"). Historical orders stay
out of open-work cards (the orders list leaves them out by default).

Health: PASSED, WARNING or FAILED (FR-OB-52a), the worst check decides.
FAILED — a required setting missing; an active connection with its
authentication latch set, or the warehouse connection inactive; more than one
cycle RUNNING or one stuck. Automatic runs Off is a state someone chose: it
passes, however long it lasts.
WARNING — automatic runs not available on this server, or a job overdue; failed
files; rejected or timed-out ACKs; 945 / 944 exceptions; transfer
discrepancies; failed or long-queued sends; open tasks older than the set age.
The shadow run and the two send switches are shown as they are.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select

from app.domain.order_status import (
    ACTIVE_STATUSES, CANCELLED, COMPLETED_STATUSES, INVOICE_WORK_STATUSES, NEEDS_REVIEW, ON_HOLD, WAITING_DELIVERY, WAREHOUSE_STATUSES,
)
from app.integrations.epms_tasks import shared_tasks_table
from app.models.config import Connection
from app.models.documents import Run
from app.models.records import OutboxRecord
from app.models.shipments import Shipment, ShipmentRow
from app.models.transfers import Receipt
from app.platform.authorization import requires
from app.platform.query import Filter, QuerySpec
from app.domain.schedule import describe, is_overdue
from app.schemas.health import CardRead, CheckRead, DashboardRead, HealthRead, JobRead, RunBrief, TaskCount
from app.services.cycle import SYSTEM as SYSTEM_SCOPE
from app.services.cycle import window_of
from app.services.notifications import gate
from app.services.operations import DocumentRepository
from app.services.order_views import OrderRepository
from app.services.problems import DOCUMENT_TYPE
from app.services.settings import (
    EDI_CONNECTION_ID, OPERATOR_USER_ID, OUTBOX_BACKLOG_MINUTES,
    SCHEDULER_PAUSED, SETTINGS, SHADOW_MODE, SYSTEM_MINUTES, TASK_AGE_HOURS,
)
from app.services.transfer_views import TransferRepository

READ = "sales.read"
PASSED, WARNING, FAILED = "PASSED", "WARNING", "FAILED"
_RANK = {PASSED: 0, WARNING: 1, FAILED: 2}
TASKS = shared_tasks_table()


@dataclass(frozen=True)
class ListLink:
    """A list the user can open: the page, its resource and its filters."""
    page: str
    resource: str
    filters: tuple[tuple[str, str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {"page": self.page, "resource": self.resource,
                "filters": [{"field": f, "op": op, "value": v} for f, op, v in self.filters]}


def _orders(*filters) -> ListLink:
    return ListLink("/orders", "orders", tuple(filters))


def _documents(*filters) -> ListLink:
    return ListLink("/documents", "documents", tuple(filters))


OPEN = ("order_status", "in", [*ACTIVE_STATUSES, ON_HOLD])
# FR-OB-52b, in its order.
DASHBOARD_CARDS: tuple[tuple[str, str, ListLink], ...] = (
    ("active", "Active", _orders(("order_status", "in", list(ACTIVE_STATUSES)))),
    ("on_hold", "On Hold", _orders(("order_status", "eq", ON_HOLD))),
    ("completed", "Completed", _orders(("order_status", "in", list(COMPLETED_STATUSES)))),
    ("cancelled", "Cancelled", _orders(("order_status", "eq", CANCELLED))),
    ("failed_files", "Failed files", _documents()),  # inbound + outbound: the two failed lists summed
    ("open_retailer", "Open retailer", _orders(("source_type", "eq", "EDI_850"), OPEN)),
    ("open_shopify", "Open Shopify", _orders(("source_type", "eq", "SHOPIFY"), OPEN)),
    ("open_manual", "Open manual", _orders(("source_type", "eq", "MANUAL"), OPEN)),
    ("warehouse_waiting", "Warehouse waiting", _orders(("order_status", "in", list(WAREHOUSE_STATUSES)))),
    ("invoice_work", "Invoice work", _orders(("order_status", "in", list(INVOICE_WORK_STATUSES)))),
)
FAILED_OUTBOUND = _documents(("direction", "eq", "OUT"), ("file_status", "eq", "Failed"))
FAILED_INBOUND = _documents(("direction", "eq", "IN"), ("processing_status", "eq", "Failed"))
WAITING_ACK = _documents(("direction", "eq", "OUT"), ("ack_status", "eq", "Waiting"))
REJECTED_ACK = _documents(("direction", "eq", "OUT"), ("ack_status", "eq", "Rejected"))
TIMEOUT_ACK = _documents(("direction", "eq", "OUT"), ("ack_status", "eq", "Timeout"))
DISCREPANCIES = ListLink("/transfers", "transfers", (("status", "eq", "Discrepancy"),))
EXCEPTIONS_945 = _documents(("kind", "eq", "945_EDI"))
EXCEPTIONS_944 = _documents(("kind", "eq", "944_EDI"))
REPOS = {"orders": OrderRepository, "documents": DocumentRepository, "transfers": TransferRepository}


def _counts(keys) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in keys:
        out[k] = out.get(k, 0) + 1
    return out


class Health:
    def __init__(self, uow, settings) -> None:
        self.uow = uow
        self.s = uow.session
        self.settings = settings  # the environment's switches (SALES_*_ENABLED)

    async def count(self, link: ListLink) -> int:
        repo = REPOS[link.resource](self.uow)
        spec = QuerySpec(page=1, page_size=1, filters=[Filter(field=f, op=op, value=v) for f, op, v in link.filters])
        return (await repo.list(spec)).total

    async def _scalar(self, stmt) -> int:
        return int((await self.s.execute(stmt)).scalar_one() or 0)

    # ── FR-OB-52a ────────────────────────────────────────────────────────────
    async def checks(self) -> HealthRead:
        now = datetime.now(timezone.utc)
        out: list[CheckRead] = []

        def add(key: str, label: str, result: str, detail: str, *, count: int | None = None,
                link: ListLink | str | None = None) -> None:
            out.append(CheckRead(key=key, label=label, result=result, detail=detail, count=count,
                                 link=link.as_dict() if isinstance(link, ListLink) else ({"page": link} if link else None)))

        # settings
        missing = [label for label, v in (
            ("operator (who receives tasks)", await SETTINGS.get(self.s, OPERATOR_USER_ID)),
            ("warehouse SFTP connection", await SETTINGS.get(self.s, EDI_CONNECTION_ID))) if v is None]
        add("settings", "Required settings", FAILED if missing else PASSED,
            f"missing: {', '.join(missing)}" if missing else "set", link="/config/connections")

        # connections
        conns = list((await self.s.execute(select(Connection).order_by(Connection.name))).scalars())
        edi_id = await SETTINGS.get(self.s, EDI_CONNECTION_ID)
        for c in conns:
            if not c.active and c.id != edi_id:
                continue
            if c.auth_failed_at is not None:
                add(f"connection:{c.id}", f"Connection {c.name}", FAILED,
                    f"authentication failed {c.auth_failed_at:%Y-%m-%d %H:%M} UTC: {c.auth_failed_reason or ''} — "
                    "not retried until cleared", link="/config/connections")
            elif not c.active:
                add(f"connection:{c.id}", f"Connection {c.name}", FAILED, "the warehouse connection is inactive",
                    link="/config/connections")
            else:
                add(f"connection:{c.id}", f"Connection {c.name}", PASSED, f"{c.kind}, {c.mode}", link="/config/connections")

        # scheduler: every job (per connection, and the system job)
        jobs = await self.jobs(now)
        running = list((await self.s.execute(select(Run).where(Run.status == "RUNNING"))).scalars())
        last_ok = (await self.s.execute(select(func.max(Run.finished_at)).where(
            Run.status.in_(("SUCCEEDED", "PARTIAL"))))).scalar_one()
        paused = await SETTINGS.get(self.s, SCHEDULER_PAUSED)
        stale = [r for r in running if (r.heartbeat_at or r.started_at) < now - timedelta(minutes=30)]
        enabled = bool(getattr(self.settings, "SALES_SCHEDULER_ENABLED", False))
        last = f"last successful run {last_ok:%Y-%m-%d %H:%M} UTC" if last_ok else "no successful run yet"
        twice = [k for k, n in _counts(f"{r.job}|{r.scope}" for r in running).items() if n > 1]
        overdue = [j.label for j in jobs if j.overdue]
        if twice:
            add("scheduler", "Scheduler", FAILED, f"a job is RUNNING twice at once: {', '.join(twice)}", link="/runs")
        elif stale:
            add("scheduler", "Scheduler", FAILED, f"a cycle is stuck RUNNING since {stale[0].started_at:%Y-%m-%d %H:%M} UTC",
                link="/runs")
        elif not enabled:
            add("scheduler", "Scheduler", WARNING, f"not available on this server (SALES_SCHEDULER_ENABLED off); {last}",
                link="/runs")
        elif paused:
            add("scheduler", "Scheduler", PASSED, f"automatic runs are off; {last}", link="/runs")
        elif overdue:
            add("scheduler", "Scheduler", WARNING, f"overdue: {', '.join(overdue)}; {last}", link="/runs")
        else:
            add("scheduler", "Scheduler", PASSED, f"{len(jobs)} jobs on schedule; {last}", link="/runs")

        # each overdue connection, as an error (user 2026-11-03): it should have run and did not
        if enabled and not paused:
            for j in jobs:
                if j.overdue and j.connection_id is not None:
                    add(f"overdue:{j.connection_id}", f"Connection {j.label} overdue", FAILED,
                        f"overdue since {j.next_run_at:%Y-%m-%d %H:%M} UTC (every {j.interval_minutes} min); last run "
                        + (f"{j.last_run_at:%Y-%m-%d %H:%M} UTC {j.last_run_status or ''}".strip() if j.last_run_at else "none"),
                        link="/config/connections")

        # files and ACKs
        cards: dict[str, int] = {}
        for key, label, link in (("failed_inbound", "Failed inbound files", FAILED_INBOUND),
                                 ("failed_outbound", "Failed outbound files", FAILED_OUTBOUND),
                                 ("rejected_ack", "Rejected ACKs (997)", REJECTED_ACK),
                                 ("ack_timeout", "ACK timeouts", TIMEOUT_ACK)):
            n = cards[key] = await self.count(link)
            add(key, label, WARNING if n else PASSED, f"{n}", count=n, link=link)
        cards["waiting_ack"] = await self.count(WAITING_ACK)

        # 945 / 944 exceptions and transfer discrepancies
        n945 = await self._scalar(select(func.count(func.distinct(Shipment.id))).select_from(Shipment).outerjoin(
            ShipmentRow, and_(ShipmentRow.shipment_id == Shipment.id, ShipmentRow.needs_review.is_(True))).where(
            Shipment.shadow.is_(False), (Shipment.order_id.is_(None)) | (ShipmentRow.id.is_not(None))))
        n944 = await self._scalar(select(func.count(Receipt.id)).where(Receipt.shadow.is_(False), Receipt.transfer_id.is_(None)))
        cards["exceptions_945"], cards["exceptions_944"] = n945, n944
        cards["transfer_discrepancies"] = await self.count(DISCREPANCIES)
        add("exceptions_945", "945 exceptions (no order, unmatched lines)", WARNING if n945 else PASSED, f"{n945}",
            count=n945, link=EXCEPTIONS_945)
        add("exceptions_944", "944s with no transfer", WARNING if n944 else PASSED, f"{n944}", count=n944, link=EXCEPTIONS_944)
        add("transfer_discrepancies", "Transfer discrepancies", WARNING if cards["transfer_discrepancies"] else PASSED,
            f"{cards['transfer_discrepancies']}", count=cards["transfer_discrepancies"], link=DISCREPANCIES)

        # the outbox: failed sends, a backlog
        failed = await self._scalar(select(func.count(OutboxRecord.id)).where(OutboxRecord.status == "FAILED"))
        backlog_min = await SETTINGS.get(self.s, OUTBOX_BACKLOG_MINUTES)
        backlog = await self._scalar(select(func.count(OutboxRecord.id)).where(
            OutboxRecord.status == "PENDING", OutboxRecord.created_at < now - timedelta(minutes=backlog_min)))
        suppressed = await self._scalar(select(func.count(OutboxRecord.id)).where(OutboxRecord.status == "SUPPRESSED"))
        add("outbox", "Outgoing sends", WARNING if failed or backlog else PASSED,
            f"{failed} failed, {backlog} waiting longer than {backlog_min} minutes, {suppressed} refused by the write guard",
            count=failed + backlog, link="/runs")

        # open tasks older than the set age
        age = await SETTINGS.get(self.s, TASK_AGE_HOURS)
        old = await self._scalar(select(func.count(TASKS.c.id)).where(
            TASKS.c.document_type == DOCUMENT_TYPE, TASKS.c.is_completed.is_(False),
            TASKS.c.created_at < now - timedelta(hours=age)))
        add("old_tasks", f"Open tasks older than {age} hours", WARNING if old else PASSED, f"{old}", count=old)

        shadow = await SETTINGS.get(self.s, SHADOW_MODE)
        gate_rows = await gate(self.s)
        result = max((c.result for c in out), key=_RANK.__getitem__, default=PASSED)
        cards["critical"] = sum(c.result == FAILED for c in out)
        cards["warnings"] = sum(c.result == WARNING for c in out)
        return HealthRead(
            jobs=jobs, scheduler_paused=paused, system_minutes=await SETTINGS.get(self.s, SYSTEM_MINUTES),
            result=result, checked_at=now, checks=out, cards=cards, shadow_mode=shadow,
            partner_writes_enabled=bool(getattr(self.settings, "SALES_PARTNER_WRITES_ENABLED", False)),
            external_email_enabled=bool(getattr(self.settings, "SALES_EXTERNAL_EMAIL_ENABLED", False)),
            scheduler_enabled=enabled, gate=gate_rows, gate_ok=all(g.ok for g in gate_rows))

    async def jobs(self, now: datetime) -> list[JobRead]:
        """Every job with its last and next run. Overdue: more than 3 intervals
        past its next run while it should run (automatic runs On here, not
        paused, inside its window)."""
        master = (await SETTINGS.get(self.s, SCHEDULER_PAUSED)) or not getattr(self.settings, "SALES_SCHEDULER_ENABLED", False)
        minutes = await SETTINGS.get(self.s, SYSTEM_MINUTES)
        last = (await self.s.execute(select(Run).where(Run.scope == SYSTEM_SCOPE).order_by(Run.started_at.desc())
                                     .limit(1))).scalar_one_or_none()
        last_sched = (await self.s.execute(select(func.max(Run.started_at)).where(
            Run.scope == SYSTEM_SCOPE, Run.trigger == "SCHEDULE"))).scalar_one()
        nxt = last_sched + timedelta(minutes=minutes) if last_sched else None
        out = [JobRead(scope=SYSTEM_SCOPE, label="System job", connection_id=None, interval_minutes=minutes,
                       schedule=f"every {minutes} min", paused=master, last_run_at=last.finished_at if last else None,
                       last_run_status=last.status if last else None,
                       last_run_summary=((last.summary or {}).get("text") or last.error) if last else None,
                       next_run_at=nxt, overdue=not master and nxt is not None and now > nxt + timedelta(minutes=3 * minutes))]
        for c in (await self.s.execute(select(Connection).where(Connection.active.is_(True)).order_by(Connection.name))).scalars():
            w = window_of(c)
            paused = master or c.schedule_paused
            out.append(JobRead(scope=f"connection:{c.id}", label=c.name, connection_id=c.id,
                               interval_minutes=c.schedule_minutes, schedule=describe(c.schedule_minutes, w),
                               paused=paused, last_run_at=c.last_run_at, last_run_status=c.last_run_status,
                               last_run_summary=c.last_run_summary, next_run_at=c.next_run_at,
                               overdue=is_overdue(now, next_run_at=c.next_run_at, minutes=c.schedule_minutes,
                                                  active=c.active, paused=paused, window=w)))
        return out

    # ── FR-OB-52b ────────────────────────────────────────────────────────────
    async def dashboard(self) -> DashboardRead:
        cards = []
        for k, label, link in DASHBOARD_CARDS:
            if k == "failed_files":
                n = await self.count(FAILED_INBOUND) + await self.count(FAILED_OUTBOUND)
                cards.append(CardRead(key=k, label=label, count=n, link=FAILED_INBOUND.as_dict() if n else link.as_dict(),
                                      parts=[CardRead(key="failed_inbound", label="Inbound", count=await self.count(FAILED_INBOUND),
                                                      link=FAILED_INBOUND.as_dict()),
                                             CardRead(key="failed_outbound", label="Outbound", count=await self.count(FAILED_OUTBOUND),
                                                      link=FAILED_OUTBOUND.as_dict())]))
            else:
                cards.append(CardRead(key=k, label=label, count=await self.count(link), link=link.as_dict()))
        tasks = (await self.s.execute(select(TASKS.c.type, func.count()).where(
            TASKS.c.document_type == DOCUMENT_TYPE, TASKS.c.is_completed.is_(False)).group_by(TASKS.c.type)
            .order_by(func.count().desc()))).all()
        review = await self.count(_orders(("on_hold_reason", "eq", NEEDS_REVIEW)))
        delivery = await self.count(_orders(("order_status", "eq", WAITING_DELIVERY)))
        runs = list((await self.s.execute(select(Run).order_by(Run.started_at.desc()).limit(5))).scalars())
        return DashboardRead(
            cards=cards, open_tasks=[TaskCount(type=t, count=n) for t, n in tasks],
            needs_review=review, waiting_delivery=delivery,
            runs=[RunBrief(id=r.id, trigger=r.trigger, status=r.status, started_at=r.started_at, finished_at=r.finished_at,
                           error=r.error) for r in runs])


class HealthService:
    def __init__(self, uow) -> None:
        self.uow = uow

    @requires(READ)
    async def health(self, settings) -> HealthRead:
        return await Health(self.uow, settings).checks()

    @requires(READ)
    async def dashboard(self) -> DashboardRead:
        return await Health(self.uow, None).dashboard()
