"""UAT 17 (closed 26 Nov 2026): "Report usage" on a scanned fire extinguisher or
eye wash station takes it out of service."""


async def _asset(hse, name, kind):
    made = await hse.post("/api/v1/safety-assets", json={"name": name, "asset_type_label": kind})
    assert made.status_code == 201, made.text
    return made.json()


async def test_a_used_extinguisher_or_eye_wash_goes_out_of_service(hse_manager, worker):
    """★ "Report usage … will be used for fire extinguishers, and eye wash
    stations to automatically flag the equipment as out of service and record it
    as such on the safety equipment registry." Whoever scanned it."""
    who, w = worker
    _, hse = hse_manager
    for name, kind in (("FE-12 by dock 3", "Fire Extinguisher"), ("Eyewash 4", "Eye Wash")):
        a = await _asset(hse, name, kind)
        scanned = (await w.get(f"/api/v1/qr/{a['qr_token']}")).json()
        assert "report_usage" in scanned["actions"], scanned

        used = await w.post(f"/api/v1/safety-assets/{a['id']}/report-usage",
                            json={"note": "  Small fire in the bin  "})
        assert used.status_code == 200, used.text
        assert used.json()["status"] == "out_of_service"
        [event] = (await hse.get(f"/api/v1/safety-assets/{a['id']}/history")).json()
        assert event["status"] == "out_of_service" and event["by_name"] == who.full_name
        assert event["reason"] == "Used: Small fire in the bin"

        # Out already: no second row, and the button is not offered again.
        again = await w.post(f"/api/v1/safety-assets/{a['id']}/report-usage", json={})
        assert again.status_code == 409, again.text
        assert len((await hse.get(f"/api/v1/safety-assets/{a['id']}/history")).json()) == 1
        assert "report_usage" not in (await w.get(f"/api/v1/qr/{a['qr_token']}")).json()["actions"]

        # Back the way any item comes back, and then it can be reported again.
        back = await hse.post(f"/api/v1/safety-assets/{a['id']}/return-to-service",
                              json={"reason": "Recharged and tagged"})
        assert back.status_code == 200, back.text
        assert "report_usage" in (await w.get(f"/api/v1/qr/{a['qr_token']}")).json()["actions"]


async def test_a_ladder_is_not_put_out_of_service_by_being_used(hse_manager, worker):
    _, w = worker
    _, hse = hse_manager
    a = await _asset(hse, "Ladder 6", "Ladder")
    assert "report_usage" not in (await w.get(f"/api/v1/qr/{a['qr_token']}")).json()["actions"]
    refused = await w.post(f"/api/v1/safety-assets/{a['id']}/report-usage", json={})
    assert refused.status_code == 422, refused.text
    assert (await hse.get(f"/api/v1/safety-assets/{a['id']}")).json()["status"] == "in_service"


async def test_report_usage_refuses_what_it_does_not_know(hse_manager, worker):
    _, w = worker
    _, hse = hse_manager
    a = await _asset(hse, "FE-13", "Fire Extinguisher")
    bad = await w.post(f"/api/v1/safety-assets/{a['id']}/report-usage", json={"used_by": "me"})
    assert bad.status_code == 422, bad.text
    assert (await hse.get(f"/api/v1/safety-assets/{a['id']}")).json()["status"] == "in_service"

