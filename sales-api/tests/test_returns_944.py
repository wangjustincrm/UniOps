"""A customer's return received at Radiant: a 944 keyed "RA CRM<Shopify no.>" (user 2026-11-13)."""
from app.services.returns_944 import ra_number
from tests.test_orders import OP, env, shopify_order  # noqa: F401 — env is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import p7, store  # noqa: F401 — p7 is a fixture


def ra_944(key: str, item="CF0092", qty=6) -> bytes:
    return (f"ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*260928*1200*U*00401*000012999*0*P*>~"
            f"GS*RE*9055651212*CANADAROYALMILK*20260928*1200*12999*X*006030~ST*944*12999~W17*F*20260928*{key}*{key}~"
            f"N1*SF*CANADA ROYAL MILK ULC*94*CRM-CLN02~N1*WH*WAREHOUSE*93*CLN02~G62*BS*20260928~"
            f"LX*1~W07*{qty}*EA**VN*{item}~G69*RETURN~N9*LT*LOT-R1~W14*{qty}~SE*9*12999~GE*1*12999~IEA*1*000012999~").encode()


def test_the_ra_key_names_the_shopify_order():
    assert ra_number("RA CRM8961") == "8961" and ra_number("RA CRM 04750") == "4750" and ra_number("ra crm8102") == "8102"
    assert ra_number("00104") is None and ra_number("RA 12") is None


async def test_a_return_944_is_linked_to_its_order_no_task_and_shows_returned(p7):  # noqa: F811
    p7.edi.env.shop.orders = [shopify_order(1, 9301)]  # 2 cases of CF0092 = 12 EA
    await p7.edi.env.pull()
    o, _ = await p7.edi.env.order(1)
    await store(p7, "944_EDI", ra_944("RA CRM9301", qty=6), "944-Radiant-RACRM9301.edi")
    await p7.tr.process_944s()
    [d] = await p7.q("SELECT id, processing_status, error FROM sales_documents WHERE remote_name = '944-Radiant-RACRM9301.edi'")
    assert d["processing_status"] == "Skipped" and d["error"].startswith("a return (RA): returned to Radiant for order")
    assert await p7.q("SELECT entity_id FROM sales_document_links WHERE document_id = :d AND entity_type = 'order'", d=d["id"]) == [{"entity_id": o.id}]
    assert await p7.q("SELECT count(*) n FROM sales_receipts") == [{"n": 0}]
    assert await p7.q("SELECT count(*) n FROM tasks WHERE type = 'sales_944_no_transfer'") == [{"n": 0}]
    [ln] = await p7.q("SELECT returned_ea FROM sales_order_lines WHERE order_id = :o", o=o.id)
    assert ln["returned_ea"] == 6
    await p7.tr.process_944s()  # nothing pending: nothing doubles
    [ln] = await p7.q("SELECT returned_ea FROM sales_order_lines WHERE order_id = :o", o=o.id)
    assert ln["returned_ea"] == 6


async def test_a_return_944_of_no_order_is_kept_with_a_task(p7):  # noqa: F811
    await store(p7, "944_EDI", ra_944("RA CRM1", qty=2), "944-Radiant-RACRM1.edi")
    await p7.tr.process_944s()
    [t] = await p7.q("SELECT title FROM tasks WHERE type = 'sales_944_no_transfer'")
    assert "Return 944 matches no order: RA CRM1" in t["title"]
