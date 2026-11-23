"""Remove nothing (user 2026-11-23): error and log text is stored whole; only display shortens it. Every `[:N]` cut in
app/ is listed with why it may stay (a varchar column, user input, a display line, a file name, a sample); a new cut fails."""
import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
CUT = re.compile(r"\[:\s*(\d{3,4})\s*\]")

ALLOWED = {  # (file, line text containing the cut) → why
    "api/v1/invoices.py": "a search box's text (input)",
    "api/v1/inventory.py": "a search box's text (input)",
    "api/v1/lots.py": "a search box's text (input)",
    "services/shadow.py": "sales_shadow_matches.reference_number is varchar(100): a key, not error text",
    "services/problems.py": "tasks.title is varchar(255) in epms-api's shared table; the whole text is in the description",
    "services/returns_944.py": "a task title (tasks.title varchar(255), epms-api's table)",
    "services/transfers.py": "a task title (tasks.title varchar(255), epms-api's table)",
    "services/shipments.py": "a task title (tasks.title varchar(255), epms-api's table)",
    "services/edi_intake.py": "a task title (tasks.title varchar(255), epms-api's table)",
    "services/intake.py": "a task title (tasks.title varchar(255), epms-api's table)",
    "services/record_files.py": "a person's file name / note (input, cleaned)",
    "services/notifications.py": "an email's Subject line (a header, not stored text)",
    "services/gateway_base.py": "a document's file name",
    "services/stock_counts.py": "sales_stock_count_lines.description is varchar(255) (Radiant's own text)",
    "domain/shopify_finance.py": "sales_order_payments.authorization / payment_id are varchar(255) / (100)",
    "domain/order_status.py": "the Detail line on screen (display)",
    "domain/radiant_stock.py": "a sample for the CSV dialect sniffer",
    "platform/guard.py": "a value quoted inside an error message (display)",
    "platform/crud_router.py": "a search box's text (input)",
    "services/invoicing.py": "a document's file name",
    "scripts/import_zoho_history.py": "a one-time CLI (file names, a comparison summary)",
}


def test_every_cut_in_app_is_listed_with_why():
    found = {f.relative_to(APP).as_posix() for f in APP.rglob("*.py") if CUT.search(f.read_text(encoding="utf-8"))}
    assert found - set(ALLOWED) == set(), "store the whole text (only display shortens it), or list it with why"
    assert set(ALLOWED) - found == set(), "listed but gone: take it out"
