"""The reader must never hand a SUPERSEDED NC purchase order to the mirror.

NC keeps every version of a changed purchase order as its OWN ``PO_ORDER`` row,
all carrying the same ``vbillcode`` and all with ``forderstatus=3``; only
``bislatest='Y'`` marks the one in force. The arrivals hang off the LATEST
version — production evidence: not a single ``PO_ARRIVEORDER_B`` row in NC
points at a ``bislatest='N'`` order.

Without the filter both versions are fetched, ``purchase_orders.number`` is
UNIQUE, and whichever version Oracle happened to return first won the number —
so the mirror could (and did, for PO-019-2505-01) keep the CANCELLED version
while the live one, carrying all three arrivals, was dropped. The PO then shows
zero received forever, in EPMS and in MRP's in-transit figure alike.

These tests drive ``fetch_nc`` against a fake Oracle cursor and assert the
predicate is present on EVERY read of ``PO_ORDER`` — the aggregate check is the
point: a future query added without it reintroduces the same bug.
"""
import re

import pytest

from app.services.nc_purchase_sync import reader

CUT = "2024-01-01 00:00:00"
WM = "2026-08-01 00:00:00"


class _FakeCursor:
    """Records every statement; answers each with rows shaped like its columns.

    The fake does NOT interpret SQL — it cannot, and pretending to would test
    the fake instead of the reader. It returns empty result sets for the order
    queries, which is enough to exercise every code path, and captures the SQL
    for inspection.
    """

    def __init__(self):
        self.statements: list[str] = []
        self.description = []

    def execute(self, sql, binds=None):
        self.statements.append(sql)
        low = sql.lower()
        if "bd_supplier" in low or "bd_measdoc" in low or "bd_currtype" in low:
            self.description = [("A",), ("B",)]
        elif "bd_material" in low:
            self.description = [("PK_MATERIAL",), ("CODE",), ("NAME",), ("ENAME",)]
        elif "po_order_b" in low:
            self.description = [("PK_ORDER_B",), ("PK_ORDER",)]
        elif "po_arriveorder" in low:
            self.description = [("PK_ARRIVEORDER",), ("MODIFIEDTIME",)]
        else:
            self.description = [("PK_ORDER",), ("MODIFIEDTIME",)]
        self._rows = []

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def close(self):
        pass


@pytest.fixture
def fake_cur(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(reader, "_connect", lambda: _FakeConnection(cur))
    return cur


def _po_order_reads(cur) -> list[str]:
    """Statements that read PO_ORDER — as the driving table or as a join.

    ``PO_ORDER_B`` is excluded: the line table has no version flag of its own
    and is always reached through an already-filtered set of order pks.
    """
    return [
        s for s in cur.statements
        if re.search(r"\bNCSC\.PO_ORDER\b(?!_B)", s, re.IGNORECASE)
    ]


def _has_latest_predicate(sql: str) -> bool:
    return re.search(r"bislatest\s*=\s*'Y'", sql, re.IGNORECASE) is not None


def test_full_load_reads_only_the_version_in_force(fake_cur):
    reader.fetch_nc(CUT, None)

    reads = _po_order_reads(fake_cur)
    assert reads, "expected the full load to read PO_ORDER at all"
    assert all(_has_latest_predicate(s) for s in reads), (
        "every PO_ORDER read must exclude superseded versions:\n"
        + "\n---\n".join(s for s in reads if not _has_latest_predicate(s))
    )


def test_incremental_reads_only_the_version_in_force(fake_cur):
    reader.fetch_nc(CUT, WM)

    reads = _po_order_reads(fake_cur)
    assert reads, "expected the incremental load to read PO_ORDER at all"
    assert all(_has_latest_predicate(s) for s in reads), (
        "every PO_ORDER read must exclude superseded versions:\n"
        + "\n---\n".join(s for s in reads if not _has_latest_predicate(s))
    )


def test_arrival_driven_incremental_branch_excludes_superseded_orders(fake_cur):
    """The arrival branch joins PO_ORDER to reach dbilldate; it must filter too.

    This is the branch that exists precisely because NC does not bump
    ``po_order.modifiedtime`` when an arrival is posted — so it is the branch
    most likely to drag a superseded order back in.
    """
    reader.fetch_nc(CUT, WM)

    arrival_join = [
        s for s in _po_order_reads(fake_cur)
        if re.search(r"po_arriveorder", s, re.IGNORECASE)
    ]
    assert arrival_join, "expected the incremental run to query arrivals"
    assert all(_has_latest_predicate(s) for s in arrival_join)
