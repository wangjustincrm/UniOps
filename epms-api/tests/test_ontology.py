"""The ontology file is data, so something has to keep it honest.

Most of these tests feed the loader a deliberately broken fragment and assert it
refuses. That is the point: the loader's value is entirely in what it rejects,
and a validator that silently accepts a typo is worse than no validator — it
buys confidence it has not earned. If one of these starts passing a bad
fragment, the guarantee described in app/ontology/epms.yaml is gone.
"""
import textwrap

import pytest

from app.core.ontology import REGISTRY, OntologyError, load

# A minimal well-formed fragment. Each test below breaks exactly one thing in
# it, so a failure names the rule that stopped working.
GOOD = """
version: 1
entities:
  purchase_order:
    model: PurchaseOrder
    label: "PO"
    perm_key: view_po
    scope: po
    date_field: created_at
    fields:
      number: {kind: text, label: "PO number"}
      created_at: {kind: datetime, label: "Created at"}
      placed_at: {kind: datetime, label: "Placed at"}
      total: {kind: money, label: "Total"}
    metrics:
      amount: {fn: sum, field: total, label: "Amount"}
"""


def _load(tmp_path, text):
    path = tmp_path / "frag.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return load(path)


def test_the_baseline_fragment_loads(tmp_path):
    """Guards the rest: if GOOD were broken, every rejection below proves nothing."""
    entities = _load(tmp_path, GOOD)
    assert set(entities) == {"purchase_order"}
    assert entities["purchase_order"].date_field == "created_at"


@pytest.mark.parametrize("broken,expect", [
    # The one that matters most: a column the model does not have would surface
    # as a confusing 422, months later, for whoever happened to ask first.
    (GOOD.replace('number: {kind: text, label: "PO number"}',
                  'nope: {kind: text, label: "Not a column"}'),
     "no such column"),
    (GOOD.replace("model: PurchaseOrder", "model: NotAModel"), "unknown model"),
    (GOOD.replace("scope: po", "scope: nonexistent"), "unknown scope"),
    (GOOD.replace("date_field: created_at", "date_field: not_a_field"),
     "is not a declared field"),
    (GOOD.replace("field: total", "field: undeclared"), "is not declared"),
    (GOOD.replace("fn: sum", "fn: median"), "unknown fn"),
    (GOOD.replace('number: {kind: text, label: "PO number"}',
                  'number: {kind: rainbow, label: "PO number"}'), "unknown kind"),
    (GOOD.replace("version: 1", "version: 99"), "unsupported version"),
])
def test_broken_fragments_are_refused(tmp_path, broken, expect):
    with pytest.raises(OntologyError) as exc:
        _load(tmp_path, broken)
    assert expect in str(exc.value)


def test_a_nullable_date_field_is_refused(tmp_path):
    """Regression for a real, silent wrong answer.

    PO originally declared date_field: placed_at, which reads like the correct
    choice — it is when the order went to the vendor. But it is only set for
    orders issued from EPMS; the ones mirrored in from NC have it NULL, which on
    the production snapshot was 6569 of 6755 rows. Every "last three months"
    question quietly answered from 3% of the data, and the replies looked
    entirely reasonable. A nullable default axis is now a startup error.
    """
    bad = GOOD.replace("date_field: created_at", "date_field: placed_at")
    with pytest.raises(OntologyError, match="nullable"):
        _load(tmp_path, bad)


def test_a_nullable_date_field_is_allowed_when_acknowledged(tmp_path):
    """The rule is "say so", not "never" — a receipt date beats a row-creation
    date even though the column allows NULL."""
    ok = GOOD.replace("date_field: created_at",
                      "date_field: placed_at\n    date_field_nullable_ok: true")
    entities = _load(tmp_path, ok)
    assert entities["purchase_order"].date_field == "placed_at"


