"""A file's name is data (a customer's PO number) and may hold any character (user 2026-11-24): one helper writes it."""
import re
from urllib.parse import unquote

import pytest

from app.platform.files import content_disposition
from tests.test_order_payment import OPERATOR, _exec, _orders, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture


@pytest.mark.parametrize(("name", "header"), [
    ("PO-123.pdf", 'attachment; filename="PO-123.pdf"'),                       # plain: exactly as before
    ("config-partners.csv", 'attachment; filename="config-partners.csv"'),
    ("PO-été.pdf", "attachment; filename=\"PO-ete.pdf\"; filename*=UTF-8''PO-%C3%A9t%C3%A9.pdf"),
    ("PO-订单.pdf", "attachment; filename=\"PO-_.pdf\"; filename*=UTF-8''PO-%E8%AE%A2%E5%8D%95.pdf"),
    ('a"b%41.pdf', "attachment; filename=\"a_b_41.pdf\"; filename*=UTF-8''a%22b%2541.pdf"),
    ("x\r\nSet-Cookie: y=1.pdf", 'attachment; filename="x_Set-Cookie: y=1.pdf"'),  # a line break never ends the header
    ("../../etc/passwd", 'attachment; filename=".._.._etc_passwd"'),
    ("", 'attachment; filename="file"')])
def test_the_header_for_a_name(name, header):
    got = content_disposition(name)
    assert got == header
    got.encode("latin-1")
    assert not re.search(r"[\r\n]", got)


def test_inline_and_every_name_is_read_back_whole():
    assert content_disposition("a.pdf", inline=True) == 'inline; filename="a.pdf"'
    for name in ("PO-Ünïcode 订单 №5.pdf", "100% \"sure\".zip", "naïve.csv"):
        star = re.search(r"filename\*=UTF-8''(.+)$", content_disposition(name))[1]
        assert unquote(star) == name


async def test_the_documents_of_an_order_whose_po_number_is_not_latin_1_download(api):  # noqa: F811
    o = (await _orders(api, 9901))["9901"]
    await _exec(api, f"UPDATE sales_orders SET source_order_number = 'PO-订单-é', source_reference = 'CRM-订单' WHERE id = {o['id']}")
    for path, start in (("po.pdf", b"%PDF"), ("packet.zip", b"PK")):
        r = await api.get(f"/api/v1/orders/{o['id']}/{path}", headers=OPERATOR)
        assert r.status_code == 200 and r.content.startswith(start), (path, r.text[:200])
        name = unquote(re.search(r"filename\*=UTF-8''(.+)$", r.headers["content-disposition"])[1])
        assert "订单" in name, name


async def test_a_browser_may_read_the_name(api):  # noqa: F811
    from app.core.config import settings
    r = await api.get("/api/v1/orders/export.csv", headers={**OPERATOR, "Origin": settings.ALLOWED_ORIGINS[0]})
    assert r.status_code == 200 and r.headers["access-control-expose-headers"] == "Content-Disposition"
    assert r.headers["content-disposition"] == 'attachment; filename="orders.csv"'


def test_no_router_writes_the_header_itself():
    import pathlib
    app = pathlib.Path(__file__).parents[1] / "app"
    assert [str(p.relative_to(app)) for p in app.rglob("*.py") if re.search(r"[\"']Content-Disposition[\"']\s*:", p.read_text())] == ["platform/files.py"]
