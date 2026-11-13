"""Radiant's inventory report as a count (user 2026-11-13): "a report per lot — what came in (944), what went
out (945 / packing lists) — against Radiant, to see the difference; keep the records, but take the latest
Radiant inventory as the start and keep the current numbers right".

  upload   the portal's file (CSV / Excel) and the moment it was taken → a count with its lines (draft)
  compare  per item and lot, up to that moment: Sales' own moves (received, returned, shipped, adjusted)
           and their balance, Radiant's figure, the difference; and what moved after it
  apply    the count becomes the start: "count" moves at its moment set every lot to Radiant's figure
           (a lot Radiant does not list goes to 0, the "(no lot)" balance of older shipments too). They are
           kept right by the stock sync: a 945 that arrives later but shipped before the count changes
           the count's move, not the figure. Every move before stays; after it, 944s and 945s add up.
  undo     the count's moves are taken away (with why); it is a draft again

Settling each difference (accept it, or keep Sales' figure for a lot until Radiant's data) comes next."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.domain.radiant_stock import StockFileError, parse_stock_file
from app.models.config import Item
from app.models.documents import Document, DocumentLink
from app.models.stock import ADJUSTED, RADIANT, StockCount, StockCountLine, StockMove, _net
from app.platform.authorization import authorize
from app.platform.errors import BusinessError
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.schemas.stock import CountCompareRow, StockCountLineRead, StockCountRead
from app.services import oplog
from app.services.record_files import MAX_BYTES

READ = "sales.read"
ADJUST = "sales.inventory.adjust"
COUNT = "count"
ENTITY = "stock_count"
IN_SOURCES = {"receipt_row": "received_ea", "return_944": "returned_ea"}
OUT_SOURCES = {"shipment_row": "shipped_ea", "order_line": "shipped_ea"}


def _own(count_id: int):
    return (StockMove.source_type == COUNT) & StockMove.source_key.like(f"{count_id}:%")


async def count_moves(s) -> int:
    """Each applied count's moves, so that at its moment every lot holds Radiant's figure (the caller commits).
    A move is added for what is missing — the ones written stay (append-only); counts in time order."""
    n = 0
    counts = list((await s.execute(select(StockCount).where(StockCount.applied_at.is_not(None))
                                   .order_by(StockCount.counted_at, StockCount.id))).scalars())
    for c in counts:
        target = {(ln.item_code, ln.lot): (ln.qty_ea, ln.expiry) for ln in
                  (await s.execute(select(StockCountLine).where(StockCountLine.count_id == c.id))).scalars()}
        bal = {(i, lot): (int(q), exp) for i, lot, q, exp in (await s.execute(
            select(StockMove.item_code, StockMove.lot, _net(RADIANT), func.max(StockMove.expiry))
            .where(StockMove.at <= c.counted_at).group_by(StockMove.item_code, StockMove.lot))).all()}
        written = dict((await s.execute(select(func.concat(StockMove.item_code, "|", func.coalesce(StockMove.lot, "")), func.count())
                                        .where(_own(c.id)).group_by(StockMove.item_code, StockMove.lot))).all())
        for k in set(target) | set(bal):
            want, exp = target.get(k, (0, None))
            have, ledger_exp = bal.get(k, (0, None))
            diff = want - have
            if not diff:
                continue
            item, lot = k
            seq = written.get(f"{item}|{lot or ''}", 0)
            key = f"{c.id}:" + hashlib.sha1(f"{item}|{lot or ''}|{seq}".encode()).hexdigest()[:24]
            s.add(StockMove(at=c.counted_at, item_code=item, lot=lot, expiry=exp or ledger_exp, qty_ea=abs(diff),
                            from_place=ADJUSTED if diff > 0 else RADIANT, to_place=RADIANT if diff > 0 else ADJUSTED,
                            source_type=COUNT, source_key=key,
                            reason=f"Radiant count of {c.counted_at:%Y-%m-%d %H:%M} UTC: {want} EA (Sales had {have})",
                            created_by_name="Sales"))
            n += 1
        await s.flush()
    return n


class StockCountRepository(Repository[StockCount]):
    model = StockCount
    search_fields = ("note", "file_name", "source")
    list_fields = ("counted_at", "source", "file_name", "note", "applied_at", "applied_by_name", "created_by_name", "created_at")
    labels = {"counted_at": "Counted at", "file_name": "File", "applied_at": "Applied", "applied_by_name": "Applied by",
              "created_by_name": "Uploaded by", "created_at": "Uploaded"}
    default_sort = ("-counted_at",)


class StockCountService(ReadOnlyService[StockCount, StockCountRead]):
    repo_cls = StockCountRepository
    read_schema = StockCountRead
    read_permission = READ

    async def enrich(self, rows: list[StockCountRead], entities: list[StockCount]) -> None:
        ids = [e.id for e in entities]
        sums = {cid: (n, int(q or 0), int(h or 0)) for cid, n, q, h in (await self.uow.session.execute(
            select(StockCountLine.count_id, func.count(), func.sum(StockCountLine.qty_ea), func.sum(StockCountLine.hold_ea))
            .where(StockCountLine.count_id.in_(ids)).group_by(StockCountLine.count_id))).all()}
        for r in rows:
            r.lines, r.total_ea, r.hold_ea = sums.get(r.id, (0, 0, 0))

    async def _count(self, count_id: int, *, lock: bool = False) -> StockCount:
        c = await self.uow.session.get(StockCount, count_id, with_for_update=lock)
        if c is None:
            raise BusinessError("stock_count.not_found", "no such count", status_code=404)
        return c

    async def create(self, *, name: str, data: bytes, counted_at: datetime, note: str | None) -> StockCountRead:
        await authorize(self.uow, ADJUST)
        s = self.uow.session
        if len(data) > MAX_BYTES:
            raise BusinessError("stock_count.too_large", f"at most {MAX_BYTES // (1024 * 1024)} MB")
        if counted_at.tzinfo is None:
            raise BusinessError("stock_count.when", "the moment needs its time zone")
        if counted_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise BusinessError("stock_count.when", "the report cannot be from the future")
        name = re.sub(r"[\\/\x00-\x1f]+", "_", (name or "").strip())[:200] or "inventory"
        try:
            rows = parse_stock_file(name, data)
        except StockFileError as e:
            raise BusinessError("stock_count.file", str(e)) from e
        sha = hashlib.sha256(data).hexdigest()
        doc = (await s.execute(select(Document).where(Document.sha256 == sha, Document.content.is_not(None)))).scalar_one_or_none()
        if doc is None:
            doc = Document(direction="IN", origin="USER", system=None, kind="ATTACHMENT", remote_name=name, size=len(data), sha256=sha,
                           content=data, created_by_name=self.uow.actor.name)
            s.add(doc)
            await s.flush()
        c = StockCount(counted_at=counted_at, note=(note or "").strip()[:2000] or None, file_name=name, document_id=doc.id)
        s.add(c)
        await s.flush()
        s.add(DocumentLink(document_id=doc.id, entity_type=ENTITY, entity_id=c.id))
        for r in rows:
            s.add(StockCountLine(count_id=c.id, item_code=r.item_code[:50], lot=(r.lot or None) and r.lot[:50], expiry=r.expiry,
                                 qty_ea=r.qty_ea, hold_ea=r.hold_ea, description=(r.description or None) and r.description[:255]))
        self.uow.set_reason(f"Radiant inventory report uploaded: {name}")
        await s.flush()
        await oplog.write(s, "INFO", "stock", f"Radiant inventory of {counted_at:%Y-%m-%d %H:%M} UTC uploaded ({len(rows)} lots): {name}",
                          document_id=doc.id)
        return await self.get(c.id)

    async def update(self, count_id: int, *, version: int, counted_at: datetime | None, note: str | None) -> StockCountRead:
        await authorize(self.uow, ADJUST)
        c = await self._count(count_id, lock=True)
        if c.version != version:
            raise BusinessError("entity.stale", "someone changed this count: reload it", status_code=409)
        if counted_at is not None:
            if c.applied_at is not None:
                raise BusinessError("stock_count.applied", "undo the count before changing its moment")
            if counted_at.tzinfo is None or counted_at > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise BusinessError("stock_count.when", "the moment needs its time zone and cannot be in the future")
            c.counted_at = counted_at
        c.note = (note or "").strip()[:2000] or None
        self.uow.set_reason("count changed")
        await self.uow.session.flush()
        return await self.get(c.id)

    async def apply(self, count_id: int, *, version: int) -> StockCountRead:
        await authorize(self.uow, ADJUST)
        s = self.uow.session
        c = await self._count(count_id, lock=True)
        if c.version != version:
            raise BusinessError("entity.stale", "someone changed this count: reload it", status_code=409)
        if c.applied_at is not None:
            raise BusinessError("stock_count.applied", "this count is already the start")
        c.applied_at, c.applied_by_name = datetime.now(timezone.utc), self.uow.actor.name
        self.uow.set_reason("Radiant count applied: every lot starts from Radiant's figure at its moment")
        await s.flush()
        n = await count_moves(s)
        await oplog.write(s, "INFO", "stock", f"Radiant count of {c.counted_at:%Y-%m-%d %H:%M} UTC applied by {self.uow.actor.name}: "
                          f"{n} lots set to Radiant's figure", document_id=c.document_id)
        return await self.get(c.id)

    async def undo(self, count_id: int, *, version: int, reason: str) -> StockCountRead:
        await authorize(self.uow, ADJUST)
        s = self.uow.session
        c = await self._count(count_id, lock=True)
        if c.version != version:
            raise BusinessError("entity.stale", "someone changed this count: reload it", status_code=409)
        if c.applied_at is None:
            raise BusinessError("stock_count.not_applied", "this count is not applied")
        gone = (await s.execute(delete(StockMove).where(_own(c.id)))).rowcount
        c.applied_at = c.applied_by_name = None
        self.uow.set_reason(f"Radiant count undone: {reason}")
        await s.flush()
        await oplog.write(s, "WARNING", "stock", f"Radiant count of {c.counted_at:%Y-%m-%d %H:%M} UTC undone by {self.uow.actor.name} "
                          f"({gone} count moves taken away): {reason}", document_id=c.document_id)
        return await self.get(c.id)

    async def lines(self, count_id: int) -> list[StockCountLineRead]:
        await authorize(self.uow, READ)
        await self._count(count_id)
        rows = (await self.uow.session.execute(select(StockCountLine).where(StockCountLine.count_id == count_id)
                                               .order_by(StockCountLine.item_code, StockCountLine.expiry, StockCountLine.lot))).scalars()
        return [StockCountLineRead.model_validate(r, from_attributes=True) for r in rows]

    async def compare(self, count_id: int) -> list[CountCompareRow]:
        """Per item and lot: Sales' moves up to the count (this count's own moves aside), Radiant's figure, the
        difference (Sales − Radiant), and what moved after it."""
        await authorize(self.uow, READ)
        s = self.uow.session
        c = await self._count(count_id)
        rows: dict[tuple[str, str | None], CountCompareRow] = {}

        def row(item: str, lot: str | None) -> CountCompareRow:
            return rows.setdefault((item, lot), CountCompareRow(item_code=item, lot=lot))

        before = (await s.execute(
            select(StockMove.item_code, StockMove.lot, StockMove.source_type, _net(RADIANT), func.max(StockMove.expiry))
            .where(StockMove.at <= c.counted_at, ~_own(c.id), (StockMove.to_place == RADIANT) | (StockMove.from_place == RADIANT))
            .group_by(StockMove.item_code, StockMove.lot, StockMove.source_type))).all()
        for item, lot, src, net, exp in before:
            r = row(item, lot)
            net = int(net)
            field = IN_SOURCES.get(src) or OUT_SOURCES.get(src)
            if field in ("shipped_ea",):
                r.shipped_ea -= net
            elif field:
                setattr(r, field, getattr(r, field) + net)
            else:
                r.adjusted_ea += net
            r.sales_ea += net
            r.expiry = max(filter(None, (r.expiry, exp)), default=None)
        for ln in (await s.execute(select(StockCountLine).where(StockCountLine.count_id == c.id))).scalars():
            r = row(ln.item_code, ln.lot)
            r.radiant_ea += ln.qty_ea
            r.hold_ea += ln.hold_ea
            r.in_report = True
            r.expiry = ln.expiry or r.expiry
        for item, lot, net in (await s.execute(
                select(StockMove.item_code, StockMove.lot, _net(RADIANT)).where(StockMove.at > c.counted_at)
                .group_by(StockMove.item_code, StockMove.lot))).all():
            if int(net) or (item, lot) in rows:
                row(item, lot).since_ea += int(net)
        names = dict((await s.execute(select(Item.item_code, Item.description).where(Item.item_code.in_({k[0] for k in rows})))).all())
        out = []
        for (item, lot), r in rows.items():
            if not (r.sales_ea or r.radiant_ea or r.since_ea or r.in_report or r.received_ea or r.shipped_ea):
                continue
            r.description = names.get(item)
            r.known_item = item in names
            r.difference_ea = r.sales_ea - r.radiant_ea
            r.now_ea = r.radiant_ea + r.since_ea
            r.kind = ("match" if not r.difference_ea else "radiant_only" if not r.sales_ea and r.in_report
                      else "no_lot" if lot is None and not r.in_report else "sales_only" if not r.in_report else "differs")
            out.append(r)
        return sorted(out, key=lambda r: (r.item_code, r.lot is None, r.expiry or datetime.max.date(), r.lot or ""))
