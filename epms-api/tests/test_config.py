"""Company Config endpoint tests."""
import pytest

CONFIG_URL = "/api/v1/config"


# ── GET config ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_config_returns_defaults(admin_client):
    """First GET creates the singleton row with sensible defaults."""
    r = await admin_client.get(CONFIG_URL)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "EPMS"
    assert data["default_currency"] == "CAD"
    assert "CAD" in data["enabled_currencies"]
    assert data["mfa_enabled"] is True
    assert data["password_expiry_days"] == 90
    # Sub-configs should be present
    assert "escalation_threshold_cad" in data["workflow_config"]
    assert "pr" in data["workflow_defs"]
    assert len(data["workflow_defs"]["pr"]) == 3
    assert len(data["workflow_defs"]["po"]) == 2
    assert len(data["workflow_defs"]["pa"]) == 2
    assert data["module_taglines"] == {}


@pytest.mark.asyncio
async def test_get_config_unauthenticated(client):
    r = await client.get(CONFIG_URL)
    assert r.status_code == 403


# ── PATCH config ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_basic_info(admin_client):
    r = await admin_client.patch(CONFIG_URL, json={
        "name": "Acme Corp",
        "tagline": "Building tomorrow",
        "delivery_address": "123 Main St, Toronto, ON",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Acme Corp"
    assert data["tagline"] == "Building tomorrow"
    assert data["delivery_address"] == "123 Main St, Toronto, ON"


@pytest.mark.asyncio
async def test_update_currency_settings(admin_client):
    r = await admin_client.patch(CONFIG_URL, json={
        "default_currency": "USD",
        "enabled_currencies": ["CAD", "USD"],
    })
    assert r.status_code == 200
    data = r.json()
    assert data["default_currency"] == "USD"
    assert data["enabled_currencies"] == ["CAD", "USD"]


@pytest.mark.asyncio
async def test_update_security_settings(admin_client):
    r = await admin_client.patch(CONFIG_URL, json={
        "mfa_enabled": False,
        "password_expiry_days": 60,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["mfa_enabled"] is False
    assert data["password_expiry_days"] == 60


@pytest.mark.asyncio
async def test_update_workflow_config(admin_client):
    # over_budget_mode lives on BudgetAdminConfig — see test_update_budget_admin_config.
    r = await admin_client.patch(CONFIG_URL, json={
        "workflow_config": {
            "escalation_threshold_cad": 80000,
            "po_low_value_bypass_enabled": True,
            "po_low_value_bypass_cad": 3000,
            "approval_reminder_days": 1,
            "approval_auto_escalation_days": 3,
            "consolidate_gm_opm_approval": False,
        }
    })
    assert r.status_code == 200
    wf = r.json()["workflow_config"]
    assert wf["escalation_threshold_cad"] == 80000
    assert wf["po_low_value_bypass_enabled"] is True
    assert wf["consolidate_gm_opm_approval"] is False


@pytest.mark.asyncio
async def test_update_workflow_defs(admin_client):
    r = await admin_client.patch(CONFIG_URL, json={
        "workflow_defs": {
            "pr": [
                {"id": "pr-step-0", "label": "Dept Manager", "role": "dept_manager"},
                {"id": "pr-step-1", "label": "Finance Manager", "role": "finance_manager"},
            ],
            "po": [
                {"id": "po-step-0", "label": "Procurement Manager", "role": "procurement_manager"},
            ],
            "pa": [
                {"id": "pa-step-0", "label": "Finance BP", "role": "finance_bp"},
                {"id": "pa-step-1", "label": "Finance Manager", "role": "finance_manager"},
            ],
        }
    })
    assert r.status_code == 200
    defs = r.json()["workflow_defs"]
    assert len(defs["pr"]) == 2
    assert len(defs["po"]) == 1


@pytest.mark.asyncio
async def test_update_sla_configs(admin_client):
    r = await admin_client.patch(CONFIG_URL, json={
        "service_gr_sla": {"reminder_days": 2, "manager_escalation_days": 4,
                           "gm_opm_escalation_days": 6, "fm_alert_days": 8},
        "gr_notification_sla": {"reminder_days": 1, "manager_escalation_days": 2},
        "collection_config": {"collection_required": False, "reminder_days": 1,
                              "manager_escalation_days": 3, "fm_alert_days": 5},
    })
    assert r.status_code == 200
    data = r.json()
    assert data["service_gr_sla"]["reminder_days"] == 2
    assert data["collection_config"]["collection_required"] is False


@pytest.mark.asyncio
async def test_update_config_non_admin_forbidden(finance_client):
    r = await finance_client.patch(CONFIG_URL, json={"name": "Hacker"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_partial_update_preserves_other_fields(admin_client):
    """PATCH with one field should not wipe other config fields."""
    # Ensure config exists with known state
    await admin_client.patch(CONFIG_URL, json={"name": "PreserveTest Corp"})

    # Only update tagline
    r = await admin_client.patch(CONFIG_URL, json={"tagline": "New tagline"})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "PreserveTest Corp"   # still preserved
    assert data["tagline"] == "New tagline"


# ── Per-module taglines ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_module_taglines_round_trips(admin_client):
    r = await admin_client.patch(CONFIG_URL, json={
        "tagline": "Unified Operations Hub",
        "module_taglines": {"epms": "Procurement", "oa": "Office Automation", "vms": "Visitor Management"},
    })
    assert r.status_code == 200
    data = r.json()
    assert data["tagline"] == "Unified Operations Hub"
    assert data["module_taglines"]["epms"] == "Procurement"
    assert data["module_taglines"]["oa"] == "Office Automation"
    assert data["module_taglines"]["vms"] == "Visitor Management"


BRANDING_URL = "/api/v1/config/public/branding"


@pytest.mark.asyncio
async def test_branding_no_module_returns_portal_tagline(admin_client, client):
    await admin_client.patch(CONFIG_URL, json={
        "tagline": "Portal Hub",
        "module_taglines": {"epms": "Procurement"},
    })
    r = await client.get(BRANDING_URL)
    assert r.status_code == 200
    assert r.json()["tagline"] == "Portal Hub"


@pytest.mark.asyncio
async def test_branding_module_returns_mapped_tagline(admin_client, client):
    await admin_client.patch(CONFIG_URL, json={
        "tagline": "Portal Hub",
        "module_taglines": {"epms": "Procurement"},
    })
    r = await client.get(BRANDING_URL, params={"module": "epms"})
    assert r.status_code == 200
    body = r.json()
    assert body["tagline"] == "Procurement"
    assert body["module"] == "epms"


@pytest.mark.asyncio
async def test_branding_blank_module_falls_back_to_portal(admin_client, client):
    await admin_client.patch(CONFIG_URL, json={
        "tagline": "Portal Hub",
        "module_taglines": {"epms": "Procurement"},
    })
    # oa has no entry → falls back to Portal tagline
    r = await client.get(BRANDING_URL, params={"module": "oa"})
    assert r.status_code == 200
    assert r.json()["tagline"] == "Portal Hub"


# ── notification_settings shallow merge + shared-mailbox validation ───────────
# The same JSONB is PATCHed by two clients (Portal's notification form and EPMS
# Admin's Role Shared Mailboxes section), each sending only the keys it owns.
# crud.config.update therefore shallow-merges this one field instead of
# replacing it wholesale — see the comment there.

@pytest.fixture
async def _reset_shared_mailboxes(admin_client):
    """Leave role_shared_mailboxes empty so these tests don't leak into others."""
    yield
    await admin_client.patch(CONFIG_URL, json={
        "notification_settings": {"role_shared_mailboxes": {}},
    })


@pytest.mark.asyncio
async def test_partial_notification_settings_update_preserves_shared_mailboxes(
    admin_client, _reset_shared_mailboxes,
):
    """A Portal-style save (only default_channel) must not wipe the mailbox map."""
    r = await admin_client.patch(CONFIG_URL, json={
        "notification_settings": {
            "default_channel": "email_only",
            "role_shared_mailboxes": {"ap_clerk": "ap@example.com"},
        },
    })
    assert r.status_code == 200

    # Portal's form only knows these keys.
    r = await admin_client.patch(CONFIG_URL, json={
        "notification_settings": {"default_channel": "both"},
    })
    assert r.status_code == 200
    ns = r.json()["notification_settings"]
    assert ns["default_channel"] == "both"
    assert ns["role_shared_mailboxes"] == {"ap_clerk": "ap@example.com"}


@pytest.mark.asyncio
async def test_shared_mailbox_map_is_replaced_wholesale_so_removal_works(
    admin_client, _reset_shared_mailboxes,
):
    """The merge is deliberately shallow: sending the full sub-dict replaces it,
    so removing a role through the EPMS UI still removes it."""
    r = await admin_client.patch(CONFIG_URL, json={
        "notification_settings": {
            "role_shared_mailboxes": {
                "ap_clerk": "ap@example.com",
                "finance_bp": "fbp@example.com",
            },
        },
    })
    assert r.status_code == 200
    assert set(r.json()["notification_settings"]["role_shared_mailboxes"]) == {"ap_clerk", "finance_bp"}

    # EPMS UI drops finance_bp and re-submits the whole map.
    r = await admin_client.patch(CONFIG_URL, json={
        "notification_settings": {
            "role_shared_mailboxes": {"ap_clerk": "ap@example.com"},
        },
    })
    assert r.status_code == 200
    mailboxes = r.json()["notification_settings"]["role_shared_mailboxes"]
    assert "finance_bp" not in mailboxes, "removing a role must actually remove it"
    assert mailboxes == {"ap_clerk": "ap@example.com"}


@pytest.mark.asyncio
async def test_invalid_shared_mailbox_address_is_rejected(admin_client, _reset_shared_mailboxes):
    """A malformed address would silently kill that role's notifications (3 failed
    SMTP attempts, no fallback to the per-member fan-out) — reject it at write time."""
    r = await admin_client.patch(CONFIG_URL, json={
        "notification_settings": {
            "role_shared_mailboxes": {"ap_clerk": "not-an-email"},
        },
    })
    assert r.status_code == 422
    assert "ap_clerk" in r.text


@pytest.mark.asyncio
async def test_valid_shared_mailbox_address_is_accepted(admin_client, _reset_shared_mailboxes):
    r = await admin_client.patch(CONFIG_URL, json={
        "notification_settings": {
            "role_shared_mailboxes": {"ap_clerk": "ap@canadaroyalmilk.com"},
        },
    })
    assert r.status_code == 200
    assert r.json()["notification_settings"]["role_shared_mailboxes"] == {
        "ap_clerk": "ap@canadaroyalmilk.com"
    }
