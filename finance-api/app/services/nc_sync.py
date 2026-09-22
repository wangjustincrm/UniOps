"""NC65 voucher sync service (UI-triggered full / incremental).

Extraction + transform ported verbatim from scripts/nc_migration/voucher_import.py
(reconciled 0-diff against NC per-account nets, 2026-07-11..13). The whole run is
synchronous (oracledb reads NC, psycopg2 writes our Postgres) and is executed on a
worker thread by the API layer. The CLI script stays for cut-over/emergency use.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings

PK_BOOK = "1001A1100000003CGCBX"            # Canada Royal Milk accounting book
FULL_CONFIRM = "FULL RELOAD"

# NC 辅助核算 global type pks (first 20 chars of a GL_FREEVALUE.typevalueN):
AUX_DEPT = "0001Z0100000000005CS"           # 部门 -> ORG_DEPT
AUX_COSTCENTER = "1003Z31000000000SP6J"     # 成本中心 -> RESA_COSTCENTER
AUX_IOITEM = "0001Z0100000000005CZ"         # 收支项目 -> BD_INOUTBUSICLASS
AUX_BANKACCOUNT = "0001Z010000000001N98"    # 银行账户 -> BD_BANKACCSUB
# NC's combined party archive: 客商 is the UNION of customers and suppliers, not a
# master of its own (user, 2026-09-22) — a supplier can generate its customer twin
# and vice versa, so one 客商 pk can resolve in BD_SUPPLIER, in BD_CUSTOMER, or in
# both (17 of this book's 144, 5 of them under DIFFERENT codes per side).
AUX_PARTNER = "0001Z0100000000005CV"        # 客商 -> BD_SUPPLIER ∪ BD_CUSTOMER
AUX_ITEM = "0001Z0100000000005CX"           # 物料基本信息 -> BD_MATERIAL
AUX_PROJECT = "0001Z0100000000005D1"        # 项目 -> BD_PROJECT
AUX_EMPLOYEE = "0001Z0100000000005CT"       # 人员档案 -> BD_PSNDOC

# BD_ACCASSITEM.CODE of 银行账户. Resolved by CODE, never by name — see
# resolve_bank_account_type_pk.
BANK_ACCOUNT_ITEM_CODE = "0011"

# Curated NC -> EPMS cost-center map (user, 2026-07-12): code decides when present,
# else classify by department.
CC_BY_CODE = {
    "E01": "MOH-0106-E01", "E02": "MOH-0106-E01", "E03": "MOH-0106-E01",
    "E04": "MOH-0106-E01", "E05": "MOH-0106-E01", "E06": "MOH-0106-E01",
    "E07": "MOH-0106-E01", "ENG": "MOH-0106-E01",
    "P01": "MOH-0104-P01", "P02": "MOH-0104-P02", "P03": "MOH-0104-P03",
    "PD": "MOH-0104-P01",
    "Q01": "MOH-0105-LAB", "Q02": "MOH-0105-LAB", "QA": "GA-0105",
    "S02": "MOH-0107-S02", "S03": "SELL-0107-S03", "SC": "GA-0107",
    "H01": "MOH-0101", "HR": "GA-0101",
}
CC_BY_DEPT = {
    "0100": "GA-0100", "0101": "GA-0101", "0103": "GA-0103",
    "0105": "GA-0105", "0107": "GA-0107", "0109": "RD-0109",
    "0110": "SELL-0110", "0111": "SELL-0111", "0112": "SELL-0112", "0113": "SELL-0113",
    # 2026-07-17: these four were missing, costing exactly the 956 lines that
    # unmapped_cc_count had been reporting all along (404+274+163+115).
    # 0106/0104 were an outright oversight — CC_BY_CODE already routes
    # E01-E07/ENG -> MOH-0106-E01 and P01-P03/PD -> MOH-0104-*, so only the
    # dept-only path lost them.
    "0106": "MOH-0106-E01", "0104": "MOH-0104-P01",
    # 0102 (named "Purchasing(NOT USE)") and 0108 have no obvious EPMS
    # counterpart. These two targets are the USER'S call (2026-07-17), not
    # inferred — do not "improve" them from the code's side.
    "0102": "GA-0107", "0108": "RD-0109",
}

# The 5 predreal expense categories. Cost centers on lines under these accounts
# are resolved ACCOUNT-AWARE via budget_actual_cc_map. Every OTHER account keeps
# the CC_BY_CODE/CC_BY_DEPT fallback above — ~20k balance-sheet/other lines rely
# on it and there is no account-aware answer for them.
_PREDREAL_ACCOUNTS = {"5101", "5301", "6601", "6602", "6603"}


def make_category_of(parent_map: dict):
    """account_code -> which of the 5 predreal categories is self-or-ancestor
    (via chart_of_accounts.parent_code), else None. Walks parents (cycle-guarded).
    NEVER infers from code-prefix — a 660303 could sit under 6603 OR 6601."""
    def category_of(code):
        seen: set = set()
        cur = code
        while cur and cur not in seen:
            if cur in _PREDREAL_ACCOUNTS:
                return cur
            seen.add(cur)
            cur = parent_map.get(cur)
        return None
    return category_of


# ── 客商 (combined party) direction ────────────────────────────────────────────
# Which side of the ledger names a CUSTOMER vs a SUPPLIER. Deliberately short and
# curated: only the receivable/payable and revenue accounts carry an unambiguous
# direction. Expense accounts are NOT listed on purpose — a 销售费用 line against
# Walmart can be trade spend payable TO the customer, and guessing there is the
# exact failure this whole change exists to remove.
_CUSTOMER_SIDE_ACCOUNTS = frozenset({"1122", "2203", "6001", "6051"})
_SUPPLIER_SIDE_ACCOUNTS = frozenset({"2202", "2201", "1123"})


def make_partner_side_of(parent_map: dict):
    """account_code -> 'customer' | 'supplier' | None, by walking
    chart_of_accounts.parent_code. NEVER infers from a code prefix, for the same
    reason make_category_of doesn't: a 660303 could sit under 6603 OR 6601."""
    def side_of(code):
        seen: set = set()
        cur = code
        while cur and cur not in seen:
            if cur in _CUSTOMER_SIDE_ACCOUNTS:
                return "customer"
            if cur in _SUPPLIER_SIDE_ACCOUNTS:
                return "supplier"
            seen.add(cur)
            cur = parent_map.get(cur)
        return None
    return side_of


