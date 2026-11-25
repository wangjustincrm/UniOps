"""Every record that names a versioned one is a part of it or not, and why (V1, 2026-11-25; platform/parts.py): a new one is on this
list first. And no bulk SQL writes a part, outside the scripts known here: it would go round the rule."""
import ast
import re
from pathlib import Path

import app as package
import app.main  # noqa: F401 — every model
from app.models.orders import Order
from app.platform.entities import Audited, Versioned
from app.platform.parts import part_of

ROOT = Path(package.__path__[0])
PARTS = {("OrderLine", "order_id"): "Order", ("InvoiceLine", "invoice_id"): "Invoice", ("TransferLine", "transfer_id"): "Transfer",
         ("StockCountLine", "count_id"): "StockCount", ("ShipmentRow", "shipment_id"): "Shipment", ("ItemUnit", "item_code"): "Item"}
# Not a part: why a change of it is no change of the record it names.
OWN_RECORD = "a record of its own, with its own version"
NAMES = "names it: which one, from a list; that one does not change"
FOUND = "what came in or happened; kept as it came, and the record shows it beside its own"
NOT_PARTS = {
    ("Connection", "partner_code"): NAMES, ("DcRouting", "partner_code"): OWN_RECORD, ("DeliveryConfirmation", "order_id"): OWN_RECORD,
    ("Document", "connection_id"): FOUND, ("GatewaySettlement", "connection_id"): FOUND, ("GatewayTransaction", "connection_id"): FOUND,
    ("Invoice", "order_id"): OWN_RECORD, ("ItemPartnerCode", "item_code"): OWN_RECORD, ("ItemPartnerCode", "partner_code"): OWN_RECORD,
    ("LogEntry", "connection_id"): FOUND, ("Order", "connection_id"): NAMES, ("Order", "parent_order_id"): NAMES,
    ("OrderLine", "item_code"): NAMES, ("TransferLine", "item_code"): NAMES,
    ("OrderPayment", "order_id"): FOUND, ("OrderRefund", "order_id"): FOUND,
    ("OrderReturn", "order_id"): "recorded beside the order, once; record_return moves the order itself (a press once)",
    ("Receipt", "transfer_id"): FOUND, ("ShadowMatch", "order_id"): OWN_RECORD, ("Shipment", "order_id"): OWN_RECORD,
    ("Transfer", "from_warehouse_id"): NAMES, ("Transfer", "to_warehouse_id"): NAMES}
# Bulk SQL on a part, by hand, with the service stopped.
KNOWN_BULK = {("scripts/start_orders_from.py", "ShipmentRow"), ("scripts/start_orders_from.py", "OrderLine"), ("scripts/start_orders_from.py", "TransferLine")}

Base = next(c for c in Order.__mro__ if c.__name__ == "Base")
MODELS = {m.class_.__tablename__: m.class_ for m in Base.registry.mappers if "__tablename__" in m.class_.__dict__}


def test_every_record_that_names_a_versioned_one_says_whether_it_is_its_part():
    found = {}
    for cls in MODELS.values():
        for fk in cls.__table__.foreign_keys:
            root = MODELS.get(fk.column.table.name)
            if root is not None and issubclass(root, Versioned):
                found[(cls.__name__, fk.parent.name)] = root.__name__
    assert set(found) == set(PARTS) | set(NOT_PARTS) and not set(PARTS) & set(NOT_PARTS)
    declared = {(c.__name__, c.__part_of__.fk): c.__part_of__.root.__name__ for c in MODELS.values() if "__part_of__" in c.__dict__}
    assert declared == PARTS == {k: found[k] for k in PARTS}


def test_a_part_s_declaration_names_its_real_key_and_a_root_that_can_be_moved():
    for cls in (c for c in MODELS.values() if "__part_of__" in c.__dict__):
        p = cls.__part_of__
        [fk] = cls.__table__.c[p.fk].foreign_keys
        assert fk.column is p.root.__table__.c[p.key] and not cls.__table__.c[p.fk].nullable
        assert issubclass(p.root, Audited) and issubclass(p.root, Versioned) and part_of(cls()) is p


def bulk(source: str) -> set[str]:
    """The parts a file writes by bulk SQL: update(X) / delete(X) / insert(X), any helper named so, or the table's name in a text."""
    names = {c for c, _ in PARTS}
    tables = {cls.__tablename__: cls.__name__ for cls in MODELS.values() if cls.__name__ in names}
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            f = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if re.search("update|delete|insert", f):
                out |= {a.id for a in node.args if isinstance(a, ast.Name) and a.id in names}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out |= {tables[t] for t in re.findall(r"(?:UPDATE|DELETE\s+FROM|INSERT\s+INTO|TRUNCATE(?:\s+TABLE)?)\s+(\w+)", node.value, re.I) if t in tables}
    return out


def test_no_bulk_sql_writes_a_part():
    found = {(str(p.relative_to(ROOT)), name) for p in ROOT.rglob("*.py") for name in bulk(p.read_text())}
    assert found == KNOWN_BULK


def test_the_bulk_check_sees_what_it_must():
    for bad in ("s.execute(update(OrderLine).values(x=1))", "s.execute(sa.delete(ShipmentRow))", "await _delete(s, counts, ItemUnit, 1)",
                "pg_insert(TransferLine)", "s.execute(text('update sales_invoice_lines set qty = 1'))",
                "s.execute(sa.text('''DELETE\n FROM sales_stock_count_lines'''))"):
        assert len(bulk(bad)) == 1, bad
    assert bulk("select(OrderLine); update(Order); s.delete(line)") == set()


def test_no_command_moves_a_record_for_its_parts_by_hand():
    """touch() is left for a press that changes nothing yet: these, and no other."""
    found = {(p.relative_to(ROOT).as_posix(), m.group(1)) for p in (ROOT / "services").rglob("*.py")
             for m in re.finditer(r"\btouch\(\w+(?:\.\w+)*, (\w+)\)", p.read_text())}
    assert found == {("services/shipments.py", "order"), ("services/invoicing.py", "inv"), ("services/invoicing.py", "order")}
