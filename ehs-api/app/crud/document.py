"""The policy library, and who has read what.

The valuable half of Read & Sign is the list of people who have *not* signed,
so `outstanding` is written first and `acknowledge` second. It is computed
against the live population — everyone active, or everyone in the named roles —
rather than against a snapshot taken at publication, because somebody who
joined last week has not read the policy either.

Republishing is supersession, not editing: v3 is a new row that retires v2,
both stay, and an acknowledgement keeps the version it was given for. Somebody
who signed v2 shows as outstanding on v3, which is the correct answer and the
reason `version` is on the acknowledgement.
"""
from __future__ import annotations

import calendar
import secrets
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import false as sa_false
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentAcknowledgement, SafetyDocument
from app.models.mirrors import User
from app.services import deadlines as deadline_service
from app.services import notify
from app.services import tasks as task_service
from app.services.numbering import next_number

DOC_DOCUMENT = task_service.DOC_DOCUMENT


def _new_qr_token() -> str:
    """Unguessable, because it is printed on a label anyone can photograph."""
    return secrets.token_urlsafe(24)


async def list_documents(
    db: AsyncSession, *, category: str | None = None, status_in: list[str] | None = None,
    limit: int = 200,
) -> list[SafetyDocument]:
    stmt = select(SafetyDocument)
    if category:
        stmt = stmt.where(SafetyDocument.category == category)
    if status_in:
        stmt = stmt.where(SafetyDocument.status.in_(status_in))
    return list((await db.execute(
        stmt.order_by(SafetyDocument.category, SafetyDocument.title).limit(limit))).scalars())


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> SafetyDocument:
    doc = await db.get(SafetyDocument, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


async def by_qr_token(db: AsyncSession, token: str) -> SafetyDocument | None:
    return (await db.execute(
        select(SafetyDocument).where(SafetyDocument.qr_token == token))).scalar_one_or_none()


async def create_document(db: AsyncSession, payload: dict, *, owner_id: uuid.UUID) -> SafetyDocument:
    owner_name = (await db.execute(
        select(User.full_name).where(User.id == owner_id))).scalar()
    doc = SafetyDocument(
        id=uuid.uuid4(),
        doc_no=await next_number(db, "document"),
        title=payload["title"],
        category=payload.get("category", "policy"),
        summary=payload.get("summary"),
        version=payload.get("version", "1.0"),
        file_id=payload.get("file_id"),
        filename=payload.get("filename"),
        effective_on=payload.get("effective_on"),
        review_months=payload.get("review_months"),
        requires_ack=payload.get("requires_ack", False),
        ack_roles=payload.get("ack_roles") or [],
        ack_user_ids=[str(u) for u in (payload.get("ack_user_ids") or [])],
        qr_token=_new_qr_token(),
        owner_id=payload.get("owner_id") or owner_id,
        owner_name=owner_name,
    )
    db.add(doc)
    await db.flush()
    return doc


def _review_due(effective_on: date, months: int | None) -> date | None:
    """The next review date. Months, so a three-yearly SDS review fits too."""
    if not months:
        return None
    year = effective_on.year + (effective_on.month - 1 + months) // 12
    month = (effective_on.month - 1 + months) % 12 + 1
    # Clamp rather than overflow: a policy effective on 31 August reviewed in
    # six months is due 28 February, not 3 March.
    return date(year, month, min(effective_on.day, calendar.monthrange(year, month)[1]))


async def publish(
    db: AsyncSession, doc: SafetyDocument, *, user_id: uuid.UUID,
) -> SafetyDocument:
    """Make it the current version, start its review clock, and ask for signatures."""
    if doc.file_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Attach the document before publishing it — there is nothing for "
            "anyone to read")
    if doc.status == "published":
        return doc

    now = datetime.now(timezone.utc)
    doc.status = "published"
    doc.published_at = now
    doc.published_by = user_id
    doc.published_by_name = (await db.execute(
        select(User.full_name).where(User.id == user_id))).scalar()
    doc.effective_on = doc.effective_on or now.date()
    doc.review_due_on = _review_due(doc.effective_on, doc.review_months)

    # Retire whatever this replaces, in the same transaction, so there is never
    # a moment when two versions are both current.
    #
    # ★ Round 5, TC-22: "policy documents that are Superseded by a later version
    # still show up in the My actions list to sign and yet when it is opened
    # there is no way to sign the superseeded document."
    #
    # This set the column and nothing else, while `retire()` three screens away
    # also closed the outstanding read-and-sign tasks. Two places retiring a
    # document, one of them doing half the job — so everybody who had not yet
    # signed v1 kept a task pointing at a version they are no longer allowed to
    # sign, *and* got a new task for v2. Through the one function now.
    if doc.supersedes_id:
        previous = await db.get(SafetyDocument, doc.supersedes_id)
        if previous is not None and previous.status == "published":
            await retire(db, previous)

    await db.flush()
    await deadline_service.ensure_document_review_deadline(db, doc)

    if doc.requires_ack:
        audience = await _audience(db, doc)
        await notify.document_published(db, doc, [p.id for p in audience])
        for person in audience:
            await task_service.open_task(
                db,
                task_type=task_service.TASK_READ_SIGN,
                doc_type=DOC_DOCUMENT,
                doc_id=doc.id,
                doc_number=doc.doc_no,
                title=f"Read and sign: {doc.title}",
                assigned_role=task_service.ROLE_ANY_EMPLOYEE,
                assigned_user_id=person.id,
                description=f"{doc.title} v{doc.version} was published and needs "
                            "your acknowledgement.",
                priority="normal",
            )
    await db.flush()
    return doc


