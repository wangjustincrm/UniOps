from decimal import Decimal

import pytest

from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import InvalidInput, NotFound
from app.platform.query import Filter, QuerySpec, SortKey, parse_filter, parse_sort
from app.platform.repository import Repository
from tests.platform.conftest import Note, Widget, WidgetRepository


async def _seed(uow_factory, rows):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        for code, name, price, hist in rows:
            uow.session.add(Widget(code=code, name=name, price=Decimal(price), historical=hist))
        await uow.commit()


ROWS = [("A1", "Apple", "3.00", False), ("B2", "Banana", "1.50", False),
        ("C3", "Cherry_pie", "9.99", False), ("H9", "Old apple", "1.00", True)]


async def test_page_total_sort_search_filters(uow_factory):
    await _seed(uow_factory, ROWS)
    async with uow_factory(SYSTEM_ACTOR) as uow:
        repo = WidgetRepository(uow)
        page = await repo.list(QuerySpec(page_size=2))
        assert page.total == 3 and [w.code for w in page.items] == ["A1", "B2"]  # default sort, no history
        page2 = await repo.list(QuerySpec(page=2, page_size=2))
        assert [w.code for w in page2.items] == ["C3"]
        desc = await repo.list(QuerySpec(sort=[SortKey(field="price", desc=True)]))
        assert [w.code for w in desc.items] == ["C3", "A1", "B2"]
        found = await repo.list(QuerySpec(search="apple"))
        assert [w.code for w in found.items] == ["A1"]
        # LIKE wildcards in the search text are literal.
        assert (await repo.list(QuerySpec(search="_"))).total == 1
        ranged = await repo.list(QuerySpec(filters=[parse_filter("price:range:1.50..3.00")]))
        assert sorted(w.code for w in ranged.items) == ["A1", "B2"]
        listed = await repo.list(QuerySpec(filters=[Filter(field="code", op="in", value=["A1", "C3"])]))
        assert [w.code for w in listed.items] == ["A1", "C3"]


async def test_historical_rows_only_on_request_and_never_on_open_work(uow_factory):
    await _seed(uow_factory, ROWS)
    async with uow_factory(SYSTEM_ACTOR) as uow:
        repo = WidgetRepository(uow)
        assert (await repo.list(QuerySpec())).total == 3
        assert (await repo.list(QuerySpec(include_historical=True))).total == 4
        assert (await repo.list(QuerySpec(include_historical=True), open_work=True)).total == 3


@pytest.mark.parametrize("spec,code", [
    (QuerySpec(sort=[SortKey(field="email")]), "query.unknown_field"),
    (QuerySpec(filters=[Filter(field="email", op="eq", value="x")]), "query.unknown_field"),
    (QuerySpec(filters=[Filter(field="code", op="range", value=["a", "b"])]), "query.unsupported_operator"),
    (QuerySpec(filters=[Filter(field="price", op="range", value=["abc", None])]), "query.bad_value"),
])
async def test_unknown_keys_and_bad_values_never_reach_sql(uow_factory, spec, code):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        with pytest.raises(InvalidInput) as e:
            await WidgetRepository(uow).list(spec)
        assert e.value.code == code


def test_query_string_parsing():
    assert parse_sort("code,-price") == [SortKey(field="code"), SortKey(field="price", desc=True)]
    assert parse_filter("code:in:a|b").value == ["a", "b"]
    assert parse_filter("price:range:..5").value == [None, "5"]
    with pytest.raises(InvalidInput):
        parse_filter("code:like:x")
    with pytest.raises(InvalidInput):
        parse_filter("nonsense")


async def test_get_missing_is_not_found(uow_factory):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        with pytest.raises(NotFound):
            await WidgetRepository(uow).get(999)


class NoteRepository(Repository[Note]):
    model = Note
    sortable = {"title": Note.title}


async def test_soft_deleted_rows_are_excluded_and_partial_unique_allows_reuse(uow_factory, person):
    async with uow_factory(person) as uow:
        repo = NoteRepository(uow)
        n = await repo.add(Note(title="t"))
        await repo.soft_delete(n)
        assert n.deleted_by == person.user_id
        await repo.add(Note(title="t"))  # unique only among live rows
        await uow.commit()
    async with uow_factory(person) as uow:
        page = await NoteRepository(uow).list(QuerySpec())
        assert page.total == 1


async def test_repository_has_no_delete():
    assert not hasattr(Repository, "delete")


# ── query bounds (review 2026-10-28)


def test_filter_cast_follows_the_column_type():
    from app.platform.query import FilterRule
    assert FilterRule(Widget.id).convert("42") == 42
    assert FilterRule(Widget.price).convert("1.5") == Decimal("1.5")
    assert FilterRule(Widget.active).convert("yes") is True
    with pytest.raises(ValueError):
        FilterRule(Widget.id).convert("abc")


async def test_bad_value_for_an_untyped_rule_is_a_422_not_a_database_error(uow_factory):
    from app.platform.query import Filter, FilterRule

    class ById(WidgetRepository):
        filterable = {"id": FilterRule(Widget.id)}

    async with uow_factory(SYSTEM_ACTOR) as uow:
        with pytest.raises(InvalidInput) as e:
            await ById(uow).list(QuerySpec(filters=[Filter(field="id", op="eq", value="abc")]))
    assert e.value.code == "query.bad_value"


def test_limits_on_page_in_lists_and_search():
    from pydantic import ValidationError

    from app.platform.query import MAX_IN_VALUES, MAX_PAGE, MAX_SEARCH, parse_filter
    with pytest.raises(ValidationError):
        QuerySpec(page=MAX_PAGE + 1)
    with pytest.raises(ValidationError):
        QuerySpec(search="x" * (MAX_SEARCH + 1))
    with pytest.raises(InvalidInput) as e:
        parse_filter("code:in:" + "|".join(str(i) for i in range(MAX_IN_VALUES + 1)))
    assert e.value.code == "query.too_many_values"
    assert len(parse_filter("code:in:" + "|".join(str(i) for i in range(MAX_IN_VALUES))).value) == MAX_IN_VALUES
    with pytest.raises(InvalidInput):
        parse_filter("code:eq:" + "x" * 201)
