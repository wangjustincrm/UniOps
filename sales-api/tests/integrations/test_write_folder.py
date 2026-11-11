from types import SimpleNamespace

import pytest

from app.integrations.sftp import write_folder
from app.platform.guard import Suppressed


def conn(**kw):
    return SimpleNamespace(**({"id": 3, "name": "TrueCommerce", "mode": "TEST", "outbound_folder": "/real/", "test_outbound_folder": None} | kw))


def test_a_test_connection_writes_into_its_test_folder():
    assert write_folder(conn(test_outbound_folder="/test/in/")) == "/test/in/"


def test_a_test_connection_without_a_test_folder_sends_nothing_and_is_suppressed():
    with pytest.raises(Suppressed) as e:
        write_folder(conn())
    assert "nothing is sent in Test mode" in str(e.value)


def test_a_live_connection_writes_into_its_outbound_folder():
    assert write_folder(conn(mode="LIVE")) == "/real/"
    with pytest.raises(RuntimeError):
        write_folder(conn(mode="LIVE", outbound_folder=None))
