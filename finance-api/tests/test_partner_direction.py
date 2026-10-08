"""NC's combined 客商 auxiliary, and which side of it a line means.

客商 (BD_ACCASSITEM code 0004) is not a master of its own: it is the UNION of
BD_SUPPLIER and BD_CUSTOMER (user, 2026-09-22). NC lets a supplier generate its
customer twin and vice versa, so one 客商 value pk can resolve on both sides —
and the generated twin gets its OWN code (5 of the live book's 17 two-sided
parties differ per side).

The auxiliary went unread entirely until 2026-09-22 because the old resolver
matched Chinese names and 客商 contains neither 供应商 nor 客户; 9,625 lines of
the live book lost their counterparty to that. These tests pin the rules that
replaced it — above all that supplier does NOT simply win, which is what the
measured data says: 2,398 receivable + ~3,120 revenue lines of those 9,625.
"""
import pytest

SUP_ID, CUS_ID = object(), object()
UNI_SUP = {"0000915": (SUP_ID, "Walmart Canada Corp.")}
UNI_CUS = {"999000042": (CUS_ID, "Walmart Canada Corp.")}


def _party(code, name="X", enabled=True, is_org=False):
    return {"code": code, "name": name, "enabled": enabled, "is_org": is_org}


WALMART = "Walmart Canada Corp."


def _both(sup_code="0000915", cus_code="999000042", **kw):
    """Walmart's real shape on the live book: the same party on both sides under
    DIFFERENT codes (supplier 0000915, generated customer 999000042)."""
    return {"VPK": {"supplier": _party(sup_code, name=WALMART, **kw),
                    "customer": _party(cus_code, name=WALMART, **kw),
                    "any_name": WALMART}}


def test_one_sided_party_uses_that_side_whatever_the_account_says():
    from app.services.nc_sync import resolve_partner
    only_sup = {"VPK": {"supplier": _party("0000915"), "any_name": "S"}}
    # even on a receivable account, a party that exists ONLY as a supplier is one
    assert resolve_partner("VPK", only_sup, "customer", UNI_SUP, UNI_CUS)[0] is SUP_ID
    only_cus = {"VPK": {"customer": _party("999000042"), "any_name": "C"}}
    assert resolve_partner("VPK", only_cus, "supplier", UNI_SUP, UNI_CUS)[0] is CUS_ID


def test_two_sided_party_is_decided_by_the_account_not_by_supplier_first():
    """The regression this file exists for. Walmart resolves on both sides under
    different codes; its 803 lines sit on receivable/revenue accounts. A
    supplier-first precedence would file every one of them under the supplier
    code 0000915 — the wrong direction for the bulk of the book."""
    from app.services.nc_sync import resolve_partner
    pid, name, outcome = resolve_partner("VPK", _both(), "customer", UNI_SUP, UNI_CUS)
    assert (pid, outcome) == (CUS_ID, "customer")
    pid, _, outcome = resolve_partner("VPK", _both(), "supplier", UNI_SUP, UNI_CUS)
    assert (pid, outcome) == (SUP_ID, "supplier")


def test_two_sided_with_no_account_direction_prefers_the_enabled_side():
    """Zhong bai Xingye's shape: supplier 0000012A is 已停用 (enablestate 3) while
    its customer twin 002000002 is live. With no direction from the account, the
    retired master must not be the one that wins."""
    from app.services.nc_sync import resolve_partner
    parties = {"VPK": {"supplier": _party("0000915", enabled=False),
                       "customer": _party("999000042", enabled=True), "any_name": "Z"}}
    pid, _, outcome = resolve_partner("VPK", parties, None, UNI_SUP, UNI_CUS)
    assert (pid, outcome) == (CUS_ID, "customer")


def test_two_sided_tie_refuses_rather_than_defaulting_to_supplier():
    """Both sides live, account says nothing. Defaulting either way reinstates the
    bug, so this returns NO id and keeps the NC name as text — and says so, which
    is what the run's counters report."""
    from app.services.nc_sync import resolve_partner
    pid, name, outcome = resolve_partner("VPK", _both(), None, UNI_SUP, UNI_CUS)
    assert pid is None
    assert outcome == "ambiguous"
    assert name == WALMART


def test_the_organisation_itself_is_never_a_counterparty():
    """NC materialises the accounting entity on both sides — BD_CUSTOMER 01010104
    and BD_SUPPLIER 9900012, both 加拿大皇家妙克, the only PK_FINANCEORG-bearing
    row in either table. Those 70 lines (all on 1511) are intercompany, and
    neither code may be treated as an external party (user, 2026-09-22)."""
    from app.services.nc_sync import resolve_partner
    parties = _both(**{"is_org": True})
    pid, _, outcome = resolve_partner("VPK", parties, "supplier", UNI_SUP, UNI_CUS)
    assert pid is None and outcome == "org"


def test_org_on_one_side_only_still_resolves_the_other():
    from app.services.nc_sync import resolve_partner
    parties = {"VPK": {"supplier": _party("0000915", is_org=True),
                       "customer": _party("999000042"), "any_name": "M"}}
    pid, _, outcome = resolve_partner("VPK", parties, None, UNI_SUP, UNI_CUS)
    assert (pid, outcome) == (CUS_ID, "customer")


def test_party_with_no_uniops_master_keeps_the_nc_name_as_text():
    from app.services.nc_sync import resolve_partner
    only_sup = {"VPK": {"supplier": _party("9999999", name="Not Mirrored"), "any_name": "N"}}
    pid, name, outcome = resolve_partner("VPK", only_sup, None, UNI_SUP, UNI_CUS)
    assert pid is None and name == "Not Mirrored" and outcome == "supplier_unmastered"


def test_value_pk_absent_from_both_archives_is_reported_not_dropped():
    from app.services.nc_sync import resolve_partner
    pid, name, outcome = resolve_partner("GONE", {}, None, UNI_SUP, UNI_CUS)
    assert pid is None and name == "GONE" and outcome == "unknown"


# ── the account-side walk ─────────────────────────────────────────────────────

def test_side_of_walks_the_coa_tree_and_never_guesses_from_a_prefix():
    """Same contract as make_category_of: a 660303 could sit under 6603 OR 6601,
    so the side comes from chart_of_accounts.parent_code, not the digits."""
    from app.services.nc_sync import make_partner_side_of
    side_of = make_partner_side_of({
        "112201": "1122", "1122": None,       # receivable leaf
        "600101": "6001", "6001": None,       # revenue leaf
        "220201": "2202", "2202": None,       # payable leaf
        "660101": "6601", "6601": None,       # expense leaf — deliberately neutral
    })
    assert side_of("112201") == "customer"
    assert side_of("600101") == "customer"
    assert side_of("220201") == "supplier"
    # an expense account carries NO direction on purpose: a 销售费用 line against a
    # customer can be trade spend payable TO them, and guessing is the bug.
    assert side_of("660101") is None
    assert side_of(None) is None
    assert side_of("9999") is None


def test_side_of_survives_a_parent_cycle():
    from app.services.nc_sync import make_partner_side_of
    side_of = make_partner_side_of({"A": "B", "B": "A"})
    assert side_of("A") is None
