"""
Budget threshold alert emails (ISS-0146 ③, BUDGET.md BCFG-004 / BUD-OI-02).

Finance → Budget Config's thresholds (budget_admin_config.yellow_threshold_pct /
red_threshold_pct, default 80 / 100) promised "Daily email digest sent to Dept.
Manager" at the warning level and "Immediate email + Teams alert to Dept.
Manager, Finance Manager, and GM/OPM" at the over-budget level; nothing sent
either. Rule question 16 was answered B — build it as described — so:
- warning → the daily digest, by email;
- over budget → checked every hour (NC actuals change only when Finance syncs
  NC vouchers, hourly by default — that is as immediate as the data is), by
  email, plus one Teams card on the company's Teams webhook when a recipient
  has chosen Teams (My Profile → notification channel) — the same webhook and
  channel rule as task notifications.

What is measured — the Budget Dashboard's own alert (BDASH-003): per cost
center × budget account, NC year-to-date actual (vouchers not yet tallied
included, the dashboard's default) ÷ the approved annual budget. Committed is
not counted (nothing books it — BACT-003). Spend with no budget counts as over.

Who gets what:
- once a day at followup_time, a line that crossed UP into the warning level →
  one digest per Department Manager listing their lines;
- within the hour, a line that crossed UP into the over-budget level → one
  email per person to the Department Manager, every Finance Manager and the
  department's GM or OPM (company_config.dept_gm_opm_mapping);
- a department with no Department Manager → the Finance Manager(s) instead
  (developer's choice — the page names no one else).

Each crossing is emailed once (budget_alert_states); when usage falls back the
mark is lowered, so a later crossing alerts again.

Gates, as for the other sweeps (developer's choice, so a release does not start
emailing on its own): notification_settings.budget_alert_enabled (default off)
and budget_alert_dry_run (default on: once a day System Admins get the list,
nobody else is emailed and nothing is marked; the hourly check stays quiet). Budget figures are read from
budget-api as the system account (no user is signed in), which sees every cost
center. Single-instance assumption, like the other sweeps.
"""
from __future__ import annotations

import asyncio
import html
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.config import get_or_create as get_config
from app.db import session as session_module
from app.models.budget_alert_state import BudgetAlertState
from app.models.department import Department
from app.models.user import User
from app.services.notification import _build_email_html, _smtp_kwargs, send_admin_alert
from app.tasks.daily_followup import (
    _RECHECK_SECONDS,
    _load_schedule,
    _seconds_until_next_run,
)

logger = logging.getLogger(__name__)

WARNING, OVER = "warning", "over"
_RANK = {None: 0, WARNING: 1, OVER: 2}
SERVICE_ACCOUNT_EMAIL = "nc-sync@epms.local"


@dataclass
class Line:
    cost_center_id: uuid.UUID
    cost_center: str
    department_id: uuid.UUID | None
    account_id: uuid.UUID
    account: str
    budget: Decimal
    actual: Decimal
    used_pct: Decimal | None       # None = spend with no budget
    level: str | None


@dataclass
class Crossing:
    line: Line
    level: str


@dataclass
class Plan:
    crossings: list[Crossing] = field(default_factory=list)
    marks: list[tuple[Line, str | None]] = field(default_factory=list)   # (line, new level)


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def level_of(budget: Decimal, used: Decimal, yellow: Decimal, red: Decimal) -> str | None:
    if budget <= 0:
        return OVER if used > 0 else None
    if used * 100 >= budget * red:
        return OVER
    if used * 100 >= budget * yellow:
        return WARNING
    return None


async def _service_token(db: AsyncSession) -> str | None:
    """A short-lived token for the system account, so budget-api / finance-api
    answer for every cost center. Prefers the NC sync service user."""
    from app.core.security import create_access_token

    user = (await db.execute(select(User).where(
        User.email == SERVICE_ACCOUNT_EMAIL, User.is_active.is_(True)))).scalar_one_or_none()
    if user is None or user.role != "system_admin":
        user = (await db.execute(select(User).where(
            User.role == "system_admin", User.is_active.is_(True))
            .order_by(User.created_at).limit(1))).scalar_one_or_none()
    return create_access_token(str(user.id), "system_admin") if user else None


