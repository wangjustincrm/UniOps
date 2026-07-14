"""Phase B — transform staged JSON and load into the EPMS database.

Dependency order: masters/resolvers → PR → PO → Invoice → PA → back-fill PR.po_id.

Safety model:
  * dry_run=True  (default): build & resolve everything in memory, run ONLY
    read-only SELECTs against the DB, never INSERT/commit. Reports what *would*
    happen. Safe to point at production.
  * dry_run=False (requires explicit --commit): add rows in batches inside a
    single transaction and commit once at the end (atomic, idempotent).
"""
from __future__ import annotations

import secrets
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

import app.models  # noqa: F401 — register all ORM models
from app.core.config import settings
from app.core.security import hash_password
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.gr import GrLineItem
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pa import PaLineItem, PaymentApplication
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PrLineItem, PurchaseRequest
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.invoice import InvoiceLineItem

from . import mappings as M
from .extract import load_staging
from .transform import clip, nz, to_date, to_decimal, to_dt

SYSTEM_USER_EMAIL = "migration@epms.local"
UNKNOWN_VENDOR_CODE = "PMS-UNKNOWN"


@dataclass
class Report:
    inserted: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    updated: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skipped_existing: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skipped_conflict: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    pr_missing_number: int = 0
    pa_orphan_no_po: int = 0
    invoices_no_vendor: int = 0
    invoices_discarded_no_po: int = 0
    created_vendors: int = 0
    temp_users_created: int = 0
    applier_fallback: int = 0
    unmatched_vendors: set = field(default_factory=set)
    unmapped_cost_centers: set = field(default_factory=set)
    unmatched_appliers: set = field(default_factory=set)
    xwalk_emails_missing: set = field(default_factory=set)

    def to_dict(self, dry_run: bool, sample: int = 30) -> dict:
        """JSON-serializable summary for the admin API / run history."""
        def _s(x):
            xs = sorted(str(i) for i in x)
            return {"count": len(xs), "sample": xs[:sample]}
        return {
            "dry_run": dry_run,
            "inserted": dict(self.inserted),
            "updated": dict(self.updated),
            "skipped_existing": dict(self.skipped_existing),
            "skipped_conflict": dict(self.skipped_conflict),
            "pr_missing_number": self.pr_missing_number,
            "pa_orphan_no_po": self.pa_orphan_no_po,
            "invoices_no_vendor": self.invoices_no_vendor,
            "invoices_discarded_no_po": self.invoices_discarded_no_po,
            "created_vendors": self.created_vendors,
            "temp_users_created": self.temp_users_created,
            "applier_fallback": self.applier_fallback,
            "unmatched_vendors": _s(self.unmatched_vendors),
            "unmapped_cost_centers": _s(self.unmapped_cost_centers),
            "unmatched_appliers": _s(self.unmatched_appliers),
            "xwalk_emails_missing": _s(self.xwalk_emails_missing),
        }

    def show(self, dry_run: bool) -> None:
        head = "DRY-RUN (no changes written)" if dry_run else "COMMITTED"
        print(f"\n===== Load report [{head}] =====")
        print("  Inserted:")
        for k in ("vendors", "pr", "pr_items", "po", "po_items", "invoices", "pa", "pa_items"):
            print(f"    {k:12} {self.inserted.get(k, 0)}")
        if any(self.updated.values()):
            print("  Updated (incremental):")
            for k in ("pr", "pr_items", "po", "po_items", "pa", "pa_items", "invoices"):
                print(f"    {k:12} {self.updated.get(k, 0)}")
        if any(self.skipped_conflict.values()):
            print("  Skipped (locally edited in EPMS / still referenced):")
            for k in ("pr", "pr_items", "po", "po_items", "pa", "pa_items", "invoices"):
                print(f"    {k:12} {self.skipped_conflict.get(k, 0)}")
        print("  Skipped (already in EPMS, idempotent):")
        for k in ("pr", "po", "pa", "invoices"):
            print(f"    {k:12} {self.skipped_existing.get(k, 0)}")
        print(f"  PR rows without a PR No (skipped):     {self.pr_missing_number}")
        print(f"  PA rows with unresolved PO (skipped):  {self.pa_orphan_no_po}")
        print(f"  Invoices discarded (no PO/PA link):    {self.invoices_discarded_no_po}")
        print(f"  Invoices with no resolvable vendor:    {self.invoices_no_vendor}")
        print(f"  Auto-created vendors (missing POID):   {self.created_vendors}")
        print(f"  Temporary user accounts created:       {self.temp_users_created}")
        print(f"  Docs routed to system user (blank Applier): {self.applier_fallback}")
        _sample("Unmatched vendor names", self.unmatched_vendors)
        _sample("Unmapped cost-center identifiers", self.unmapped_cost_centers)
        _sample("Unmatched appliers", self.unmatched_appliers)
        _sample("Crosswalk emails missing in EPMS", self.xwalk_emails_missing)


def _sample(label: str, s: set, n: int = 15) -> None:
    if not s:
        return
    items = sorted(str(x) for x in s)
    extra = f" (+{len(items) - n} more)" if len(items) > n else ""
    print(f"  {label} [{len(items)}]: {', '.join(items[:n])}{extra}")


class _Adder:
    """Batches db.add + flush in real runs; a no-op in dry-run."""

    def __init__(self, db: AsyncSession, dry_run: bool, batch_size: int = 500):
        self.db, self.dry_run, self.batch_size = db, dry_run, batch_size
        self._n = 0

    async def add(self, obj) -> None:
        if self.dry_run:
            return
        self.db.add(obj)
        self._n += 1
        if self._n % self.batch_size == 0:
            await self.db.flush()

    async def add_now(self, obj) -> None:
        """Add + flush immediately — for parent rows (vendors/users) that later
        rows FK-reference, so they always exist before dependents are flushed."""
        if self.dry_run:
            return
        self.db.add(obj)
        await self.db.flush()

    async def flush(self) -> None:
        if not self.dry_run:
            await self.db.flush()