def resolve_partner(vpk, parties, side, uni_sup, uni_cust):
    """A 客商 value pk -> (partner_id, partner_name, outcome).

    NC has no standalone 客商 master: it is the union of BD_SUPPLIER and
    BD_CUSTOMER (user, 2026-09-22), and because either can generate its twin, one
    pk can resolve on both sides — under DIFFERENT codes (5 of this book's 17
    two-sided parties). So this deliberately does NOT reuse the `sup or cust`
    precedence the separate 供应商档案/客户档案 auxiliaries use: those two carry
    their direction in the auxiliary TYPE, while 客商 does not, and supplier-first
    would pick the wrong direction for the bulk of the data (2,398 receivable +
    ~3,120 revenue lines of 9,625).

    Rules, in order:
      * an org-derived row is never a trading party — NC materialises the company
        itself on both sides (BD_CUSTOMER 01010104 / BD_SUPPLIER 9900012, the only
        PK_FINANCEORG-bearing row in each table). Those sides are dropped.
      * resolves on exactly one side -> that side, disabled or not (the history is
        real even when the master has since been retired).
      * resolves on both -> the account's side decides.
      * both, and the account carries no direction -> the one that is still
        enabled, if exactly one is.
      * still tied -> NO id. The NC code is kept as text and the line is counted
        as ambiguous. Defaulting to supplier here would reinstate the bug.
    """
    rec = (parties or {}).get(vpk)
    if not rec:
        return None, (vpk or None), "unknown"
    sup, cust = rec.get("supplier"), rec.get("customer")
    if sup and sup.get("is_org"):
        sup = None
    if cust and cust.get("is_org"):
        cust = None
    if not sup and not cust:
        return None, (rec.get("any_name") or vpk), "org"
    pick = None
    if sup and not cust:
        pick = ("supplier", sup)
    elif cust and not sup:
        pick = ("customer", cust)
    elif side == "customer":
        pick = ("customer", cust)
    elif side == "supplier":
        pick = ("supplier", sup)
    else:
        live = [(k, v) for k, v in (("supplier", sup), ("customer", cust))
                if v.get("enabled")]
        if len(live) == 1:
            pick = live[0]
    if pick is None:
        return None, (sup or cust).get("name") or vpk, "ambiguous"
    kind, row = pick
    idx = uni_sup if kind == "supplier" else uni_cust
    hit = idx.get(row["code"])
    if hit:
        return hit[0], hit[1], kind
    return None, row.get("name") or row["code"], kind + "_unmastered"


class NcSyncError(ValueError):
    """An NC value we refuse to guess about. Aborts the run."""


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


@dataclass
class NcExtract:
    """Raw NC reads, pre-transform. Tests inject a fake one."""
    ccy: dict           # pk_currtype -> currency code
    aux: dict           # freevalueid -> (dept_code, cc_code, io_code, sup_code, cust_code,
                        #                 bank_code)
    vouchers: list      # (pk, year, period, num, explanation, prepareddate, creationtime,
                        #  tallydate, pk_system)
    details: list       # (pk_voucher, detailindex, accountcode, dr, cr, ldr, lcr,
                        #  pk_currtype, excrate1, explanation, assid)
    max_creationtime: str | None
    tallied: set        # EVERY tallied pk in the book (NOT watermark-limited) —
                        # drives the status backfill, see _sync_statuses
    # BD_BANKACCSUB rows referenced by any voucher:
    # (nc_pk, code, accnum, name, accname, currency, bank_name). Upserted into
    # nc_bank_accounts before the lines are written, so jv lines have an id to
    # point at. Default () keeps old fixtures constructible.
    bank_accounts: tuple = ()
    # NC 客商 value pk -> {"supplier": {...}, "customer": {...}, "any_name": str}.
    # Each side carries code/name/enabled/is_org. Built from BD_SUPPLIER and
    # BD_CUSTOMER because 客商 has no master of its own. Default None keeps old
    # fixtures constructible.
    parties: dict | None = None


# ── auxiliary (辅助核算) type resolution ───────────────────────────────────────
# Keyed on BD_ACCASSITEM.CODE, never on the Chinese name. nc_coa_sync already
# learned what substring matching does here (its AUX_ITEM_MAP comment: the old
# NAME_MAP put both 项目类型 and 政府拨款项目 onto `project`), and the voucher
# side had the same bug for a different type: resolve_aux_type_pks matched
# 「供应商」/「客户」, and NC's combined party archive is called 客商 — which
# contains neither. That silently dropped the party on 9,625 lines of this book.
#
# AUX_ITEM_MAP is THE catalog (code -> our dim_code); inverting it here keeps one
# source of truth across the COA sync and the voucher sync. Measured 2026-09-22:
# every code it uses resolves to exactly one BD_ACCASSITEM row on the live book.
# Imported lazily inside _dim_to_code(): nc_coa_sync imports nc_configured from
# THIS module at import time, so a module-level import here is a cycle.


def _dim_to_code() -> dict:
    from app.services.nc_coa_sync import AUX_ITEM_MAP
    return {dim: code for code, dim in AUX_ITEM_MAP.items()}

# The slots decode_aux_row fills. Each is (our dim_code) -> the GL_FREEVALUE
# prefix we look for. Slots with no value-table lookup still land in
# jv_line_dimensions as a raw code, which is what makes them queryable at all.
AUX_SLOTS = (
    "department", "cost_center", "income_expense_item",
    "supplier", "customer", "partner", "bank_account",
    "item", "item_category", "project", "employee",
)

# Frozen pks, validated against the catalog every run so a catalog change is a
# loud failure rather than a silently empty dimension (same contract as
# resolve_bank_account_type_pk).
_AUX_CONSTANTS = {"department": AUX_DEPT, "cost_center": AUX_COSTCENTER,
                  "income_expense_item": AUX_IOITEM, "bank_account": AUX_BANKACCOUNT,
                  "partner": AUX_PARTNER, "item": AUX_ITEM, "project": AUX_PROJECT}

# Slots whose unknown value pks are kept raw rather than dropped. A value whose
# master row is gone still scopes/labels the line; dropping it would silently
# merge it into "unassigned" (3 of 129 live bank values already do this).
_KEEP_RAW_VPK = frozenset({"bank_account", "partner", "item", "project", "employee"})


def resolve_aux_types_by_code(items) -> dict:
    """[(pk_accassitem, code, name)] -> {slot: pk}, resolved BY CODE.

    Refuses to guess: a code that matches several catalog rows raises rather
    than picking one, and every frozen constant must be the pk its code
    resolved to. A slot whose code is absent from the catalog is simply missing
    from the result — that dimension then decodes to empty instead of aborting
    the run, because NC catalogs legitimately differ between orgs.
    """
    dim_to_code = _dim_to_code()
    by_code: dict = {}
    for pk, code, _name in items:
        by_code.setdefault((code or "").strip(), []).append(pk)
    out: dict = {}
    for slot in AUX_SLOTS:
        code = dim_to_code.get(slot)
        if not code:
            continue
        hits = by_code.get(code) or []
        if not hits:
            continue
        if len(set(hits)) > 1:
            raise RuntimeError(
                f"BD_ACCASSITEM code {code!r} ({slot}) matches {len(set(hits))} rows "
                f"{sorted(set(hits))!r} — refusing to guess which auxiliary it is")
        out[slot] = hits[0]
    for slot, const in _AUX_CONSTANTS.items():
        if slot in out and out[slot] != const:
            raise RuntimeError(
                f"aux type pk mismatch for {slot}: constant {const!r} != catalog "
                f"{out[slot]!r} — typevalue-prefix assumption broke")
    return out


