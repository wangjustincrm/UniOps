"""Closed value sets of orders (design §3.3, PRD 9.1, FR-OB-08)."""
SOURCE_TYPES = ("SHOPIFY", "EDI_850", "MANUAL")
LINE_STATUSES = ("Active", "Shipped", "Short Shipped", "Cancelled", "Error")
HOLD_REASONS = ("Payment Required",)
# FR-OB-03d / FR-OB-12: the Shopify financial statuses that satisfy payment.
PAYMENT_SATISFIED = ("paid", "partially_refunded")
