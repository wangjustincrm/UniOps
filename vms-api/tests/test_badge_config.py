"""Badge structured-config: defaults, deep-merge, schema, endpoints."""
import pytest

from app.services.badge_config import (
    DEFAULT_BADGE_CONFIG,
    META_FIELD_KEYS,
    deep_merge,
)


def test_default_config_shape():
    cfg = DEFAULT_BADGE_CONFIG
    assert cfg["band"]["title"] == "VISITOR"
    assert set(cfg["band"]["areas"].keys()) == {
        "office", "warehouse", "production_non_gmp",
        "production_gmp", "laboratory", "all",
    }
    assert [m["key"] for m in cfg["meta_fields"]][:3] == [
        "visit_date", "host", "valid_until",
    ]
    assert cfg["qr"]["show"] is True
    assert cfg["footer"]["lines"][0].startswith("Must be accompanied")
    assert cfg["style"]["name_size_pt"] == 22


def test_meta_field_keys_whitelist():
    assert META_FIELD_KEYS == {
        "visit_date", "host", "valid_until",
        "vehicle_plate", "accompanying_count", "visit_purpose",
    }


def test_deep_merge_overrides_nested_without_dropping_siblings():
    merged = deep_merge(DEFAULT_BADGE_CONFIG, {"band": {"title": "GUEST"}})
    assert merged["band"]["title"] == "GUEST"
    assert merged["band"]["show_zone"] is True
    assert "areas" in merged["band"]
    assert merged["qr"]["label"] == "Scan to check out"


def test_deep_merge_replaces_lists_wholesale():
    merged = deep_merge(DEFAULT_BADGE_CONFIG, {"footer": {"lines": ["One line"]}})
    assert merged["footer"]["lines"] == ["One line"]


def test_deep_merge_does_not_mutate_default():
    deep_merge(DEFAULT_BADGE_CONFIG, {"band": {"title": "X"}})
    assert DEFAULT_BADGE_CONFIG["band"]["title"] == "VISITOR"


from app.schemas.badge_config import BadgeConfig
from app.services.badge_config import deep_merge as _dm


def test_badgeconfig_accepts_default():
    cfg = BadgeConfig.model_validate(DEFAULT_BADGE_CONFIG)
    assert cfg.band.title == "VISITOR"
    assert cfg.style.name_align == "left"


def test_badgeconfig_rejects_bad_hex_color():
    bad = _dm(DEFAULT_BADGE_CONFIG, {"style": {"text_color": "blue"}})
    with pytest.raises(ValueError):
        BadgeConfig.model_validate(bad)


def test_badgeconfig_rejects_unknown_meta_key():
    bad = _dm(DEFAULT_BADGE_CONFIG, {})
    bad["meta_fields"] = [{"key": "ssn", "label": "SSN", "visible": True}]
    with pytest.raises(ValueError):
        BadgeConfig.model_validate(bad)


def test_badgeconfig_rejects_bad_align():
    bad = _dm(DEFAULT_BADGE_CONFIG, {"style": {"name_align": "justify"}})
    with pytest.raises(ValueError):
        BadgeConfig.model_validate(bad)


@pytest.mark.asyncio
async def test_badge_config_get_returns_merged_defaults(admin):
    _, client = admin
    resp = await client.get("/api/v1/admin/badge-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["band"]["title"] == "VISITOR"
    assert len(body["meta_fields"]) == 6
    assert body["style"]["name_align"] == "left"


@pytest.mark.asyncio
async def test_badge_config_put_persists_and_merges(admin):
    _, client = admin
    got = (await client.get("/api/v1/admin/badge-config")).json()
    got["band"]["title"] = "GUEST PASS"
    got["footer"]["lines"] = ["Return at front desk"]
    resp = await client.put("/api/v1/admin/badge-config", json=got)
    assert resp.status_code == 200, resp.text
    assert resp.json()["band"]["title"] == "GUEST PASS"

    again = (await client.get("/api/v1/admin/badge-config")).json()
    assert again["band"]["title"] == "GUEST PASS"
    assert again["footer"]["lines"] == ["Return at front desk"]


@pytest.mark.asyncio
async def test_badge_config_put_rejects_bad_color(admin):
    _, client = admin
    got = (await client.get("/api/v1/admin/badge-config")).json()
    got["style"]["text_color"] = "notacolor"
    resp = await client.put("/api/v1/admin/badge-config", json=got)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_badge_config_get_requires_admin(requester):
    _, client = requester
    resp = await client.get("/api/v1/admin/badge-config")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_badge_config_put_requires_admin(requester):
    _, client = requester
    resp = await client.put("/api/v1/admin/badge-config", json={})
    assert resp.status_code == 403