def decode_aux_row(typevalues, slots) -> dict:
    """One GL_FREEVALUE row's typevalue1..9 -> {slot: code}, for every slot present.

    `slots` carries the resolved type pks and their code lookups:
      {"department": (pk, {vpk: code}), "partner": (pk, {vpk: code}), ...}
    A slot's `want` may be a single pk or a set (a multi-org catalog can hold
    several 档案 rows for one dimension).

    Returns a DICT, not a positional tuple: this grew from 6 slots to 11 and a
    tuple that long is a misalignment waiting to happen. A dict has no ordering
    to get wrong, and callers name what they read.

    Two NC quirks decide the body: values shorter than 40 chars are absent, and
    '~' is NC's blank sentinel — a '~' value pk must come back empty, not as the
    literal '~'. For slots in _KEEP_RAW_VPK an unknown value pk falls back to the
    raw pk rather than dropping the dimension: 3 of 129 live bank values point at
    BD_BANKACCSUB rows that no longer exist, and dropping them would silently
    merge those banks into "unassigned". Slots outside that set decode to "".
    """
    out = {slot: "" for slot in slots}
    for tv in typevalues:
        if not tv or len(tv) < 40:
            continue
        tpk, vpk = tv[:20], tv[20:40]
        for slot, (want, codes) in slots.items():
            hit = tpk in want if isinstance(want, (set, frozenset)) else tpk == want
            if not hit:
                continue
            clean_vpk = _strip_char(vpk)
            if clean_vpk:
                out[slot] = codes.get(clean_vpk, codes.get(vpk, "")) or (
                    clean_vpk if slot in _KEEP_RAW_VPK else "")
            break
    return out

def resolve_bank_account_type_pk(items) -> str:
    """[(pk_accassitem, code, name)] -> the 银行账户 type pk, resolved BY CODE.

    Deliberately NOT name-matched the way resolve_aux_type_pks matches the other
    five. NC carries three auxiliaries whose names all start 银行 — 银行类别
    (0022), 银行档案 (0023) and 银行账户 (0011) — and nc_coa_sync already learned
    what substring matching does to that kind of set (its AUX_ITEM_MAP comment:
    the old NAME_MAP put both 项目类型 and 政府拨款项目 onto `project`).
    BD_ACCASSITEM.CODE is the stable key.

    Validates against the frozen constant the same way resolve_aux_type_pks does,
    so a catalog change is a loud failure rather than a silently empty dimension.
    """
    hits = {pk for pk, code, _name in items if (code or "").strip() == BANK_ACCOUNT_ITEM_CODE}
    if not hits:
        raise RuntimeError(
            f"BD_ACCASSITEM has no row with code {BANK_ACCOUNT_ITEM_CODE!r} (银行账户) — "
            "the bank-account auxiliary cannot be decoded, so account 100201 would "
            "import with every bank mixed together")
    if AUX_BANKACCOUNT not in hits:
        raise RuntimeError(
            f"bank-account aux type pk mismatch: constant {AUX_BANKACCOUNT!r} not among "
            f"resolved {sorted(hits)!r} — typevalue-prefix assumption broke")
    if len(hits) > 1:
        raise RuntimeError(
            f"BD_ACCASSITEM has {len(hits)} rows with code {BANK_ACCOUNT_ITEM_CODE!r}: "
            f"{sorted(hits)!r} — refusing to guess which one vouchers use")
    return AUX_BANKACCOUNT


def _tallied(tallydate) -> bool:
    """Has NC posted this voucher to its ledger?

    GL_VOUCHER.TALLYDATE is CHAR(19), so Oracle space-pads it: an empty one comes
    back as '~' + 18 spaces, NOT '~'. Comparing it raw reports every voucher as
    tallied — which silently made this whole draft/posted split a no-op and left
    $1.73M of un-tallied entries in the GL. SQL hides it (Oracle pads the literal
    too, so `tallydate = '~'` matches), and so do fixtures, which hand over clean
    values. Only real Oracle shows it. Strip before deciding.
    """
    return bool(tallydate) and tallydate.strip() not in ("", "~")


def _voucher_state(discardflag, tempsaveflag, errmessage) -> str:
    """NC's 凭证状态: 正常 / 错误 / 作废 / 暂存 -> our stored code.

    These are three independent CHAR flags, NOT the VOUCHERKIND enum — that one
    separates 期初/年结/成本结转 from ordinary vouchers and is stored alongside.
    Measured on the live book 2026-09-22: 41,334 normal, 1 discarded, 0 error,
    0 tempsave. The filter is near-useless on today's data and is built anyway,
    because the discarded one is currently invisible: the sync drops it at the
    source, so nothing downstream can even report that it exists.

    Order matters: a discarded voucher that also carries an error message is
    discarded first — it is out of the ledger either way.
    """
    if (discardflag or "").strip() == "Y":
        return "discarded"
    if (tempsaveflag or "").strip() == "Y":
        return "tempsave"
    if _strip_char(errmessage):
        return "error"
    return "normal"


def _max_creationtime(values) -> str | None:
    """max() over a batch of raw CREATIONTIME values, ignoring the same
    Oracle CHAR(19) blank/sentinel padding _tallied() guards against.

    creationtime drives the incremental watermark (watermark_to feeds the
    next run's `creationtime >= :wm` filter). '~' + 18 spaces is truthy and
    sorts above every real timestamp (0x7E > any digit), so a naive
    max(v for v in values if v) would let one poisoned row set the watermark
    to the sentinel forever — every later incremental then matches nothing
    and the sync silently imports zero rows, with no error to notice.
    """
    cleaned = [v for v in values if v and v.strip() not in ("", "~")]
    return max(cleaned, default=None)


