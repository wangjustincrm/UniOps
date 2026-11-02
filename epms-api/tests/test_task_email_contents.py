"""What the task emails say — PRD §3.9.2 / GR-N-001.

ISS-0145: the PR returned-for-revision email went out as "Comment: {comment}"
and never said who returned it. The engine writes the task with no room for
either; the dispatcher now reads them from the return's approval event.
"""
import uuid

import pytest

import app.db.session as session_module
from app.crud import user as user_crud
from app.models.approval import ApprovalEvent
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.schemas.auth import RegisterRequest
from app.services import notification

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mails(monkeypatch):
    sent: list[tuple[str, str, str]] = []

    async def _fake_send_email(to, subject, html, **kwargs):
        sent.append((to, subject, html))

    monkeypatch.setattr("app.services.email.send_email", _fake_send_email)
    return sent


def _mine(mails, user) -> list[tuple[str, str, str]]:
    return [m for m in mails if m[0] == user.email]


async def _user(db, full_name: str, role: str = "requester"):
    u = await user_crud.create(db, RegisterRequest(
        email=f"mail-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=full_name, role=role))
    await db.commit()
    return u


async def _pr(db, owner, **kw) -> PurchaseRequest:
    pr = PurchaseRequest(number=f"PR-MAIL-{uuid.uuid4().hex[:6]}", title=kw.pop("title", "Office chairs"),
                         type=3, status=kw.pop("status", "returned"), created_by=owner.id, **kw)
    db.add(pr)
    await db.commit()
    return pr


async def _send(db, task: Task) -> None:
    db.add(task)
    await db.commit()
    await notification.dispatch_task_notification(task, db)
    await db.commit()


async def test_returned_pr_email_shows_the_comment_and_who_returned_it(mails):
    async with session_module.AsyncSessionLocal() as db:
        requester = await _user(db, "Rita Requester")
        manager = await _user(db, "Manny Manager", role="dept_manager")
        pr = await _pr(db, requester)
        db.add(ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                             step_idx=0, action="return", actor_id=manager.id,
                             actor_role="dept_manager", comment="Just for test <b>now</b>"))
        await _send(db, Task(
            type="revise_pr", document_type="pr", document_id=pr.id, document_number=pr.number,
            assigned_role="requester", assigned_user_id=requester.id,
            title=f"Revise PR: {pr.number} — {pr.title}",
            description="Your PR has been returned for revision."))

    [(_, subject, body)] = _mine(mails, requester)
    assert subject == f"Your PR {pr.number} has been returned for revision"
    assert "{comment}" not in body and "{returned_by}" not in body
    assert "Just for test &lt;b&gt;now&lt;/b&gt;" in body          # escaped (NTF-008)
    assert "<b>Returned by:</b> Manny Manager" in body


async def test_the_latest_return_is_the_one_quoted(mails):
    async with session_module.AsyncSessionLocal() as db:
        requester = await _user(db, "Rita Requester")
        manager = await _user(db, "Manny Manager", role="dept_manager")
        pr = await _pr(db, requester)
        db.add(ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                             step_idx=0, action="return", actor_id=manager.id,
                             actor_role="dept_manager", comment="nonono"))
        await db.commit()
        db.add(ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                             step_idx=0, action="return", actor_id=manager.id,
                             actor_role="dept_manager", comment="Just for test"))
        await db.commit()
        await _send(db, Task(
            type="revise_pr", document_type="pr", document_id=pr.id, document_number=pr.number,
            assigned_role="requester", assigned_user_id=requester.id, title="Revise PR"))

    [(_, _, body)] = _mine(mails, requester)
    assert "<b>Comment:</b> Just for test" in body and "nonono" not in body


async def test_returned_signoff_email_shows_the_comment(mails):
    """Same gap on po_signoff_returned (revise_po_signoff, document_type posign)."""
    async with session_module.AsyncSessionLocal() as db:
        submitter = await _user(db, "Sam Submitter")
        signer = await _user(db, "Opal OPM", role="opm")
        po_id = uuid.uuid4()
        db.add(ApprovalEvent(document_type="posign", document_id=po_id, document_number="PO-SIGN-1",
                             step_idx=1, action="return", actor_id=signer.id,
                             actor_role="opm", comment="Wrong vendor"))
        await _send(db, Task(
            type="revise_po_signoff", document_type="posign", document_id=po_id,
            document_number="PO-SIGN-1", assigned_role="requester",
            assigned_user_id=submitter.id, title="Revise PO sign-off: PO-SIGN-1"))

    [(_, _, body)] = _mine(mails, submitter)
    assert "<b>Comment:</b> Wrong vendor" in body and "{comment}" not in body
