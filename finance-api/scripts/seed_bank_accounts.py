"""Idempotent seed of Canada Royal Milk's real bank accounts (A3 workbench).

Source: May 2026 statements (JPM Chase US + Toronto, Bank of China Toronto,
ICBC Canada, RBC). GL mapping (ledger_account_code) is left for finance to set
in the COA mappings / account editor. Re-running is safe: upsert by
(bank_name, account_masked, currency). Run:

  docker compose -f docker-compose.dev.yml run --rm finance-api \
    python scripts/seed_bank_accounts.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.base import AsyncSessionLocal
from app.models.bank import BankAccount

ACCOUNTS = [
    # name, bank_name, account_masked, currency
    ("Chase US Commercial Checking",  "JPMorgan Chase (US)",          "3251",  "USD"),
    ("Chase Toronto Operating",       "JPMorgan Chase (Toronto)",     "2940",  "CAD"),
    ("Chase Toronto USD",             "JPMorgan Chase (Toronto)",     "2941",  "USD"),
    ("Bank of China Operating",       "Bank of China (Toronto)",      "0128",  "CAD"),
    ("Bank of China USD",             "Bank of China (Toronto)",      "0139",  "USD"),
    ("Bank of China CNY",             "Bank of China (Toronto)",      "1994",  "CNY"),
    ("ICBC Current CAD",              "ICBC (Canada)",                "8518",  "CAD"),
    ("ICBC Current USD",              "ICBC (Canada)",                "8518",  "USD"),
    ("ICBC Current CNY",              "ICBC (Canada)",                "8518",  "CNY"),
    ("RBC Operating",                 "Royal Bank of Canada",         "376-0", "CAD"),
    ("RBC USD",                       "Royal Bank of Canada",         "035-1", "USD"),
]


async def main() -> None:
    created = updated = 0
    async with AsyncSessionLocal() as db:
        for name, bank, masked, ccy in ACCOUNTS:
            existing = (await db.execute(
                select(BankAccount).where(
                    BankAccount.bank_name == bank,
                    BankAccount.account_masked == masked,
                    BankAccount.currency == ccy,
                )
            )).scalar_one_or_none()
            if existing:
                existing.name = name
                existing.is_active = True
                updated += 1
            else:
                db.add(BankAccount(name=name, bank_name=bank, account_masked=masked,
                                   currency=ccy, is_active=True))
                created += 1
        await db.commit()
    print(f"bank accounts seeded: created={created} updated={updated} total={len(ACCOUNTS)}")


if __name__ == "__main__":
    asyncio.run(main())