def _strip_char(v: str | None) -> str | None:
    """Strip an Oracle CHAR(n) value, treating both '' and the '~' blank
    sentinel (same one _tallied()/_max_creationtime() guard against on
    TALLYDATE/CREATIONTIME) as absent. Used for PK_SYSTEM."""
    s = (v or "").strip()
    return s if s not in ("", "~") else None


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _orig_side(dr: Decimal, cr: Decimal) -> tuple[Decimal, Decimal]:
    """Keep an NC line on its ORIGINAL column, sign preserved (§14.7).

    NC records 红字 (reversals) as a NEGATIVE amount in its own column, and its
    科目余额表 sums each column signed (negatives net in-column). Storing the
    amounts as-is makes our 本期发生额 match NC. The earlier `_net_side` collapsed
    every line to one side by net (dr-cr), which FLIPPED a red credit into a
    positive debit and inflated gross 发生额 (measured: 660101 971,338 vs NC
    579,026). Net (dr-cr) is identical either way, so closing balances never
    change.

    The ONLY case we still net is a genuinely both-POSITIVE line — `ck_jv_lines_
    one_side` forbids orig_debit>0 AND orig_credit>0. Exactly 1 such line exists
    in the CRM book (2 with any both-nonzero), so netting it is negligible and
    keeps the go-forward one-sided invariant intact. Reds never trigger this
    (they are ≤ 0), so they are always kept signed."""
    if dr > 0 and cr > 0:
        n = dr - cr
        return (n, Decimal("0")) if n >= 0 else (Decimal("0"), -n)
    return (dr, cr)


def _resolve_dims(assid, aux, cc_map_rows, category, uni_cc, uni_dept, uni_ba, uni_sup,
                  uni_cust, uni_bank=None, parties=None, side=None):
    """-> dict of the resolved dimensions for one line.

    Cost center: for the 5 predreal categories (`category` is the category code)
    resolve ACCOUNT-AWARE via budget_actual_cc_map — an unmapped combo (e.g.
    engineering dept 0106 in 6602) returns None and surfaces as an exception. For
    every other account (`category is None`) keep the account-blind CC_BY_CODE/
    CC_BY_DEPT fallback.

    Party: the separate 供应商档案/客户档案 auxiliaries keep their long-standing
    supplier-first precedence — their auxiliary TYPE already states the direction,
    so there is nothing to disambiguate. The combined 客商 auxiliary is resolved
    by resolve_partner() instead, which needs the account's side; it only fills in
    when the two archives did not already name a party.

    Bank account: the code is kept even when nc_bank_accounts has no row for it
    (some GL_FREEVALUE values point at pks BD_BANKACCSUB no longer holds — 3 of
    129 on the live book). A code with no master still scopes a reconciliation;
    a dropped one would silently merge that bank into "unassigned"."""
    from app.services.cc_map_import import resolve_uniops_cc
    a = aux.get(assid) or {}
    d = a.get("department", "")
    c = a.get("cost_center", "")
    io = a.get("income_expense_item", "")
    sup, cust = a.get("supplier", ""), a.get("customer", "")
    bank = a.get("bank_account", "")
    if category is not None:
        uni_code = resolve_uniops_cc(cc_map_rows, category, d, c)
    else:
        uni_code = CC_BY_CODE.get(c) if c else CC_BY_DEPT.get(d)
    partner_id = partner_name = None
    partner_outcome = None
    code = sup or cust
    if code:
        hit = (uni_sup.get(sup) if sup else None) or (uni_cust.get(cust) if cust else None)
        if hit:
            partner_id, partner_name = hit
        else:
            partner_name = code
    elif a.get("partner"):
        partner_id, partner_name, partner_outcome = resolve_partner(
            a["partner"], parties, side, uni_sup, uni_cust)
    return {
        "cost_center_id": uni_cc.get(uni_code) if uni_code else None,
        "department_id": uni_dept.get(d) if d else None,
        "io_code": io or None,
        "budget_account_id": uni_ba.get(io) if io else None,
        "partner_id": partner_id,
        "partner_name": partner_name,
        "partner_outcome": partner_outcome,
        "nc_cc_code": c or None,
        "had_cc_hint": bool(c or d),
        "bank_account_id": (uni_bank or {}).get(bank) if bank else None,
        "bank_code": bank or None,
        "aux": a,
    }

def transform(extract: NcExtract, uni_cc: dict, uni_dept: dict, uni_ba: dict,
              uni_sup: dict, uni_cust: dict, skip_pks: set,
              cc_map_rows: list | None = None, category_of=None,
              uni_bank: dict | None = None, side_of=None) -> tuple[list, list, list, int, dict]:
    """NC rows -> (voucher dicts, line tuples, dim tuples, unmapped_cc, counters).

    Skips vouchers whose pk is in skip_pks (incremental pk-dedup). `category_of`
    (from make_category_of) maps a line's account to its predreal category; when
    it (or cc_map_rows) is absent, cost centers fall back to CC_BY_CODE/CC_BY_DEPT.
    `side_of` (from make_partner_side_of) gives the account's customer/supplier
    direction, which is what disambiguates the combined 客商 auxiliary.

    Every auxiliary the catalog knows lands in `dims` as one jv_line_dimensions
    row, not just the two that had columns of their own. That is what makes the
    Aux. Acctg column complete and the auxiliary filters queryable at all — NC
    puts 物料基本信息 on 113,866 of this book's 323,740 lines, and every one of
    them used to arrive empty."""
    pk2id, vouchers = {}, []
    for (pk, year, period, num, expl, pdate, _ctime, tallydate, pk_system,
         vkind, discard, tempsave, errmsg, attach,
         prepared, checked, manager, vtype) in extract.vouchers:
        if pk in skip_pks:
            continue
        jid = uuid.uuid4()
        pk2id[pk] = jid
        vdate = (pdate[:10] if pdate and len(pdate) >= 10 else f"{year}-{period}-01")
        num_i = int(num) if num is not None else 0
        vouchers.append({
            # JV- prefix + 4-padded, same shape as go-forward numbers (user 2026-07-13;
            # next_jv_number is max-based so the shared namespace can't collide).
            "id": jid, "jv_number": f"JV-{year}{period}-{num_i:04d}",
            "period": f"{year}-{period}", "vdate": vdate,
            "summary": (expl or "")[:255], "nc_pk": pk,
            # NC's TALLYDATE empty = not yet posted to NC's ledger. Mirror that:
            # the GL and Account Balance both read status == POSTED only, so an
            # un-tallied voucher must not colour reports (spec §14.4).
            # A discarded/tempsave/error voucher is not a ledger entry, so it
            # never reaches `posted` even if NC had tallied it before the flag
            # was set — the GL and Account Balance read status == POSTED only.
            "status": ("posted" if _tallied(tallydate)
                       and _voucher_state(discard, tempsave, errmsg) == "normal"
                       else "draft"),
            # NC PK_SYSTEM is CHAR (space-padded); an empty one comes back as the
            # same '~' blank-sentinel _tallied() strips off TALLYDATE. Store the
            # stripped raw code, or None for blank/sentinel.
            "source_subsystem": _strip_char(pk_system),
            "nc_num": num_i,
            "nc_voucher_kind": int(vkind) if vkind is not None else None,
            "nc_voucher_state": _voucher_state(discard, tempsave, errmsg),
            "nc_voucher_type_name": _strip_char(vtype),
            "nc_prepared_name": _strip_char(prepared),
            "nc_checked_name": _strip_char(checked),
            "nc_manager_name": _strip_char(manager),
            "nc_attachment_count": int(attach) if attach is not None else 0,
        })

    lines, dims, unmapped = [], [], 0
    counters: dict = {}
    for (pk, idx, acct, dr, cr, ldr, lcr, curr, rate, expl, assid,
         dqty, cqty, price, unitname, oppsubj) in extract.details:
        jid = pk2id.get(pk)
        if jid is None:
            continue
        odr, ocr = _orig_side(_d(dr), _d(cr))
        ldr_, lcr_ = _orig_side(_d(ldr), _d(lcr))
        acct_s = (acct or "").strip() or None
        category = category_of(acct_s) if category_of else None
        side = side_of(acct_s) if side_of else None
        r = _resolve_dims(
            assid, extract.aux, cc_map_rows or [], category,
            uni_cc, uni_dept, uni_ba, uni_sup, uni_cust, uni_bank,
            parties=extract.parties, side=side)
        if r["had_cc_hint"] and r["cost_center_id"] is None:
            unmapped += 1
        if r["partner_outcome"]:
            counters[r["partner_outcome"]] = counters.get(r["partner_outcome"], 0) + 1
        ccy_code = extract.ccy.get(curr)
        if ccy_code is None:
            raise NcSyncError(f"voucher line {pk}/{idx}: currency pk {curr!r} not in "
                              f"BD_CURRTYPE — refusing to default it to CAD")
        lid = uuid.uuid4()
        # NC keeps the quantity on whichever side the amount is on; one signed
        # quantity is what the detail view and the unit price both need.
        qty = _d(dqty) - _d(cqty)
        lines.append((
            lid, jid, int(idx or 0), acct_s,
            (expl or "")[:255], odr, ocr, ldr_, lcr_,
            ccy_code, _d(rate) if rate else Decimal("1"),
            r["cost_center_id"], r["department_id"], r["budget_account_id"],
            r["partner_id"], r["partner_name"], r["nc_cc_code"], r["bank_account_id"],
            qty or None, _strip_char(unitname), _d(price) or None,
            (_strip_char(oppsubj) or "")[:200] or None))
        # Both of these keep their dedicated column too; the dimension rows are
        # what the auxiliary filter and the Aux. Acctg column read.
        for slot, code in (r["aux"] or {}).items():
            if not code:
                continue
            value_id = {"income_expense_item": r["budget_account_id"],
                        "bank_account": r["bank_account_id"],
                        "partner": r["partner_id"]}.get(slot)
            dims.append((uuid.uuid4(), lid, slot, value_id, code[:255]))
    return vouchers, lines, dims, unmapped, counters


