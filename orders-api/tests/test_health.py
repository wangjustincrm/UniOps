async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "orders-api"}


async def test_health_db(client):
    r = await client.get("/api/v1/health/db")
    assert r.status_code == 200
    assert r.json()["db"] == "reachable"


def test_conftest_refuses_non_test_database(monkeypatch):
    import tests.conftest as c

    monkeypatch.setattr(c, "TEST_DB", "epms")
    try:
        c._refuse_non_test_db()
    except RuntimeError as e:
        assert "_test" in str(e)
    else:
        raise AssertionError("a database name without _test was accepted")


def test_conftest_accepts_test_database():
    import tests.conftest as c

    c._refuse_non_test_db()  # the default name ends in _test
