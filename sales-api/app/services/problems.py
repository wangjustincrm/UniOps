"""Action Required tasks of the Sales module (design §3.5): one open task per
problem in the shared Task Inbox, assigned to the operator (X2). Task types
are `sales_<event>`; their labels and links are registered in P8."""
from __future__ import annotations

from app.integrations.epms_tasks import ProblemTask, TaskService, shared_tasks_table
from app.services.settings import OPERATOR_USER_ID, SETTINGS

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


async def _operator(session):
    return await SETTINGS.get(session, OPERATOR_USER_ID)


def task_service(notify) -> TaskService:
    return TaskService(table=shared_tasks_table(), prefix=PREFIX, document_type=DOCUMENT_TYPE,
                       assigned_role=ROLE, assignee=_operator, notify=notify)


def problem(event: str, title: str, description: str | None = None, *, urgent: bool = False) -> ProblemTask:
    return ProblemTask(task_type=f"sales_{event}", title=title[:255], description=description,
                       priority="urgent" if urgent else "normal")