def fetch_from_nc(watermark: str | None) -> NcExtract:
    """Live NC read (oracledb, read-only). watermark: only vouchers with
    creationtime >= watermark (pk-dedup upstream makes >= safe)."""
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port,
                           service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()
        cur.execute("select pk_currtype, code from NCSC.BD_CURRTYPE")
        ccy = {pk: code for pk, code in cur.fetchall()}

        cur.execute("select pk_accassitem, code, name from NCSC.BD_ACCASSITEM")
        assitems = list(cur.fetchall())
        type_pks = resolve_aux_types_by_code(assitems)
        aux_bank_pk = resolve_bank_account_type_pk(assitems)

        # BD_BANKACCSUB.PK_BANKACCSUB is CHAR, so Oracle blank-pads it; the
        # GL_FREEVALUE slice is a bare 20 chars. rtrim both sides or every bank
        # lookup misses and 100201 imports with no bank at all (same class of bug
        # as TALLYDATE's '~' padding — see _tallied).
        # NAME is the label NC's own 科目余额表 prints ("RBC加拿大元活期户");
        # ACCNAME is the holder ("RBC") and repeats across accounts. Join out to
        # BD_BANKDOC for the bank itself ("RBC-York Street").
        cur.execute("select sub.pk_bankaccsub, sub.code, sub.accnum, sub.name, sub.accname, "
                    "       sub.pk_currtype, doc.name "
                    "  from NCSC.BD_BANKACCSUB sub "
                    "  left join NCSC.BD_BANKACCBAS bas on bas.pk_bankaccbas = sub.pk_bankaccbas "
                    "  left join NCSC.BD_BANKDOC doc on doc.pk_bankdoc = bas.pk_bankdoc")
        bank_rows = [(_strip_char(bpk), _strip_char(code), _strip_char(accnum),
                      _strip_char(name), _strip_char(accname), pk_ccy, _strip_char(bank))
                     for bpk, code, accnum, name, accname, pk_ccy, bank in cur]
        bank_codes = {r[0]: r[1] for r in bank_rows if r[0] and r[1]}

        # ENABLESTATE 2 = 已启用 (1 未启用, 3 已停用). PK_FINANCEORG is set on
        # exactly one row per table and only on one: the organisation NC
        # materialises as its own trading party (BD_CUSTOMER 01010104 /
        # BD_SUPPLIER 9900012, both 加拿大皇家妙克). Those are not counterparties
        # and resolve_partner drops them — user, 2026-09-22.
        cur.execute("select pk_supplier, code, name, enablestate, pk_financeorg "
                    "from NCSC.BD_SUPPLIER")
        sup_rows = list(cur.fetchall())
        cur.execute("select pk_customer, code, name, enablestate, pk_financeorg "
                    "from NCSC.BD_CUSTOMER")
        cust_rows = list(cur.fetchall())
        sup_codes = {pk: code for pk, code, _n, _e, _f in sup_rows}
        cust_codes = {pk: code for pk, code, _n, _e, _f in cust_rows}

        def _party(code, name, enablestate, financeorg):
            return {"code": (code or "").strip(), "name": (name or "").strip(),
                    "enabled": enablestate == 2,
                    "is_org": bool(_strip_char(financeorg))}
        parties: dict = {}
        for side, rows in (("supplier", sup_rows), ("customer", cust_rows)):
            for pk, code, name, enablestate, financeorg in rows:
                rec = parties.setdefault(pk, {})
                rec[side] = _party(code, name, enablestate, financeorg)
                rec.setdefault("any_name", (name or "").strip())

        cur.execute("select pk_material, code from NCSC.BD_MATERIAL")
        item_codes = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_project, code from NCSC.BD_PROJECT")
        proj_codes = {pk: code for pk, code in cur.fetchall()}

        cur.execute("select pk_dept, code from NCSC.ORG_DEPT")
        dept = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_costcenter, cccode from NCSC.RESA_COSTCENTER")
        cc = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_inoutbusiclass, code from NCSC.BD_INOUTBUSICLASS")
        io = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select freevalueid, typevalue1, typevalue2, typevalue3, typevalue4, "
                    "typevalue5, typevalue6, typevalue7, typevalue8, typevalue9 "
                    "from NCSC.GL_FREEVALUE")
        # Every auxiliary the catalog knows, keyed by our dim_code. A slot whose
        # type pk the catalog does not carry is simply absent, so the dimension
        # decodes empty instead of aborting the run. `partner` deliberately has
        # NO code lookup: 客商 spans two masters under possibly different codes,
        # so the raw value pk is carried through and resolve_partner() picks the
        # side once the line's account is known.
        slots = {}
        for slot, lookup in (("department", dept), ("cost_center", cc),
                             ("income_expense_item", io), ("supplier", sup_codes),
                             ("customer", cust_codes), ("bank_account", bank_codes),
                             ("item", item_codes), ("project", proj_codes),
                             ("partner", {})):
            pk = type_pks.get(slot)
            if pk:
                slots[slot] = (pk, lookup)
        aux = {}
        for row in cur:
            aux[row[0]] = decode_aux_row(row[1:], slots)
        used_bank_codes = {a.get("bank_account") for a in aux.values()
                           if a.get("bank_account")}

        # Discarded (作废) vouchers ARE fetched now and marked instead of dropped.
        # Filtering them at the source made them invisible: nothing downstream
        # could report that one exists, which is what the user hit. They never
        # reach `posted` (see transform), so they still colour no report.
        # 制单/审核/记账 come from SM_USER — PK_PREPARED/PK_CHECKED/PK_MANAGER are
        # populated on 41,335/40,121/41,335 of this book's vouchers while
        # PK_CASHER and APPROVER are empty, which is exactly what NC's own list
        # shows. CHAR pks need trimming before they will join.
        vq = ("select v.pk_voucher, v.year, v.period, v.num, v.explanation, "
              "       v.prepareddate, v.creationtime, v.tallydate, v.pk_system, "
              "       v.voucherkind, v.discardflag, v.tempsaveflag, v.errmessage, "
              "       v.attachment, up.user_name, uc.user_name, um.user_name, bt.name "
              "  from NCSC.GL_VOUCHER v "
              "  left join NCSC.SM_USER up on up.cuserid = trim(v.pk_prepared) "
              "  left join NCSC.SM_USER uc on uc.cuserid = trim(v.pk_checked) "
              "  left join NCSC.SM_USER um on um.cuserid = trim(v.pk_manager) "
              "  left join NCSC.BD_VOUCHERTYPE bt on bt.pk_vouchertype = trim(v.pk_vouchertype) "
              " where v.pk_accountingbook = :b")
        if watermark:
            cur.execute(vq + " and v.creationtime >= :wm", b=PK_BOOK, wm=watermark)
        else:
            cur.execute(vq, b=PK_BOOK)
        vouchers = list(cur.fetchall())
        max_ct = _max_creationtime(v[6] for v in vouchers)

        # Status backfill feed: the whole book's tally facts, deliberately NOT
        # watermark-limited. A voucher created in June and tallied in July keeps
        # its June creationtime, so the watermark would never bring it back and
        # it would sit at draft forever (spec §14.4.1). Two columns x ~40k rows.
        cur.execute("select pk_voucher, tallydate, discardflag, tempsaveflag, errmessage "
                    "from NCSC.GL_VOUCHER where pk_accountingbook = :b", b=PK_BOOK)
        tallied = {pk for pk, td, dis, tmp, err in cur
                   if _tallied(td) and _voucher_state(dis, tmp, err) == "normal"}

        # details: fetch the whole book; transform() filters by pk2id membership.
        # DEBITQUANTITY/CREDITQUANTITY are filled on 108,163 of this book's
        # 323,740 lines, PRICE on 37,815 and UNITNAME on 22,031 — the model has
        # had quantity/unit/price columns and the detail modal has rendered them
        # since day one, but the sync never wrote any of it. OPPOSITESUBJ (对方科目,
        # 313,444 lines) is what NC's own voucher query filters on.
        cur.execute(
            "select pk_voucher, detailindex, accountcode, debitamount, creditamount, "
            "localdebitamount, localcreditamount, pk_currtype, excrate1, explanation, assid, "
            "debitquantity, creditquantity, price, unitname, oppositesubj "
            "from NCSC.GL_DETAIL where pk_accountingbook = :b", b=PK_BOOK)
        details = list(cur.fetchall())
    finally:
        con.close()
    # Only the accounts vouchers actually reference — BD_BANKACCSUB holds 13k rows
    # for the whole FeiHe group, of which this book uses ~129.
    bank_master = tuple((pk, code, accnum, name, accname, ccy.get(pk_ccy), bank)
                        for pk, code, accnum, name, accname, pk_ccy, bank in bank_rows
                        if code and code in used_bank_codes)
    return NcExtract(ccy=ccy, aux=aux, vouchers=vouchers, details=details,
                     parties=parties,
                     max_creationtime=max_ct, tallied=tallied,
                     bank_accounts=bank_master)


