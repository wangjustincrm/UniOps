"""What a file just generated came to (stage 3, review of l): queued — or it failed its checks, nothing was queued, and whoever
pressed is told so. One place, for a 940 and a 943, wherever the press was.

And a file that would take a live one's place and fails its checks (user 2026-11-25): nothing is replaced. The old file, the
record, its lines and its tasks stay as they were (FileRefused: the attempt is undone), the person is told on the spot with what
they typed, and the attempt is kept in the log and the audit — who, why, and what was wrong."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sql

from app.domain.status import OutboundFile
from app.models.documents import Document
from app.platform.audit import audit_act
from app.platform.errors import BusinessError
from app.services import oplog

log = logging.getLogger(__name__)


class FileRefused(BusinessError):
    """The new 940 / 943 failed its checks, and a live one would have been replaced by it."""
    status_code = 422

    def __init__(self, what: str, problems: list[str], lines: Any = None) -> None:
        super().__init__("file.failed_checks", f"the new {what} failed its checks, so nothing was changed and the {what} that went stays: "
                         + "; ".join(problems), {"problems": problems})
        self.what, self.problems, self.lines = what, problems, lines


async def keep_attempt(uow_factory: Any, uow: Any, record: tuple[type, int], act: str, why: str | None, e: FileRefused, *, step: str,
                       label: str, context: dict[str, Any] | None = None, **ids: int) -> None:
    """The refused attempt, in a unit of its own (its own was undone), as the person: a log row and an audit row of the record
    (`act`, e.g. "amend_refused"). No document, no task, no email; the record's version does not move. It never changes the answer."""
    try:
        async with uow_factory(uow.actor) as u:
            # The request still holds the record's lock: this unit only reads it, and waits for nothing longer than this.
            await u.session.execute(sql("SET LOCAL lock_timeout = '2s'"))
            text = f"{label}: {act.replace('_', ' ')}, the new {e.what} failed its checks ({'; '.join(e.problems)}); nothing changed" + (f": {why}" if why else "")
            u.set_reason(text)
            audit_act(u.session, act, await u.session.get(record[0], record[1]))
            await oplog.write(u.session, "WARNING", step, text, context={"problems": e.problems, "lines": e.lines, "why": why, **(context or {})}, **ids)
            await u.commit()
    except Exception:  # noqa: BLE001 — the person is told what was wrong all the same
        log.exception("could not keep the refused attempt: %s %s", act, record)


def came_to(doc: Document, ctx: Any = None) -> tuple[str | None, str | None]:
    """(what to say, what went wrong): one of the two. With a record action's context, said there."""
    what = doc.kind.split("_")[0]  # 940 | 943
    if doc.file_status == OutboundFile.READY_TO_SEND.value:
        note, problem = f"{what} {doc.st02} queued", None
    else:
        why = doc.error or "; ".join((doc.body_json or {}).get("errors", []))
        note, problem = None, f"the {what} failed its checks and was not sent" + (f": {why}" if why else "")
    if ctx is not None:
        ctx.note, ctx.problem = note, problem
    return note, problem
