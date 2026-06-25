"""Company Config endpoint tests."""
import pytest

CONFIG_URL = "/api/v1/config"
TA_URL = "/api/v1/config/temp-assignments"


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
async def test_update_dept_mapping(admin_client):
    r = await admin_client.patch(CONFIG_URL, json={
        "dept_gm_opm_mapping": {"dept-1": "gm", "dept-2": "opm"},
    })
    assert r.status_code == 200
    assert r.json()["dept_gm_opm_mapping"]["dept-1"] == "gm"


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


# ── Temp Assignments ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_temp_assignments_empty(admin_client):
    """Temp assignments list is accessible to all authenticated users."""
    r = await admin_client.get(TA_URL)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_create_temp_assignment_with_real_user(admin_client):
    """Create a temp assignment delegating GM to admin user."""
    from app.core.security import create_access_token
    from app.schemas.auth import RegisterRequest
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    # Get the admin's own user ID from token — easier: just create via PATCH + read back
    # Simpler: create a real user via the users API, then create temp assignment
    users_r = await admin_client.get("/api/v1/users")
    assert users_r.status_code == 200
    users = users_r.json()
    assert len(users) >= 1
    target_user_id = users[0]["id"]

    r = await admin_client.post(TA_URL, json={
        "delegate_user_id": target_user_id,
        "role_key": "gm",
        "start_date": "2026-04-01",
        "end_date": "2026-04-15",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["role_key"] == "gm"
    assert data["delegate_user_id"] == target_user_id

    # List returns it
    list_r = await admin_client.get(TA_URL)
    assert list_r.status_code == 200
    assert any(ta["id"] == data["id"] for ta in list_r.json())

    # Delete it
    del_r = await admin_client.delete(f"{TA_URL}/{data['id']}")
    assert del_r.status_code == 204

    # No longer in list
    list_r2 = await admin_client.get(TA_URL)
    assert not any(ta["id"] == data["id"] for ta in list_r2.json())


@pytest.mark.asyncio
async def test_temp_assignment_invalid_dates(admin_client):
    users_r = await admin_client.get("/api/v1/users")
    target_user_id = users_r.json()[0]["id"]
    r = await admin_client.post(TA_URL, json={
        "delegate_user_id": target_user_id,
        "role_key": "opm",
        "start_date": "2026-04-15",
        "end_date": "2026-04-01",   # end before start
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_nonexistent_temp_assignment(admin_client):
    import uuid
    r = await admin_client.delete(f"{TA_URL}/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_temp_assignment_non_admin_forbidden(finance_client):
    import uuid
    r = await finance_client.post(TA_URL, json={
        "delegate_user_id": str(uuid.uuid4()),
        "role_key": "gm",
        "start_date": "2026-04-01",
        "end_date": "2026-04-15",
    })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_config_includes_temp_assignments(admin_client):
    """GET /config should embed current temp_assignments."""
    r = await admin_client.get(CONFIG_URL)
    assert r.status_code == 200
    assert "temp_assignments" in r.json()
    assert isinstance(r.json()["temp_assignments"], list)


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
