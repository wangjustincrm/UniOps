"""Lookup indexes the reads need (sales21_lookup_indexes, 2026-11-10), in one
place: a column that a page or a job finds rows by, and a foreign key whose
child rows are read by it. The table's own constraints stay with its model."""
from sqlalchemy import Index

from app.models.documents import DocumentLink
from app.models.invoices import Invoice, InvoiceLine
from app.models.orders import Order
from app.models.payments import OrderRefundLine
from app.models.shipments import ShipmentRow
from app.models.transfers import Receipt, Transfer

INDEXES = (
    # An order's documents (its Documents tab, Documents → Orders): the unique key leads with document_id.
    Index("ix_sales_document_links_entity", DocumentLink.entity_type, DocumentLink.entity_id),
    # A 945 / 997 / shadow comparison finds its order by the PO.
    Index("ix_sales_orders_source_order_number", Order.source_order_number),
    # The working lists (every Orders view but All): only the orders not historical, few among many
    # once old orders are imported (2026-11-11: Open went from 23 s to 0.1 s over 8,734 orders).
    Index("ix_sales_orders_open", Order.order_number, postgresql_where=Order.historical.is_(False)),
    # The 940 queue and the oldest-first views.
    Index("ix_sales_orders_first_received_at", Order.first_received_at),
    # Documents: the transfer and invoice of each document on the page.
    Index("ix_sales_transfers_document_id", Transfer.document_id),
    Index("ix_sales_receipts_transfer_id", Receipt.transfer_id),
    Index("ix_sales_invoices_document_id", Invoice.document_id),
    # An order line's shipped, invoiced and refunded rows.
    Index("ix_sales_shipment_rows_order_line_id", ShipmentRow.order_line_id),
    Index("ix_sales_invoice_lines_order_line_id", InvoiceLine.order_line_id),
    Index("ix_sales_order_refund_lines_order_line_id", OrderRefundLine.order_line_id),
)
