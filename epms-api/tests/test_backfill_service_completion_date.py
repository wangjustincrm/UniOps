"""回填脚本的选取口径 —— 直接跑脚本里那两条 SQL,而不是复述一遍。

脚本本身是 standalone psycopg2(跑在容器/宿主上,不经过 app),所以这里用
conftest 的 psycopg2 fixture 在同一张测试库上验它的 WHERE 到底圈住了谁。
"""
import uuid
from datetime import date

import psycopg2.extras
import pytest

from scripts.backfill_service_completion_date import (
    CANDIDATES_SQL,
    GR_VOID_STATUSES,
    OPEN_PO_STATUSES,
    SERVICE_TYPES,
    UPDATE_SQL,
)

PARAMS = {
    "types": list(SERVICE_TYPES),
    "open_statuses": list(OPEN_PO_STATUSES),
    "void_statuses": list(GR_VOID_STATUSES),
}


@pytest.fixture
def seed(pg_cur, system_user_id):
    """建 vendor,返回一个「造一条 PR+PO(可选 GR)」的工厂。

    用户复用 conftest 的 system_user_id(走 writer.ensure_system_user_sync)——
    users 表有好几列 NOT NULL 且无默认,手写 INSERT 会随 schema 漂移而反复失败。
    这里要验的是脚本的 WHERE,不是建用户。
    """
    uid = system_user_id
    vid = uuid.uuid4()
    pg_cur.execute(
        "insert into business_partners "
        "(id, code, name, category, contact_name, contact_email, payment_terms, "
        " currency, is_active, is_supplier, is_customer) "
        "values (%s,%s,'Acme Services','supplier','AP','ap@acme.example','net30',"
        "'CAD',true,true,false)",
        (vid, f"V-BF-{uuid.uuid4().hex[:6]}"))

    def _make(*, pr_type=4, po_status="issued", completion=None,
              expected_delivery=None, required_by=None, gr_status=None):
        tag = uuid.uuid4().hex[:8]
        pr_id, po_id = uuid.uuid4(), uuid.uuid4()
        # 列清单照测试库的实际 schema 写。测试库是 Base.metadata.create_all 建的,
        # SQLAlchemy 的 Python 端 default= 不落成 server default,所以这里
        # NOT NULL 无默认的列比生产库多(is_prepaid / over_budget /
        # approval_step_idx / subtotal ...),必须全部显式给值。
        pg_cur.execute(
            "insert into purchase_requests "
            "(id, number, title, type, status, vendor_id, vendor_name, currency, "
            " amount, is_prepaid, over_budget, approval_step_idx, created_by, "
            " required_by, service_completion_date) "
            "values (%s,%s,'Duct cleaning',%s,'approved',%s,'Acme Services','CAD',"
            "1000,false,false,0,%s,%s,%s)",
            (pr_id, f"PR-BF-{tag}", pr_type, vid, uid, required_by, completion))
        pg_cur.execute(
            "insert into purchase_orders "
            "(id, number, title, type, status, vendor_id, vendor_name, currency, "
            " subtotal, tax_amount, tax_rate, total, is_prepaid, approval_step_idx, "
            " created_by, pr_id, expected_delivery) "
            "values (%s,%s,'Duct cleaning',%s,%s,%s,'Acme Services','CAD',"
            "1000,0,0,1000,false,0,%s,%s,%s)",
            (po_id, f"PO-BF-{tag}", pr_type, po_status, vid, uid, pr_id,
             expected_delivery))
        if gr_status:
            pg_cur.execute(
                "insert into goods_receipts "
                "(id, number, title, gr_type, procurement_type, po_id, po_number, "
                " vendor_id, vendor_name, currency, status, created_by) "
                "values (%s,%s,'Duct cleaning','service',%s,%s,%s,%s,'Acme Services',"
                "'CAD',%s,%s)",
                (uuid.uuid4(), f"GR-BF-{tag}", pr_type, po_id, f"PO-BF-{tag}",
                 vid, gr_status, uid))
        return pr_id, f"PO-BF-{tag}"

    return _make


def _candidates(pg_conn):
    with pg_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(CANDIDATES_SQL, PARAMS)
        return {r["po_number"]: r for r in cur.fetchall()}


def test_po_expected_delivery_wins(pg_conn, seed):
    _, po = seed(expected_delivery=date(2026, 6, 30), required_by=date(2026, 5, 1))
    assert _candidates(pg_conn)[po]["inferred"] == date(2026, 6, 30)


def test_falls_back_to_pr_required_by(pg_conn, seed):
    _, po = seed(expected_delivery=None, required_by=date(2026, 5, 1))
    assert _candidates(pg_conn)[po]["inferred"] == date(2026, 5, 1)


def test_a_row_with_neither_date_is_listed_but_uninferable(pg_conn, seed):
    """留在结果里是刻意的 —— 脚本要把它报出来说「这条推不出日期」,而不是静默丢掉。"""
    _, po = seed(expected_delivery=None, required_by=None)
    assert _candidates(pg_conn)[po]["inferred"] is None


def test_type_6_is_included(pg_conn, seed):
    _, po = seed(pr_type=6, expected_delivery=date(2026, 6, 30))
    assert po in _candidates(pg_conn)


@pytest.mark.parametrize("pr_type", [1, 2, 3, 5])
def test_physical_types_are_excluded(pg_conn, seed, pr_type):
    _, po = seed(pr_type=pr_type, expected_delivery=date(2026, 6, 30))
    assert po not in _candidates(pg_conn)


def test_a_pr_that_already_has_a_date_is_excluded(pg_conn, seed):
    """重跑安全性:已经有值的行被 WHERE 排除,第二遍报 0 条。"""
    _, po = seed(completion=date(2026, 7, 1), expected_delivery=date(2026, 6, 30))
    assert po not in _candidates(pg_conn)


@pytest.mark.parametrize("po_status", ["draft", "fully_received", "closed", "cancelled"])
def test_closed_or_unstarted_pos_are_left_alone(pg_conn, seed, po_status):
    _, po = seed(po_status=po_status, expected_delivery=date(2026, 6, 30))
    assert po not in _candidates(pg_conn)


@pytest.mark.parametrize("gr_status", ["pending_ack", "confirmed"])
def test_a_po_that_already_has_a_gr_is_left_alone(pg_conn, seed, gr_status):
    _, po = seed(expected_delivery=date(2026, 6, 30), gr_status=gr_status)
    assert po not in _candidates(pg_conn)


@pytest.mark.parametrize("gr_status", ["rejected", "cancelled"])
def test_a_void_gr_does_not_protect_the_po(pg_conn, seed, gr_status):
    _, po = seed(expected_delivery=date(2026, 6, 30), gr_status=gr_status)
    assert po in _candidates(pg_conn)


def test_the_update_lands_and_is_idempotent(pg_conn, seed):
    pr_id, po = seed(expected_delivery=date(2026, 6, 30))
    with pg_conn.cursor() as cur:
        cur.execute(UPDATE_SQL, {"value": date(2026, 6, 30), "pr_id": pr_id})
        assert cur.rowcount == 1
        cur.execute("select service_completion_date from purchase_requests where id=%s",
                    (pr_id,))
        assert cur.fetchone()[0] == date(2026, 6, 30)
        # 第二次不该再改任何行(UPDATE 自带 `is null` 守卫)
        cur.execute(UPDATE_SQL, {"value": date(2026, 1, 1), "pr_id": pr_id})
        assert cur.rowcount == 0
    assert po not in _candidates(pg_conn)
