import pytest
from sqlalchemy import inspect
from app.db.base import Base


def test_provenance_columns_exist():
    po = Base.metadata.tables["purchase_orders"]
    assert "source" in po.c and "nc_source_pk" in po.c
    gr = Base.metadata.tables["goods_receipts"]
    assert "source" in gr.c and "nc_source_pk" in gr.c
    assert "nc_source_pk" in Base.metadata.tables["po_line_items"].c
    assert "nc_source_pk" in Base.metadata.tables["gr_line_items"].c
    assert "nc_purchase_sync_runs" in Base.metadata.tables