# ── run lifecycle (worker) ─────────────────────────────────────────────────────────
import logging
import threading
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values, register_uuid
from sqlalchemy.engine.url import make_url

register_uuid()

logger = logging.getLogger(__name__)

_start_lock = threading.Lock()
STALE_AFTER = timedelta(minutes=30)
_CHUNK = 5000


class SyncAlreadyRunning(Exception):
    pass


def _pg_dsn() -> str:
    u = make_url(settings.database_url)
    return (f"host={u.host} port={u.port or 5432} dbname={u.database} "
            f"user={u.username} password={u.password}")


def _mark(dsn, run_id, **fields):
    """Small autocommit update on the run row (progress counters)."""
    con = psycopg2.connect(dsn); con.autocommit = True
    cur = con.cursor()
    sets = ", ".join(f"{k} = %s" for k in fields)
    cur.execute(f"update nc_sync_runs set {sets}, updated_at = now() where id = %s",
                (*fields.values(), run_id))
    con.close()


def _sync_statuses(cur, tallied: set) -> tuple[int, int]:
    """Align every NC-sourced voucher's status with NC's tally fact.

    Incremental skips pks it has already imported and its watermark is on
    creationtime, so a voucher tallied AFTER import never returns through that
    path — without this it would sit at draft forever. Diff first and update only
    what actually changed (usually nothing), same shape as the COA sync.
    """
    cur.execute("select nc_source_pk, status from journal_vouchers "
                "where nc_source_pk is not null")
    current = dict(cur.fetchall())
    to_posted = [pk for pk, st in current.items() if pk in tallied and st != "posted"]
    to_draft = [pk for pk, st in current.items() if pk not in tallied and st != "draft"]
    if to_posted:
        cur.execute("update journal_vouchers set status = 'posted', updated_at = now() "
                    "where nc_source_pk = any(%s)", (to_posted,))
    if to_draft:
        cur.execute("update journal_vouchers set status = 'draft', updated_at = now() "
                    "where nc_source_pk = any(%s)", (to_draft,))
    return len(to_posted), len(to_draft)