def test_shipped_date_fields_are_non_nullable_or_acknowledged():
    """Belt and braces on the real fragment. A nullable axis is allowed only
    where the yaml opts in, which forces whoever adds one to look at the
    coverage first."""
    from app.core.ontology import _TABLE_MODELS

    acknowledged = {"goods_receipt"}  # received_at: 0 of 948 null, see the yaml
    for entity in REGISTRY.values():
        # A table-backed entity's columns are all declared nullable because the
        # ontology does not know the real constraint. Their axes are checked
        # against the actual is_nullable, and the actual NULL rate, in
        # test_ontology_matches_the_database.
        if entity.model.__tablename__ in _TABLE_MODELS:
            continue
        nullable = getattr(entity.model, entity.date_field).property.columns[0].nullable
        if nullable:
            assert entity.name in acknowledged, (
                f"{entity.name}.{entity.date_field} is nullable and not acknowledged"
            )


def test_enum_without_values_is_refused(tmp_path):
    bad = GOOD.replace('number: {kind: text, label: "PO number"}',
                       'number: {kind: enum, label: "PO number"}')
    with pytest.raises(OntologyError, match="enum needs values"):
        _load(tmp_path, bad)


def test_link_to_unknown_entity_is_refused(tmp_path):
    bad = GOOD + """
    links:
      ghost:
        target: no_such_entity
        cardinality: many_to_one
        local: pr_id
        remote: id
        label: "Nowhere"
"""
    with pytest.raises(OntologyError, match="unknown target"):
        _load(tmp_path, bad)


def test_link_through_a_column_that_does_not_exist_is_refused(tmp_path):
    bad = GOOD + """
    links:
      self_ref:
        target: purchase_order
        cardinality: many_to_one
        local: not_a_column
        remote: id
        label: "Broken"
"""
    with pytest.raises(OntologyError, match="has no column"):
        _load(tmp_path, bad)


def test_link_to_a_column_missing_on_the_far_side_is_refused(tmp_path):
    """The far end is checked too — it is the half that is easy to get wrong."""
    bad = GOOD + """
    links:
      self_ref:
        target: purchase_order
        cardinality: many_to_one
        local: pr_id
        remote: not_a_column
        label: "Broken"
"""
    with pytest.raises(OntologyError, match="has no column"):
        _load(tmp_path, bad)


def test_empty_fragment_is_refused(tmp_path):
    with pytest.raises(OntologyError, match="no entities"):
        _load(tmp_path, "version: 1\nentities: {}\n")


# ── the shipped fragment ──────────────────────────────────────────────────────


def test_shipped_ontology_covers_the_purchasing_chain():
    """The chain has to be complete end to end — a gap in the middle turns
    "where did this go?" into an answer that stops halfway."""
    assert {"purchase_request", "purchase_order", "goods_receipt",
            "invoice", "payment_application"} <= set(REGISTRY)


def test_every_entity_gates_on_a_permission_that_exists_in_the_matrix():
    """perm_key is looked up in the caller's effective permissions, and a key no
    role can hold reads False for everyone — the entity would be invisible to
    every user including admins, with nothing to indicate why.

    The keys here are the ones the Access Control Matrix defines; this test says
    the ontology may not invent its own.
    """
    known = {
        "view_pr", "view_po", "view_gr", "view_invoice", "view_pa",
        "view_finance", "mrp.report.view",
    }
    for entity in REGISTRY.values():
        assert entity.perm_key in known, (
            f"{entity.name} gates on {entity.perm_key!r}, which is not a matrix "
            f"key this ontology has been checked against")


def test_finance_and_mrp_entities_carry_no_row_filter_on_purpose():
    """Their scope is the permission and nothing else, which is worth stating
    once in a test rather than leaving as an absence.

    If either of these ever needs a row filter — a department manager seeing
    their own cost centre's postings, say — this test is where that decision
    gets recorded, not somewhere it can happen by accident.
    """
    from app.core.ontology import scope_permission_only

    for name in ("chart_of_account", "journal_voucher", "journal_voucher_line",
                 "ap_invoice", "bank_account", "business_partner",
                 "wms_inventory_lot"):
        assert REGISTRY[name].apply_scope is scope_permission_only


def test_every_planning_table_picks_one_run():
    """The correctness guard, asserted rather than assumed.

    mrp_mps_lines, mrp_forecast_lines and mrp_purchase_lines each hold one set of
    rows per run, and they are not increments of each other: two released MPS
    runs carry 96 and 92 lines for the same materials. A query with no run filter
    sums them — a near-exact doubling, and nothing about the number looks wrong.
    It is a scope rather than a prompt rule precisely so a planner cannot omit it.
    """
    from app.core.ontology import (scope_forecast_current, scope_mps_in_force,
                                   scope_purchase_latest_run)

    assert REGISTRY["mrp_mps_line"].apply_scope is scope_mps_in_force
    assert REGISTRY["mrp_forecast_line"].apply_scope is scope_forecast_current
    assert REGISTRY["mrp_purchase_suggestion"].apply_scope is scope_purchase_latest_run


