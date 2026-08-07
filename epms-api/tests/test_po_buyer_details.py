"""Buyer-supplied detail on NC-imported POs (columns, endpoint, authz).

The columns are human-owned: the NC mirror never sources or writes them
(see app/services/nc_purchase_sync/writer.py). This file covers the schema
shape; tests/test_nc_purchase_writer.py covers the re-sync protection.
"""
from app.db.base import Base


def test_buyer_detail_columns_exist():
    po = Base.metadata.tables["purchase_orders"]
    assert "buyer_notes" in po.c
    assert "incoterms" in po.c
    assert "buyer_edited_at" in po.c
    assert "sample" in Base.metadata.tables["po_line_items"].c
