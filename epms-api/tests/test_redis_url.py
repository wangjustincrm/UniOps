"""REDIS_URL construction — optional REDIS_PASSWORD support.

Backward-compat is the whole point: production Redis is unauthenticated today
(the 2026-07-15 MFA outage — REDIS_HOST pointed at a box with no Redis at all —
led to installing Redis with NO password, guarded only by ufw). REDIS_PASSWORD
must roll out *before* `requirepass` is set on that Redis, so when the env var
is unset/empty the URL must be byte-for-byte identical to today's unauthenticated
form. Getting this backward-compat wrong breaks login again.
"""
import redis.asyncio as aioredis

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "JWT_SECRET_KEY": "test-secret",
        "REDIS_HOST": "redishost",
        "REDIS_PORT": 6379,
        "REDIS_DB": 0,
    }
    base.update(overrides)
    return Settings(**base)


def test_redis_url_no_password_is_unchanged():
    """Empty/unset REDIS_PASSWORD must produce the exact pre-existing URL shape."""
    s = _settings(REDIS_PASSWORD="")
    assert s.REDIS_URL == "redis://redishost:6379/0"
    assert "@" not in s.REDIS_URL


def test_redis_url_password_none_default_is_unchanged():
    """Default (field omitted entirely) must also be the unauthenticated form."""
    s = _settings()
    assert s.REDIS_URL == "redis://redishost:6379/0"


def test_redis_url_with_plain_password():
    s = _settings(REDIS_PASSWORD="pwd")
    assert s.REDIS_URL == "redis://:pwd@redishost:6379/0"


def test_redis_url_with_special_char_password_escapes_and_round_trips():
    """Password containing @ : / # must not break URL parsing.

    Don't just assert the string looks right — assert redis.asyncio.from_url
    parses the ORIGINAL password back out. A wrong-but-plausible-looking
    escaping scheme could still pass a string-shape assertion.
    """
    special_pw = "p@ss:w/rd#1"
    s = _settings(REDIS_PASSWORD=special_pw)

    # URL must remain parseable (no bare unescaped separators breaking netloc)
    assert s.REDIS_URL.startswith("redis://:")
    assert s.REDIS_URL.endswith("@redishost:6379/0")

    client = aioredis.from_url(s.REDIS_URL, decode_responses=True)
    parsed_password = client.connection_pool.connection_kwargs.get("password")
    assert parsed_password == special_pw
