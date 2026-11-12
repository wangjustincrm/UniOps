"""Closed value sets of the configuration tables (design §3.1, FR-OB-08).

One source for the database CHECK constraints (app/models/config.py) and the
API's DTOs (app/schemas/), so the two can never disagree; a test pins the
DTOs' Literal types to these tuples.
"""
CHANNELS = ("SHOPIFY", "TRUECOMMERCE", "MANUAL")
SETTLEMENT_MODES = ("PREPAID", "INVOICE")
SENDING_MODES = ("AUTOMATIC", "MANUAL")
INVOICE_METHODS = ("810_JSON", "EMAIL", "NONE")
WAREHOUSE_ROLES = ("SHIPPER", "WAREHOUSE", "TRANSFER_SOURCE")
CONNECTION_KINDS = ("SFTP", "SHOPIFY")
AFTER_READ = ("LEAVE", "MOVE", "DELETE")
NOTIFICATION_CHANNELS = ("TASK", "EMAIL", "BOTH")
