from app.scripts.read_shopify_history import number, older_than


def test_only_the_orders_below_the_cut_off_are_history():
    page = {"orders": [{"id": 1, "order_number": 9540}, {"id": 2, "order_number": 9541}, {"id": 3, "name": "#1200"}, {"id": 4}]}
    assert [o["id"] for o in older_than(page, 9541)["orders"]] == [1, 3]
    assert len(older_than(page, None)["orders"]) == 4


def test_the_number_comes_from_order_number_or_the_name():
    assert number({"order_number": 9600}) == 9600
    assert number({"name": "#8911"}) == 8911
    assert number({"name": "draft"}) is None