def test_money_ends_up_somewhere_countable():
    """Invoice and PA are where the chain turns into money, so both need a
    summable amount — a report about spend that cannot add anything up is not a
    report."""
    for name, metric in (("invoice", "total"), ("payment_application", "amount")):
        entity = REGISTRY[name]
        assert metric in entity.metrics, f"{name} has no {metric} metric"
        assert entity.metrics[metric].fn == "sum"


def test_the_two_wide_scopes_are_wired_to_their_own_callables():
    """Invoice and PA do not share PO's simple subquery filter: an invoice
    reaches people through five routes and a PA carries an ownership filter on
    top of visibility. Falling back to a simpler scope would silently widen
    both."""
    assert REGISTRY["invoice"].apply_scope.__name__ == "scope_invoice"
    assert REGISTRY["payment_application"].apply_scope.__name__ == "scope_pa"


def test_the_chain_is_navigable_in_both_directions():
    """PR → PO → GR forward, and back again. A one-way chain would leave the
    planner able to ask "what came from this PR" but not "where did this come
    from", which is the more common question."""
    pr, po, gr = (REGISTRY["purchase_request"], REGISTRY["purchase_order"],
                  REGISTRY["goods_receipt"])

    assert pr.links["orders"].target == "purchase_order"
    assert po.links["originating_pr"].target == "purchase_request"
    assert po.links["receipts"].target == "goods_receipt"
    assert gr.links["order"].target == "purchase_order"

    assert pr.links["orders"].cardinality == "one_to_many"
    assert po.links["originating_pr"].cardinality == "many_to_one"


def test_every_scope_callable_is_wired():
    """An entity with no row filter would return everything to everyone."""
    for entity in REGISTRY.values():
        assert callable(entity.apply_scope), entity.name
        assert entity.perm_key, entity.name


# ── coded values ──────────────────────────────────────────────────────────────


def test_a_coded_field_carries_what_its_codes_mean():
    """The assistant told someone purchase requests have no type classification.

    They do — `type` is an integer column with six defined values — but the
    ontology described it as a bare number, so nothing downstream could turn a
    question about services into type=4 or read a stored 4 back as "Service".
    An unlabelled code is unanswerable in both directions.
    """
    field = REGISTRY["purchase_request"].fields["type"]
    labels = dict(field.value_labels)
    assert labels["4"] == "Service"
    assert set(labels) == {"1", "2", "3", "4", "5", "6"}


def test_pr_and_po_agree_on_what_a_code_means():
    """Same codes, same column, copied into both entities.

    The front end already keeps two copies of this table and they have drifted
    apart — PrEditPage says "Raw Mat./Pack.", GrCreatePage says "Raw Materials /
    Packaging". Two more copies that disagree would let the assistant answer the
    same question differently depending on which entity it queried.
    """
    assert (dict(REGISTRY["purchase_request"].fields["type"].value_labels)
            == dict(REGISTRY["purchase_order"].fields["type"].value_labels))


# ── tables this service reads but does not own ────────────────────────────────