# ── Resolver context ────────────────────────────────────────────────────────────

class Resolvers:
    def __init__(self, db: AsyncSession, adder: _Adder, report: Report):
        self.db, self.adder, self.report = db, adder, report
        self.vendor_by_code: dict[str, tuple[uuid.UUID, str]] = {}
        self.vl_by_code: dict[str, dict] = {}  # POID-candidate code → vendorlist row
        self.vendor_codes: set[str] = set()
        self.user_by_email: dict[str, uuid.UUID] = {}
        self.user_by_fullname: dict[str, uuid.UUID] = {}
        self.cc_by_code: dict[str, tuple[uuid.UUID, str, str]] = {}
        self.system_user_id: uuid.UUID | None = None
        self.unknown_vendor: tuple[uuid.UUID, str] | None = None

    async def load(self) -> None:
        for vid, code, name in (await self.db.execute(
            select(Vendor.id, Vendor.code, Vendor.name)
        )).all():
            self.vendor_by_code[code] = (vid, name)
            self.vendor_codes.add(code)
        # vendorlist (POID → details) — used to enrich a vendor we must create
        for v in load_staging("vendorlist.json"):
            poid = v.get("POID")
            if poid is None:
                continue
            for cand in M.poid_code_candidates(poid):
                self.vl_by_code.setdefault(cand, v)
        for uid, email, full_name in (await self.db.execute(
            select(User.id, User.email, User.full_name)
        )).all():
            self.user_by_email[email.strip().lower()] = uid
            if full_name and full_name.strip():
                self.user_by_fullname.setdefault(full_name.strip().lower(), uid)
        dept_name = {
            did: dname
            for did, dname in (await self.db.execute(select(Department.id, Department.name))).all()
        }
        for cid, code, name, did in (await self.db.execute(
            select(CostCenter.id, CostCenter.code, CostCenter.name, CostCenter.department_id)
        )).all():
            self.cc_by_code[code] = (cid, name, dept_name.get(did, ""))

        # Report crosswalk emails that don't exist in EPMS yet.
        for email in set(M.USER_XWALK.values()):
            if email.strip().lower() not in self.user_by_email:
                self.report.xwalk_emails_missing.add(email)

        self.system_user_id = await self._ensure_system_user()
        self.unknown_vendor = await self._ensure_unknown_vendor()

    async def _ensure_system_user(self) -> uuid.UUID:
        existing = self.user_by_email.get(SYSTEM_USER_EMAIL)
        if existing:
            return existing
        uid = uuid.uuid4()
        u = User(
            id=uid, email=SYSTEM_USER_EMAIL,
            hashed_password=hash_password(secrets.token_urlsafe(24)),
            full_name="PMS Migration", role="system_admin",
            is_active=True, must_change_password=True,
        )
        await self.adder.add_now(u)
        self.user_by_email[SYSTEM_USER_EMAIL] = uid
        return uid

    async def _ensure_unknown_vendor(self) -> tuple[uuid.UUID, str]:
        for vid, code, name in (await self.db.execute(
            select(Vendor.id, Vendor.code, Vendor.name).where(Vendor.code == UNKNOWN_VENDOR_CODE)
        )).all():
            return (vid, name)
        vid = uuid.uuid4()
        v = Vendor(
            id=vid, code=UNKNOWN_VENDOR_CODE, name="PMS Unknown Vendor",
            category="general", contact_name="N/A",
            contact_email="noreply@canadaroyalmilk.ca", payment_terms="net30",
        )
        await self.adder.add_now(v)
        self.vendor_codes.add(UNKNOWN_VENDOR_CODE)
        return (vid, "PMS Unknown Vendor")

    # ── resolution helpers ──
    async def user_for(self, applier: str | None) -> uuid.UUID:
        # 1) explicit crosswalk (user.txt + aliases)
        email = M.resolve_user_email(applier)
        if email:
            uid = self.user_by_email.get(email.strip().lower())
            if uid:
                return uid
        if not applier or not applier.strip():
            self.report.applier_fallback += 1
            return self.system_user_id  # type: ignore[return-value]
        name = applier.strip()
        # 2a) match by EPMS user full_name (accounts added with full_name = applier)
        uid = self.user_by_fullname.get(name.lower())
        if uid:
            return uid
        # 2b) match by <name>@canadaroyalmilk.com (existing EPMS account)
        if " " not in name:
            uid = self.user_by_email.get(f"{name}@canadaroyalmilk.com".lower())
            if uid:
                return uid
        # 3) auto-create a temporary account so authorship is preserved per person.
        #    Use the crosswalk email when known, else a slug of the name.
        import re
        slug = re.sub(r"[^a-z0-9]", "", name.lower()) or "user"
        addr = (email or f"{slug}@canadaroyalmilk.com").strip().lower()
        uid = self.user_by_email.get(addr)
        if uid:
            return uid
        uid = uuid.uuid4()
        u = User(
            id=uid, email=clip(addr, 255),
            hashed_password=hash_password(secrets.token_urlsafe(24)),
            full_name=clip(name, 255), role="requester",
            is_active=False, must_change_password=True,
        )
        await self.adder.add_now(u)
        self.user_by_email[addr] = uid
        self.report.temp_users_created += 1
        self.report.unmatched_appliers.add(f"{name} -> {addr} (temp)")
        return uid

    def cost_center_for(self, dept: str | None, cc: str | None):
        """Return (cc_id|None, cc_name, dept_name)."""
        code = M.resolve_cc_code(dept, cc)
        if code and code in self.cc_by_code:
            cid, cname, dname = self.cc_by_code[code]
            return cid, cname, dname
        if (dept or cc):
            self.report.unmapped_cost_centers.add(f"{(dept or '').strip()}{(cc or '').strip()}")
        return None, clip(cc, 255), clip(dept, 255)

    async def resolve_vendor(self, po_number: str | None):
        """Resolve a vendor strictly by the POID embedded in the PO number (rule B
        — no name matching). Returns (id, name) or None when there is no POID.
        When the POID isn't an EPMS vendor yet, create one (code=POID, enriched
        from the legacy vendorlist)."""
        tok = M.poid_from_po_number(po_number)
        if not tok:
            return None
        cands = M.poid_code_candidates(tok)
        for cand in cands:
            if cand in self.vendor_by_code:
                return self.vendor_by_code[cand]
        # not in EPMS → create from vendorlist
        vlrow = next((self.vl_by_code[c] for c in cands if c in self.vl_by_code), {})
        code = tok  # the PO-number token is already the canonical vendor code
        name = clip(vlrow.get("Title") or f"PMS Vendor {code}", 255)
        vid = uuid.uuid4()
        v = Vendor(
            id=vid, code=code, name=name, category="general",
            contact_name=clip(vlrow.get("Contractor") or "N/A", 255),
            contact_email=clip(vlrow.get("EmailAddress") or "noreply@canadaroyalmilk.ca", 255),
            phone=clip(vlrow.get("Phone"), 50),
            address=vlrow.get("Address"),
            payment_terms=M.payment_terms_from_days(vlrow.get("NetTerm_x0028_Days_x0029_")),
        )
        await self.adder.add_now(v)
        self.vendor_by_code[code] = (vid, name)
        self.vendor_codes.add(code)
        self.report.created_vendors += 1
        self.report.inserted["vendors"] += 1
        return (vid, name)


