"""Re-route in-flight documents stranded at an optional (Director/Supervisor) step.

Thin CLI around engine.reroute_stranded_optional_steps (the same logic the
`POST /approval/v1/routing/reroute-inflight` admin endpoint runs). When a dept's
Director/Supervisor mapping (or a user's role) changes, documents already parked
at that optional step keep the task assigned to the OLD approver, who can no
longer pass _actor_can_approve for that step → every Approve 409s. The engine
skips unconfigured optional steps for NEW documents but never re-routes in-flight
ones; this advances each stranded doc to its next real approver (or approves it).

Dry-run by DEFAULT (rolls back). Pass --apply to commit. Refuses ENVIRONMENT=
production unless --allow-production. Only touches docs whose current step is now
genuinely skippable — validly-configured approvals are left alone.

Run (dev):
    docker cp reroute_inflight_optional_steps.py <approval-container>:/app/scripts/
    docker exec <approval-container> python -m scripts.reroute_inflight_optional_steps
    docker exec <approval-container> python -m scripts.reroute_inflight_optional_steps --apply
(find the container name with:  docker ps --format '{{.Names}}' | grep approval)
"""
import argparse
import asyncio
import os
import sys

from app.crud.engine import reroute_stranded_optional_steps
from app.db.base import AsyncSessionLocal


async def main(apply: bool):
    async with AsyncSessionLocal() as db:
        results = await reroute_stranded_optional_steps(db)
        for r in results:
            skip_desc = ", ".join(f"step{s['step']}:{s['role']}" for s in r["skipped"])
            print(f"  {r['doc_type'].upper()} {r['number']}: "
                  f"skip [{skip_desc}] -> step{r['to_step']} ({r['new_role']})")
        if apply:
            await db.commit()
            print(f"\nAPPLIED: rerouted {len(results)} document(s).")
        else:
            await db.rollback()
            print(f"\nDRY-RUN: would reroute {len(results)} document(s). "
                  f"Re-run with --apply to commit.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    ap.add_argument("--allow-production", action="store_true",
                    help="permit running when ENVIRONMENT=production")
    args = ap.parse_args()

    if os.environ.get("ENVIRONMENT", "").lower() == "production" and not args.allow_production:
        print("Refusing to run against ENVIRONMENT=production without --allow-production.",
              file=sys.stderr)
        sys.exit(2)

    asyncio.run(main(apply=args.apply))