async def test_ontology_matches_the_database():
    """Every `table` entity must match the real table it claims to map.

    This is the whole safety net for reaching finance-api's and mrp-api's tables
    without copying their models. The field list in the YAML *is* the mapping, so
    nothing else would notice if the owning service renamed a column — the query
    would simply fail at runtime, for whoever happened to ask first.

    Checked against the real database, never the test one: the test database is
    built by create_all from these same declarations, so comparing to it would be
    comparing the ontology to itself and passing every time.

    Two things are checked, and the second is the one that has actually caught a
    bug. Types must be compatible; and the entity's time axis must really be
    populated. purchase_order shipped with `placed_at` as its axis, NULL on 97%
    of rows, and every "last N months" answer quietly omitted them.
    """
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings
    from app.core.ontology import _TABLE_MODELS

    external = {e.name: e for e in REGISTRY.values()
                if e.model.__tablename__ in _TABLE_MODELS}
    assert external, "no table-backed entities — has the loader changed?"

    # ontology kind -> information_schema data_type values that can carry it
    ok_types = {
        "text": {"character varying", "text", "character", "uuid"},
        "enum": {"character varying", "text", "character"},
        "money": {"numeric", "double precision", "real"},
        "int": {"integer", "bigint", "smallint"},
        "bool": {"boolean"},
        "date": {"date"},
        "datetime": {"timestamp with time zone", "timestamp without time zone"},
    }

    problems: list[str] = []
    engine = create_async_engine(str(settings.DATABASE_URL))
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns WHERE table_schema = 'public'"
            ))).all()
            actual: dict[str, dict[str, str]] = {}
            for table, column, dtype in rows:
                actual.setdefault(table, {})[column] = dtype

            axis_checks: list[tuple[str, str, str]] = []
            for name, entity in external.items():
                table = entity.model.__tablename__
                cols = actual.get(table)
                if cols is None:
                    problems.append(f"{name}: table {table!r} does not exist")
                    continue
                for fname, field in entity.fields.items():
                    if field.is_expression:
                        # Computed here, so there is no column to compare it to.
                        # Its correctness is covered by the behavioural tests
                        # instead — a wrong expression shows up as the wrong
                        # rows, not as a schema mismatch.
                        continue
                    dtype = cols.get(fname)
                    if dtype is None:
                        problems.append(
                            f"{name}.{fname}: no such column in {table}")
                    elif dtype not in ok_types.get(field.kind, set()):
                        problems.append(
                            f"{name}.{fname}: declared {field.kind!r} but the "
                            f"column is {dtype!r}")
                if entity.date_field in cols:
                    axis_checks.append((name, table, entity.date_field))

            # A row-insert stamp is almost never the date a question means, and
            # on anything migrated in it is the day of the migration. The ledger
            # lines shipped with created_at as their axis: 323,431 rows, all of
            # them 2026, so every question about an earlier year came back empty
            # and the reply said there were no postings. NULL-rate does not catch
            # this — the column is 100% populated and 100% wrong.
            for name, table, column in list(axis_checks):
                if column != "created_at":
                    continue
                spans = (await conn.execute(sa.text(
                    f"SELECT count(DISTINCT date_trunc('year', {column})) "
                    f"FROM {table}"))).scalar_one()
                # A candidate only counts if it is populated. boms.effective_from
                # is a date column that is NULL on all 286 rows — offering it as
                # the alternative would be trading one useless axis for another.
                has_real_date = False
                for n, f in REGISTRY[name].fields.items():
                    if (f.kind not in ("date", "datetime") or n == "created_at"
                            or f.is_expression):
                        continue
                    filled = (await conn.execute(sa.text(
                        f"SELECT count({n}) FROM {table}"))).scalar_one()
                    if filled:
                        has_real_date = True
                        break
                if spans <= 1 and has_real_date:
                    problems.append(
                        f"{name}.{column}: every row falls in one year, so this "
                        f"is a row-insert stamp rather than a date the data is "
                        f"about — period questions will return nothing for every "
                        f"other year. Point date_field at a real date, reaching "
                        f"through a link if the date lives on the parent.")

            for name, table, column in axis_checks:
                total, missing = (await conn.execute(sa.text(
                    f"SELECT count(*), count(*) FILTER (WHERE {column} IS NULL) "
                    f"FROM {table}"))).one()
                if not total:
                    # An empty table says nothing about coverage. Worth knowing
                    # for another reason — see test_no_entity_maps_an_empty_table.
                    continue
                share = missing / total
                if share > 0.02 and not REGISTRY[name].nullable_axis_ok:
                    problems.append(
                        f"{name}.{column}: the time axis is NULL on {missing} of "
                        f"{total} rows ({share:.0%}), so every period question "
                        f"silently drops them. Either pick a column that is "
                        f"always set, or set date_field_nullable_ok and record "
                        f"the coverage next to it.")
    finally:
        await engine.dispose()

    assert not problems, "ontology disagrees with the database:\n  " + \
        "\n  ".join(problems)


