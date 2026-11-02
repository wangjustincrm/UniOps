"""The shadow run's reference files and comparison (design §5.4; user
decision 2026-11-02, reduced scope: 940s now; 997 / 945 / 944 matching
against the references arrives with P5).

- `ShadowRun.import_references`: each file in SALES_ZOHO_REFERENCE_DIR (a
  read-only folder the user fills with what Zoho sent) is stored once as an
  OUT document with origin ZOHO_REFERENCE. Never parsed into orders, never sent.
- `ShadowRun.compare`: for each reference 940, the Sales order (W05 03 = the
  retailer PO, else W05 02 = the Shopify order number) and the 940 Sales would
  send for it — built as a dry run (control number 0, no sequence, no
  document, no outbox row) — compared segment by segment (app/domain/shadow.py).
  Shadow orders that would qualify for a 940 and that no reference matched
  are `Sales only`. A marked expected difference holds while the differences
  stay the same.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import and_, exists, func, select

from app.domain.edi940 import build_940
from app.domain.intake import classify
from app.domain.order_rules import facts_of, qualifies
from app.domain.shadow import compare, ignore_rules, reference_940
from app.domain.status import Ack
from app.domain.status import Fulfillment as F
from app.domain.x12 import X12Error
from app.models.config import Partner
from app.models.documents import Document, DocumentLink
from app.models.orders import Order
from app.models.shadow import ShadowMatch
from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import requires
from app.platform.errors import BusinessError
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.schemas.shadow import ShadowCompare, ShadowMatchRead
from app.services import oplog
from app.services.outbound import LIVE_940_FILE, TORONTO
from app.domain.shadow import STATUSES

logger = logging.getLogger(__name__)

READ = "sales.read"
OPERATE = "sales.order.operate"
STEP = "shadow"
DRY_RUN_CONTROL = 0          # never taken from sales_x12_control_seq
MAX_FILE_BYTES = 10_000_000
SYSTEM_OF = {"940_EDI": "RADIANT", "943_EDI": "RADIANT", "810_JSON": "TRUECOMMERCE"}


@dataclass
class ShadowReport:
    imported: int = 0
    already_here: int = 0
    not_imported: int = 0
    compared: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


class ShadowRun:
    def __init__(self, *, uow_factory, outbound, reference_dir: str | None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.uow_factory = uow_factory
        self.outbound = outbound
        self.reference_dir = reference_dir or None
        self.clock = clock

    async def run(self, *, run_id: int | None = None) -> ShadowReport:
        report = ShadowReport()
        if self.reference_dir:
            await self.import_references(report, run_id=run_id)
        await self.compare(report, run_id=run_id)
        return report

    # ── the reference folder ─────────────────────────────────────────────────
    async def import_references(self, report: ShadowReport, *, run_id: int | None = None) -> None:
        root = Path(self.reference_dir)
        if not root.is_dir():
            await oplog.write_alone(self.uow_factory, "ERROR", STEP, f"reference folder {root} is not readable",
                                    run_id=run_id)
            report.not_imported += 1
            return
        for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")):
            rel = str(path.relative_to(root))
            try:
                stat = path.stat()
                if stat.st_size > MAX_FILE_BYTES:
                    raise ValueError(f"larger than {MAX_FILE_BYTES} bytes")
                data = path.read_bytes()
            except (OSError, ValueError) as e:
                report.not_imported += 1
                await oplog.write_alone(self.uow_factory, "WARNING", STEP, f"reference {rel} not imported: {e}",
                                        run_id=run_id)
                continue
            sha = hashlib.sha256(data).hexdigest()
            async with self.uow_factory(SYSTEM_ACTOR) as uow:
                s = uow.session
                found = (await s.execute(select(Document.id, Document.origin).where(
                    Document.sha256 == sha, Document.content.is_not(None)))).first()
                if found is not None:
                    if found.origin == "ZOHO_REFERENCE":
                        report.already_here += 1
                    else:
                        report.not_imported += 1
                        await oplog.write(s, "WARNING", STEP, f"reference {rel}: same bytes as document {found.id} "
                                          f"({found.origin}); not imported", run_id=run_id, document_id=found.id)
                        await uow.commit()
                    continue
                c = classify(data)
                doc = Document(direction="OUT", origin="ZOHO_REFERENCE", system=SYSTEM_OF.get(c.kind), kind=c.kind,
                               remote_name=rel, size=len(data),
                               remote_mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc), sha256=sha,
                               content=data, isa13=c.isa13, gs06=c.gs06, st02=c.st02, shadow=True,
                               body_json=_reference_keys(data) if c.kind == "940_EDI" else None,
                               error=None if c.readable else c.problem)
                s.add(doc)
                await s.flush()
                await oplog.write(s, "INFO", STEP, f"reference {rel} stored as {c.kind} (never sent)",
                                  run_id=run_id, document_id=doc.id)
                await uow.commit()
                report.imported += 1

    # ── the comparison ───────────────────────────────────────────────────────
    async def compare(self, report: ShadowReport, *, run_id: int | None = None) -> None:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            refs = list((await uow.session.execute(select(Document.id).where(
                Document.origin == "ZOHO_REFERENCE", Document.kind == "940_EDI").order_by(Document.id))).scalars())
        matched: set[int] = set()
        for ref_id in refs:
            try:
                async with self.uow_factory(SYSTEM_ACTOR) as uow:
                    status, order_id = await self._compare_one(uow, ref_id)
                    await uow.commit()
            except Exception as e:  # noqa: BLE001 — one reference never stops the others
                logger.exception("shadow compare of document %s failed", ref_id)
                await oplog.write_alone(self.uow_factory, "ERROR", STEP, f"reference {ref_id}: comparison failed: {e}",
                                        run_id=run_id, document_id=ref_id)
                continue
            if order_id is not None:
                matched.add(order_id)
            report.compared += 1
            report.by_status[status] = report.by_status.get(status, 0) + 1
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            n = await self._sales_only(uow, matched)
            await uow.commit()
        if n:
            report.by_status["Sales only"] = n

    async def _dry_run(self, s, order: Order) -> bytes:
        partner = (await s.execute(select(Partner).where(Partner.code == order.partner_code))).scalar_one()
        snap = await self.outbound.snapshot(s, order, partner)
        return build_940(snap, DRY_RUN_CONTROL, self.clock().astimezone(TORONTO)).content

    async def _find_order(self, s, data: bytes) -> tuple[list[Order], str | None]:
        ref = reference_940(data)
        if ref.po:
            q = select(Order).where(Order.source_type == "EDI_850", Order.source_order_number == ref.po)
            key = ref.po
        else:
            key = (ref.order_number or "").lstrip("#")
            q = select(Order).where(Order.source_type == "SHOPIFY",
                                    func.ltrim(Order.source_order_number, "#") == key)
        return list((await s.execute(q.order_by(Order.id))).scalars()), key

    async def _compare_one(self, uow, ref_id: int) -> tuple[str, int | None]:
        s = uow.session
        content = (await s.execute(select(Document.content).where(Document.id == ref_id))).scalar_one()
        row = (await s.execute(select(ShadowMatch).where(ShadowMatch.reference_document_id == ref_id,
                                                         ShadowMatch.kind == "940").with_for_update())).scalar_one_or_none()
        values: dict[str, Any] = dict(kind="940", order_id=None, reference_number=None, partner_code=None,
                                      sales_text=None, rows=None, differences=0, diff_signature=None, note=None)
        try:
            orders, key = await self._find_order(s, content)
            values["reference_number"] = key
        except X12Error as e:
            values |= dict(status="Unreadable", note=str(e))
            orders = None
        if orders is not None:
            if len(orders) != 1:
                values |= dict(status="No Sales order",
                               note=None if not orders else f"{len(orders)} orders match: {', '.join(str(o.order_number) for o in orders)}")
            else:
                order = orders[0]
                sales = await self._dry_run(s, order)
                c = compare(content, sales)
                values |= dict(order_id=order.id, partner_code=order.partner_code, sales_text=sales.decode("ascii", "replace"),
                               rows=c.rows, differences=c.differences, diff_signature=c.signature)
                if c.differences == 0:
                    values["status"] = "Match"
                elif row is not None and row.expected_signature and row.expected_signature == c.signature:
                    values["status"] = "Expected difference"
                else:
                    values["status"] = "Differs"
        await self._write(s, row, values, reference_document_id=ref_id)
        return values["status"], values["order_id"]

    async def _sales_only(self, uow, matched: set[int]) -> int:
        """Shadow orders that would qualify for a 940 now and that no reference matched."""
        s = uow.session
        now = self.clock()
        rows = (await s.execute(select(Order, Partner).join(Partner, Partner.code == Order.partner_code).where(
            Order.shadow.is_(True), Order.fulfillment_status == F.PENDING.value).order_by(Order.id).limit(2000))).all()
        want: dict[int, Order] = {}
        for o, p in rows:
            facts, pf = facts_of(o, p)
            if o.id not in matched and qualifies(replace(facts, shadow=False), pf, now)[0]:
                want[o.id] = o
        existing = {r.order_id: r for r in (await s.execute(select(ShadowMatch).where(
            ShadowMatch.reference_document_id.is_(None), ShadowMatch.kind == "940").with_for_update())).scalars()}
        for oid, r in existing.items():
            if oid not in want:
                await s.delete(r)  # a reference arrived, or it no longer qualifies
        for oid, o in want.items():
            sales = await self._dry_run(s, o)
            await self._write(s, existing.get(oid), dict(
                kind="940", status="Sales only", order_id=oid, reference_number=o.source_order_number,
                partner_code=o.partner_code, sales_text=sales.decode("ascii", "replace"), rows=None, differences=0,
                diff_signature=None, note="no Zoho 940 in the reference folder for this order"), reference_document_id=None)
        await s.flush()
        return len(want)

    async def _write(self, s, row: ShadowMatch | None, values: dict[str, Any], *, reference_document_id: int | None) -> None:
        """Only a change is written (and audited)."""
        if row is None:
            s.add(ShadowMatch(reference_document_id=reference_document_id, compared_at=self.clock(), version=1, **values))
            return
        if any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            row.compared_at = self.clock()


    # ── real 997s and 945s against the references (moved from the shadow run to P5) ──
    async def match_997(self, uow, doc: Document, groups) -> None:
        """A 997 Radiant sent for a Zoho 940: the reference with that group /
        set control, and what Sales would have recorded. Never applied."""
        s = uow.session
        sets = [(g, st) for g in groups for st in (g.sets or [])]
        ref = None
        for g, st in sets:
            ref = (await s.execute(select(Document).where(
                Document.origin == "ZOHO_REFERENCE", Document.kind == "940_EDI",
                func.ltrim(Document.gs06, "0") == g.group_control.lstrip("0"),
                func.ltrim(Document.st02, "0") == st.control.lstrip("0")).limit(1))).scalar_one_or_none()
            if ref is not None:
                break
        would = {"acks": [{"functional_id": g.functional_id, "group_control": g.group_control, "set": st.type,
                           "set_control": st.control, "accepted": st.accepted, "code": st.code, "reasons": st.reasons}
                          for g, st in sets],
                 "would": ("mark the 940 Accepted" if all(st.accepted for _, st in sets) else
                           "mark the 940 Rejected and raise a task") if sets else "nothing (no AK2)"}
        order_id = None
        if ref is not None and ref.body_json:
            orders = await _orders_for_reference(s, ref.body_json)
            order_id = orders[0].id if len(orders) == 1 else None
        await self._match_row(s, doc, kind="997", ref=ref, order_id=order_id, key=sets[0][1].control if sets else None,
                              status="Match" if ref is not None else "No reference", would=would)

    async def match_945(self, uow, doc: Document, sets, plans) -> None:
        """A 945 for a Zoho 940: the reference by W05 (order number / PO) and
        what applying it would have done (the plan: order, lines, short,
        checks, status). Never applied."""
        s = uow.session
        sh, plan = sets[0], plans[0]
        ref = await _reference_for_key(s, sh.order_key_raw, sh.po)
        would = {"sets": [{"order_key": x.order_key_raw, "ship_date": str(x.ship_date) if x.ship_date else None,
                           "rows": len(x.rows), "shipped_ea": sum(r.qty_shipped for r in x.rows), **p.summary()}
                          for x, p in zip(sets, plans)]}
        status = "No reference" if ref is None else ("Match" if plan.order_id is not None else "No Sales order")
        await self._match_row(s, doc, kind="945", ref=ref, order_id=plan.order_id, key=sh.order_key_raw,
                              status=status, would=would, note=plan.match_note)

    async def _match_row(self, s, doc: Document, *, kind: str, ref: Document | None, order_id: int | None,
                         key: str | None, status: str, would: dict, note: str | None = None) -> None:
        row = (await s.execute(select(ShadowMatch).where(ShadowMatch.document_id == doc.id))).scalar_one_or_none()
        partner = (await s.execute(select(Order.partner_code).where(Order.id == order_id))).scalar_one_or_none() if order_id else None
        await self._write(s, row, dict(kind=kind, status=status, order_id=order_id, reference_number=(key or "")[:100] or None,
                                       partner_code=partner, sales_text=None, rows=None, differences=0, diff_signature=None,
                                       note=note, document_id=doc.id, would=would),
                          reference_document_id=ref.id if ref is not None else None)
        await s.flush()


def _reference_keys(data: bytes) -> dict | None:
    try:
        r = reference_940(data)
    except X12Error:
        return None
    return {"w05_02": r.order_number, "w05_03": r.po}


async def _orders_for_reference(s, keys: dict) -> list[Order]:
    if keys.get("w05_03"):
        q = select(Order).where(Order.source_type == "EDI_850", Order.source_order_number == keys["w05_03"])
    else:
        q = select(Order).where(Order.source_type == "SHOPIFY",
                                func.ltrim(Order.source_order_number, "#") == (keys.get("w05_02") or "").lstrip("#"))
    return list((await s.execute(q.order_by(Order.id))).scalars())


async def _reference_for_key(s, key_raw: str | None, po: str | None) -> Document | None:
    """The Zoho 940 a 945 answers: W06-06 = its W05 03 (PO), else W06-02 = its
    W05 02 (zero-padding and one trailing letter ignored, FR-OB-28)."""
    from app.domain.edi945 import order_key
    refs = list((await s.execute(select(Document).where(
        Document.origin == "ZOHO_REFERENCE", Document.kind == "940_EDI").order_by(Document.id.desc()))).scalars())
    want = order_key(key_raw or "")
    for ref in refs:
        keys = ref.body_json
        if keys is None:  # imported before the keys were kept
            content = (await s.execute(select(Document.content).where(Document.id == ref.id))).scalar_one()
            keys = ref.body_json = _reference_keys(content) or {}
        if po and keys.get("w05_03") == po:
            return ref
        if want.base is not None and keys.get("w05_02") and order_key(keys["w05_02"]).base == want.base:
            return ref
    return None


# ── switching the shadow run on ──────────────────────────────────────────────

async def mark_open_orders(uow, *, run_id: int | None = None) -> int:
    """At switch-on, every open order becomes Zoho's (shadow = true), so none
    becomes eligible for a 940 when the shadow run ends (cutover risk found on
    dev2, 2026-11-02). Open: not Shipped, not Cancelled, not historical, and no
    live 940 (that order is already Sales'). Each change is audited; switching
    off never clears the flag."""
    s = uow.session
    live = exists().where(and_(
        DocumentLink.entity_type == "order", DocumentLink.entity_id == Order.id,
        Document.id == DocumentLink.document_id, Document.kind == "940_EDI", Document.direction == "OUT",
        Document.file_status.in_([f.value for f in LIVE_940_FILE]),
        Document.ack_status.not_in((Ack.REJECTED.value, Ack.TIMEOUT.value))))
    orders = list((await s.execute(select(Order).where(
        Order.shadow.is_(False), Order.historical.is_(False),
        Order.fulfillment_status.not_in((F.SHIPPED.value, F.CANCELLED.value)), ~live,
    ).order_by(Order.id).with_for_update())).scalars())
    for o in orders:
        o.shadow = True
    await s.flush()
    await oplog.write(s, "INFO", STEP, f"shadow run on: {len(orders)} open order(s) marked as Zoho's (never sent a 940 by Sales)",
                      run_id=run_id, context={"orders": [o.id for o in orders][:500]})
    return len(orders)


# ── the page ─────────────────────────────────────────────────────────────────

class ShadowMatchRepository(Repository[ShadowMatch]):
    model = ShadowMatch
    search_fields = ("reference_number", "partner_code", "note", "expected_reason")
    list_fields = ("id", "kind", "status", "reference_document_id", "document_id", "order_id", "reference_number",
                   "partner_code", "differences", "expected_reason", "note", "compared_at")
    labels = {"reference_document_id": "Zoho file", "document_id": "Warehouse file", "order_id": "Order",
              "reference_number": "Order no. / PO",
              "expected_reason": "Why expected"}
    enums = {"status": STATUSES}
    default_sort = ("-compared_at",)
    groupable = ("status", "partner_code", "kind")
    presets = (
        Preset("all", "All"),
        Preset("to_check", "To check", {"filters": [{"field": "status", "op": "in",
                                                     "value": ["Differs", "No Sales order", "Sales only", "Unreadable",
                                                               "No reference"]}]}),
        Preset("answers", "997 / 945", {"filters": [{"field": "kind", "op": "in", "value": ["997", "945"]}]}),
        Preset("differs", "Differs", {"filters": [{"field": "status", "op": "eq", "value": "Differs"}]}),
        Preset("match", "Match", {"filters": [{"field": "status", "op": "in", "value": ["Match", "Expected difference"]}]}),
    )


class ShadowMatchService(ReadOnlyService[ShadowMatch, ShadowMatchRead]):
    repo_cls = ShadowMatchRepository
    read_schema = ShadowMatchRead
    read_permission = READ

    @requires(READ)
    async def summary(self) -> dict[str, int]:
        rows = (await self.uow.session.execute(select(ShadowMatch.status, func.count()).group_by(ShadowMatch.status))).all()
        counts = {s: 0 for s in STATUSES}
        counts.update({k: v for k, v in rows})
        return counts

    @requires(READ)
    async def compare_view(self, id: int) -> ShadowCompare:
        m = await self.repo.get(id)
        name = None
        if m.reference_document_id is not None:
            name = (await self.uow.session.execute(select(Document.remote_name).where(
                Document.id == m.reference_document_id))).scalar_one_or_none()
        rows = m.rows
        if rows is None and m.sales_text:  # Sales only: no Zoho side
            from app.domain.x12 import segments
            rows = [{"zoho": None, "sales": "*".join(seg), "same": False} for seg in segments(m.sales_text)]
        doc_name = None
        if m.document_id is not None:
            doc_name = (await self.uow.session.execute(select(Document.remote_name).where(
                Document.id == m.document_id))).scalar_one_or_none()
        return ShadowCompare(**ShadowMatchRead.model_validate(m).model_dump(), rows=rows or [],
                             reference_name=name, ignored=ignore_rules() if m.kind == "940" else [],
                             document_name=doc_name, would=m.would)

    @requires(OPERATE)
    async def mark_expected(self, id: int, reason: str, version: int | None) -> ShadowMatchRead:
        """The differences are known and accepted — while they stay the same
        (a later run that finds other differences sets it back to Differs)."""
        m = await self.repo.get(id, for_update=True)
        if version is not None and m.version != version:
            raise BusinessError("entity.stale", "someone else changed this comparison", status_code=409)
        if m.status != "Differs":
            raise BusinessError("shadow.not_differs", f"only a comparison that differs can be marked (it is {m.status})")
        self.uow.set_reason(f"expected difference: {reason}")
        m.status, m.expected_signature, m.expected_reason = "Expected difference", m.diff_signature, reason
        await self.uow.session.flush()
        return self.to_read(m)
