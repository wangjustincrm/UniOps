"""The two Access Control keys for banking — shared by bank.py and bank_recon.py.

Both files used to gate their writes on finance.coa.manage ("Manage Chart of
Accounts"), so the matrix had no bank row at all, and letting a Payment Officer
reconcile meant letting them edit the chart of accounts. See identity-api
migration 0014_bank_perms for the grants.

  finance.bank.reconcile  import statements / payment files, match, sign off
  finance.bank.settings   the bank-account master — which NC account each one is

Resolved through the shared authz package: primary role ∪ additional roles
(payment_officer is an ADDITIONAL role), read from the matrix on every call,
system_admin always admitted.
"""
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission

RECONCILE = "finance.bank.reconcile"
SETTINGS = "finance.bank.settings"

_LABELS = {RECONCILE: "Bank Reconciliation", SETTINGS: "Manage Bank Accounts"}
_GATES = {key: require_permission(key) for key in _LABELS}


async def can(db: AsyncSession, user: dict, key: str) -> bool:
    try:
        await _GATES[key](user, db)
        return True
    except HTTPException:
        return False


async def require(db: AsyncSession, user: dict, key: str) -> None:
    if not await can(db, user, key):
        raise HTTPException(
            status_code=403,
            detail=f"This needs the '{_LABELS[key]}' permission ({key}). "
                   f"It is granted per role in Portal -> Access Control.")
