"""One-time bulk password reset.

Sets EVERY user's password to the standard initial password (INITIAL_PASSWORD,
"Feihe12#$"), forces a change on next login (must_change_password=True), and
stamps password_changed_at=now() so the expiry clock starts fresh.

After this runs, all current credentials are invalidated — every user must sign
in with the initial password and immediately set a new one.

Dry run (counts only, no writes):
    python -m scripts.reset_all_passwords

Apply:
    python -m scripts.reset_all_passwords --apply

Optionally limit to active users only:
    python -m scripts.reset_all_passwords --apply --active-only
"""
import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import INITIAL_PASSWORD
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User


async def run(apply: bool, active_only: bool) -> None:
    async with AsyncSessionLocal() as db:
        q = select(User)
        if active_only:
            q = q.where(User.is_active.is_(True))
        users = list((await db.execute(q)).scalars().all())

        print(f"Matched {len(users)} user(s)"
              f"{' (active only)' if active_only else ''}.")
        if not apply:
            print("Dry run — no changes written. Re-run with --apply to commit.")
            for u in users[:20]:
                print(f"  would reset: {u.email}")
            if len(users) > 20:
                print(f"  … and {len(users) - 20} more")
            return

        now = datetime.now(timezone.utc)
        for u in users:
            u.hashed_password = hash_password(INITIAL_PASSWORD)
            u.must_change_password = True
            u.password_changed_at = now
        await db.commit()
        print(f"Reset {len(users)} user(s) to the initial password. "
              f"All must change on next login.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Bulk-reset all user passwords.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write changes (default is a dry run).")
    ap.add_argument("--active-only", action="store_true",
                    help="Only reset active users.")
    args = ap.parse_args()
    asyncio.run(run(apply=args.apply, active_only=args.active_only))


if __name__ == "__main__":
    main()
