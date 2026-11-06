"""Task notification outbox — the one place a new task turns into an email.

Tasks are written by four services (approval-api, vms-api, finance-api and
this one) straight into the shared tasks table. Each used to be responsible for
mailing its own tasks afterwards, and most never did: before this existed only
PR / PO / GR / invoice tasks were emailed, while PA, agreement, expense-claim,
budget-plan and VMS approvers got nothing (prod, 2026-10-08).

Every task is now born with notified_at = NULL. This loop picks those rows up,
applies the admin's per-task-type policy (services/task_notify_policy.py) and
stamps them:
  * email ON + "immediate" → sent now, through the same dispatcher as before
    (recipient resolution, delegation, shared mailboxes, personal opt-out);
  * email ON + "digest"    → stamped; the daily digest lists it while open;
  * email OFF              → stamped, nothing sent.
Setting notified_at back to NULL (notification.queue_task_notification) asks
for one more notification — how reassignment re-notifies.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified

from app.crud.config import get_or_create as get_config
from app.db import session as session_module
from app.models.task import Task
from app.services.notification import dispatch_task_notification
from app.services.task_notify_policy import effective_policy

logger = logging.getLogger(__name__)

POLL_SECONDS = 30
BATCH = 50
# A row must have sat still this long before it is picked up: the call site may
# still be adding notify_vars / settling the assignee in the same request.
SETTLE = timedelta(seconds=15)
STARTED_KEY = "task_notifier_started_at"


async def _adopt_backlog_once(db) -> None:
    """First run ever: stamp whatever is still un-notified without sending.

    The migration stamped every row that existed then, but the old containers
    keep creating (and mailing) tasks until they are replaced — those must not
    be mailed a second time by this loop. Runs once; the marker lives in
    notification_settings and the generic config save cannot overwrite it.
    """
    cfg = await get_config(db)
    ns = dict(cfg.notification_settings or {})
    if ns.get(STARTED_KEY):
        return
    now = datetime.now(timezone.utc)
    result = await db.execute(
        text("UPDATE tasks SET notified_at = :now WHERE notified_at IS NULL"),
        {"now": now},
    )
    ns[STARTED_KEY] = now.isoformat()
    cfg.notification_settings = ns
    flag_modified(cfg, "notification_settings")
    await db.commit()
    logger.info("Task notifier: first start — %d pre-existing task(s) stamped, none sent",
                result.rowcount or 0)


async def run_once() -> int:
    """Handle one batch. Returns how many tasks were stamped."""
    async with session_module.AsyncSessionLocal() as db:
        await _adopt_backlog_once(db)
        cfg = await get_config(db)
        ns = cfg.notification_settings or {}
        cutoff = datetime.now(timezone.utc) - SETTLE
        tasks = (await db.execute(
            select(Task)
            .where(Task.notified_at.is_(None), Task.updated_at < cutoff)
            .order_by(Task.created_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )).scalars().all()
        for task in tasks:
            email, mode = effective_policy(ns, task.type)
            if email and mode == "immediate" and not task.is_completed:
                # Never raises — failures land in notification_logs.
                await dispatch_task_notification(task, db, extra_vars=task.notify_vars or {})
            task.notified_at = datetime.now(timezone.utc)
        await db.commit()
        return len(tasks)


async def task_notifier_loop() -> None:
    """Started from app/main.py lifespan; cancelled on shutdown."""
    logger.info("Task notifier: started (poll=%ds)", POLL_SECONDS)
    while True:
        try:
            handled = await run_once()
            if handled == BATCH:
                continue  # backlog — go again without waiting
        except Exception as exc:  # noqa: BLE001 — one bad batch must not kill the loop
            logger.error("Task notifier error: %s", exc)
        await asyncio.sleep(POLL_SECONDS)
