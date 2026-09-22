"""NC bank-account auxiliary (银行账户, BD_ACCASSITEM code '0011').

Why this dimension gets its own test file: account `100201 Checking` is a single
postable account, so this auxiliary is the ONLY thing separating RBC from Bank of
China from JPMorgan — including the two sides of an internal bank-to-bank
transfer, which both land on 100201 and net to zero without it. Before migration
0036, `jv_line_dimensions` held exactly one dim_code (`income_expense_item`) and
this one was measured at zero rows on the production snapshot.
"""
import uuid

import pytest

BANK_PK = "0001Z010000000001N98"        # AUX_BANKACCOUNT — 银行账户
RBC_VPK = "1001A1100000003CGYDM"        # a real BD_BANKACCSUB pk (RBC CAD)
DEPT_PK = "0001Z0100000000005CS"


# ── type-pk resolution ─────────────────────────────────────────────────────────

def _items(extra=()):
    """BD_ACCASSITEM as (pk, code, name) — the three 银行* siblings included,
    because they are the whole reason this resolver is code-keyed."""
    return [
        (DEPT_PK, "0001", "部门"),
        (BANK_PK, "0011", "银行账户"),
        ("1001Z01000000000HQWF", "0022", "银行类别"),
        ("1001Z01000000000HQWG", "0023", "银行档案"),
        *extra,
    ]


def test_resolves_bank_account_type_by_code():
    from app.services.nc_sync import AUX_BANKACCOUNT, resolve_bank_account_type_pk
    assert resolve_bank_account_type_pk(_items()) == AUX_BANKACCOUNT


def test_bank_siblings_do_not_win_on_a_name_substring():
    """银行类别 and 银行档案 both contain 银行. A name-needle resolver of the kind
    resolve_aux_type_pks uses would collect all three; the code-keyed one must
    return only 0011. This is the failure nc_coa_sync's AUX_ITEM_MAP already
    documents for 项目类型 vs 政府拨款项目."""
    from app.services.nc_sync import resolve_bank_account_type_pk
    got = resolve_bank_account_type_pk(_items())
    assert got == BANK_PK
    assert got not in {"1001Z01000000000HQWF", "1001Z01000000000HQWG"}


def test_missing_bank_account_item_is_loud():
    from app.services.nc_sync import resolve_bank_account_type_pk
    without = [r for r in _items() if r[1] != "0011"]
    with pytest.raises(RuntimeError, match="0011"):
        resolve_bank_account_type_pk(without)


def test_repointed_constant_is_loud():
    """If NC's catalog ever moves 银行账户 to a different pk, the frozen constant
    must fail rather than decode nothing — an empty dimension reads as "this book
    has no bank accounts", which is indistinguishable from the pre-0036 bug."""
    from app.services.nc_sync import resolve_bank_account_type_pk
    moved = [(p if c != "0011" else "WRONGPK0000000000001"[:20], c, n)
             for p, c, n in _items()]
    with pytest.raises(RuntimeError, match="mismatch"):
        resolve_bank_account_type_pk(moved)


def test_duplicate_bank_account_item_refuses_to_guess():
    from app.services.nc_sync import resolve_bank_account_type_pk
    with pytest.raises(RuntimeError, match="refusing to guess"):
        resolve_bank_account_type_pk(_items(extra=[("OTHERPK0000000000001"[:20], "0011", "银行账户")]))


# ── GL_FREEVALUE row decoding ──────────────────────────────────────────────────

def _slots():
    return {"department": (DEPT_PK, {"DEPTV": "0104"}),
            "cost_center": ("CCPK0000000000000001"[:20], {}),
            "income_expense_item": ("IOPK0000000000000001"[:20], {}),
            "supplier": (set(), {}),
            "customer": (set(), {}),
            "bank_account": (BANK_PK, {RBC_VPK: "1033760"})}


def _tv(type_pk, value_pk):
    """A GL_FREEVALUE typevalueN: 20-char type pk + 20-char value pk."""
    return f"{type_pk:<20}{value_pk:<20}"


def test_decodes_bank_code_from_any_slot():
    """On the live book every 100201 line carries the bank in typevalue1, but the
    slot is not guaranteed — the decoder scans all nine rather than pinning one."""
    from app.services.nc_sync import decode_aux_row
    for slot in range(9):
        tvs = [None] * 9
        tvs[slot] = _tv(BANK_PK, RBC_VPK)
        assert decode_aux_row(tvs, _slots())["bank_account"] == "1033760", f"slot {slot + 1}"


def test_blank_sentinel_is_not_a_bank_code():
    """NC writes '~' for an empty auxiliary value (15 rows on the live book). It
    must decode to empty — a literal '~' would become a phantom bank account that
    silently collects lines."""
    from app.services.nc_sync import decode_aux_row
    got = decode_aux_row([_tv(BANK_PK, "~")], _slots())
    assert got["bank_account"] == ""


