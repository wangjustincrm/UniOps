"""Log levels raised for a while (user 2026-11-23, ≈ Serilog MinimumLevel + Override): an override raises a logger
and expires back by itself (at most 24 h, default 60 min); a bad stored value is none (safe-off); every change is
audited with its reason; Health shows the levels; a raised sales.* logger never switches the quiet third-party ones
on; a SALES_SECRET_* value is never in a log call."""
import logging
import pathlib
import re
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.platform import log_levels as levels
from app.platform.actors import SYSTEM_ACTOR
from tests.test_order_payment import FINANCE, OPERATOR, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture

NOW = datetime(2026, 11, 23, 15, tzinfo=timezone.utc)


def test_an_override_raises_a_logger_and_expires_back_to_its_own_level():
    lg = logging.getLogger("test.levels.area")
    lg.setLevel(logging.WARNING)
    o = levels.override("test.levels.area", "DEBUG", 60, NOW, "Op", "chasing an error")
    assert levels.apply([o], NOW + timedelta(minutes=59)) == [o] and lg.level == logging.DEBUG
    assert levels.apply([o], NOW + timedelta(minutes=61)) == [] and lg.level == logging.WARNING  # back by itself
    # a sales.* override leaves the quiet third-party loggers quiet
    levels.configure("INFO")
    levels.apply([levels.override("sales", "DEBUG", 5, NOW, None, None)], NOW)
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("sales.request").getEffectiveLevel() == logging.DEBUG
    levels.apply([], NOW)


def test_the_limits_and_a_bad_stored_value():
    assert levels.check("app.services", "DEBUG", 60) is None
    assert "always expires" in levels.check("app.services", "DEBUG", 24 * 60 + 1)
    assert "always expires" in levels.check("app.services", "DEBUG", 0)
    assert levels.check("bad name!", "DEBUG", 60) and levels.check("x", "TRACE", 60)
    for bad in ("x", [{"logger": "x"}], [{"logger": "x", "level": "DEBUG", "until": "2026-11-23T15:00:00"}]):
        try:
            levels.parse(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


async def test_raised_from_health_audited_shown_and_a_bad_value_is_safe_off(api):  # noqa: F811
    r = await api.post("/api/v1/config/log-levels", json={"logger": "app.services.shopify_intake", "level": "debug",
                                                          "reason": "Shopify pass fails"}, headers=FINANCE)
    assert r.status_code == 403  # sales.config only
    r = await api.post("/api/v1/config/log-levels", json={"logger": "app.services.shopify_intake", "level": "DEBUG",
                                                          "minutes": 2000, "reason": "too long"}, headers=OPERATOR)
    assert r.status_code == 422 and r.json()["error"]["code"] == "logging.invalid"
    r = await api.post("/api/v1/config/log-levels", json={"logger": "app.services.shopify_intake", "level": "debug",
                                                          "reason": "Shopify pass fails"}, headers=OPERATOR)
    assert r.status_code == 200, r.text
    [o] = r.json()["overrides"]
    until = datetime.fromisoformat(o["until"])
    assert o["level"] == "DEBUG" and timedelta(minutes=59) < until - datetime.now(timezone.utc) <= timedelta(minutes=60)
    assert r.json()["effective"]["app.services.shopify_intake"] == "DEBUG"
    [a] = await api.env.q("SELECT reason, user_name FROM sales_audit WHERE entity = 'sales_settings' AND reason LIKE 'log level%'")
    assert a["reason"] == "log level app.services.shopify_intake → DEBUG for 60 min: Shopify pass fails" and a["user_name"]
    h = (await api.get("/api/v1/operations/health", headers=OPERATOR)).json()["log_levels"]
    assert [x["logger"] for x in h["overrides"]] == ["app.services.shopify_intake"] and h["max_minutes"] == 1440
    r = await api.post("/api/v1/config/log-levels/clear", json={"logger": "app.services.shopify_intake", "reason": "found it"},
                       headers=OPERATOR)
    assert r.json()["overrides"] == [] and logging.getLogger("app.services.shopify_intake").level == logging.NOTSET
    # a value stored by hand that does not parse: no override (the server's own levels)
    async with api.env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text("UPDATE sales_settings SET value = '\"DEBUG forever\"' WHERE key = 'logging.overrides'"))
        await u.commit()
    from app.services.settings import SETTINGS
    SETTINGS.invalidate()
    h = (await api.get("/api/v1/operations/health", headers=OPERATOR)).json()["log_levels"]
    assert h["overrides"] == []


def test_no_log_call_carries_a_secret_value():
    """Hold the line at every level: a log call never reads a SALES_SECRET_* value (names are fine)."""
    app = pathlib.Path(__file__).resolve().parents[1] / "app"
    call = re.compile(r"\b(?:log|logger|_log)\.(?:debug|info|warning|error|exception|critical)\((?:[^()]|\([^()]*\))*\)", re.S)
    reads = re.compile(r"environ(?:\.get)?\s*[\[(]\s*f?[\"']SALES_SECRET|getenv\(\s*f?[\"']SALES_SECRET|password\(\)|\.secret\b")
    bad = [f"{f.relative_to(app)}: {m.group(0)[:80]}" for f in app.rglob("*.py") for m in call.finditer(f.read_text(encoding="utf-8"))
           if reads.search(m.group(0))]
    assert bad == []
    seen = sum(len(call.findall(f.read_text(encoding="utf-8"))) for f in app.rglob("*.py"))
    assert seen > 20 and reads.search('logger.info("x %s", os.environ["SALES_SECRET_SMTP"])')  # the guard sees calls
