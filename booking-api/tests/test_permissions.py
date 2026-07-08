import pytest

from app.core.permissions import has_permission

# has_permission(role, key, stored_matrix) — pure function under the dependency

def test_system_admin_always_allowed():
    assert has_permission("system_admin", "manage_meeting_rooms", {}) is True

def test_stored_matrix_wins():
    m = {"requester": {"view_booking": False}}
    assert has_permission("requester", "view_booking", m) is False

def test_missing_key_falls_back_to_default_view_true():
    assert has_permission("requester", "view_booking", {"requester": {}}) is True

def test_missing_key_falls_back_to_default_manage_false():
    assert has_permission("requester", "manage_meeting_rooms", {}) is False
