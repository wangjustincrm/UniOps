"""A send that is still waiting or whose outcome is not known is never made twice (stage 3 g–k, user 2026-11-24).

One rule for every sending action, read under the record's own lock: its outbox rows of these kinds.
- PENDING: it waits for the next run of the system job; IN_FLIGHT: it is being sent.
- IN_FLIGHT whose lease has passed (a crash): it may have gone out, so it still blocks — and says so and how it ends: the
  worker's next pass closes it as FAILED ("outcome unknown"), which no longer blocks.
- DONE, FAILED, SUPPRESSED, CANCELLED: do not block; nor does another kind (the company's copy of an invoice)."""
from __future__ import annotations

from typing import Any

from app.models.records import OutboxRecord
from app.platform.errors import BusinessError
from app.platform.outbox import Outbox

OUTBOX = Outbox(OutboxRecord)
NEXT = 'the next run of the system job ("Run the system job now" on Health, for who may retry files: sales.file.retry)'


def unknown_outcome(what: str, then: str) -> str:
    return (f"{what} was started and its outcome was never recorded: it may have gone out. "
            f"{NEXT[0].upper()}{NEXT[1:]} closes it; then {then}")


async def queued_refusal(s: Any, kinds: tuple[str, ...], entity: Any, what: str) -> BusinessError | None:
    state = await OUTBOX.waiting(s, kinds, entity)
    if state is None:
        return None
    if state == "unknown":
        return BusinessError("send.outcome_unknown", unknown_outcome(what, "check whether it arrived before sending it again"), {"state": state})
    text = f"{what} is being sent now: look again in a moment" if state == "sending" else f"{what} is already queued: it goes with {NEXT}"
    return BusinessError("send.queued", text, {"state": state})


async def refuse_queued(s: Any, kinds: tuple[str, ...], entity: Any, what: str) -> None:
    if e := await queued_refusal(s, kinds, entity, what):
        raise e
