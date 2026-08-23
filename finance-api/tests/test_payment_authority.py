"""Segregation of duties (2026-08-13): payment EXECUTION authority.

Pins `_check_can_pay` directly (no HTTP layer) — the same gate
`app/api/v1/payments.py::execute_payment` / `execute_batch` / `remittance.py`
all call. ap_clerk's removal is the entire point of this task: a version of
`_PAY_ROLES` that only *adds* payment_officer without removing ap_clerk would
pass every other suite in this repo silently. The negative case
(`test_ap_clerk_can_no_longer_execute_payments`) is therefore the one that
matters most here, not an afterthought alongside the additions.
"""
import uuid

import sqlalchemy as sa

from app.crud import payment_execute
from app.crud.payment_execute import PaymentPermissionError


def _user(role: str, user_id: uuid.UUID | None = None) -> dict:
    """Shape `_check_can_pay` consumes: a decoded-JWT-like dict with `role`
    (primary role) and `sub` (user id, read via `uuid.UUID(str(user["sub"]))`
    in `_check_can_pay`)."""
    return {"role": role, "sub": str(user_id or uuid.uuid4())}


async def test_ap_clerk_can_no_longer_execute_payments(db_session):
    try:
        await payment_execute._check_can_pay(db_session, _user(role="ap_clerk"))
        assert False, "ap_clerk must be denied payment execution (SoD, 2026-08-13)"
    except PaymentPermissionError:
        pass


async def test_payment_officer_can_execute_payments(db_session):
    await payment_execute._check_can_pay(db_session, _user(role="payment_officer"))


async def test_payment_officer_works_as_an_additional_role(db_session):
    """payment_officer held as an ADDITIONAL role (identity user_roles), not
    the JWT's primary role — `_user_role_codes` reads user_roles and unions it
    with the primary role (`app/crud/payment_execute.py:43-49`). This is how
    the role is actually expected to be held in production."""
    user_id = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'payment_officer')"),
        {"u": str(user_id)})
    await payment_execute._check_can_pay(
        db_session, _user(role="requester", user_id=user_id))


async def test_finance_manager_remains_a_fallback(db_session):
    """Availability fallback: if the payment_officer holder is away, payment
    must not deadlock."""
    await payment_execute._check_can_pay(db_session, _user(role="finance_manager"))


async def test_system_admin_as_additional_role_is_denied(db_session):
    """Fix round 1 (2026-08-13): the first draft generalized the
    additional-role check to `codes & _PAY_ROLES`, which also newly admitted
    system_admin held as an ADDITIONAL role. This codebase treats
    system_admin as a PRIMARY-role grant only (budget_scope.py's
    FULL_ACCESS_PRIMARY vs FULL_ACCESS_ASSIGNED encodes the same split, as do
    admin.py's require_system_admin and the shared uniops_authz package) — an
    unprivileged primary role plus system_admin tacked on as a secondary
    grant must NOT unlock payment execution. Locks _PAY_ROLES_ASSIGNED's
    exclusion of system_admin."""
    user_id = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'system_admin')"),
        {"u": str(user_id)})
    try:
        await payment_execute._check_can_pay(
            db_session, _user(role="requester", user_id=user_id))
        assert False, (
            "system_admin held as an ADDITIONAL role must not confer payment "
            "execution authority (fix round 1, 2026-08-13)"
        )
    except PaymentPermissionError:
        pass
