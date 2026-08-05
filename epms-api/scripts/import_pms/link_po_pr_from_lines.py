"""One-shot repair: relink imported POs to their PR via the LINE-level chain
(PO Item.PRITEMID -> PR Item -> PR number) when the header-level link is missing.

Root cause (2026-08-05, PO-085-2607-03): the header backfills (backfill_po_pr_links
and link_po_pr_from_sp) match on the PMS "Purchase Request".PONo header field —
but PMS users didn't always fill it. The PO Item rows still carry PRITEMID, so
the PO->PR relationship survives at line level. PMS has no PR-less POs; any
EPMS PO with pr_id NULL that maps through PRITEMID lost its link in the import.
(That one caused a requester-role email blast — see crud/gr.py.)

For every EPMS PO with pr_id NULL whose number maps (via SharePoint PO Item ->
PR Item) to exactly ONE PR that exists in the DB, this mirrors
link_po_pr_from_sp._apply: sets po.pr_id/pr_number/created_by, fixes a degraded
title, back-refs pr.po_id/po_number (only when the PR isn't already linked to a
different live PO — conflicts are reported, not overwritten), and backfills
pr_id/pr_number on the PO's GRs. NC-sourced POs never match the PMS map, so
they are naturally untouched.

Idempotent. Dry-run by DEFAULT; --commit writes.

Run (mount the repo into an epms-api container; needs SP_* + POSTGRES_* env):
  docker run --rm --env-file .env -v <repo>/epms-api:/app -w /app <epms-api image> \
      python -m scripts.import_pms.link_po_pr_from_lines [--commit]
"""
import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select, text

import app.db.session as session_module
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest

from .sharepoint import SharePointClient


def _build_line_map(sp: SharePointClient) -> dict[str, set[str]]:
    """PO number -> {PR numbers} via PO Item.PRITEMID -> PR Item.ID -> PR Item.Title."""
    pr_items = sp.get_list_items("PR Item", select=["ID", "Title"])
    pr_by_id = {str(it["ID"]): (it.get("Title") or "").strip() for it in pr_items}

    po_items = sp.get_list_items("PO Item", select=["ID", "Title", "PRITEMID"])
    mapping: dict[str, set[str]] = defaultdict(set)
    for it in po_items:
        po_no = (it.get("Title") or "").strip()
        pr_no = pr_by_id.get(str(it.get("PRITEMID") or "").strip(), "")
        if po_no and pr_no:
            mapping[po_no].add(pr_no)
    return mapping


async def run(commit: bool) -> None:
    print("Fetching SharePoint line-level PO->PR map (PO Item / PR Item) ...")
    mapping = _build_line_map(SharePointClient())
    print(f"  {len(mapping)} PO number(s) carry a line-level PR reference")

    linked = skipped_no_map = skipped_multi = skipped_no_pr_row = conflicts = 0
    async with session_module.AsyncSessionLocal() as db:
        pos = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.pr_id.is_(None))
        )).scalars().all()
        print(f"  {len(pos)} EPMS PO(s) with pr_id NULL")

        for po in pos:
            pr_nos = mapping.get(po.number)
            if not pr_nos:
                skipped_no_map += 1          # NC-sourced / genuinely unknown to PMS
                continue
            if len(pr_nos) > 1:
                skipped_multi += 1
                print(f"  !! {po.number}: lines point at {sorted(pr_nos)} — ambiguous, skipped")
                continue
            pr_no = next(iter(pr_nos))
            pr = (await db.execute(
                select(PurchaseRequest).where(PurchaseRequest.number == pr_no)
            )).scalar_one_or_none()
            if pr is None:
                skipped_no_pr_row += 1
                print(f"  !! {po.number}: PR {pr_no} not in DB — skipped")
                continue

            note = ""
            po.pr_id = pr.id
            po.pr_number = pr.number
            if pr.created_by:
                po.created_by = pr.created_by
            if po.title == po.number and pr.title:
                po.title = pr.title
            if pr.po_id is None:
                pr.po_id = po.id
                pr.po_number = po.number
            elif pr.po_id != po.id:
                conflicts += 1
                note = f" (PR already back-refs {pr.po_number}; back-ref left as-is)"
            n_gr = (await db.execute(text(
                "UPDATE goods_receipts SET pr_id = :pr_id, pr_number = :pr_no "
                "WHERE po_id = :po_id AND pr_id IS NULL"
            ), {"pr_id": pr.id, "pr_no": pr.number, "po_id": po.id})).rowcount
            linked += 1
            print(f"  -> {po.number}  <=  {pr.number}  [+{n_gr} GR(s)]{note}")

        print(f"\nSummary: link {linked}, no-map {skipped_no_map}, ambiguous {skipped_multi}, "
              f"pr-missing {skipped_no_pr_row}, back-ref conflicts {conflicts}")
        if commit:
            await db.commit()
            print("COMMITTED.")
        else:
            await db.rollback()
            print("DRY-RUN — rolled back. Re-run with --commit to write.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="write changes (default: dry-run)")
    args = ap.parse_args()
    asyncio.run(run(commit=args.commit))


if __name__ == "__main__":
    main()
