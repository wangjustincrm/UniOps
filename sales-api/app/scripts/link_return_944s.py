"""The customer's returns (RA 944s) already kept as files, linked to their Shopify orders (RA CRM<n> = order n)
the way a new one is (app/services/returns_944): the order's Documents and Returned EA, then the inventory
sync takes it back from that customer. Idempotent.

    python -m app.scripts.link_return_944s            # dry run
    python -m app.scripts.link_return_944s --apply
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.deps import uow_factory
from app.domain.edi944 import parse_944
from app.models.documents import Document
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services.returns_944 import RA_ERROR, apply_return_944


async def main(args: ScriptArgs) -> dict:
    out = []
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        docs = list((await s.execute(select(Document).where(Document.kind == "944_EDI", Document.error.like(f"{RA_ERROR}%"))
                                     .order_by(Document.id).with_for_update())).scalars())
        for d in docs:
            content = (await s.execute(select(Document.content).where(Document.id == d.id))).scalar_one()
            r = parse_944(bytes(content))
            res = await apply_return_944(u, d, r)
            out.append({"document": d.id, "key": r.transfer_key, "result": res, "note": d.error})
        if args.apply:
            await u.commit()
    return {"files": out}


if __name__ == "__main__":
    script_main(main)
