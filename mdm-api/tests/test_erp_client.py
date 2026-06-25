from datetime import datetime
import pytest
import httpx
from app.services.erp_client import ErpClient, ErpError


@pytest.mark.anyio
async def test_fetch_materials_success_parses_data():
    payload = {
        "code": 200,
        "message": "ok",
        "data": [{"part_NO": "X1", "description": "test", "unit_MEAS": "PCS", "rowversion": "2026-01-01 00:00:00"}],
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport, base_url="http://stub") as http:
        client = ErpClient(http=http)
        records = await client.fetch_materials(datetime(1900, 1, 1))
    assert len(records) == 1
    assert records[0]["part_NO"] == "X1"


@pytest.mark.anyio
async def test_fetch_materials_error_code_raises():
    payload = {"code": 40004, "message": "query failed", "data": []}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport, base_url="http://stub") as http:
        client = ErpClient(http=http)
        with pytest.raises(ErpError) as exc:
            await client.fetch_materials(datetime(1900, 1, 1))
        assert exc.value.code == 40004
        assert "query failed" in str(exc.value)


@pytest.mark.anyio
async def test_fetch_materials_network_error_raises():
    def boom(req):
        raise httpx.ConnectError("dns failure")
    transport = httpx.MockTransport(boom)
    async with httpx.AsyncClient(transport=transport, base_url="http://stub") as http:
        client = ErpClient(http=http)
        with pytest.raises(ErpError):
            await client.fetch_materials(datetime(1900, 1, 1))