def _mark_terminal(dsn, run_id, **fields):
    """Terminal update that refuses to overwrite an already-terminal row
    (e.g. a run swept as abandoned must not flip back to success)."""
    con = psycopg2.connect(dsn); con.autocommit = True
    cur = con.cursor()
    sets = ", ".join(f"{k} = %s" for k in fields)
    cur.execute(f"update nc_sync_runs set {sets}, updated_at = now() "
                f"where id = %s and status = 'running'",
                (*fields.values(), run_id))
    con.close()


def start_run(mode: str, started_by, *, fetch=fetch_from_nc,
              pg_dsn: str | None = None, run_worker: bool = True):
    """Single-flight gate + run-row insert. Synchronous — the API layer threads it.
    Returns the new run id. Raises SyncAlreadyRunning if a live run exists."""
    dsn = pg_dsn or _pg_dsn()
    with _start_lock:
        con = psycopg2.connect(dsn); con.autocommit = True
        cur = con.cursor()
        # auto-fail stale 'running' rows (crashed container), then check liveness
        cur.execute("update nc_sync_runs set status = 'failed', error = 'abandoned', "
                    "finished_at = now(), updated_at = now() "
                    "where status = 'running' and updated_at < %s",
                    (datetime.now(timezone.utc) - STALE_AFTER,))
        cur.execute("select id from nc_sync_runs where status = 'running'")
        if cur.fetchone():
            con.close()
            raise SyncAlreadyRunning("an NC sync is already running")
        run_id = uuid.uuid4()
        cur.execute("insert into nc_sync_runs (id, mode, status, started_by, started_at, "
                    "created_at, updated_at) values (%s, %s, 'running', %s, now(), now(), now())",
                    (run_id, mode, started_by))
        con.close()
    if run_worker:
        _run_worker(run_id, mode, fetch, dsn)
    return run_id


