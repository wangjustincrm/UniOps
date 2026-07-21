"""Re-sync in-flight approval documents to the CURRENT routing config.

Thin CLI around engine.resync_inflight_approvals (the same logic the
`POST /approval/v1/routing/resync-inflight` admin endpoint runs). When approval
routing config changes (dept gm↔opm mapping, Director/Supervisor routing, who
holds a role) or a workflow's step list changes, already-submitted documents are
not re-routed — their approval_step_idx can point at the wrong/out-of-range step
(→ 409 / "no permission") and/or their task stays assigned to the old approver.
This realigns each doc's step to its open task's real step, skips
now-unconfigured optional steps, and reassigns drifted approvers.

Dry-run by DEFAULT (rolls back). Pass --apply to commit. Refuses ENVIRONMENT=
production unless --allow-production. Idempotent — only touches out-of-sync docs.

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

from app.crud.engine import resync_inflight_approvals
from app.db.base import AsyncSessionLocal


async def main(apply: bool):
    async with AsyncSessionLocal() as db:
        result = await resync_inflight_approvals(db)
        for r in result["resynced"]:
            print(f"  {r['doc_type'].upper()} {r['number']}: {'; '.join(r['actions'])}")
        for e in result["errors"]:
            print(f"  ! {e['doc_type']} {e['doc_id']}: {e['error']}")
        n, errs = len(result["resynced"]), len(result["errors"])
        if apply:
            await db.commit()
            print(f"\nAPPLIED: re-synced {n} document(s); {errs} error(s).")
        else:
            await db.rollback()
            print(f"\nDRY-RUN: would re-sync {n} document(s); {errs} error(s). "
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
