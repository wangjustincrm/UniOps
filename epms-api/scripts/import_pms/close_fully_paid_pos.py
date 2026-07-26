"""一次性修复:按 UniOps 库内真实付款把已付清的 PO 收口为 closed。

背景:PMS 导入的历史 PO 因老系统 GR 不严谨,大量实际已付清的 PO 仍停在开放态,
灌爆发票 Match PO 候选列表。姊妹脚本 close_paid_pos.py 只按老系统
SharePoint 的 PaymentStatus=PAID 标记收口,该标记本身有缺失/不严谨,漏网仍在。

本脚本以库内真实付款为准:一个 PO 关联的 **processed** PA 的 payment_amount
合计 >= PO.total,即把 PO 状态置为 closed(不管是否录收货)。关闭后自动从
Match PO 候选中消失(_MATCHABLE_PO_STATUSES 不含 closed)。

判定:
  已付金额 = SUM(pa.payment_amount) WHERE pa.po_id=po.id
             AND pa.status='processed' AND pa.currency=po.currency
  命中     = 已付金额 >= po.total  (且 po.total > 0)

用法(在 epms-api 目录):
  1) 预览(默认 dry-run,不写库):
       python -m scripts.import_pms.close_fully_paid_pos
  2) 确认无误后落库:
       python -m scripts.import_pms.close_fully_paid_pos --commit

安全边界:
  * 只动 status ∈ {issued, approved, partially_received, fully_received} 的 PO
    (cancelled / in_review / draft / submitted / returned / rejected / closed 一律不碰)
  * po.total > 0 才处理(避免 0 金额 PO 被 0>=0 误关)
  * 只算 processed PA,且币种须与 PO 一致(currency 无值的 PA 视为 BUG,不在此处理)
  * 生产库需显式 --commit;任何异常整体回滚;幂等,可重跑
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from sqlalchemy import func, select, update

# 与 load.py / close_paid_pos.py 相同的会话入口(读 epms-api 的 settings/.env)
import app.db.session as session_module
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder

# 允许被收口的现状态 —— 开放态才收口
OPEN_STATUSES = ("issued", "approved", "partially_received", "fully_received")


async def find_fully_paid(db) -> list[tuple]:
    """返回 [(id, number, status, total, paid), ...],均为已付 >= total 的开放态 PO。"""
    paid_expr = func.coalesce(func.sum(PaymentApplication.payment_amount), Decimal("0"))
    rows = (await db.execute(
        select(
            PurchaseOrder.id,
            PurchaseOrder.number,
            PurchaseOrder.status,
            PurchaseOrder.total,
            paid_expr.label("paid"),
        )
        .join(
            PaymentApplication,
            (PaymentApplication.po_id == PurchaseOrder.id)
            & (PaymentApplication.status == "processed")
            & (PaymentApplication.currency == PurchaseOrder.currency),
            isouter=True,
        )
        .where(
            PurchaseOrder.status.in_(OPEN_STATUSES),
            PurchaseOrder.total > 0,
        )
        .group_by(
            PurchaseOrder.id,
            PurchaseOrder.number,
            PurchaseOrder.status,
            PurchaseOrder.total,
        )
        .having(paid_expr >= PurchaseOrder.total)
    )).all()
    return [(r.id, r.number, r.status, r.total, r.paid) for r in rows]


async def run(commit: bool) -> None:
    async with session_module.AsyncSessionLocal() as db:
        target = await find_fully_paid(db)
        print(f"开放态且已付(processed PA)>= PO 金额,将收口为 closed 的:{len(target)} 个")
        for _id, no, st, total, paid in target[:20]:
            print(f"  {no}  ({st} -> closed)  total={total}  paid={paid}")
        if len(target) > 20:
            print(f"  ... 其余 {len(target) - 20} 个")

        if not target:
            return

        if not commit:
            print("\nDRY-RUN:未写库。确认后加 --commit 执行。")
            return

        ids = [_id for _id, *_ in target]
        result = await db.execute(
            update(PurchaseOrder)
            .where(
                PurchaseOrder.id.in_(ids),
                PurchaseOrder.status.in_(OPEN_STATUSES),  # 兜底:并发下仍只动开放态
            )
            .values(status="closed")
        )
        await db.commit()
        print(f"\n已收口 {result.rowcount} 个 PO 为 closed。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="真正写库(默认 dry-run)")
    args = ap.parse_args()
    asyncio.run(run(commit=args.commit))