def _run_worker(run_id, mode: str, fetch, dsn: str) -> None:
    try:
        con = psycopg2.connect(dsn); con.autocommit = False
        cur = con.cursor()
        cur.execute("select nc_source_pk from journal_vouchers where nc_source_pk is not null")
        existing = {r[0] for r in cur.fetchall()}
        cur.execute("select watermark_to from nc_sync_runs where status = 'success' "
                    "and watermark_to is not null order by started_at desc limit 1")
        row = cur.fetchone()
        prev_wm = row[0] if row else None

        extract = fetch(prev_wm if mode == "incremental" else None)

        cur.execute("select code, id from cost_centers")
        uni_cc = dict(cur.fetchall())
        cur.execute("select code, id from departments")
        uni_dept = dict(cur.fetchall())
        # budget_accounts is owned by budget-api; may not exist in this DB
        try:
            sp = con.cursor()
            sp.execute("savepoint _ba")
            sp.execute("select code, id from budget_accounts")
            uni_ba = dict(sp.fetchall())
            sp.execute("release savepoint _ba")
            sp.close()
        except Exception:  # noqa: BLE001
            cur.execute("rollback to savepoint _ba")
            cur.execute("release savepoint _ba")
            logger.warning("budget_accounts table not found; income/expense dims will have "
                           "value_id=NULL — check the finance DB schema")
            uni_ba = {}

        # erp_suppliers mirrors NC BD_SUPPLIER; may not exist in very minimal DBs
        try:
            sp = con.cursor()
            sp.execute("savepoint _sup")
            sp.execute("select erp_supplier_code, id, supplier_name from erp_suppliers")
            uni_sup = {c: (i, n) for c, i, n in sp.fetchall()}
            sp.execute("release savepoint _sup")
            sp.close()
        except Exception:  # noqa: BLE001
            cur.execute("rollback to savepoint _sup")
            cur.execute("release savepoint _sup")
            logger.warning("erp_suppliers table not found; partner_id will be NULL for all "
                           "supplier lines — check the finance DB schema")
            uni_sup = {}

        try:
            sp = con.cursor()
            sp.execute("savepoint _cust")
            sp.execute("select code, id, name from nc_customers")
            uni_cust = {c: (i, n) for c, i, n in sp.fetchall()}
            sp.execute("release savepoint _cust")
            sp.close()
        except Exception:  # noqa: BLE001
            cur.execute("rollback to savepoint _cust")
            cur.execute("release savepoint _cust")
            logger.warning("nc_customers table not found; partner_id will be NULL for all "
                           "customer lines — check the finance DB schema")
            uni_cust = {}

        # COA tree -> category resolver (leaf line account -> predreal category header)
        cur.execute("select code, parent_code from chart_of_accounts")
        parent_map = {code: parent for code, parent in cur.fetchall()}
        category_of = make_category_of(parent_map)
        # The same tree, asked a different question: which side of the ledger does
        # this account name? That is what disambiguates NC's combined 客商.
        side_of = make_partner_side_of(parent_map)
        # budget_actual_cc_map (account-aware CC map); may not exist in minimal DBs
        try:
            sp = con.cursor()
            sp.execute("savepoint _ccmap")
            sp.execute("select account_code, dept_code, nc_cc_code, uniops_cc_code "
                       "from budget_actual_cc_map")
            cc_map_rows = [dict(zip(("account_code", "dept_code", "nc_cc_code", "uniops_cc_code"), r))
                           for r in sp.fetchall()]
            sp.execute("release savepoint _ccmap")
            sp.close()
        except Exception:  # noqa: BLE001
            cur.execute("rollback to savepoint _ccmap")
            cur.execute("release savepoint _ccmap")
            logger.warning("budget_actual_cc_map not found; predreal accounts have NO cost "
                           "center until it is imported — run scripts/import_cc_map.py")
            cc_map_rows = []

        # nc_bank_accounts is written HERE, before transform, because every jv line
        # needs an id to point at. Upsert (never delete-and-reload): a code that
        # stops appearing in vouchers still has reconciliation matches hanging off
        # it. Keyed on nc_pk — NC lets a code be re-typed, and the pk is what the
        # voucher aux actually carries.
        bank_upserted = 0
        if extract.bank_accounts:
            execute_values(cur,
                "insert into nc_bank_accounts "
                "(id, nc_pk, code, acc_num, name, acc_name, bank_name, currency, "
                " created_at, updated_at) "
                "values %s on conflict (nc_pk) do update set "
                " code = excluded.code, acc_num = excluded.acc_num, "
                " name = excluded.name, acc_name = excluded.acc_name, "
                " bank_name = excluded.bank_name, currency = excluded.currency, "
                " updated_at = now()",
                [(uuid.uuid4(), pk, code, accnum, name, accname, bank, bccy)
                 for pk, code, accnum, name, accname, bccy, bank in extract.bank_accounts],
                template="(%s,%s,%s,%s,%s,%s,%s,%s, now(), now())")
            bank_upserted = cur.rowcount
        cur.execute("select code, id from nc_bank_accounts")
        uni_bank = dict(cur.fetchall())
        logger.info("nc_sync run %s: %d bank accounts upserted, %d known",
                    run_id, bank_upserted, len(uni_bank))

        skip = existing if mode == "incremental" else set()
        vouchers, lines, dims, unmapped, partner_counts = transform(
            extract, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust, skip,
            cc_map_rows=cc_map_rows, category_of=category_of, uni_bank=uni_bank,
            side_of=side_of)

        # 客商 outcomes. `ambiguous` and `org` are the two deliberate refusals:
        # a party that resolves on both sides with nothing to separate them, and
        # the organisation itself. Both keep the NC code as text and NO id —
        # defaulting either one to supplier is the bug this replaced.
        if partner_counts:
            logger.info("nc_sync run %s: 客商 outcomes %s", run_id,
                        ", ".join(f"{k}={v}" for k, v in sorted(partner_counts.items())))
        for refusal in ("ambiguous", "org", "unknown"):
            if partner_counts.get(refusal):
                logger.warning(
                    "nc_sync run %s: %d lines carry a 客商 value resolved as %r — "
                    "partner_id left NULL, NC code kept as partner_name text",
                    run_id, partner_counts[refusal], refusal)

        # A bank-account value NC has but BD_BANKACCSUB no longer does: the raw pk
        # survives in jv_line_dimensions.value_text, but the line scopes to no
        # bank, so it silently leaves every reconciliation. Say so.
        unresolved_bank = sum(1 for d in dims if d[2] == "bank_account" and d[3] is None)
        if unresolved_bank:
            logger.warning(
                "nc_sync run %s: %d lines carry a bank-account value with no "
                "BD_BANKACCSUB master row — those lines scope to NO bank account "
                "(raw NC pk kept in jv_line_dimensions.value_text)",
                run_id, unresolved_bank)

        deleted = 0
        if mode == "full":
            cur.execute("delete from journal_vouchers where nc_source_pk is not null")
            deleted = cur.rowcount

        # Indexed, not a positional unpack: the line tuple has grown twice now and
        # a `_`-padded unpack silently mis-binds every column after the new one.
        tot: dict = {}
        for ln in lines:
            t = tot.setdefault(ln[1], [Decimal("0")] * 4)
            t[0] += ln[5]; t[1] += ln[6]; t[2] += ln[7]; t[3] += ln[8]

        v_rows = [(v["id"], v["jv_number"], "JV", v["vdate"], v["period"], v["summary"],
                   v["status"], "nc", "nc_voucher", v["jv_number"], v["nc_pk"], v["source_subsystem"],
                   v["nc_num"], v["nc_voucher_kind"], v["nc_voucher_state"],
                   v["nc_voucher_type_name"], v["nc_prepared_name"], v["nc_checked_name"],
                   v["nc_manager_name"], v["nc_attachment_count"],
                   *(tot.get(v["id"], [Decimal("0")] * 4))) for v in vouchers]
        for i in range(0, len(v_rows), _CHUNK):
            execute_values(cur,
                "insert into journal_vouchers "
                "(id, jv_number, voucher_word, voucher_date, fiscal_period, summary, status, "
                " source_service, source_doc_type, source_doc_number, nc_source_pk, source_subsystem, "
                " nc_num, nc_voucher_kind, nc_voucher_state, nc_voucher_type_name, "
                " nc_prepared_name, nc_checked_name, nc_manager_name, nc_attachment_count, "
                " total_debit, total_credit, total_local_debit, total_local_credit, "
                " created_at, updated_at) values %s",
                v_rows[i:i + _CHUNK],
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                         "%s,%s,%s,%s,%s,%s,%s,%s, now(), now())")
            _mark(dsn, run_id, vouchers_inserted=min(i + _CHUNK, len(v_rows)))
        for i in range(0, len(lines), _CHUNK):
            execute_values(cur,
                "insert into journal_voucher_lines "
                "(id, jv_id, line_no, account_code, summary, orig_debit, orig_credit, "
                " local_debit, local_credit, currency, fx_rate, cost_center_id, department_id, "
                " income_expense_item_id, partner_id, partner_name, nc_cc_code, bank_account_id, "
                " quantity, unit, price, opposite_subject, "
                " created_at, updated_at) values %s",
                lines[i:i + _CHUNK],
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                         "%s,%s,%s,%s, now(), now())")
            _mark(dsn, run_id, lines_inserted=min(i + _CHUNK, len(lines)))
        for i in range(0, len(dims), _CHUNK):
            execute_values(cur,
                "insert into jv_line_dimensions "
                "(id, jv_line_id, dim_code, value_id, value_text, created_at, updated_at) "
                "values %s",
                dims[i:i + _CHUNK],
                template="(%s,%s,%s,%s,%s, now(), now())")
            _mark(dsn, run_id, dims_inserted=min(i + _CHUNK, len(dims)))

        n_posted, n_draft = _sync_statuses(cur, extract.tallied)
        logger.info("nc_sync run %s: status backfill flipped %d to posted, %d to draft",
                    run_id, n_posted, n_draft)

        # What the run could not PLACE (as opposed to could not read): lines with
        # no resolvable cost center / income-expense item never reach the Budget
        # Dashboard, and used to leave no trace at all. One standing Admin Task,
        # refreshed here inside the same transaction, closes itself when clean.
        from app.services import jv_validation_tasks
        outcome = jv_validation_tasks.raise_or_clear(cur, run_id)
        logger.info("nc_sync run %s: JV validation task %s", run_id, outcome)

        # superseded-run guard: if sweeper already marked us abandoned, do not commit.
        cur.execute("select status from nc_sync_runs where id = %s for update", (run_id,))
        row = cur.fetchone()
        if not row or row[0] != "running":
            con.rollback(); con.close()
            return
        con.commit(); con.close()
        _mark_terminal(dsn, run_id, status="success", finished_at=datetime.now(timezone.utc),
                       vouchers_deleted=deleted, vouchers_inserted=len(vouchers),
                       lines_inserted=len(lines), dims_inserted=len(dims),
                       unmapped_cc_count=unmapped, watermark_from=prev_wm,
                       watermark_to=extract.max_creationtime or prev_wm)
    except Exception as e:  # noqa: BLE001 — terminal state must always be written
        try:
            con.rollback(); con.close()
        except Exception:
            pass
        _mark_terminal(dsn, run_id, status="failed", error=str(e)[:2000],
                       finished_at=datetime.now(timezone.utc))
