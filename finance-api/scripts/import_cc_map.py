"""Load budget_actual_cc_map from finance's `Budget vs Actual Mapping.xlsx`.

Truncate + reload (idempotent). Run INSIDE the finance-api container (its
DATABASE_URL points at the right DB). NEVER run from the host — the host .env
points at production.

Usage:
    docker exec uniops_finance_api python scripts/import_cc_map.py "/path/to/Budget vs Actual Mapping.xlsx"
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete  # noqa: E402

from app.db.base import AsyncSessionLocal  # noqa: E402
from app.models.cc_map import BudgetActualCcMap  # noqa: E402
from app.services.cc_map_import import _rows_from_xlsx  # noqa: E402


async def main(path: str):
    rows = _rows_from_xlsx(path)
    async with AsyncSessionLocal() as s:
        await s.execute(delete(BudgetActualCcMap))
        for r in rows:
            s.add(BudgetActualCcMap(**r))
        await s.commit()
    print(f"import_cc_map: loaded {len(rows)} rows")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('usage: python scripts/import_cc_map.py "<xlsx_path>"')
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1]))
