"""一次性回填:增量导入丢失的 PO→PR 关联。

背景(2026-07-10):load.py 的 pono_to_pr 映射只在 PR 于当批新插入时填充;
PR 已存在(早批导入)或增量没拉到 PR 行时,同批新 PO 拿不到关联 —— pr_id/pr_number
为空、title 退化为 PO 号、type 用默认值、created_by 落到 system 用户。
根因已修(load.py PO 段增加跨批次 DB 种子),本脚本修存量。

判定:PO.pr_id IS NULL 且存在 PR.po_number == PO.number(原生 EPMS PO 都带 pr_id,
不会误伤)。回填 pr_id / pr_number / type / created_by;title 仅当仍是退化值
(== PO 号)时替换为 PR 号(与导入的正常行为一致)。

两级查找:
  1) EPMS 内 join:PR.po_number == PO.number
  2) --pr-data 提供 SharePoint PR 全量 json 时,按 SP 的 PONo→PR_No 权威映射兜底
     (覆盖"PR 先导入、老系统后回填 PONo、增量不更新已存在 PR"的场景),
     并顺带修正过期的 PR.po_number。

用法(epms-api 目录 / 容器内,--pr-data 建议总是提供):
  python -m scripts.import_pms.backfill_po_pr_links            # dry-run
  python -m scripts.import_pms.backfill_po_pr_links --commit
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

import app.db.session as session_module
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest


def _sp_pono_to_prno(pr_data):
    if pr_data is None:
        return {}
    rows = json.loads(Path(pr_data).read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("items") or []
    m = {}
    for r in rows:
        pono = str(r.get("PONo") or "").strip()
        prno = str(r.get("PR_x0020_No") or "").strip()
        if pono and prno:
            m.setdefault(pono, prno)
    return m


def _apply(po, pr):
    po.pr_id = pr.id
    po.pr_number = pr.number
    po.type = pr.type
    po.created_by = pr.created_by or po.created_by
    if po.title == po.number:
        po.title = pr.number


async def run(commit: bool, pr_data=None) -> None:
    sp_map = _sp_pono_to_prno(pr_data)
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

        # ── 二级:SP 权威映射兜底 ──
        sp_rows = []
        if sp_map:
            leftovers = [po for po in (await db.execute(
                select(PurchaseOrder).where(PurchaseOrder.pr_id.is_(None))
                .order_by(PurchaseOrder.number)
            )).scalars().all() if po.id not in {p.id for p, _ in rows}]
            wanted = {po.number: po for po in leftovers if po.number in sp_map}
            if wanted:
                prnos = {sp_map[n] for n in wanted}
                prs = {pr.number: pr for pr in (await db.execute(
                    select(PurchaseRequest).where(PurchaseRequest.number.in_(prnos))
                )).scalars().all()}
                for pono, po in sorted(wanted.items()):
                    pr = prs.get(sp_map[pono])
                    if pr is not None:
                        sp_rows.append((po, pr))
            print(f"二级(SP PONo->PR 兜底)可回填:{len(sp_rows)} 个")
            for po, pr in sp_rows:
                mark = "(修正 PR.po_number)" if pr.po_number != po.number else ""
                print(f"  {po.number} -> {pr.number} {mark}")
            resolved = {p.id for p, _ in rows} | {p.id for p, _ in sp_rows}
            unresolved = [po.number for po in leftovers if po.id not in resolved]
            if unresolved:
                print(f"仍无法关联(SP 中该 PO 无对应 PR):{len(unresolved)} 个")
                for n in unresolved[:10]:
                    print(f"  {n}")

        if not commit:
            print("\nDRY-RUN:未写库。确认后加 --commit 执行。")
            return

        for po, pr in rows:
            _apply(po, pr)
        for po, pr in sp_rows:
            _apply(po, pr)
            if pr.po_number != po.number:
                pr.po_number = po.number   # 修正过期的 PR.po_number
        await db.commit()
        print(f"\n已回填 {len(rows) + len(sp_rows)} 个 PO 的 PR 关联。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="真正写库(默认 dry-run)")
    ap.add_argument("--pr-data", default=None,
                    help="SharePoint PR 全量 json(pr_merged.json),启用二级兜底")
    args = ap.parse_args()
    asyncio.run(run(commit=args.commit, pr_data=args.pr_data))
