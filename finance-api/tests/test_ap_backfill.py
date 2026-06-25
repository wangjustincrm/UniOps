"""Backfill mapping (EPMS invoices + OA expense_invoices → ap_invoices)."""
import uuid
from datetime import date, datetime, timezone

import pytest


def test_epms_args_maps_status_and_fields():
    from scripts.backfill_ap_invoices import _epms_args

    class _Inv:
        id = uuid.uuid4(); internal_ref = "INV-2026-0001"
        vendor_id = uuid.uuid4(); vendor_name = "ULINE"; vendor_invoice_number = "V-1"
        amount = 1000; tax_amount = 130; total_amount = 1130; currency = "CAD"
        invoice_date = date(2026, 6, 17); due_date = date(2026, 7, 17)
        status = "matched"; po_id = uuid.uuid4(); po_number = "PO-1"

    class _Tax:
        line_no = 1; tax_code = "HST_ON"; taxable_amount = 1000; tax_amount = 130; recoverable = True

    source, sid, payload, tax_lines = _epms_args(_Inv(), [_Tax()])
    assert source == "epms" and sid == _Inv.id
    assert payload["status"] == "posted" and payload["source_status"] == "matched"
    assert payload["amount"] == "1000.00" and payload["total_amount"] == "1130.00"
    assert payload["invoice_date"] == date(2026, 6, 17)
    assert tax_lines[0]["tax_code"] == "HST_ON" and tax_lines[0]["taxable_base"] == "1000"


def test_epms_args_paid_and_partial():
    from scripts.backfill_ap_invoices import _epms_args
    class _Inv:
        id = uuid.uuid4(); internal_ref = "r"; vendor_id = None; vendor_name = "v"
        vendor_invoice_number = "x"; amount = 1; tax_amount = 0; total_amount = 1
        currency = "CAD"; invoice_date = date(2026, 1, 1); due_date = None
        po_id = None; po_number = None; status = "paid"
    assert _epms_args(_Inv(), [])[2]["status"] == "paid"
    _Inv.status = "partially_paid"
    assert _epms_args(_Inv(), [])[2]["status"] == "partially_paid"
    _Inv.status = "unmatched"
    assert _epms_args(_Inv(), [])[2]["status"] == "draft"


def test_oa_args_maps_status_and_date_fallback():
    from scripts.backfill_ap_invoices import _oa_args

    class _Row:
        id = uuid.uuid4(); invoice_number = "INV-OA-1"
        vendor_id = None; vendor_name = "ULINE"
        invoice_date = None; due_date = None; currency = "CAD"
        subtotal = 1000; tax_amount = 130; total_amount = 1130
        status = "used"; created_at = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

    source, sid, payload, tax_lines = _oa_args(_Row())
    assert source == "oa" and sid == _Row.id
    assert payload["status"] == "posted"
    assert payload["invoice_date"] == date(2026, 6, 20)
    assert payload["po_id"] is None and tax_lines == []
