"""一次性修复:把老系统已付清(PaymentStatus=PAID)但导入后停在开放状态的 PO 收口为 closed。

背景(2026-07-10):老系统 GR 不严谨,大量实际已付清关闭的 PO 其 Status0 仍为 OPEN,
导入映射(修复前不看 PaymentStatus)把它们落成 issued —— 海量僵尸 PO 灌爆发票匹配
候选列表。映射已修(mappings.map_po_status:PAID → closed),本脚本修存量。

用法(在 epms-api 目录):
  1) 先做一次 PO 全量抽取,拿到带 PaymentStatus 的完整 po.json:
       python -m scripts.import_pms extract --only po --full   # 参数以 extract.py 实际 CLI 为准
  2) 预览(默认 dry-run,不写库):
       python -m scripts.import_pms.close_paid_pos
  3) 确认无误后落库:
       python -m scripts.import_pms.close_paid_pos --commit

安全边界:
  * 只动 status ∈ {issued, approved, partially_received, fully_received} 的 PO
    (cancelled / in_review / draft / closed 一律不碰)
  * 只按 PO number(SharePoint Title)精确匹配
  * 生产库需显式 --commit;任何异常整体回滚
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select, update

# 与 load.py 相同的会话入口(读 epms-api 的 settings/.env)
import app.db.session as session_module
from app.models.po import PurchaseOrder

DEFAULT_DATA = Path(__file__).parent / "data" / "po.json"
# 允许被收口的现状态 —— 开放态才收口
OPEN_STATUSES = ("issued", "approved", "partially_received", "fully_received")


def paid_po_numbers(data_path: Path) -> list[str]:
    rows = json.loads(data_path.read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("items") or []
    out = []
    for r in rows:
        if (str(r.get("PaymentStatus") or "").strip().upper()) == "PAID":
            no = str(r.get("Title") or "").strip()
            if no:
                out.append(no)
    return sorted(set(out))


async def run(commit: bool, data_path: Path) -> None:
    numbers = paid_po_numbers(data_path)
    print(f"po.json 中 PaymentStatus=PAID 的 PO:{len(numbers)} 个")
    if not numbers:
        return

    async with session_module.AsyncSessionLocal() as db:
        target = list((await db.execute(
            select(PurchaseOrder.number, PurchaseOrder.status)
            .where(PurchaseOrder.number.in_(numbers),
                   PurchaseOrder.status.in_(OPEN_STATUSES))
        )).all())
        print(f"EPMS 中处于开放状态、将被收口为 closed 的:{len(target)} 个")
        for no, st in target[:20]:
            print(f"  {no}  ({st} -> closed)")
        if len(target) > 20:
            print(f"  ... 其余 {len(target) - 20} 个")

        if not commit:
            print("\nDRY-RUN:未写库。确认后加 --commit 执行。")
            return

        result = await db.execute(
            update(PurchaseOrder)
            .where(PurchaseOrder.number.in_([no for no, _ in target]),
                   PurchaseOrder.status.in_(OPEN_STATUSES))
            .values(status="closed")
        )
        await db.commit()
        print(f"\n已收口 {result.rowcount} 个 PO 为 closed。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="真正写库(默认 dry-run)")
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="po.json 路径(默认 data/po.json)")
    args = ap.parse_args()
    asyncio.run(run(commit=args.commit, data_path=args.data))