async def new_version(
    db: AsyncSession, doc: SafetyDocument, *, version: str, owner_id: uuid.UUID,
) -> SafetyDocument:
    """Start a draft that will replace this one when it is published."""
    draft = SafetyDocument(
        id=uuid.uuid4(),
        doc_no=await next_number(db, "document"),
        title=doc.title, category=doc.category, summary=doc.summary,
        version=version,
        review_months=doc.review_months,
        requires_ack=doc.requires_ack, ack_roles=list(doc.ack_roles or []),
        # ★ The named people come across too. Without this a document sent to
        # three people and to no role became a document with no audience at
        # all on its next revision — and no audience means the plant, so one
        # correction would put it on four hundred people's lists.
        ack_user_ids=[str(u) for u in (doc.ack_user_ids or [])],
        qr_token=_new_qr_token(),
        supersedes_id=doc.id,
        owner_id=doc.owner_id or owner_id, owner_name=doc.owner_name,
    )
    db.add(draft)
    await db.flush()
    return draft


async def retire(db: AsyncSession, doc: SafetyDocument) -> SafetyDocument:
    doc.status = "retired"
    await task_service.close_tasks_for(db, doc_type=DOC_DOCUMENT, doc_id=doc.id)
    await db.flush()
    return doc


async def replacement_for(
    db: AsyncSession, doc: SafetyDocument
) -> SafetyDocument | None:
    """The version that superseded this one, if any.

    The link is stored on the newer document pointing back, so this is the
    reverse lookup. The newest wins if a document was superseded twice — that
    is the one somebody has to read.
    """
    # ★ Published only. A draft successor is not in force, and the retired page
    # offers this as "the one in force" with a link — sending somebody to sign a
    # draft would be worse than leaving them on the retired version. Raised in
    # review by uniops-41.
    return (await db.execute(
        select(SafetyDocument)
        .where(SafetyDocument.supersedes_id == doc.id,
               SafetyDocument.status == "published")
        .order_by(SafetyDocument.created_at.desc())
        .limit(1))).scalar_one_or_none()


async def _audience(db: AsyncSession, doc: SafetyDocument) -> list[User]:
    """Who has to sign this one.

    An empty `ack_roles` means everybody. A list narrows it, matched against
    the primary role *and* any additional role — a person who is a first aider
    in addition to their job is in the first-aid procedure's audience.
    """
    roles = list(doc.ack_roles or [])
    named = [uuid.UUID(str(u)) for u in (doc.ack_user_ids or [])]

    # Neither roles nor names: the plant. That is what an unqualified policy
    # means and it is the existing behaviour.
    if not roles and not named:
        return list((await db.execute(
            select(User).where(User.is_active.is_(True)))).scalars())

    # ★ The union, not the intersection. Naming somebody adds them to a role's
    # audience rather than narrowing it — the same mistake was made once on the
    # toolbox talk, where naming a supervisor filtered the list instead of
    # replacing it and a lead hand outside the role received nothing at all.
    by_role = set((await db.execute(
        text("SELECT user_id FROM user_roles WHERE role_code = ANY(:roles)"),
        {"roles": roles})).scalars().all()) if roles else set()
    reachable = by_role | set(named)

    return list((await db.execute(
        select(User).where(
            User.is_active.is_(True),
            (User.role.in_(roles) if roles else sa_false())
            | (User.id.in_(reachable) if reachable else sa_false()),
        ))).scalars())