async def read_lines(token: str, fiscal_year: int, yellow: Decimal, red: Decimal) -> list[Line]:
    """Every visible cost center × budget account with a budget or spend."""
    from app.services import budget_client

    scope = await budget_client.get_actuals_scope(token)
    ccs = [cc for cc in ((scope or {}).get("cost_centers") or []) if cc.get("id")]
    sem = asyncio.Semaphore(6)

    async def _one(cc):
        async with sem:
            data = await budget_client.get_actuals_summary(
                token, fiscal_year, uuid.UUID(cc["id"]), include_unposted=True)
        return cc, (data or {}).get("accounts") or []

    lines: list[Line] = []
    for cc, accounts in await asyncio.gather(*(_one(cc) for cc in ccs)):
        dept = cc.get("department_id")
        for a in accounts:
            budget, used = _d(a.get("annual_budget")), _d(a.get("committed")) + _d(a.get("actual_spent"))
            if budget <= 0 and used <= 0:
                continue
            lines.append(Line(
                cost_center_id=uuid.UUID(cc["id"]),
                cost_center=f"{cc.get('code') or ''} {cc.get('name') or ''}".strip(),
                department_id=uuid.UUID(dept) if dept else None,
                account_id=uuid.UUID(str(a["account_id"])),
                account=f"{a.get('account_code') or ''} {a.get('account_name') or ''}".strip(),
                budget=budget, actual=used,
                used_pct=(used * 100 / budget).quantize(Decimal("0.1")) if budget > 0 else None,
                level=level_of(budget, used, yellow, red),
            ))
    return lines


async def plan_alerts(db: AsyncSession, lines: list[Line], fiscal_year: int) -> Plan:
    """Compare each line with the level last alerted: a rise is a crossing to
    email; any change moves the mark."""
    marks = {(s.cost_center_id, s.account_id): s.level for s in (await db.execute(
        select(BudgetAlertState).where(BudgetAlertState.fiscal_year == fiscal_year))).scalars()}
    plan = Plan()
    for line in lines:
        before = marks.get((line.cost_center_id, line.account_id))
        if _RANK[line.level] > _RANK[before]:
            plan.crossings.append(Crossing(line, line.level))
        if line.level != before:
            plan.marks.append((line, line.level))
    return plan


async def _apply_marks(db: AsyncSession, plan: Plan, fiscal_year: int) -> None:
    now = datetime.now(timezone.utc)
    for line, level in plan.marks:
        row = (await db.execute(select(BudgetAlertState).where(
            BudgetAlertState.cost_center_id == line.cost_center_id,
            BudgetAlertState.account_id == line.account_id,
            BudgetAlertState.fiscal_year == fiscal_year))).scalar_one_or_none()
        if level is None:
            if row is not None:
                await db.delete(row)
            continue
        if row is None:
            db.add(BudgetAlertState(cost_center_id=line.cost_center_id, account_id=line.account_id,
                                    fiscal_year=fiscal_year, level=level,
                                    used_pct=line.used_pct, alerted_at=now))
        else:
            row.level, row.used_pct, row.alerted_at = level, line.used_pct, now


async def _recipients(db: AsyncSession, cfg, crossings: list[Crossing]) -> dict[uuid.UUID, list[Crossing]]:
    """user id → the crossings that person is told about."""
    from app.core.access_scope import role_holder_ids

    holders = await role_holder_ids(db, ("dept_manager", "finance_manager", "gm", "opm"))
    fms = holders.get("finance_manager", set())
    managers_by_dept: dict[uuid.UUID, set[uuid.UUID]] = {}
    dm_ids = holders.get("dept_manager", set())
    if dm_ids:
        for uid, dept in (await db.execute(select(User.id, User.department_id).where(
                User.id.in_(dm_ids), User.is_active.is_(True)))).all():
            if dept is not None:
                managers_by_dept.setdefault(dept, set()).add(uid)
    gm_opm = cfg.dept_gm_opm_mapping or {}

    out: dict[uuid.UUID, list[Crossing]] = {}
    for c in crossings:
        dept = c.line.department_id
        dms = managers_by_dept.get(dept, set()) if dept else set()
        to: set[uuid.UUID] = set(dms) if dms else set(fms)    # no Dept Manager → FM
        if c.level == OVER:
            to |= fms
            post = gm_opm.get(str(dept)) if dept else None
            if post in ("gm", "opm"):
                to |= holders.get(post, set())
        for uid in to:
            out.setdefault(uid, []).append(c)
    return out


