"""The running module's long-lived objects, built once (design §13.14): the
gateways, the intake, the scheduled cycle and the background-task holder.
The API reaches them through `get_runtime` (overridden in tests); the
lifespan starts the cycle's loop only when SALES_SCHEDULER_ENABLED is true.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from app.container import Container, RefusalRecord, build_container
from app.integrations.sftp import AsyncsshSftp
from app.models.documents import Run
from app.models.records import OutboxRecord
from app.platform.jobs import Background
from app.platform.outbox import Outbox, OutboxWorker
from app.services import oplog
from app.services.cycle import SalesCycle, settings_schedule
from app.services.intake import Intake
from app.services.problems import task_service


@dataclass
class Runtime:
    container: Container
    uow_factory: Any
    intake: Intake
    cycle: SalesCycle
    background: Background


def build_runtime(settings: Any, uow_factory: Any, *, sftp: Any = None) -> Runtime:
    async def refusal_sink(r: RefusalRecord) -> None:  # the "would have written" row (§5.5)
        await oplog.write_alone(uow_factory, "WARNING", "guard", r.message, context=r.context)

    container = build_container(
        settings,
        real_sftp=lambda: AsyncsshSftp(allowed_hosts=settings.sftp_allowed_hosts, secret=os.environ.get),
        refusal_sink=refusal_sink,
    )
    if sftp is not None:
        container.sftp = sftp
    intake = Intake(uow_factory=uow_factory, sftp=container.sftp, tasks=task_service(container.notify_tasks))
    worker = OutboxWorker(Outbox(OutboxRecord), {}, uow_factory)  # no outbox kinds before P4
    cycle = SalesCycle(intake=intake, outbox_worker=worker, run_model=Run, uow_factory=uow_factory,
                       schedule=settings_schedule(uow_factory))
    return Runtime(container, uow_factory, intake, cycle, Background())


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    """The app's runtime (FastAPI dependency; tests override it)."""
    global _runtime
    if _runtime is None:
        from app.core.config import settings
        from app.core.deps import uow_factory
        _runtime = build_runtime(settings, uow_factory)
    return _runtime
