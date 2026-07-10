"""一次性回填:增量导入丢失的 PO→PR 关联。

背景(2026-07-10):load.py 的 pono_to_pr 映射只在 PR 于当批新插入时填充;
PR 已存在(早批导入)或增量没拉到 PR 行时,同批新 PO 拿不到关联 —— pr_id/pr_number
为空、title 退化为 PO 号、type 用默认值、created_by 落到 system 用户。
根因已修(load.py PO 段增加跨批次 DB 种子),本脚本修存量。

判定:PO.pr_id IS NULL 且存在 PR.po_number == PO.number(原生 EPMS PO 都带 pr_id,
不会误伤)。回填 pr_id / pr_number / type / created_by;title 仅当仍是退化值
(== PO 号)时替换为 PR 号(与导入的正常行为一致)。

用法(epms-api 目录 / 容器内):
  python -m scripts.import_pms.backfill_po_pr_links            # dry-run
  python -m scripts.import_pms.backfill_po_pr_links --commit
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

import app.db.session as session_module
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest


async def run(commit: bool) -> None:
    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(PurchaseOrder, PurchaseRequest)
            .join(PurchaseRequest, PurchaseRequest.po_number == PurchaseOrder.number)
            .where(PurchaseOrder.pr_id.is_(None))
            .order_by(PurchaseOrder.number)
        )).all()
        print(f"pr_id 为空且可按 PR.po_number 回填的 PO:{len(rows)} 个")
        for po, pr in rows:
            title_fix = " +title" if po.title == po.number else ""
            print(f"  {po.number} -> {pr.number}{title_fix}")

        if not commit:
            print("\nDRY-RUN:未写库。确认后加 --commit 执行。")
            return

        for po, pr in rows:
            po.pr_id = pr.id
            po.pr_number = pr.number
            po.type = pr.type
            po.created_by = pr.created_by or po.created_by
            if po.title == po.number:
                po.title = pr.number
        await db.commit()
        print(f"\n已回填 {len(rows)} 个 PO 的 PR 关联。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="真正写库(默认 dry-run)")
    args = ap.parse_args()
    asyncio.run(run(commit=args.commit))