def _table(crossings: list[Crossing], dept_names: dict) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(dept_names.get(c.line.department_id, '—'))}</td>"
        f"<td>{html.escape(c.line.cost_center)}</td>"
        f"<td>{html.escape(c.line.account)}</td>"
        f"<td style='text-align:right'>{c.line.budget:,.2f}</td>"
        f"<td style='text-align:right'>{c.line.actual:,.2f}</td>"
        f"<td style='text-align:right'>{'no budget' if c.line.used_pct is None else f'{c.line.used_pct}%'}</td>"
        "</tr>"
        for c in sorted(crossings, key=lambda c: (c.line.cost_center, c.line.account))
    )
    return ("<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse'>"
            "<tr><th>Department</th><th>Cost center</th><th>Account</th><th>Annual budget</th>"
            "<th>Actual (NC, incl. unposted)</th><th>Used</th></tr>" + rows + "</table>")


def _teams_text(crossings: list[Crossing], dept_names: dict, names: list[str]) -> str:
    lines = [
        f"- {dept_names.get(c.line.department_id, '—')} · {c.line.cost_center} · {c.line.account}: "
        f"{c.line.actual:,.2f} of {c.line.budget:,.2f} "
        f"({'no budget' if c.line.used_pct is None else f'{c.line.used_pct}%'})"
        for c in sorted(crossings, key=lambda c: (c.line.cost_center, c.line.account))
    ]
    return "\n\n".join(lines + [f"For: {', '.join(sorted(names))}"])


async def _send_teams(cfg, users: dict, by_user: dict[uuid.UUID, list[Crossing]],
                      dept_names: dict, fiscal_year: int, red) -> bool:
    """One card for the over-budget lines, when at least one of their
    recipients has chosen Teams and has a Teams account (the rule
    notification.py applies to task notifications). The webhook posts to one
    company channel, so one card names everyone it is for."""
    from app.services.teams import send_teams_card

    webhook = (cfg.notification_settings or {}).get("teams_webhook_url")
    if not webhook:
        return False
    over: dict = {}
    names: list[str] = []
    for uid, crossings in by_user.items():
        user = users.get(uid)
        if (user is None or not user.is_active or not user.teams_account
                or user.notification_channel not in ("teams_only", "both")):
            continue
        mine = [c for c in crossings if c.level == OVER]
        if not mine:
            continue
        names.append(user.full_name or user.email)
        for c in mine:
            over[(c.line.cost_center_id, c.line.account_id)] = c
    if not over:
        return False
    try:
        await send_teams_card(
            webhook, f"Budget alert: {len(over)} line(s) reached {red}% in FY{fiscal_year}",
            _teams_text(list(over.values()), dept_names, names))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Budget alerts: Teams card failed: %s", exc)
        return False


async def _send(db: AsyncSession, cfg, by_user: dict[uuid.UUID, list[Crossing]],
                dept_names: dict, fiscal_year: int, yellow, red) -> int:
    from app.services.email import send_email

    if (cfg.notification_settings or {}).get("default_channel") == "none":
        return 0
    users = {u.id: u for u in (await db.execute(
        select(User).where(User.id.in_(list(by_user))))).scalars()} if by_user else {}
    sent = 0
    for uid, crossings in by_user.items():
        user = users.get(uid)
        if user is None or not user.is_active or not user.email:
            continue
        if user.notification_channel in ("none", "teams_only"):
            continue
        over = [c for c in crossings if c.level == OVER]
        warn = [c for c in crossings if c.level == WARNING]
        parts = [f"<p>Hi {html.escape(user.full_name or user.email)},</p>"]
        if over:
            parts.append(f"<p><b>{len(over)} budget line(s) reached the over-budget threshold "
                         f"({red}%) in FY{fiscal_year}:</b></p>" + _table(over, dept_names))
        if warn:
            parts.append(f"<p><b>{len(warn)} budget line(s) reached the warning threshold "
                         f"({yellow}%) in FY{fiscal_year}:</b></p>" + _table(warn, dept_names))
        parts.append("<p>Used = NC year-to-date actual (vouchers not yet tallied included) ÷ "
                     "the approved annual budget, as on Finance → Budget Dashboard. You are "
                     "told once per line when it crosses a threshold.</p>"
                     f"<p>{html.escape(cfg.name or 'UniOps')}</p>")
        subject = (f"Budget alert: {len(over)} line(s) over budget" if over
                   else f"Budget warning: {len(warn)} line(s) at {yellow}% or more")
        try:
            await send_email(user.email, subject, _build_email_html("".join(parts)), **_smtp_kwargs(cfg))
            sent += 1
        except Exception as exc:  # noqa: BLE001 — one bad address must not stop the rest
            logger.error("Budget alerts: email to %s failed: %s", user.email, exc)
    await _send_teams(cfg, users, by_user, dept_names, fiscal_year, red)
    return sent


