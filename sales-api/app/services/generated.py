"""What a file just generated came to (stage 3, review of l): queued — or it failed its checks, nothing was queued, and whoever
pressed is told so. One place, for a 940 and a 943, wherever the press was."""
from __future__ import annotations

from typing import Any

from app.domain.status import OutboundFile
from app.models.documents import Document


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
