"""NC (thin-capable) and WMS (thick-only) share one process. Whichever
connects first must put the process in THICK mode when the client exists,
or the other can never connect (DPY-2019, found on dev1)."""
import sys
import types

import pytest

from app.services import oracle_client


@pytest.fixture
def fake_oracledb(monkeypatch, tmp_path):
    calls = []
    def connect(**k):
        calls.append("connect")
        raise RuntimeError("stop")

    mod = types.SimpleNamespace(init_oracle_client=lambda lib_dir: calls.append("init"),
                                defaults=types.SimpleNamespace(fetch_decimals=False),
                                makedsn=lambda *a, **k: "dsn", connect=connect)
    monkeypatch.setitem(sys.modules, "oracledb", mod)
    monkeypatch.setattr(oracle_client, "_done", False)
    monkeypatch.setattr(oracle_client.settings, "oracle_client_lib", str(tmp_path))
    return calls


@pytest.mark.parametrize("reader", ["nc", "wms"])
def test_first_connection_of_either_source_goes_thick_once(fake_oracledb, monkeypatch, reader):
    from app.services.nc_sync import reader as nc
    from app.services.wms_sync import reader as wms
    first, second = (nc._connect, wms._ensure_thick) if reader == "nc" else (wms._ensure_thick, nc._connect)
    for fn in (first, second):
        try:
            fn()
        except RuntimeError:
            pass
    # Thick initialised exactly once, and BEFORE the first connection —
    # the old NC reader connected first, which locks the process into thin.
    assert fake_oracledb.count("init") == 1
    assert fake_oracledb[0] == "init"


def test_no_client_directory_stays_thin(fake_oracledb, monkeypatch):
    monkeypatch.setattr(oracle_client.settings, "oracle_client_lib", "/nonexistent")
    oracle_client.ensure_mode()
    assert "init" not in fake_oracledb
