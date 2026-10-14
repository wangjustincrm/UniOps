"""The sync replaces the mirrors atomically and ties them out against NC.

The extract is faked: a tiny, internally consistent NC (flows that sum to the
on-hand, a ledger that rolls to the month-end balance, posted GL lines that
equal GL_BALANCE). Each test then breaks one thing and checks that the
tie-out names it — a tie-out that only ever says "OK" proves nothing
(feedback_uniops_denial_test_needs_admission_test).
"""
import copy
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import InvSyncState, InvTieoutIssue, NcGlLine, NcIcFlow, NcWarehouse
from app.services.nc_sync.service import run_nc_sync

D = Decimal


def _nc():
    return {
        "tables": {
            "warehouse": [
                {"pk_stordoc": "W11", "code": "SC11", "name": "Raw", "iscalculatedinvcost": "Y"},
                {"pk_stordoc": "W16", "code": "SC16", "name": "Old", "iscalculatedinvcost": "N"},
            ],
            "material": [{"pk_material": "M1", "code": "CR0025", "name": "乳糖", "ename": "Lactose"}],
            "ic_flow": [
                {"pk_flow": "F1", "cwarehouseid": "W11", "cmaterialoid": "M1", "vbatchcode": "B1",
                 "ninnum": D("100"), "dbizdate": "2026-08-01 10:00:00", "fbillflag": 3},
                {"pk_flow": "F2", "cwarehouseid": "W11", "cmaterialoid": "M1", "vbatchcode": "B1",
                 "noutnum": D("30"), "dbizdate": "2026-08-20 10:00:00", "fbillflag": 3},
            ],
            "ic_onhand": [
                {"pk_onhanddim": "H1", "cwarehouseid": "W11", "cmaterialoid": "M1", "vbatchcode": "B1",
                 "nonhandnum": D("70"), "nlocknum": D("0")},
            ],
            "ia_period": [{"caccountid": "P8", "caccountperiod": "2026-08"}],
            "ia_ledger": [
                {"cdetailledgerid": "L1", "cinventoryid": "M1", "caccountperiod": "2026-08",
                 "fdispatchflag": 0, "nnum": D("100"), "nmny": D("200")},
                {"cdetailledgerid": "L2", "cinventoryid": "M1", "caccountperiod": "2026-08",
                 "fdispatchflag": 1, "nnum": D("30"), "nmny": D("60")},
                # An unclosed-period row must NOT enter the 2026-08 check.
                {"cdetailledgerid": "L3", "cinventoryid": "M1", "caccountperiod": "2026-09",
                 "fdispatchflag": 0, "nnum": D("5"), "nmny": D("10")},
            ],
            "ia_monthnab": [{"cmonthnabid": "N1", "cinventoryid": "M1", "caccountperiod": "2026-08",
                             "nabnum": D("70"), "nabmny": D("140")}],
            "gl_line": [
                {"pk_detail": "G1", "pk_voucher": "V1", "year": "2026", "period": "08",
                 "tallydate": "2026-09-02", "pk_system": "IA", "account_code": "140302",
                 "material_pk": "M1", "debit_amount": D("140"), "debit_qty": D("70")},
                # Unposted: in the mirror, but not in GL_BALANCE — must not trip the tie-out.
                {"pk_detail": "G2", "pk_voucher": "V2", "year": "2026", "period": "10",
                 "tallydate": "~", "pk_system": "GL", "account_code": "140302",
                 "material_pk": "M1", "debit_amount": D("999")},
            ],
        },
        # oracledb returns count(*) as Decimal (fetch_decimals=True) — the
        # first real-NC run crashed storing it in JSONB.
        "filtered": {"ia_ledger_deleted": D("3")},
        "reference": {"gl_balance": [
            {"year": "2026", "account_code": "140302", "amount": D("140"), "qty": D("70")},
        ]},
    }


async def _issues(db):
    return (await db.execute(select(InvTieoutIssue).order_by(InvTieoutIssue.check_name))).scalars().all()


