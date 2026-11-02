"""Operations Health and the Order dashboard (FR-OB-52 / 52a / 52b).

Every count is a list query: a card or check names its resource and filters,
and the number is that list's total — the same definition the list it opens
shows (FR-OB-52: "never from a condition of its own"). Historical orders stay
out of open-work cards (the orders list leaves them out by default).

Health: PASSED, WARNING or FAILED (FR-OB-52a), the worst check decides.
FAILED — a required setting missing; an active connection with its
authentication latch set, or the warehouse connection inactive; more than one
cycle RUNNING or one stuck; the scheduler paused longer than the alarm.
WARNING — the scheduler paused (within the alarm) or not running here; failed
files; rejected or timed-out ACKs; 945 / 944 exceptions; transfer
discrepancies; failed or long-queued sends; open tasks older than the set age.
The shadow run and the two send switches are shown as they are.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select

from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.integrations.epms_tasks import shared_tasks_table
from app.models.config import Connection, Setting as SettingRow
from app.models.documents import Run
from app.models.records import OutboxRecord
from app.models.shipments import Shipment, ShipmentRow
from app.models.transfers import Receipt
from app.platform.authorization import requires
from app.platform.query import Filter, QuerySpec
from app.schemas.health import CardRead, CheckRead, DashboardRead, HealthRead, RunBrief, TaskCount
from app.services.notifications import gate
from app.services.operations import DocumentRepository
from app.services.order_views import OrderRepository
from app.services.problems import DOCUMENT_TYPE
from app.services.settings import (
    EDI_CONNECTION_ID, OPERATOR_USER_ID, OUTBOX_BACKLOG_MINUTES, PAUSED_ALARM_MINUTES, SCHEDULER_INTERVAL_MINUTES,
    SCHEDULER_PAUSED, SETTINGS, SHADOW_MODE, TASK_AGE_HOURS,
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


OPEN = ("order_state", "in", ["Active", "On Hold"])
# FR-OB-52b, in its order.
DASHBOARD_CARDS: tuple[tuple[str, str, ListLink], ...] = (
    ("active", "Active", _orders(("order_state", "eq", "Active"))),
    ("on_hold", "On Hold", _orders(("order_state", "eq", "On Hold"))),
    ("completed", "Completed", _orders(("order_state", "eq", "Completed"))),
    ("cancelled", "Cancelled", _orders(("order_state", "eq", "Cancelled"))),
    ("failed_files", "Failed files", _documents()),  # inbound + outbound: the two failed lists summed
    ("open_retailer", "Open retailer", _orders(("source_type", "eq", "EDI_850"), OPEN)),
    ("open_shopify", "Open Shopify", _orders(("source_type", "eq", "SHOPIFY"), OPEN)),
    ("open_manual", "Open manual", _orders(("source_type", "eq", "MANUAL"), OPEN)),
    ("warehouse_waiting", "Warehouse waiting", _orders(("fulfillment_status", "in", [
        F.PENDING.value, F.GENERATED_940.value, F.SENT_940.value, F.ACCEPTED_940.value]))),
    ("invoice_work", "Invoice work", _orders(("invoice_status", "in", [
        Inv.WAITING_FOR_DELIVERY_CONFIRMATION.value, Inv.READY.value, Inv.QUEUED.value, Inv.FAILED.value]))),
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

        # scheduler
        running = list((await self.s.execute(select(Run).where(Run.status == "RUNNING"))).scalars())
        last_ok = (await self.s.execute(select(func.max(Run.finished_at)).where(
            Run.status.in_(("SUCCEEDED", "PARTIAL"))))).scalar_one()
        interval = await SETTINGS.get(self.s, SCHEDULER_INTERVAL_MINUTES) or 5
        paused = await SETTINGS.get(self.s, SCHEDULER_PAUSED)
        stale = [r for r in running if (r.heartbeat_at or r.started_at) < now - timedelta(minutes=30)]
        enabled = bool(getattr(self.settings, "SALES_SCHEDULER_ENABLED", False))
        last = f"last successful run {last_ok:%Y-%m-%d %H:%M} UTC" if last_ok else "no successful run yet"
        if len(running) > 1:
            add("scheduler", "Scheduler", FAILED, f"{len(running)} cycles are RUNNING at once", link="/runs")
        elif stale:
            add("scheduler", "Scheduler", FAILED, f"a cycle is stuck RUNNING since {stale[0].started_at:%Y-%m-%d %H:%M} UTC",
                link="/runs")
        elif paused:
            row = (await self.s.execute(select(SettingRow).where(SettingRow.key == SCHEDULER_PAUSED.key))).scalar_one_or_none()
            since = (row.updated_at or row.created_at) if row is not None else None
            alarm = await SETTINGS.get(self.s, PAUSED_ALARM_MINUTES)
            over = since is not None and since < now - timedelta(minutes=alarm)
            add("scheduler", "Scheduler", FAILED if over else WARNING,
                f"paused{f' since {since:%Y-%m-%d %H:%M} UTC' if since else ''}"
                f"{f' — longer than {alarm} minutes' if over else ''}; {last}", link="/runs")
        elif not enabled:
            add("scheduler", "Scheduler", WARNING, f"not running in this environment (SALES_SCHEDULER_ENABLED off); {last}",
                link="/runs")
        elif last_ok is None or last_ok < now - timedelta(minutes=3 * interval):
            add("scheduler", "Scheduler", WARNING, f"no successful cycle in the last {3 * interval} minutes; {last}", link="/runs")
        else:
            add("scheduler", "Scheduler", PASSED, f"every {interval} minutes; {last}", link="/runs")

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
            result=result, checked_at=now, checks=out, cards=cards, shadow_mode=shadow,
            partner_writes_enabled=bool(getattr(self.settings, "SALES_PARTNER_WRITES_ENABLED", False)),
            external_email_enabled=bool(getattr(self.settings, "SALES_EXTERNAL_EMAIL_ENABLED", False)),
            scheduler_enabled=enabled, gate=gate_rows, gate_ok=all(g.ok for g in gate_rows))

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
        review = await self.count(_orders(("fulfillment_status", "eq", F.REVIEW.value)))
        delivery = await self.count(_orders(("invoice_status", "eq", Inv.WAITING_FOR_DELIVERY_CONFIRMATION.value)))
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
