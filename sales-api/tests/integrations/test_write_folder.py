from types import SimpleNamespace

import pytest

from app.integrations.sftp import write_folder, writes_allowed
from app.platform.guard import Suppressed


def conn(**kw):
    return SimpleNamespace(**({"id": 3, "name": "TrueCommerce", "can_write": False, "host": "h.example", "outbound_folder": "/real/",
                               "inbound_folder": "/out/", "processed_folder": "/archive/", "error_folder": "/error/"} | kw))


def test_with_write_off_a_send_is_suppressed_never_failed():
    with pytest.raises(Suppressed) as e:
        write_folder(conn())
    assert "writing is off" in str(e.value)


def test_with_write_on_a_send_goes_to_the_write_folder():
    assert write_folder(conn(can_write=True)) == "/real/"
    with pytest.raises(RuntimeError):
        write_folder(conn(can_write=True, outbound_folder=None))


def test_the_guard_lets_through_only_the_write_folder_and_the_read_sides_moves():
    allow = writes_allowed(SimpleNamespace(sftp_write_hosts=frozenset({"h.example"})))
    on = conn(can_write=True)
    assert allow("put", (conn(), "/real/", "a", b"x"), {}) == "writing is off for this connection"
    assert allow("put", (on, "/real", "a", b"x"), {}) is None                       # a trailing slash does not matter
    assert allow("put", (on, "/archive/", "a", b"x"), {}) == "a put outside the write folder"
    assert allow("move", (on, "/out/a.edi", "/archive/a.edi"), {}) is None
    assert allow("move", (on, "/out/a.edi", "/error/a.edi"), {}) is None
    assert allow("move", (on, "/real/a.edi", "/archive/a.edi"), {}) == "a move outside the read folder and its archive / error folders"
    assert allow("move", (on, "/out/a.edi", "/real/a.edi"), {}) == "a move outside the read folder and its archive / error folders"
    assert allow("delete", (on, "/out/a.edi"), {}) is None
    assert allow("delete", (on, "/real/a.edi"), {}) == "a delete outside the read folder"
    assert allow("put", (conn(can_write=True, host="other"), "/real/", "a", b"x"), {}) == "host not in SALES_SFTP_WRITE_HOSTS"
