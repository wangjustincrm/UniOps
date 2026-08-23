"""app.services.mdm_client — material name resolution (material-names gap fix).

`resolve_material_names` is the read-endpoint-safe wrapper every read
endpoint that shows a bare `material_code` (forecast grid/export,
consignment stock list, net-requirement) goes through to fill in `name`.
These tests exercise it directly, at the unit level, separate from any one
endpoint: it must never raise, regardless of what the underlying
`fetch_materials` call does.
"""
import httpx
import pytest

from app.services import mdm_client


@pytest.mark.anyio
async def test_resolve_material_names_returns_the_map_on_success(monkeypatch):
    monkeypatch.setattr(
        mdm_client, "fetch_materials",
        lambda token: {"S0093": "Whole Milk Powder 25kg", "S0060": None},
    )
    names = await mdm_client.resolve_material_names("some-token")
    assert names == {"S0093": "Whole Milk Powder 25kg", "S0060": None}


@pytest.mark.anyio
async def test_resolve_material_names_degrades_to_empty_dict_when_mdm_unreachable(monkeypatch):
    """mdm-api being down (or erroring, or timing out) must never raise out
    of resolve_material_names — every caller's `.get(code)` must simply come
    back None, the same "name always null" behavior these endpoints had
    before this lookup existed."""
    def _boom(token):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(mdm_client, "fetch_materials", _boom)
    names = await mdm_client.resolve_material_names("some-token")
    assert names == {}


@pytest.mark.anyio
async def test_resolve_material_names_degrades_on_non_2xx_status(monkeypatch):
    """A reachable-but-erroring mdm-api (401/500/...) must degrade exactly
    like an unreachable one — fetch_materials raises httpx.HTTPStatusError
    via resp.raise_for_status(), which must also be swallowed."""
    def _boom(token):
        request = httpx.Request("GET", "http://mdm/mdm/v1/materials")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(mdm_client, "fetch_materials", _boom)
    names = await mdm_client.resolve_material_names("some-token")
    assert names == {}


def test_fetch_valid_material_codes_derives_from_fetch_materials(monkeypatch):
    """forecast_io.fetch_valid_material_codes (Task 2's import-validation
    path) must keep its strict "raise on failure" contract — it now derives
    from mdm_client.fetch_materials rather than duplicating its own
    httpx.Client/paging loop, so this pins that the codes it returns really
    are just fetch_materials' keys. Patches `forecast_io.fetch_materials`
    (the name as imported there via `from app.services.mdm_client import
    fetch_materials`), not `mdm_client.fetch_materials` — import binds a
    separate reference, so patching the origin module wouldn't reach the
    call site, same reasoning as consignment.py's `lookup_lot` idiom."""
    from app.services import forecast_io

    monkeypatch.setattr(
        forecast_io, "fetch_materials",
        lambda token: {"S0093": "Whole Milk Powder", "CF0086": "Cream Filling"},
    )
    codes = forecast_io.fetch_valid_material_codes("some-token")
    assert codes == {"S0093", "CF0086"}


def test_fetch_valid_material_codes_still_raises_on_failure(monkeypatch):
    """Unlike resolve_material_names, the import path must still fail loudly
    — app/api/v1/forecast.py's import_forecast depends on this to turn an
    mdm-api outage into a clear 503 rather than silently importing against
    an empty valid-codes set."""
    from app.services import forecast_io

    def _boom(token):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(forecast_io, "fetch_materials", _boom)
    with pytest.raises(httpx.ConnectError):
        forecast_io.fetch_valid_material_codes("some-token")