async def acknowledged_at(
    db: AsyncSession, doc: SafetyDocument, *, user_id: uuid.UUID
) -> datetime | None:
    """When this person signed this version, if they have.

    Asked by the scanned-code screen so it can say "you signed this in August"
    rather than offering to take a signature it would then quietly discard —
    `acknowledge` is idempotent and returns the original record, so pressing the
    button would report success for something that happened months ago.
    """
    return (await db.execute(
        select(DocumentAcknowledgement.acknowledged_at).where(
            DocumentAcknowledgement.document_id == doc.id,
            DocumentAcknowledgement.user_id == user_id,
            DocumentAcknowledgement.version == doc.version,
        ))).scalar_one_or_none()


async def acknowledge(
    db: AsyncSession, doc: SafetyDocument, *, user_id: uuid.UUID, signature: str | None,
) -> DocumentAcknowledgement:
    if doc.status != "published":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{doc.doc_no} is {doc.status}; only a published version can be signed")

    existing = (await db.execute(
        select(DocumentAcknowledgement).where(
            DocumentAcknowledgement.document_id == doc.id,
            DocumentAcknowledgement.user_id == user_id,
            DocumentAcknowledgement.version == doc.version,
        ))).scalar_one_or_none()
    if existing is not None:
        return existing

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person")

    ack = DocumentAcknowledgement(
        id=uuid.uuid4(), document_id=doc.id, version=doc.version,
        user_id=user.id, user_name=user.full_name, user_role=user.role,
        signature=signature,
    )
    db.add(ack)
    # Only this person's. One signature is one person having read it, and
    # closing the rest would tell the plant they had all read something they
    # have not seen.
    await task_service.close_tasks_for(
        db, doc_type=DOC_DOCUMENT, doc_id=doc.id,
        task_type=task_service.TASK_READ_SIGN,
        assigned_user_id=user_id, completed_by=user_id)
    await db.flush()
    return ack


async def acknowledgements(db: AsyncSession, doc: SafetyDocument) -> list[DocumentAcknowledgement]:
    return list((await db.execute(
        select(DocumentAcknowledgement).where(
            DocumentAcknowledgement.document_id == doc.id,
            DocumentAcknowledgement.version == doc.version,
        ).order_by(DocumentAcknowledgement.acknowledged_at))).scalars())


async def signing_status(db: AsyncSession, doc: SafetyDocument) -> dict:
    """Signed and not signed, both by name. The second list is the useful one."""
    signed = {a.user_id: a for a in await acknowledgements(db, doc) if a.user_id}
    audience = await _audience(db, doc)
    outstanding = [
        {"user_id": str(u.id), "user_name": u.full_name}
        for u in audience if u.id not in signed
    ]
    outstanding.sort(key=lambda r: r["user_name"])
    return {
        "version": doc.version,
        "audience": len(audience),
        "signed": len(signed),
        "outstanding": outstanding,
        "percent": round(len(signed) * 100 / len(audience)) if audience else 0,
    }


async def mine_outstanding(db: AsyncSession, user_id: uuid.UUID) -> list[SafetyDocument]:
    """Published documents this person is expected to sign and has not."""
    docs = await list_documents(db, status_in=["published"])
    out: list[SafetyDocument] = []
    for doc in docs:
        if not doc.requires_ack:
            continue
        if not any(u.id == user_id for u in await _audience(db, doc)):
            continue
        signed = (await db.execute(
            select(DocumentAcknowledgement.id).where(
                DocumentAcknowledgement.document_id == doc.id,
                DocumentAcknowledgement.user_id == user_id,
                DocumentAcknowledgement.version == doc.version,
            ))).scalar()
        if signed is None:
            out.append(doc)
    return out