# ── helpers ─────────────────────────────────────────────────────────────────────

def _group_items(rows: list[dict], key: str = "Title") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[str(r.get(key) or "").strip()].append(r)
    return out


async def _existing_numbers(db: AsyncSession, col) -> set[str]:
    return {row[0] for row in (await db.execute(select(col))).all()}


async def _existing_map(db: AsyncSession, model, number_col) -> dict[str, tuple]:
    """{number: (id, updated_at)} for upsert + conflict detection."""
    rows = (await db.execute(select(number_col, model.id, model.updated_at))).all()
    return {r[0]: (r[1], r[2]) for r in rows}


def _is_local_edit(updated_at, modified) -> bool:
    """True if the EPMS row was edited after the SharePoint change we're applying
    (so an incremental sync should not clobber it)."""
    return bool(updated_at and modified and updated_at > modified)


def _stamp(obj, modified) -> None:
    """Set updated_at = SharePoint Modified and FORCE it into the UPDATE's SET
    clause. Without flag_modified, re-assigning the same value is a no-op and the
    column's onupdate=now() would override it — corrupting the sync watermark."""
    if modified:
        obj.updated_at = modified
        flag_modified(obj, "updated_at")


# ── main ──────────────────────────────────────────────────────────────────────

async def run_load(
    only: set[str] | None = None,
    dry_run: bool = True,
    batch_size: int = 500,
    db_url: str | None = None,
    mode: str = "insert",
    reconstruct: bool = True,
    dedup_invoices: bool = True,
) -> Report:
    """mode='insert' → new docs only (existing skipped). mode='upsert' →
    incremental: existing docs get header-level updates (unless locally edited),
    new docs are inserted in full."""
    upsert = mode == "upsert"
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    print(f"Target EPMS DB: {host}  [{mode}]  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    report = Report()

    # staged data
    pr_rows = load_staging("pr.json") + load_staging("pr_backup.json")
    po_rows = load_staging("po.json") + [
        {**r, "_from_backup": True} for r in load_staging("po_backup.json")
    ]
    pa_rows = load_staging("pa.json") + load_staging("pa_backup.json")
    pr_items = _group_items(load_staging("pr_item.json"))
    po_items = _group_items(load_staging("po_item.json"))
    pa_items = _group_items(load_staging("pa_item.json"))
    invoice_rows = load_staging("invoice.json")

    # cross-reference id maps (SharePoint id → new UUID)
    prno_to_id: dict[str, uuid.UUID] = {}
    pritem_to_lineid: dict[str, uuid.UUID] = {}
    pono_to_id: dict[str, uuid.UUID] = {}
    pono_to_vendor: dict[str, tuple[uuid.UUID, str]] = {}
    poitem_to_lineid: dict[str, uuid.UUID] = {}
    poitem_to_pono: dict[str, str] = {}
    invid_to_id: dict[int, uuid.UUID] = {}
    # PR linkage by its PONo (for PO.pr_id / PO.type derivation)
    pono_to_pr: dict[str, dict] = {}

    try:
        async with sf() as db:
            adder = _Adder(db, dry_run, batch_size)
            res = Resolvers(db, adder, report)
            await res.load()

            run_all = not only
            pr_map = await _existing_map(db, PurchaseRequest, PurchaseRequest.number)
            po_map = await _existing_map(db, PurchaseOrder, PurchaseOrder.number)
            pa_map = await _existing_map(db, PaymentApplication, PaymentApplication.pa_number)
            existing_pr, existing_po, existing_pa = set(pr_map), set(po_map), set(pa_map)
            existing_inv = await _existing_numbers(db, Invoice.internal_ref)

            # ── PR ──────────────────────────────────────────────────────────────
            if run_all or "pr" in only:
                seen: set[str] = set()
                for r in pr_rows:
                    number = clip(r.get("PR_x0020_No"), 30)
                    if not number:
                        report.pr_missing_number += 1
                        continue
                    if number in M.PR_SKIP:   # business-flagged junk records
                        continue
                    if number in seen:
                        continue
                    seen.add(number)
                    pono = (r.get("PONo") or "").strip() or None
                    if number in existing_pr:
                        if upsert:
                            await _upsert_pr(db, dry_run, report, pr_map[number], r, res,
                                             pr_items.get(number, []))
                        else:
                            report.skipped_existing["pr"] += 1
                        continue
                    vend = await res.resolve_vendor(pono)
                    cc_id, cc_name, dept_name = res.cost_center_for(r.get("Department"), r.get("CostCenter"))
                    created_by = await res.user_for(r.get("Applier"))
                    created_at = to_dt(r.get("Created"))
                    pid = uuid.uuid4()
                    pr = PurchaseRequest(
                        id=pid, number=number,
                        title=clip(nz(r.get("Title"), number), 255),
                        type=M.PR_TYPE_MAP.get((r.get("PRType") or "").strip(), M.PR_TYPE_DEFAULT),
                        status=M.map_pr_status(r.get("Status"), has_po=bool(pono)),
                        currency=M.normalize_currency(r.get("Currency")),
                        amount=to_decimal(r.get("TotalPrice")),
                        vendor_id=vend[0] if vend else None,
                        vendor_name=clip(r.get("Vendor"), 255),
                        cost_center_id=cc_id, cost_center_name=cc_name, department_name=dept_name,
                        budget_code=clip(r.get("GLCode"), 100),
                        project_code=clip(r.get("ProjectNo"), 100),
                        po_number=clip(pono, 30),
                        created_by=created_by,
                        created_at=created_at, updated_at=to_dt(r.get("Modified")) or created_at,
                    )
                    await adder.add(pr)
                    report.inserted["pr"] += 1
                    prno_to_id[number] = pid
                    if pono:
                        pono_to_pr.setdefault(pono, {
                            "pr_id": pid, "pr_number": number,
                            "type": pr.type, "created_by": created_by,
                        })
                    for idx, it in enumerate(pr_items.get(number, [])):
                        lid = uuid.uuid4()
                        li = PrLineItem(
                            id=lid, pr_id=pid,
                            description=clip(nz(it.get("Description"), "(no description)"), 500),
                            material_id=clip(it.get("CRMPartNo"), 50),
                            supplier_item_id=clip(it.get("PartNo"), 100),
                            qty=to_decimal(it.get("Qty")),
                            unit=clip(nz(it.get("UOM"), "EA"), 30),
                            unit_price=to_decimal(it.get("UnitPrice")),
                            line_total=to_decimal(it.get("Total_x0020_Price")),
                            sort_order=idx,
                        )
                        await adder.add(li)
                        report.inserted["pr_items"] += 1
                        pritem_to_lineid[str(it.get("ID"))] = lid
                await adder.flush()

            # ── PO ──────────────────────────────────────────────────────────────
            if run_all or "po" in only:
                # 跨批次 PR 种子:PR 在早前批次已导入(或本次增量没拉到它的行)时,
                # pono_to_pr 不会在 PR 段被填充 —— 新 PO 会丢 pr_id/title/type/created_by。
                # 从库里按 PR.po_number 反查补齐(同 2026-07-06 发票→vendor 的跨批修复)。
                batch_ponos = {clip(r.get("Title"), 40) for r in po_rows} - {None, ""}
                missing_ponos = [n for n in batch_ponos if n not in pono_to_pr]
                if missing_ponos:
                    rows_db = (await db.execute(
                        select(PurchaseRequest.id, PurchaseRequest.number,
                               PurchaseRequest.type, PurchaseRequest.created_by,
                               PurchaseRequest.po_number)
                        .where(PurchaseRequest.po_number.in_(missing_ponos))
                    )).all()
                    for pid_db, prnum_db, ptype_db, pcb_db, pono_db in rows_db:
                        pono_to_pr.setdefault(pono_db, {
                            "pr_id": pid_db, "pr_number": prnum_db,
                            "type": ptype_db, "created_by": pcb_db,
                        })
                seen = set()
                for r in po_rows:
                    number = clip(r.get("Title"), 40)
                    if not number or number in seen:
                        continue
                    seen.add(number)
                    if number in existing_po:
                        if upsert:
                            await _upsert_po(db, dry_run, report, po_map[number], r, res,
                                             po_items.get(number, []))
                        else:
                            report.skipped_existing["po"] += 1
                        continue
                    vid, vname = (await res.resolve_vendor(number)) or res.unknown_vendor
                    pr_link = pono_to_pr.get(number)
                    lines = po_items.get(number, [])
                    subtotal = sum((to_decimal(it.get("TotalPrice")) for it in lines), Decimal("0"))
                    created_at = to_dt(r.get("Created"))
                    oid = uuid.uuid4()
                    po = PurchaseOrder(
                        id=oid, number=number,
                        title=clip(pr_link["pr_number"] if pr_link else number, 255),
                        type=pr_link["type"] if pr_link else M.PR_TYPE_DEFAULT,
                        status=M.map_po_status(r.get("Status"), r.get("Status0"), r.get("ReceiveStatus"), r.get("PaymentStatus"), r.get("_from_backup", False)),
                        approval_step_idx=M.po_approval_step_idx(r.get("Status")),
                        currency=M.normalize_currency(r.get("Currency")),
                        subtotal=subtotal,
                        tax_rate=Decimal("0"), tax_amount=Decimal("0"),
                        total=to_decimal(r.get("TotalPrice"), default=subtotal),
                        vendor_id=vid, vendor_name=clip(vname, 255),
                        is_prepaid=bool(r.get("PayFirst")),
                        notes=_po_notes(r),
                        pr_id=pr_link["pr_id"] if pr_link else None,
                        pr_number=clip(pr_link["pr_number"], 30) if pr_link else None,
                        created_by=pr_link["created_by"] if pr_link else res.system_user_id,
                        created_at=created_at, updated_at=to_dt(r.get("Modified")) or created_at,
                    )
                    await adder.add(po)
                    report.inserted["po"] += 1
                    pono_to_id[number] = oid
                    pono_to_vendor[number] = (vid, vname)
                    for idx, it in enumerate(lines):
                        lid = uuid.uuid4()
                        li = PoLineItem(
                            id=lid, po_id=oid,
                            pr_line_id=pritem_to_lineid.get(str(it.get("PRITEMID"))),
                            description=clip(nz(it.get("Description"), "(no description)"), 500),
                            supplier_item_id=clip(it.get("PartNo"), 100),
                            qty=to_decimal(it.get("QTY")),
                            unit=clip(nz(it.get("UOM"), "EA"), 30),
                            unit_price=to_decimal(it.get("UnitPrice")),
                            line_total=to_decimal(it.get("TotalPrice")),
                            received_qty=to_decimal(it.get("ReceivedQTY")),
                            sort_order=idx,
                        )
                        await adder.add(li)
                        report.inserted["po_items"] += 1
                        sp_id = str(it.get("ID"))
                        poitem_to_lineid[sp_id] = lid
                        poitem_to_pono[sp_id] = number
                await adder.flush()

            # ── Invoices ──────────────────────────────────────────────────────────
            if run_all or "invoice" in only:
                # aggregate amount + po linkage from PO items (preferred) then PA items
                inv_amount: dict[int, Decimal] = defaultdict(Decimal)
                inv_pono: dict[int, str] = {}
                for it in load_staging("po_item.json"):
                    pono = str(it.get("Title") or "").strip()
                    # poitem_to_pono is otherwise only filled for POs inserted this
                    # run; seed it for every staged PO item so the pa_item linkage
                    # path (below) resolves invoices tied to already-imported POs.
                    if pono:
                        poitem_to_pono.setdefault(str(it.get("ID")), pono)
                    iid = it.get("InvoiceID")
                    if iid:
                        inv_amount[int(iid)] += to_decimal(it.get("TotalPrice"))
                        # Only map to a NON-EMPTY PO number: a po_item row with a
                        # blank Title must not poison inv_pono via setdefault (it
                        # would lock the invoice to '' → unresolvable → wrongly
                        # "PMS Unknown Vendor" even when a LATER row carries the
                        # real PO number).
                        if pono:
                            inv_pono.setdefault(int(iid), pono)
                # invoice → "paid?" via the PA that references it
                pa_status_by_no = {
                    str(r.get("Title") or "").strip(): (r.get("Status") or "")
                    for r in pa_rows
                }
                inv_paid: dict[int, bool] = {}
                for it in load_staging("pa_item.json"):
                    iid = it.get("InvoiceID")
                    if not iid:
                        continue
                    iid = int(iid)
                    if iid not in inv_amount:  # PA-item-only invoice → use its amount/po
                        inv_amount[iid] += to_decimal(it.get("TotalPrice"))
                        po_no = poitem_to_pono.get(str(it.get("POITEMID")))
                        if po_no:
                            inv_pono.setdefault(iid, po_no)
                    st = pa_status_by_no.get(str(it.get("Title") or "").strip(), "")
                    if "PAID" in st.upper():
                        inv_paid[iid] = True

                # Incremental: an invoice usually points at a PO that did NOT
                # change this run, so that PO isn't in pono_to_id / pono_to_vendor
                # (those only hold POs loaded in THIS batch). Seed both maps from
                # the DB for every referenced PO number that's missing, so the
                # invoice inherits its existing PO's vendor + po_id instead of
                # falling back to "PMS Unknown Vendor". (No-op on a full run, where
                # every PO is already in the batch maps.)
                referenced_ponos = {pn for pn in inv_pono.values() if pn}
                missing_ponos = {pn for pn in referenced_ponos if pn not in pono_to_id}
                if missing_ponos:
                    db_pos = (await db.execute(
                        select(PurchaseOrder.number, PurchaseOrder.id,
                               PurchaseOrder.vendor_id, PurchaseOrder.vendor_name)
                        .where(PurchaseOrder.number.in_(missing_ponos))
                    )).all()
                    for num, pid, vid, vname in db_pos:
                        pono_to_id.setdefault(num, pid)
                        pono_to_vendor.setdefault(num, (vid, vname))

                # A PO number counts as "real" if it exists in EPMS (pono_to_id,
                # seeded above) or in the PMS PO List. An invoice whose only link is
                # to a PO in NEITHER points at a phantom PO (deleted from PMS while a
                # po_item lingered) — there's no vendor to resolve, so discard it.
                pms_po_numbers = {str(r.get("Title") or "").strip() for r in po_rows
                                  if str(r.get("Title") or "").strip()}

                # Group SharePoint INVOICE rows by (vendor, Invoice No). The legacy
                # PMS enters one row PER PO, so a single invoice spanning several POs
                # appears as duplicate Invoice Nos — a violation of our Invoice-No
                # uniqueness rule. Collapse each group into ONE invoice; when it
                # covers >1 PO, model the split with line-level invoice_po_allocations
                # (the detail page renders "Purchase Orders (N)" from them).
                inv_groups: dict[tuple, dict] = {}
                for r in invoice_rows:
                    iid = int(r.get("ID"))
                    po_no = inv_pono.get(iid)
                    # Discard invoices with no REAL PO link: either no PO Item/PA
                    # Item references the invoice at all, or the referenced PO exists
                    # in neither EPMS nor the PMS PO List (phantom PO). Both would
                    # otherwise land as a PO-less "PMS Unknown Vendor" placeholder.
                    if not po_no or (po_no not in pono_to_id and po_no not in pms_po_numbers):
                        report.invoices_discarded_no_po += 1
                        continue
                    invoice_no = clip(nz(r.get("Title"), str(iid)), 100)
                    po_id = pono_to_id.get(po_no)
                    vend = pono_to_vendor.get(po_no) or res.unknown_vendor
                    g = inv_groups.setdefault((vend[0], invoice_no), {
                        "iids": [], "vendor": vend, "invoice_no": invoice_no,
                        "po_amounts": {}, "po_order": [], "no_po_amount": Decimal("0"),
                        "rep": r, "rep_iid": iid, "paid_all": True,
                    })
                    g["iids"].append(iid)
                    if iid < g["rep_iid"]:
                        g["rep_iid"], g["rep"] = iid, r
                    amt = inv_amount.get(iid, Decimal("0"))
                    if po_id is not None:
                        if po_id not in g["po_amounts"]:
                            g["po_order"].append((po_id, po_no))
                        # max (not sum) so accidental same-PO duplicate rows don't double-count
                        g["po_amounts"][po_id] = max(g["po_amounts"].get(po_id, Decimal("0")), amt)
                    else:
                        g["no_po_amount"] = max(g["no_po_amount"], amt)
                    if not inv_paid.get(iid):
                        g["paid_all"] = False

                for (_vendor_id, _no), g in inv_groups.items():
                    rep, rep_iid = g["rep"], g["rep_iid"]
                    internal_ref = clip(f"INV-{rep_iid}", 30)
                    if internal_ref in existing_inv:
                        report.skipped_existing["invoices"] += 1
                        for iid in g["iids"]:
                            invid_to_id[iid] = None  # present but unknown uuid
                        continue
                    vend = g["vendor"]
                    distinct_pos = g["po_order"]
                    amount = sum(g["po_amounts"].values(), Decimal("0")) + g["no_po_amount"]
                    inv_date = to_date(rep.get("IssueDate")) or (to_dt(rep.get("Created")) or None)
                    if hasattr(inv_date, "date"):
                        inv_date = inv_date.date()
                    if inv_date is None:
                        inv_date = to_date(rep.get("Created"))
                    primary_id, primary_no = distinct_pos[0] if distinct_pos else (None, None)
                    iuid = uuid.uuid4()

                    # Multi-PO → one synthetic invoice line + allocation per PO.
                    line_items: list = []
                    alloc_rows: list[InvoicePoAllocation] = []
                    if len(distinct_pos) > 1:
                        for pid, pno in distinct_pos:
                            amt = g["po_amounts"][pid]
                            line = InvoiceLineItem(
                                id=uuid.uuid4(), description=clip(f"PO {pno}", 500),
                                quantity=Decimal("1"), unit_price=amt, line_total=amt,
                            )
                            line_items.append(line.model_dump(mode="json"))
                            alloc_rows.append(InvoicePoAllocation(
                                invoice_id=iuid, invoice_line_id=line.id,
                                po_id=pid, po_line_id=None,
                                allocated_amount=amt, allocated_tax=Decimal("0"),
                                allocated_total=amt,
                            ))

                    if vend is res.unknown_vendor:
                        report.invoices_no_vendor += 1
                    inv = Invoice(
                        id=iuid, internal_ref=internal_ref,
                        vendor_invoice_number=g["invoice_no"],
                        vendor_id=vend[0], vendor_name=clip(vend[1], 255),
                        amount=amount, tax_amount=Decimal("0"), total_amount=amount,
                        invoice_date=inv_date, due_date=inv_date,
                        status="paid" if g["paid_all"] else "matched",
                        line_items=line_items,
                        uploaded_by=res.system_user_id,
                        uploaded_by_name="PMS Migration",
                        po_id=primary_id, po_number=clip(primary_no, 40) if primary_no else None,
                        created_at=to_dt(rep.get("Created")),
                        updated_at=to_dt(rep.get("Modified")) or to_dt(rep.get("Created")),
                    )
                    await adder.add(inv)
                    for a in alloc_rows:
                        await adder.add(a)
                    report.inserted["invoices"] += 1
                    for iid in g["iids"]:
                        invid_to_id[iid] = iuid  # all source rows point to the merged invoice
                await adder.flush()

            # ── PA ──────────────────────────────────────────────────────────────
            if run_all or "pa" in only:
                seen = set()
                for r in pa_rows:
                    number = clip(r.get("Title"), 30)
                    if not number or number in seen:
                        continue
                    seen.add(number)
                    if number in existing_pa:
                        if upsert:
                            await _upsert_pa(db, dry_run, report, pa_map[number], r, res,
                                             pa_items.get(number, []))
                        else:
                            report.skipped_existing["pa"] += 1
                        continue
                    po_no = (r.get("PONO") or "").strip() or None
                    po_id = pono_to_id.get(po_no) if po_no else None
                    if not po_id:
                        report.pa_orphan_no_po += 1
                        continue  # po_id is NOT NULL — cannot import without a PO
                    vend = pono_to_vendor.get(po_no) or (await res.resolve_vendor(po_no)) or res.unknown_vendor
                    lines = pa_items.get(number, [])
                    subtotal = to_decimal(
                        r.get("ItemsTotal"),
                        default=sum((to_decimal(it.get("TotalPrice")) for it in lines), Decimal("0")),
                    )
                    tax = to_decimal(r.get("Tax"))
                    shipping = to_decimal(r.get("FreightFee"))
                    other = to_decimal(r.get("OtherFee"))
                    payment = to_decimal(r.get("TotalPrice"), default=subtotal + tax + shipping + other)
                    invoice_ids = []
                    for it in lines:
                        iid = it.get("InvoiceID")
                        if iid and invid_to_id.get(int(iid)):
                            uid = invid_to_id[int(iid)]
                            if str(uid) not in invoice_ids:
                                invoice_ids.append(str(uid))
                    created_at = to_dt(r.get("Created"))
                    auid = uuid.uuid4()
                    pa = PaymentApplication(
                        id=auid, pa_number=number,
                        title=clip(f"Payment for {po_no}", 255),
                        po_id=po_id, po_number=clip(po_no, 40),
                        vendor_id=vend[0], vendor_name=clip(vend[1], 255),
                        invoice_ids=invoice_ids, gr_ids=[], pa_type="regular",
                        subtotal=subtotal, tax_amount=tax, shipping_amount=shipping,
                        other_charges=other, payment_amount=payment,
                        currency=M.normalize_currency(r.get("Currency")),
                        status=M.map_pa_status(r.get("Status"), r.get("Status0")),
                        approval_step_idx=M.pa_approval_step_idx(r.get("Status")),
                        created_by=await res.user_for(r.get("Applier")),
                        created_at=created_at, updated_at=to_dt(r.get("Modified")) or created_at,
                    )
                    await adder.add(pa)
                    report.inserted["pa"] += 1
                    for idx, it in enumerate(lines):
                        li = PaLineItem(
                            id=uuid.uuid4(), pa_id=auid,
                            po_line_id=poitem_to_lineid.get(str(it.get("POITEMID"))),
                            description=clip(nz(it.get("Description"), "(no description)"), 500),
                            qty=to_decimal(it.get("QTY")),
                            unit=clip(nz(it.get("UOM"), "EA"), 30),
                            unit_price=to_decimal(it.get("UnitPrice")),
                            line_total=to_decimal(it.get("TotalPrice")),
                            sort_order=idx,
                        )
                        await adder.add(li)
                        report.inserted["pa_items"] += 1
                await adder.flush()

            # ── back-fill PR.po_id now that PO UUIDs are known ───────────────────
            # PR.po_number is already set; attach the FK for PRs whose PONo
            # resolved to an imported PO. Objects are in the identity map post-flush.
            if not dry_run and (run_all or "pr" in only):
                for r in pr_rows:
                    number = clip(r.get("PR_x0020_No"), 30)
                    pono = (r.get("PONo") or "").strip() or None
                    if number in prno_to_id and pono in pono_to_id:
                        obj = await db.get(PurchaseRequest, prno_to_id[number])
                        if obj is not None:
                            obj.po_id = pono_to_id[pono]
                            # keep updated_at == SP Modified so incremental sync
                            # doesn't mistake this back-fill for a local edit
                            _stamp(obj, to_dt(r.get("Modified")))

            # ── reconstruct approval_events for imported docs ────────────────────
            # Runs alongside the import (same transaction): every PR/PO/PA that
            # lacks an audit trail gets one rebuilt from creator + workflow. See
            # reconstruct.py. HoldBy (AP clerk) is read from the PA source rows.
            if reconstruct:
                from .reconstruct import reconstruct_events
                # Map pa_number → AP-clerk HoldBy. A real name wins over blank/"None"
                # (the backup list has no HoldBy, and the main list stores literal
                # "None" for unheld PAs) so a later blank row can't clobber a real one.
                pa_holdby: dict[str, str | None] = {}
                for r in pa_rows:
                    t = clip(r.get("Title"), 30)
                    if not t:
                        continue
                    hb = (r.get("HoldBy") or "").strip()
                    if hb and hb.lower() != "none":
                        pa_holdby[t] = hb
                    else:
                        pa_holdby.setdefault(t, None)
                await reconstruct_events(
                    db, adder, res, dry_run=dry_run, pa_holdby=pa_holdby, only=only,
                )

            # ── dedup invoices + their attachments ───────────────────────────────
            # Self-healing pass (same transaction): collapses any invoices that
            # share a (vendor, No) into one with multi-PO allocations, and removes
            # duplicate attachment rows. Harmless on a clean DB (finds nothing).
            # See scripts/dedup_invoices.py.
            if dedup_invoices and (run_all or "invoice" in only):
                from scripts.dedup_invoices import dedup_invoices_pass, print_dedup_stats
                dedup_stats = await dedup_invoices_pass(db)
                print_dedup_stats(dedup_stats, dry_run)

            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    report.show(dry_run)
    return report


def _pr_item_vals(it: dict) -> dict:
    return {
        "description": clip(nz(it.get("Description"), "(no description)"), 500),
        "material_id": clip(it.get("CRMPartNo"), 50),
        "supplier_item_id": clip(it.get("PartNo"), 100),
        "qty": to_decimal(it.get("Qty")),
        "unit": clip(nz(it.get("UOM"), "EA"), 30),
        "unit_price": to_decimal(it.get("UnitPrice")),
        "line_total": to_decimal(it.get("Total_x0020_Price")),
    }


def _po_item_vals(it: dict) -> dict:
    return {
        "description": clip(nz(it.get("Description"), "(no description)"), 500),
        "supplier_item_id": clip(it.get("PartNo"), 100),
        "qty": to_decimal(it.get("QTY")),
        "unit": clip(nz(it.get("UOM"), "EA"), 30),
        "unit_price": to_decimal(it.get("UnitPrice")),
        "line_total": to_decimal(it.get("TotalPrice")),
        "received_qty": to_decimal(it.get("ReceivedQTY")),
    }


def _pa_item_vals(it: dict) -> dict:
    return {
        "description": clip(nz(it.get("Description"), "(no description)"), 500),
        "qty": to_decimal(it.get("QTY")),
        "unit": clip(nz(it.get("UOM"), "EA"), 30),
        "unit_price": to_decimal(it.get("UnitPrice")),
        "line_total": to_decimal(it.get("TotalPrice")),
    }


async def _sync_items(db, report, key, model, doc_fk, doc_id, staged, vals_fn, ref_cols) -> None:
    """Refresh a doc's line items from the staged PMS rows on incremental upsert.

    Headers-only upsert left line-item edits made in PMS after the first import
    (e.g. discount rows corrected from positive to negative) stranded in EPMS
    forever. Rows are matched by position (sort_order == staged index): PMS item
    lists are append-ordered and the initial import assigned sort_order from the
    same enumeration. Matching in place keeps line-item UUIDs stable — GR lines,
    PA lines and invoice allocations reference them. Surplus EPMS rows (deleted
    in PMS) are removed only when nothing in ref_cols points at them. An empty
    staged list means the doc wasn't in the item file (or the extract failed) —
    never treat that as "delete everything"."""
    if not staged:
        return
    existing = (await db.execute(
        select(model).where(getattr(model, doc_fk) == doc_id).order_by(model.sort_order)
    )).scalars().all()
    for idx, it in enumerate(staged):
        vals = vals_fn(it)
        if idx < len(existing):
            obj = existing[idx]
            dirty = False
            for k, v in vals.items():
                if getattr(obj, k) != v:
                    setattr(obj, k, v)
                    dirty = True
            if dirty:
                report.updated[key] += 1
        else:
            db.add(model(id=uuid.uuid4(), sort_order=idx, **{doc_fk: doc_id}, **vals))
            report.inserted[key] += 1
    for obj in existing[len(staged):]:
        if any([await db.scalar(select(exists().where(col == obj.id))) for col in ref_cols]):
            report.skipped_conflict[key] += 1
        else:
            await db.delete(obj)
            report.updated[key] += 1


async def _upsert_pr(db, dry_run, report, existing, r, res, items) -> None:
    obj_id, updated_at = existing
    modified = to_dt(r.get("Modified"))
    if _is_local_edit(updated_at, modified):
        report.skipped_conflict["pr"] += 1
        return
    report.updated["pr"] += 1
    if dry_run:
        return
    obj = await db.get(PurchaseRequest, obj_id)
    if obj is None:
        return
    pono = (r.get("PONo") or "").strip() or None
    vend = await res.resolve_vendor(pono)
    cc_id, cc_name, dept_name = res.cost_center_for(r.get("Department"), r.get("CostCenter"))
    obj.status = M.map_pr_status(r.get("Status"), has_po=bool(pono))
    obj.currency = M.normalize_currency(r.get("Currency"))
    obj.amount = to_decimal(r.get("TotalPrice"))
    if vend:
        obj.vendor_id, obj.vendor_name = vend[0], clip(vend[1], 255)
    if cc_id:
        obj.cost_center_id, obj.cost_center_name, obj.department_name = cc_id, cc_name, dept_name
    obj.budget_code = clip(r.get("GLCode"), 100)
    obj.project_code = clip(r.get("ProjectNo"), 100)
    obj.po_number = clip(pono, 30)
    await _sync_items(db, report, "pr_items", PrLineItem, "pr_id", obj_id, items,
                      _pr_item_vals, [PoLineItem.pr_line_id])
    _stamp(obj, modified)


async def _upsert_po(db, dry_run, report, existing, r, res, items) -> None:
    obj_id, updated_at = existing
    modified = to_dt(r.get("Modified"))
    if _is_local_edit(updated_at, modified):
        report.skipped_conflict["po"] += 1
        return
    report.updated["po"] += 1
    if dry_run:
        return
    obj = await db.get(PurchaseOrder, obj_id)
    if obj is None:
        return
    obj.status = M.map_po_status(r.get("Status"), r.get("Status0"), r.get("ReceiveStatus"), r.get("PaymentStatus"), r.get("_from_backup", False))
    obj.approval_step_idx = M.po_approval_step_idx(r.get("Status"))
    obj.currency = M.normalize_currency(r.get("Currency"))
    obj.total = to_decimal(r.get("TotalPrice"), default=obj.total)
    obj.is_prepaid = bool(r.get("PayFirst"))
    obj.notes = _po_notes(r)
    await _sync_items(db, report, "po_items", PoLineItem, "po_id", obj_id, items,
                      _po_item_vals,
                      [PaLineItem.po_line_id, GrLineItem.po_line_id, InvoicePoAllocation.po_line_id])
    if items:
        obj.subtotal = sum((to_decimal(it.get("TotalPrice")) for it in items), Decimal("0"))
    _stamp(obj, modified)


async def _upsert_pa(db, dry_run, report, existing, r, res, items) -> None:
    obj_id, updated_at = existing
    modified = to_dt(r.get("Modified"))
    if _is_local_edit(updated_at, modified):
        report.skipped_conflict["pa"] += 1
        return
    report.updated["pa"] += 1
    if dry_run:
        return
    obj = await db.get(PaymentApplication, obj_id)
    if obj is None:
        return
    subtotal = to_decimal(r.get("ItemsTotal"), default=obj.subtotal)
    tax = to_decimal(r.get("Tax"))
    shipping = to_decimal(r.get("FreightFee"))
    other = to_decimal(r.get("OtherFee"))
    obj.status = M.map_pa_status(r.get("Status"), r.get("Status0"))
    obj.approval_step_idx = M.pa_approval_step_idx(r.get("Status"))
    obj.currency = M.normalize_currency(r.get("Currency"))
    obj.subtotal, obj.tax_amount, obj.shipping_amount, obj.other_charges = subtotal, tax, shipping, other
    obj.payment_amount = to_decimal(r.get("TotalPrice"), default=subtotal + tax + shipping + other)
    await _sync_items(db, report, "pa_items", PaLineItem, "pa_id", obj_id, items,
                      _pa_item_vals, [])
    _stamp(obj, modified)


def _po_notes(r: dict) -> str | None:
    bits = []
    freight = to_decimal(r.get("Freight"))
    if freight:
        bits.append(f"Freight: {freight}")
    if r.get("Comment"):
        bits.append(str(r["Comment"]))
    note = " | ".join(bits)
    return note or None
