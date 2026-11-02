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
from app.integrations.email import SmtpEmail
from app.integrations.shopify import HttpShopify
from app.models.documents import Run
from app.models.records import OutboxRecord
from app.platform.jobs import Background
from app.platform.outbox import Outbox, OutboxWorker
from app.services.outbound import SEND_940, Outbound940
from app.services import oplog
from app.services.cycle import SalesCycle, settings_schedule
from app.services.edi_intake import EdiIntake
from app.services.intake import Intake
from app.services.shadow import ShadowRun
from app.services.invoicing import SEND_INVOICE, Invoicing
from app.services.transfers import SEND_943, Transfers
from app.services.shipments import CLOSE, FULFILL, Shipments
from app.services.shopify_intake import ShopifyIntake
from app.services.problems import task_service


@dataclass
class Runtime:
    container: Container
    uow_factory: Any
    intake: Intake
    shopify_intake: ShopifyIntake
    edi_intake: EdiIntake
    cycle: SalesCycle
    background: Background
    outbound: Outbound940 | None = None
    shipments: Shipments | None = None
    invoicing: Invoicing | None = None
    transfers: Transfers | None = None


def build_runtime(settings: Any, uow_factory: Any, *, sftp: Any = None, shopify: Any = None) -> Runtime:
    async def refusal_sink(r: RefusalRecord) -> None:  # the "would have written" row (§5.5)
        await oplog.write_alone(uow_factory, "WARNING", "guard", r.message, context=r.context)

    container = build_container(
        settings,
        real_sftp=lambda: AsyncsshSftp(allowed_hosts=settings.sftp_allowed_hosts, secret=os.environ.get),
        real_shopify=lambda: HttpShopify(secret=os.environ.get),
        real_email=lambda: SmtpEmail(
            host=getattr(settings, "SALES_SMTP_HOST", ""), port=getattr(settings, "SALES_SMTP_PORT", 587),
            username=getattr(settings, "SALES_SMTP_USER", "") or None,
            password=lambda: os.environ.get("SALES_SECRET_SMTP"), sender=getattr(settings, "SALES_SMTP_FROM", ""),
            starttls=getattr(settings, "SALES_SMTP_STARTTLS", True)),
        refusal_sink=refusal_sink,
    )
    if sftp is not None:
        container.sftp = sftp
    if shopify is not None:
        container.shopify = shopify
    tasks = task_service(container.notify_tasks)
    intake = Intake(uow_factory=uow_factory, sftp=container.sftp, tasks=tasks)
    shopify_intake = ShopifyIntake(uow_factory=uow_factory, shopify=container.shopify, tasks=tasks)
    edi_intake = EdiIntake(uow_factory=uow_factory, tasks=tasks)
    outbound = Outbound940(uow_factory=uow_factory, tasks=tasks, sftp=container.sftp)
    shadow = ShadowRun(uow_factory=uow_factory, outbound=outbound,
                       reference_dir=getattr(settings, "SALES_ZOHO_REFERENCE_DIR", "") or None)
    outbound.shadow = shadow
    shipments = Shipments(uow_factory=uow_factory, tasks=tasks, shopify=container.shopify, outbound=outbound, shadow=shadow)
    invoicing = Invoicing(uow_factory=uow_factory, tasks=tasks, sftp=container.sftp, email=container.email)
    transfers = Transfers(uow_factory=uow_factory, tasks=tasks, sftp=container.sftp, shadow=shadow)
    outbound.transfers = transfers
    worker = OutboxWorker(Outbox(OutboxRecord), {SEND_940: outbound.outbox_kind(), FULFILL: shipments.fulfill_kind(),
                                                 CLOSE: shipments.close_kind(), SEND_INVOICE: invoicing.outbox_kind(),
                                                 SEND_943: transfers.outbox_kind()},
                          uow_factory)
    cycle = SalesCycle(intake=intake, shopify_intake=shopify_intake, edi_intake=edi_intake, outbound=outbound,
                       shadow=shadow, shipments=shipments, invoicing=invoicing, transfers=transfers, outbox_worker=worker, run_model=Run, uow_factory=uow_factory,
                       schedule=settings_schedule(uow_factory))
    return Runtime(container, uow_factory, intake, shopify_intake, edi_intake, cycle, Background(), outbound, shipments,
                   invoicing, transfers)


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    """The app's runtime (FastAPI dependency; tests override it)."""
    global _runtime
    if _runtime is None:
        from app.core.config import settings
        from app.core.deps import uow_factory
        _runtime = build_runtime(settings, uow_factory)
    return _runtime