def test_unknown_bank_value_keeps_the_raw_pk():
    """3 of 129 live values point at BD_BANKACCSUB rows that no longer exist.
    Dropping the value would merge those banks into "unassigned"; keeping the raw
    pk leaves the line recoverable and visible."""
    from app.services.nc_sync import decode_aux_row
    got = decode_aux_row([_tv(BANK_PK, "GONEPK00000000000001"[:20])], _slots())
    assert got["bank_account"] == "GONEPK00000000000001"[:20]


def test_short_typevalue_is_absent_not_truncated():
    from app.services.nc_sync import decode_aux_row
    assert decode_aux_row([BANK_PK], _slots())["bank_account"] == ""      # 20 chars, no value pk
    assert decode_aux_row([""], _slots())["bank_account"] == ""
    assert decode_aux_row([None], _slots())["bank_account"] == ""


def test_other_dimensions_still_decode():
    """The positive half of the assertions above: a decoder that returned empty
    for everything would satisfy every "must be empty" test in this file."""
    from app.services.nc_sync import decode_aux_row
    got = decode_aux_row([_tv(DEPT_PK, "DEPTV"), _tv(BANK_PK, RBC_VPK)], _slots())
    assert got["department"] == "0104"
    assert got["bank_account"] == "1033760"


# ── transform() ────────────────────────────────────────────────────────────────

def _extract(bank_code):
    from app.services.nc_sync import NcExtract
    return NcExtract(
        ccy={"CADPK": "CAD"},
        aux={"A1": {"bank_account": bank_code}},
        vouchers=[("P1", "2026", "07", 1, "RBC payment run", "2026-07-02 09:00:00",
                   "2026-07-03 08:00:00", "2026-07-03 09:00:00", "GL",
                   0, "N", "N", None, 0, "SUNQI", "LIUYUHONG", "LIUYUHONG", "记账凭证")],
        details=[("P1", 1, "100201", 0, 48336.03, 0, 48336.03, "CADPK", 1, "", "A1",
                  0, 0, 0, None, None)],
        max_creationtime="2026-07-03 08:00:00", tallied={"P1"})


def test_transform_sets_the_line_column_and_the_dim_row():
    from app.services.nc_sync import transform
    bank_id = uuid.uuid4()
    _, lines, dims, _, _ = transform(
        _extract("1033760"), {}, {}, {}, {}, {}, set(), uni_bank={"1033760": bank_id})
    assert lines[0][17] is bank_id                     # journal_voucher_lines.bank_account_id
    bank_dims = [d for d in dims if d[2] == "bank_account"]
    assert len(bank_dims) == 1
    assert bank_dims[0][3] is bank_id                  # value_id
    assert bank_dims[0][4] == "1033760"                # value_text = raw NC code


def test_transform_keeps_the_code_when_the_master_is_missing():
    from app.services.nc_sync import transform
    _, lines, dims, _, _ = transform(
        _extract("1033760"), {}, {}, {}, {}, {}, set(), uni_bank={})
    assert lines[0][17] is None
    bank_dims = [d for d in dims if d[2] == "bank_account"]
    assert len(bank_dims) == 1 and bank_dims[0][4] == "1033760"


def test_transform_emits_nothing_when_there_is_no_bank_aux():
    from app.services.nc_sync import transform
    _, lines, dims, _, _ = transform(
        _extract(""), {}, {}, {}, {}, {}, set(), uni_bank={"1033760": uuid.uuid4()})
    assert lines[0][17] is None
    assert not [d for d in dims if d[2] == "bank_account"]


def test_transform_without_uni_bank_does_not_crash():
    """nc_sync's CLI/emergency path and older callers pass no uni_bank."""
    from app.services.nc_sync import transform
    _, lines, dims, _, _ = transform(_extract("1033760"), {}, {}, {}, {}, {}, set())
    assert lines[0][17] is None
    assert [d[4] for d in dims if d[2] == "bank_account"] == ["1033760"]


# ── the dimension is expandable, not just carried ──────────────────────────────

def test_bank_account_is_a_supported_expansion():
    """Before 0036 this dim was in DIM_LABELS but NOT in _dimensions(), so
    list_dims reported it `supported: false` and expand_by_dims rejected it."""
    from app.crud.account_balance import DIM_LABELS, _check_dims, _dimensions
    assert "bank_account" in _dimensions()
    assert DIM_LABELS["bank_account"] == "Bank Account"
    reg = _check_dims(["bank_account"])
    assert reg["bank_account"][0].key == "bank_account_id"


def test_unsupported_dims_still_rejected():
    """Positive/negative pair: _check_dims accepting bank_account must not mean
    it accepts anything."""
    from app.crud.account_balance import BadDims, _check_dims
    with pytest.raises(BadDims):
        _check_dims(["bank_category"])
    with pytest.raises(BadDims):
        _check_dims([])
