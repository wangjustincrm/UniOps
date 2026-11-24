"""The file register's record actions, one at a time: read a received file again, or ignore it (stage 3 d; neither sends
anything), and send a sent file again (stage 3 k), by its owner's own permission and rules. A document has no version: its
status, and its owner's rules, refuse a repeat."""
from __future__ import annotations

from app.models.documents import Document
from app.platform.authorization import authorize
from app.platform.bulk_actions import Action, ActionRegistry, refusal
from app.services.invoicing import resend_reason
from app.services.operations import FILE_RETRY, DocumentActions, DocumentRepository, Owner


async def stuck_check(uow, d: Document) -> str | None:  # noqa: ARG001
    return None if DocumentActions._stuck(d) else f"only a received file that failed (it is {d.processing_status})"


async def read_again_run(uow, d: Document, data, ctx) -> None:  # noqa: ARG001
    await DocumentActions(uow)._run_read_again(d, ctx.runtime.tasks)


async def ignore_run(uow, d: Document, data, ctx) -> None:  # noqa: ARG001
    await DocumentActions(uow)._run_ignore(d, ctx.reason, ctx.runtime.tasks)


async def send_again_check(uow, d: Document) -> str | None:
    return await refusal(DocumentActions(uow)._owner(d))


async def send_again_prepare(uow, ids: list[int], ctx) -> dict[int, Owner]:
    """Before anything: the owner's permission (403) and, for an invoice already sent, why (422)."""
    svc, out = DocumentActions(uow), {}
    for id in ids:
        d = await uow.session.get(Document, id)
        if d is None or await send_again_check(uow, d):
            continue  # said of the record itself
        out[id] = await svc._owner(d)
        await authorize(uow, out[id].permission)
        if out[id].again:
            resend_reason(ctx.reason)
    return out


async def send_again_run(uow, d: Document, data, ctx) -> None:  # noqa: ARG001
    ctx.note = await DocumentActions(uow)._run_send_again(ctx.prepared[d.id], ctx.runtime, ctx.reason)


DOCUMENT_ACTIONS = ActionRegistry(DocumentRepository, [
    Action("read_again", label="Read again", permission=FILE_RETRY, check=stuck_check, run=read_again_run, context=True, bulk=False, versioned=False),
    Action("ignore", label="Ignore", permission=FILE_RETRY, check=stuck_check, run=ignore_run, context=True, bulk=False, versioned=False,
           reason="required"),
    Action("send_again", label="Send again", permission=FILE_RETRY, check=send_again_check, prepare=send_again_prepare, run=send_again_run,
           context=True, bulk=False, versioned=False, lock=False, reason="optional",
           sends="the 940, the 943 or the invoice of its order or transfer"),
])
