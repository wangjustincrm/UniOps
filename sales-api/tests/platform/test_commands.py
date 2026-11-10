"""Loading a command's record (review 2026-11-10): a person's command names the version it saw."""
from types import SimpleNamespace

import pytest

from app.platform.commands import ANY_VERSION, Stale, check_version
from app.platform.errors import InvalidInput


def test_the_version_seen_passes_another_is_stale_with_both_versions():
    check_version(SimpleNamespace(version=3), 3)
    with pytest.raises(Stale) as e:
        check_version(SimpleNamespace(version=4), 3, "this order")
    assert (e.value.code, e.value.status_code, e.value.details) == ("entity.stale", 409, {"current_version": 4, "sent_version": 3})
    assert "this order was changed by someone else" in e.value.message


def test_leaving_the_version_out_is_refused_not_taken_as_whatever_is_there():
    with pytest.raises(InvalidInput) as e:
        check_version(SimpleNamespace(version=4), None)
    assert e.value.code == "entity.version_required"


def test_a_system_caller_acts_on_what_is_there_only_by_saying_so():
    check_version(SimpleNamespace(version=99), ANY_VERSION)