async def test_consistent_nc_ties_out_and_records_counts(db_session):
    result = await run_nc_sync(db_session, extract=_nc)

    assert result["tieout_ok"] is True
    assert result["rows"]["ic_flow"] == 2
    assert result["filtered"]["ia_ledger_deleted"] == 3
    state = await db_session.get(InvSyncState, "nc")
    assert state.status == "success" and state.tieout_ok is True and state.tieout_issue_count == 0
    assert state.last_success_at is not None and state.last_success_at == state.last_synced_at
    assert await _issues(db_session) == []
    # Admission side: the mirror really holds the rows (not "OK because empty").
    assert (await db_session.execute(select(func.count()).select_from(NcGlLine))).scalar_one() == 2
    sc16 = (await db_session.execute(select(NcWarehouse).where(NcWarehouse.code == "SC16"))).scalar_one()
    assert sc16.iscalculatedinvcost is False


async def test_ic_flow_not_matching_onhand_is_named(db_session):
    nc = _nc()
    nc["tables"]["ic_onhand"][0]["nonhandnum"] = D("69")
    result = await run_nc_sync(db_session, extract=lambda: nc)

    assert result["tieout_ok"] is False
    [issue] = await _issues(db_session)
    assert issue.check_name == "ic_flow_vs_onhand"
    assert issue.mirror_value == D("70") and issue.nc_value == D("69")
    assert "SC11" in issue.label and "CR0025" in issue.label and "B1" in issue.label


async def test_ia_ledger_not_rolling_to_monthnab_is_named_for_qty_and_amount(db_session):
    nc = _nc()
    nc["tables"]["ia_monthnab"][0].update(nabnum=D("71"), nabmny=D("150"))
    await run_nc_sync(db_session, extract=lambda: nc)

    issues = await _issues(db_session)
    assert {(i.check_name, i.measure) for i in issues} == {
        ("ia_ledger_vs_monthnab", "qty"), ("ia_ledger_vs_monthnab", "amount")}


async def test_gl_posted_lines_not_matching_balance_is_named(db_session):
    nc = _nc()
    nc["reference"]["gl_balance"][0]["amount"] = D("141")
    await run_nc_sync(db_session, extract=lambda: nc)

    [issue] = await _issues(db_session)
    assert (issue.check_name, issue.measure, issue.key) == ("gl_lines_vs_balance", "amount", "2026|140302")


async def test_second_sync_replaces_not_appends(db_session):
    await run_nc_sync(db_session, extract=_nc)
    nc = _nc()
    nc["tables"]["ic_flow"] = nc["tables"]["ic_flow"][:1]
    nc["tables"]["ic_onhand"][0]["nonhandnum"] = D("100")
    await run_nc_sync(db_session, extract=lambda: nc)

    assert (await db_session.execute(select(func.count()).select_from(NcIcFlow))).scalar_one() == 1
    assert await _issues(db_session) == []


async def test_failed_extract_keeps_previous_snapshot_and_records_error(db_session):
    await run_nc_sync(db_session, extract=_nc)
    before = (await db_session.get(InvSyncState, "nc")).last_success_at

    def boom():
        raise ConnectionError("ORA-12170: TNS:Connect timeout occurred")

    with pytest.raises(ConnectionError):
        await run_nc_sync(db_session, extract=boom)

    state = await db_session.get(InvSyncState, "nc")
    await db_session.refresh(state)
    assert state.status == "failed"
    assert "ORA-12170" in state.last_error
    assert state.last_success_at == before                 # data age unchanged
    assert state.last_synced_at > before                   # but the attempt is recorded
    assert (await db_session.execute(select(func.count()).select_from(NcIcFlow))).scalar_one() == 2


async def test_failure_after_delete_rolls_back_to_previous_snapshot(db_session):
    """A row that breaks the INSERT (here: an over-long code) must not leave the
    mirror half-emptied."""
    await run_nc_sync(db_session, extract=_nc)
    nc = copy.deepcopy(_nc())
    nc["tables"]["warehouse"][0]["code"] = "X" * 500
    with pytest.raises(Exception):
        await run_nc_sync(db_session, extract=lambda: nc)

    codes = (await db_session.execute(select(NcWarehouse.code).order_by(NcWarehouse.code))).scalars().all()
    assert codes == ["SC11", "SC16"]
