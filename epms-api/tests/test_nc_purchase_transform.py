from app.services.nc_purchase_sync.reader import nc_configured


def test_nc_configured_false_when_unset(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "nc_host", None, raising=False)
    assert nc_configured() is False


def test_nc_configured_true_when_all_set(monkeypatch):
    from app.core.config import settings
    for f, v in [("nc_host", "h"), ("nc_service", "ORCL"), ("nc_user", "u"), ("nc_password", "p")]:
        monkeypatch.setattr(settings, f, v, raising=False)
    assert nc_configured() is True
