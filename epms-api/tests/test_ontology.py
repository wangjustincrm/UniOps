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
    date_field: placed_at
    fields:
      number: {kind: text, label: "PO number"}
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
    assert entities["purchase_order"].date_field == "placed_at"


@pytest.mark.parametrize("broken,expect", [
    # The one that matters most: a column the model does not have would surface
    # as a confusing 422, months later, for whoever happened to ask first.
    (GOOD.replace('number: {kind: text, label: "PO number"}',
                  'nope: {kind: text, label: "Not a column"}'),
     "no such column"),
    (GOOD.replace("model: PurchaseOrder", "model: NotAModel"), "unknown model"),
    (GOOD.replace("scope: po", "scope: nonexistent"), "unknown scope"),
    (GOOD.replace("date_field: placed_at", "date_field: created_at"),
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
    assert set(REGISTRY) == {"purchase_request", "purchase_order", "goods_receipt",
                             "invoice", "payment_application"}


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