async def test_no_entity_maps_an_empty_table():
    """An entity over a table with no rows answers every question with "none".

    That reads as "this never happened" when the truth is "this is not in use" —
    ar_invoices, bank_transactions, fiscal_periods and exchange_rates are all
    empty today, and none of them are in the ontology for exactly this reason.
    If one gains rows later it should be added deliberately, not by an entity
    that has been quietly answering "nothing" the whole time.
    """
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings
    from app.core.ontology import _TABLE_MODELS

    external = [e for e in REGISTRY.values()
                if e.model.__tablename__ in _TABLE_MODELS]
    empty: list[str] = []
    engine = create_async_engine(str(settings.DATABASE_URL))
    try:
        async with engine.connect() as conn:
            for entity in external:
                table = entity.model.__tablename__
                n = (await conn.execute(
                    sa.text(f"SELECT count(*) FROM {table}"))).scalar_one()
                if not n:
                    empty.append(f"{entity.name} ({table})")
    finally:
        await engine.dispose()

    assert not empty, (
        "these entities map empty tables and would answer 'none' to everything: "
        + ", ".join(empty))


async def test_no_entity_offers_a_column_that_is_always_null():
    """A field that is NULL on every row is a filter that matches nothing.

    Same failure as an entity over an empty table: the reply says "none found"
    where the truth is "this was never filled in". boms.effective_from is the
    live example — a date column, NULL on all 286 rows — and it is kept out of
    the ontology for this reason rather than left in as harmless.
    """
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings
    from app.core.ontology import _TABLE_MODELS

    external = [e for e in REGISTRY.values()
                if e.model.__tablename__ in _TABLE_MODELS]
    empty: list[str] = []
    engine = create_async_engine(str(settings.DATABASE_URL))
    try:
        async with engine.connect() as conn:
            for entity in external:
                table = entity.model.__tablename__
                total = (await conn.execute(
                    sa.text(f"SELECT count(*) FROM {table}"))).scalar_one()
                if not total:
                    continue
                for fname, field in entity.fields.items():
                    if field.is_expression:
                        continue
                    filled = (await conn.execute(sa.text(
                        f"SELECT count({fname}) FROM {table}"))).scalar_one()
                    if not filled:
                        empty.append(f"{entity.name}.{fname}")
    finally:
        await engine.dispose()

    assert not empty, (
        "these fields are NULL on every row, so any filter on them matches "
        "nothing: " + ", ".join(empty))


def test_bom_entities_default_to_the_default_recipe():
    """A product has several BOMs and only one is the default.

    CF0063 has six packaging BOMs, versions 1.0 to 1.5, five of them approved —
    and they are not variants of one quantity: v1.2 builds in batches of 1000 and
    v1.4 in batches of 660. Adding their lines produces a number with no meaning.
    "Which products use CS0059" is 2 against the default recipes and 4 across all
    of them; the second is not a more complete answer, it is a wrong one.

    Asserted here because the marker lives on the NC mirror rather than on
    `boms`, so this is reached through nc_source_pk and would be easy to lose in
    a later refactor of either side.
    """
    assert REGISTRY["bom"].default_filter == ("is_default", True)
    assert REGISTRY["bom_line"].default_filter == ("bom.is_default", True)
    # And it must be a default, not a scope: asking about versions has to switch
    # it off, or "how many versions does CS0026 have" answers 1 against 7.
    assert "version" in REGISTRY["bom"].default_filter_stand_down
    assert "bom.version" in REGISTRY["bom_line"].default_filter_stand_down


def test_no_metric_sums_a_per_batch_quantity():
    """qty_per is measured against each recipe's own batch size and in each
    line's own unit. A sum over it adds kilograms to pieces at three different
    scales — a number that looks like an answer and is not one."""
    assert not any(m.field == "qty_per" and m.fn == "sum"
                   for m in REGISTRY["bom_line"].metrics.values())


def test_a_default_filter_must_explain_itself():
    """It changes what a query means, so the reply has to be able to say so."""
    for entity in REGISTRY.values():
        if entity.default_filter:
            assert entity.default_filter_note, (
                f"{entity.name} narrows queries silently")
            assert entity.default_filter_stand_down, (
                f"{entity.name} has no way to ask about the dimension it "
                f"filters on, which makes it a scope wearing a default's name")
