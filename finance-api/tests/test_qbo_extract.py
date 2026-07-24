# tests/test_qbo_extract.py
from scripts.qbo_import.extract import build_where, extract_entity


class FakeClient:
    """Records the where-clauses query_all was called with; returns canned rows."""
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def query_all(self, entity, where=""):
        self.calls.append((entity, where))
        return iter(self._rows)


def test_build_where_full_is_empty():
    assert build_where(None) == ""


def test_build_where_incremental_uses_lastupdated():
    w = build_where("2019-05-02T10:00:00-07:00")
    assert w == "MetaData.LastUpdatedTime > '2019-05-02T10:00:00-07:00'"


def test_extract_entity_full_collects_rows():
    c = FakeClient([{"Id": "1"}, {"Id": "2"}])
    rows = extract_entity(c, "Bill", since=None)
    assert [r["Id"] for r in rows] == ["1", "2"]
    assert c.calls == [("Bill", "")]


def test_extract_entity_incremental_passes_where():
    c = FakeClient([{"Id": "3"}])
    extract_entity(c, "Bill", since="2019-05-02T10:00:00-07:00")
    assert c.calls[0][1] == "MetaData.LastUpdatedTime > '2019-05-02T10:00:00-07:00'"
