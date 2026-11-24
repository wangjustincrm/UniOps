"""The file register's record actions (stage 3 d): read a received file again, or ignore it — neither sends anything. A
document has no version: its status refuses a repeat. Sending a file again is not here."""
from __future__ import annotations

from app.models.documents import Document
from app.platform.bulk_actions import Action, ActionRegistry
from app.services.operations import FILE_RETRY, DocumentActions, DocumentRepository


async def stuck_check(uow, d: Document) -> str | None:  # noqa: ARG001
    return None if DocumentActions._stuck(d) else f"only a received file that failed (it is {d.processing_status})"


async def read_again_run(uow, d: Document, data, ctx) -> None:  # noqa: ARG001
    await DocumentActions(uow)._run_read_again(d, ctx.runtime.tasks)


async def ignore_run(uow, d: Document, data, ctx) -> None:  # noqa: ARG001
    await DocumentActions(uow)._run_ignore(d, ctx.reason, ctx.runtime.tasks)


DOCUMENT_ACTIONS = ActionRegistry(DocumentRepository, [
    Action("read_again", label="Read again", permission=FILE_RETRY, check=stuck_check, run=read_again_run, context=True, bulk=False, versioned=False),
    Action("ignore", label="Ignore", permission=FILE_RETRY, check=stuck_check, run=ignore_run, context=True, bulk=False, versioned=False,
           reason="required"),
])
