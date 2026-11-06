"""Action Required tasks of the Sales module (design §3.5): one open task per
problem in the shared Task Inbox, assigned to the operator (X2). Task types
are `sales_<event>`; their labels and links are registered in epms and the
Portal (P8). Every raised problem goes through the notification rules
(app/services/notifications.py, FR-OB-56): who gets the task, and whether an
email goes out too."""
from __future__ import annotations

from app.integrations.epms_tasks import ProblemTask, TaskService, shared_tasks_table
from app.services.settings import OPERATOR_USER_ID, SETTINGS, SHADOW_MODE

PREFIX, DOCUMENT_TYPE, ROLE = "SALES", "sales", "sales_operator"

# Events raised by P2 (intake and the scheduler).
UNREADABLE_FILE = "unreadable_file"      # entity: document id (FR-OB-70)
SFTP_FETCH_FAILED = "sftp_fetch_failed"  # entity: connection id (FR-OB-51)
SFTP_AUTH_FAILED = "sftp_auth_failed"    # entity: connection id (FR-OB-50, AC-OB-48)
FILE_NOT_STORED = "file_not_stored"      # entity: connection id (FR-OB-74)
# P3 (orders and the Shopify channel)
ORDER_REVIEW = "order_review"            # entity: order id (FR-OB-15, AC-OB-13)
ORDER_ARCHIVED = "order_archived"        # entity: order id (FR-OB-03d)
SHOPIFY_FETCH_FAILED = "shopify_fetch_failed"  # entity: connection id (FR-OB-51)
SHOPIFY_AUTH_FAILED = "shopify_auth_failed"    # entity: connection id (FR-OB-50)
ORDER_FILE_UNREADABLE = "order_file_bad"       # entity: document id (FR-OB-70)
# P4 (940 / 997)
EDI_940_FAILED = "940_failed"             # entity: order id (FR-OB-24, FR-OB-29a)
EDI_940_REJECTED = "940_rejected"         # entity: order id (FR-OB-22a, AC-OB-32)
EDI_940_TIMEOUT = "940_timeout"           # entity: order id (FR-OB-23, AC-OB-33)
EDI_997_UNMATCHED = "997_unmatched"       # entity: document id (FR-OB-22a)
CHANGED_AFTER_940 = "changed_after_940"   # entity: order id (FR-OB-16, AC-OB-18 / 70)
# P5 (945 and the Shopify write-back)
SHORT_SHIPMENT = "short_shipment"          # entity: order id (FR-OB-25, AC-OB-37)
SECOND_945 = "second_945"                  # entity: order id (FR-OB-25, AC-OB-73)
EDI_945_NO_ORDER = "945_no_order"          # entity: shipment id (FR-OB-25c / FR-OB-28, AC-OB-38 / 78)
EDI_945_LINE_UNMATCHED = "945_line"        # entity: shipment id (FR-OB-25a, AC-OB-57)
SHOPIFY_WRITE_FAILED = "shopify_write"     # entity: shipment id (FR-OB-11 / 11a)
# P6 (invoicing, cancellation / return after shipment)
INVOICE_HOLD = "invoice_hold"              # entity: order id (FR-OB-25 lot exception, part case, no price)
INVOICE_FAILED = "invoice_failed"          # entity: order id (FR-OB-30)
ORDER_RETURN = "order_return"              # entity: order id (FR-OB-19)
CANCELLED_AFTER_940 = "cancel_after_940"   # entity: order id (FR-OB-18, AC-OB-40; the email is P8)
# P7 (transfers)
TRANSFER_FAILED = "transfer_failed"        # entity: transfer id (FR-OB-81: checks / upload)
EDI_943_REJECTED = "943_rejected"          # entity: transfer id (FR-OB-82, AC-OB-65)
EDI_943_TIMEOUT = "943_timeout"            # entity: transfer id (FR-OB-82 / FR-OB-23)
TRANSFER_DISCREPANCY = "transfer_discrepancy"  # entity: transfer id (FR-OB-84, AC-OB-67 / 76)
SECOND_944 = "second_944"                  # entity: transfer id (FR-OB-83, AC-OB-68)
EDI_944_NO_TRANSFER = "944_no_transfer"    # entity: receipt id (FR-OB-84)
# P8 (events that were only logged until now)
SEND_REFUSED = "send_refused"              # entity: outbox row id (§5.5: a send refused by the write guard)
EDI_945_VALIDATION = "945_validation"      # entity: shipment id (FR-OB-25d; notify only)
PARTIAL_RECEIPT = "partial_receipt"        # entity: transfer id (FR-OB-84; notify only)
# Order refs (user 2026-11-03)
PARTNER_NO_ABBREV = "partner_no_abbrev"    # entity: partner id (its orders get no Order ref)


