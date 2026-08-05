"""app/services/wms_lot_lookup.py::lookup_lot — direct unit coverage.

test_consignment.py exercises the API layer with `lookup_lot` monkeypatched
away entirely (per the task brief, so the endpoint tests don't depend on
Oracle/WMS) — that leaves the function's own "never raise, never block the
save" contract unverified by anything committed. This file closes that gap
by mocking `oracledb` itself (the real package is importable on this host,
so we monkeypatch its `makedsn`/`connect` attributes rather than faking
`sys.modules`, the same object `wms_lot_lookup.py`'s local `import oracledb`
resolves to) and never touching real network/Oracle-client state.

`_ensure_thick` is monkeypatched to a no-op in every test that reaches the
query path — the real one calls `oracledb.init_oracle_client(lib_dir=...)`,
which would try to load an actual Instant Client from
`settings.oracle_client_lib` and fail loudly on a host that doesn't have one
(as this dev machine doesn't), independent of anything these tests are
trying to verify.
"""
import oracledb
import pytest

from app.services import wms_lot_lookup

_NOT_FOUND = {"found": False, "production_date": None, "expiry_date": None}


class _FakeCursor:
    def __init__(self, row=None, raise_on_execute=None):
        self._row = row
        self._raise_on_execute = raise_on_execute

    def execute(self, *args, **kwargs):
        if self._raise_on_execute is not None:
            raise self._raise_on_execute

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self, row=None, raise_on_execute=None):
        self._cursor = _FakeCursor(row=row, raise_on_execute=raise_on_execute)
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


# ── 1. WMS not configured — never attempts a connection ────────────────────


def test_not_configured_returns_not_found_without_connecting(monkeypatch):
    monkeypatch.setattr(wms_lot_lookup, "wms_configured", lambda: False)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("oracledb.connect must not be called when WMS is not configured")

    monkeypatch.setattr(oracledb, "connect", _fail_if_called)

    result = wms_lot_lookup.lookup_lot("HGC1976532", "CF0086")
    assert result == _NOT_FOUND


# ── 2. connect()/query raises — swallowed, connection closed, never leaks ──


def test_connect_raises_returns_not_found_and_does_not_propagate(monkeypatch):
    monkeypatch.setattr(wms_lot_lookup, "wms_configured", lambda: True)
    monkeypatch.setattr(wms_lot_lookup, "_ensure_thick", lambda: None)
    monkeypatch.setattr(oracledb, "makedsn", lambda *a, **k: "fake-dsn")

    def _raise_connect(*args, **kwargs):
        raise RuntimeError("ORA-12541: TNS:no listener")

    monkeypatch.setattr(oracledb, "connect", _raise_connect)

    result = wms_lot_lookup.lookup_lot("HGC1976532", "CF0086")
    assert result == _NOT_FOUND  # must not propagate the RuntimeError


def test_query_raises_returns_not_found_and_closes_connection(monkeypatch):
    monkeypatch.setattr(wms_lot_lookup, "wms_configured", lambda: True)
    monkeypatch.setattr(wms_lot_lookup, "_ensure_thick", lambda: None)
    monkeypatch.setattr(oracledb, "makedsn", lambda *a, **k: "fake-dsn")

    fake_con = _FakeConnection(raise_on_execute=RuntimeError("ORA-03113: end-of-file on communication channel"))
    monkeypatch.setattr(oracledb, "connect", lambda *a, **k: fake_con)

    result = wms_lot_lookup.lookup_lot("HGC1976532", "CF0086")
    assert result == _NOT_FOUND  # must not propagate
    assert fake_con.closed is True, "the connection must be closed even when the query raises (no handle leak)"


# ── 3. Query path: no row / found row / malformed date strings ─────────────


def test_no_matching_row_returns_not_found(monkeypatch):
    monkeypatch.setattr(wms_lot_lookup, "wms_configured", lambda: True)
    monkeypatch.setattr(wms_lot_lookup, "_ensure_thick", lambda: None)
    monkeypatch.setattr(oracledb, "makedsn", lambda *a, **k: "fake-dsn")

    fake_con = _FakeConnection(row=None)
    monkeypatch.setattr(oracledb, "connect", lambda *a, **k: fake_con)

    result = wms_lot_lookup.lookup_lot("NO-SUCH-LOT", "NO-SUCH-SKU")
    assert result == _NOT_FOUND
    assert fake_con.closed is True


def test_found_row_parses_lotatt01_lotatt02_into_dates(monkeypatch):
    monkeypatch.setattr(wms_lot_lookup, "wms_configured", lambda: True)
    monkeypatch.setattr(wms_lot_lookup, "_ensure_thick", lambda: None)
    monkeypatch.setattr(oracledb, "makedsn", lambda *a, **k: "fake-dsn")

    fake_con = _FakeConnection(row=("2025-01-03", "2027-01-02"))
    monkeypatch.setattr(oracledb, "connect", lambda *a, **k: fake_con)

    from datetime import date
    result = wms_lot_lookup.lookup_lot("HGC1976532", "CF0086")
    assert result == {
        "found": True,
        "production_date": date(2025, 1, 3),
        "expiry_date": date(2027, 1, 2),
    }
    assert fake_con.closed is True


@pytest.mark.parametrize("row", [
    (None, None),
    ("", ""),
    ("not-a-date", "also-not-a-date"),
])
def test_found_row_with_malformed_dates_returns_none_dates_not_an_exception(monkeypatch, row):
    monkeypatch.setattr(wms_lot_lookup, "wms_configured", lambda: True)
    monkeypatch.setattr(wms_lot_lookup, "_ensure_thick", lambda: None)
    monkeypatch.setattr(oracledb, "makedsn", lambda *a, **k: "fake-dsn")

    fake_con = _FakeConnection(row=row)
    monkeypatch.setattr(oracledb, "connect", lambda *a, **k: fake_con)

    result = wms_lot_lookup.lookup_lot("HGC1976532", "CF0086")
    # A row was found (query matched) but the raw date strings are unusable —
    # this must still report found=True with dates=None, not raise.
    assert result == {"found": True, "production_date": None, "expiry_date": None}
