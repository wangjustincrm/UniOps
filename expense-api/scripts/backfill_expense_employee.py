"""Backfill empty employee_name / department_name / department_id on expense_claims.

Historical claims created before the create-endpoint fix were persisted with an
empty employee_name/department_name (and NULL department_id) because the JWT
access token never carried those fields (identity-api create_access_token emits
only {sub, role, type}). The Expense Claims list therefore showed a blank
Employee column. This script resolves the missing values from the shared
users/departments tables (same physical DB) by employee_id.

Safe by default:
  - dry-run unless --apply is passed
  - refuses a non-local DB host unless --allow-production is passed
  - only touches rows that are actually empty AND resolvable (leaves the rest)
  - per-row SAVEPOINT: one unresolvable row never aborts the batch

Usage (prod, from an expense-api container / with DATABASE_URL set):
    python -m scripts.backfill_expense_employee                       # dry-run
    python -m scripts.backfill_expense_employee --apply --allow-production
"""
import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

_SELECT = text("""
    SELECT ec.id,
           ec.claim_number,
           ec.employee_id,
           ec.employee_name,
           ec.department_name,
           ec.department_id,
           u.full_name        AS resolved_name,
           u.department_id     AS resolved_dept_id,
           d.name              AS resolved_dept_name
    FROM expense_claims ec
    LEFT JOIN users u        ON u.id = ec.employee_id
    LEFT JOIN departments d  ON d.id = u.department_id
    WHERE ec.employee_id IS NOT NULL
      AND (COALESCE(ec.employee_name, '')   = ''
        OR COALESCE(ec.department_name, '') = ''
        OR ec.department_id IS NULL)
    ORDER BY ec.created_at
""")

_UPDATE = text("""
    UPDATE expense_claims
    SET employee_name   = :employee_name,
        department_name = :department_name,
        department_id   = :department_id,
        updated_at      = now()
    WHERE id = :id
""")


def _is_local(url: str) -> bool:
    return any(h in url for h in ("localhost", "127.0.0.1", "@postgres:", "@postgres/"))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--allow-production", action="store_true",
                    help="permit running against a non-local DB host")
    args = ap.parse_args()

    url = settings.database_url
    host = url.split("@", 1)[-1]
    if not _is_local(url) and not args.allow_production:
        raise SystemExit(
            f"Refusing to run against non-local DB ({host}). Pass --allow-production if intended.")

    engine = create_async_engine(url, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    planned = skipped = applied = 0
    async with maker() as session:
        rows = (await session.execute(_SELECT)).all()
        print(f"Scanning: {len(rows)} claim(s) with empty employee/department and a non-null employee_id\n")
        for r in rows:
            new_name = r.employee_name or (r.resolved_name or "")
            new_dept_name = r.department_name or (r.resolved_dept_name or "")
            new_dept_id = r.department_id or r.resolved_dept_id

            # Nothing resolvable (employee not in users, or user has no dept) → skip.
            if r.resolved_name is None:
                print(f"  SKIP  {r.claim_number}: employee_id {r.employee_id} not found in users")
                skipped += 1
                continue
            if (new_name, new_dept_name, new_dept_id) == (
                    r.employee_name or "", r.department_name or "", r.department_id):
                print(f"  SKIP  {r.claim_number}: nothing to change")
                skipped += 1
                continue

            print(f"  {'APPLY' if args.apply else 'PLAN '} {r.claim_number}: "
                  f"employee_name '{r.employee_name or ''}' -> '{new_name}', "
                  f"department_name '{r.department_name or ''}' -> '{new_dept_name}', "
                  f"department_id {r.department_id} -> {new_dept_id}")
            planned += 1
            if args.apply:
                async with session.begin_nested():
                    await session.execute(_UPDATE, {
                        "id": r.id,
                        "employee_name": new_name,
                        "department_name": new_dept_name,
                        "department_id": new_dept_id,
                    })
                applied += 1
        if args.apply:
            await session.commit()

    await engine.dispose()
    verb = "Applied" if args.apply else "Planned (dry-run — re-run with --apply to write)"
    print(f"\n{verb}: {planned} update(s), {applied} written, {skipped} skipped.")


if __name__ == "__main__":
    asyncio.run(main())