async def partners_without_abbreviation(session, tasks) -> int:
    """One task per active partner with no abbreviation (raised by the system
    job; completed when the abbreviation is set). Returns how many."""
    from sqlalchemy import or_, select

    from app.models.config import Partner
    missing = list((await session.execute(select(Partner).where(
        Partner.active.is_(True), or_(Partner.abbrev.is_(None), Partner.abbrev == "")))).scalars())
    for p in missing:
        await tasks.raise_problem(session, PARTNER_NO_ABBREV, p.id, problem(
            PARTNER_NO_ABBREV, f"Partner {p.code} needs an abbreviation",
            f"Partner {p.name} ({p.code}) has no abbreviation. It starts every Order ref (e.g. LCL-4887314746, A.3): "
            "its new orders go to review until it is set under Configuration → Partners."))
    return len(missing)


async def _operator(session):
    return await SETTINGS.get(session, OPERATOR_USER_ID)


# During the shadow run (§5.4) only intake and connection problems raise
# tasks — they would be real problems after the cutover too. Every other
# problem (review, archive, 940, 997, …) is logged instead: Zoho is live.
SHADOW_RAISED = frozenset({UNREADABLE_FILE, SFTP_FETCH_FAILED, SFTP_AUTH_FAILED, FILE_NOT_STORED,
                           SHOPIFY_FETCH_FAILED, SHOPIFY_AUTH_FAILED, ORDER_FILE_UNREADABLE})


class SalesTasks(TaskService):
    async def raise_problem(self, session, event: str, entity: object, task: ProblemTask, **kw):
        if event not in SHADOW_RAISED and await SETTINGS.get(session, SHADOW_MODE):
            from app.services import oplog  # oplog → models; kept local like the other late imports
            await oplog.write(session, "INFO", "shadow", f"shadow run: no task raised: {task.title}",
                              context={"event": event, "entity": str(entity)})
            return None
        from app.services import notifications as n  # notifications → schemas → models; kept local
        rule = await n.active_rule(session, event)
        users, addresses = await n.recipients(session, rule)
        key = self.problem_key(event, entity)
        task_id, new = None, True
        if n.wants_task(event, rule):
            new = await self.open_task(session, key) is None
            assignee = users[0] if users else None
            task_id = await super().raise_problem(session, event, entity, task, assignee_id=assignee)
            if new and task_id is not None:
                await n.record_task(session, event, key, assignee or await self.assignee(session), task.title, task_id)
        if n.wants_email(rule) and addresses and new:  # once per problem, never on a refresh
            await n.queue_email(session, event, key, addresses, task.title, task.description)
        return task_id

    async def write_task(self, session, event: str, entity: object, task: ProblemTask, **kw):
        """The task alone, past the rules and the shadow run (a rule's test)."""
        return await TaskService.raise_problem(self, session, event, entity, task, **kw)


def task_service(notify) -> TaskService:
    return SalesTasks(table=shared_tasks_table(), prefix=PREFIX, document_type=DOCUMENT_TYPE,
                      assigned_role=ROLE, assignee=_operator, notify=notify)


def problem(event: str, title: str, description: str | None = None, *, urgent: bool = False) -> ProblemTask:
    return ProblemTask(task_type=f"sales_{event}", title=title[:255], description=description,
                       priority="urgent" if urgent else "normal")


async def send_refused(tasks, session, row, what: str, exc: BaseException | None) -> None:
    """A send the write guard refused (§5.5): it was not made and will not be
    made by itself — a person requeues it once the switch or connection is
    right. One task per outbox row."""
    await tasks.raise_problem(session, SEND_REFUSED, row.id, problem(
        SEND_REFUSED, f"Not sent: {what}"[:255],
        f"{what} was not sent: the write guard refused it ({exc}). Nothing goes out by itself: check the switch "
        f"and the connection, then send it again from its order, invoice or transfer."))


def hook_failed(tasks):
    """The outbox's report of a result that could not be recorded: the send may have happened
    while the order or document still shows it did not. One task per outbox row."""
    async def report(row, exc: BaseException, uow) -> None:
        await tasks.raise_problem(uow.session, SEND_REFUSED, row.id, problem(
            SEND_REFUSED, f"Check a send: {row.kind} (outbox row {row.id})"[:255],
            f"The {row.kind} of outbox row {row.id} ran, but recording its result failed: {exc}. The order or document "
            "may show the wrong state. Check it before sending again."))
    return report