async def run_budget_alerts(over_only: bool = False) -> None:
    """The daily run reports every crossing; the hourly run (over_only) only
    the crossings into over budget, and marks only those, so a warning still
    reaches the daily digest."""
    logger.info("Budget alerts: starting %s sweep", "hourly over-budget" if over_only else "daily")
    try:
        async with session_module.AsyncSessionLocal() as db:
            cfg = await get_config(db)
            ns = cfg.notification_settings or {}
            if not ns.get("budget_alert_enabled", False):
                logger.info("Budget alerts: disabled by admin toggle — skipping run")
                await db.commit()
                return
            if over_only and ns.get("budget_alert_dry_run", True):
                await db.commit()       # the dry-run list goes out once a day
                return
            bac = cfg.budget_admin_config or {}
            yellow = Decimal(str(bac.get("yellow_threshold_pct", 80)))
            red = Decimal(str(bac.get("red_threshold_pct", 100)))
            fiscal_year = datetime.now(timezone.utc).year
            token = await _service_token(db)
            if token is None:
                logger.error("Budget alerts: no active system_admin account to read budgets as")
                await db.commit()
                return

            lines = await read_lines(token, fiscal_year, yellow, red)
            plan = await plan_alerts(db, lines, fiscal_year)
            if over_only:
                plan = Plan(crossings=[c for c in plan.crossings if c.level == OVER],
                            marks=[(ln, lv) for ln, lv in plan.marks if lv == OVER])
            dept_names = {d.id: d.name for d in (await db.execute(select(Department))).scalars()}
            logger.info("Budget alerts: %d line(s) read, %d new crossing(s)", len(lines), len(plan.crossings))

            if ns.get("budget_alert_dry_run", True):
                if plan.crossings:
                    await send_admin_alert(
                        f"[Dry run] {len(plan.crossings)} budget line(s) crossed a threshold",
                        "<p>The budget alert emails are in <b>dry-run</b> mode: nobody else was "
                        "emailed and nothing was marked as alerted. These lines would have been "
                        "reported:</p>"
                        + _table(plan.crossings, dept_names)
                        + "<p>Turn off <b>Budget Threshold Alerts — Dry Run</b> in Portal → Admin → "
                          "Notification Settings once this list looks right.</p>",
                        db,
                    )
                await db.commit()
                return

            # Mark first and commit, then send — the marks are data correctness and
            # must not sit in an open transaction across SMTP round-trips.
            await _apply_marks(db, plan, fiscal_year)
            await db.commit()
            if plan.crossings:
                by_user = await _recipients(db, cfg, plan.crossings)
                sent = await _send(db, cfg, by_user, dept_names, fiscal_year, yellow, red)
                logger.info("Budget alerts: %d email(s) sent", sent)
        logger.info("Budget alerts: sweep finished")
    except Exception as exc:  # noqa: BLE001 — one failure must not kill the loop
        logger.error("Budget alerts: sweep failed: %s", exc)


OVER_BUDGET_CHECK_SECONDS = 3600


async def budget_over_alerts_loop() -> None:
    """Over budget is "immediate": checked every hour, the default NC voucher
    sync interval — the actuals cannot change faster than that."""
    while True:
        await asyncio.sleep(OVER_BUDGET_CHECK_SECONDS)
        await run_budget_alerts(over_only=True)


async def budget_alerts_loop() -> None:
    """Same loop shape as the other daily sweeps."""
    while True:
        hour, minute = await _load_schedule()
        wait = _seconds_until_next_run(hour, minute)
        if wait > _RECHECK_SECONDS:
            await asyncio.sleep(_RECHECK_SECONDS)
            continue
        logger.info("Budget alerts: next run in %.0f seconds (at %02d:%02dZ)", wait, hour, minute)
        await asyncio.sleep(wait)
        await run_budget_alerts()
