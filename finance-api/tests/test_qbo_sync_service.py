from datetime import timedelta
from app.services import qbo_sync


def test_configured_reflects_env(monkeypatch, tmp_path):
    monkeypatch.setattr(qbo_sync, "ENV_PATH", tmp_path / "absent.env")
    assert qbo_sync.qbo_configured() is False
    p = tmp_path / "qbo_conn.env"
    p.write_text("CLIENT_ID=x\nCLIENT_SECRET=y\nREALM_ID=z\nREFRESH_TOKEN=t\n", encoding="utf-8")
    monkeypatch.setattr(qbo_sync, "ENV_PATH", p)
    assert qbo_sync.qbo_configured() is True


def test_stale_after_is_a_timedelta():
    assert isinstance(qbo_sync.STALE_AFTER, timedelta)
