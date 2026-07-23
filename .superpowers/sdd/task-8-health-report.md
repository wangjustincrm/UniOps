# Task 8: identity health must probe DB + Redis (not return hardcoded ok)

Branch: `main`, base HEAD before this change: `3021e46`.

## Background (today's incident)

Company-wide MFA rollout made OTP mandatory. identity's Redis host had no
Redis installed (missing for 33 days, unnoticed). identity couldn't reach
Redis, so no OTP could be issued, so **no one could log in**. During triage,
`docker ps` / `docker inspect` showed identity as `Up (healthy)` the whole
time, because `GET /health` was:

```python
@router.get("/health")
async def health():
    return {"status": "ok", "service": "identity-api"}
```

— a hardcoded dict, touching neither DB nor Redis. It is green under any
dependency failure. That actively misled triage (an early hypothesis was
"crash loop", which wasted a cycle) because the one signal that's supposed to
say "something's wrong" said nothing was wrong.

## Design decisions

1. **DB down → HTTP 503.** identity's core functions (auth, authz, audit) all
   require the DB. If DB is unreachable, identity genuinely can do nothing
   useful. Marking this unhealthy (and letting an orchestrator restart it, if
   one exists) is the correct, proportionate response.

2. **Redis down → HTTP 200, `"status": "degraded"`, `"redis": "fail: <reason>"`.**
   This is the load-bearing decision. Redis is only used for OTP storage and
   the refresh-token blacklist — i.e. MFA login and logout/refresh
   revocation. Everything else (non-MFA login, token verification, every
   other endpoint) works fine with Redis down. If a Redis outage made
   `/health` return non-200, and something in the deploy topology treats
   "unhealthy" as "kill/restart the container," a **partial, single-feature
   outage would escalate into full identity unavailability** — strictly
   worse than today's incident, where non-MFA users (if MFA had been
   disabled) could still have logged in. So Redis failures are always
   surfaced (never hidden — that's the whole point of this fix) but never
   change the status code.

3. **Every probe has a hard 2s timeout (`PROBE_TIMEOUT_SECONDS`), and DB/Redis
   probes run concurrently via `asyncio.gather`.** A probe against a
   dependency that isn't outright refusing connections but is silently
   dropping packets (e.g. a firewall, not "service down") can hang
   indefinitely. If `/health` itself hangs waiting on that, we've reproduced
   today's failure mode one level up: now the *health check* is the thing
   silently stuck, and the orchestrator's healthcheck `timeout: 5s` (see
   below) would need to catch it — right up against the compose config,
   not with headroom to spare. Running both probes concurrently (rather than
   sequentially) keeps the worst case (both dependencies hang) at ~2s
   instead of ~4s, leaving comfortable margin under the compose `timeout: 5s`.

4. **Implementation shape copies the existing repo pattern for probing**,
   found via `grep -rn "def health" --include=*.py */app/`: every other
   service (epms-api, budget-api, mdm-api, vms-api, finance-api,
   approval-api, booking-api) does `await session.execute(text("SELECT 1"))`
   inside a try/except that sets `response.status_code = 503` on failure —
   epms-api's `/health/redis` does the identical thing with `await
   redis.ping()`. **No existing service, however, combines both probes into
   a single `/health` endpoint with differentiated status-code treatment**
   (DB fatal, Redis non-fatal) — every other service keeps `/health` as a
   hardcoded liveness stub and puts the real probes behind separate
   `/health/db` / `/health/redis` readiness endpoints that nothing in
   `docker-compose.prod.yml` actually calls. That means **all of those
   services have the same false-green problem** identity had — noted in the
   task brief as a separate, intentionally out-of-scope issue for this
   change (identity only, per explicit instruction).

## Red (before implementation) — actual output

Command: `pytest -q tests/test_health.py` run from `identity-api/` with the
local `.venv`, against the *old* hardcoded `health.py`:

```
>       assert body["db"] == "ok"
E       KeyError: 'db'
tests\test_health.py:28: KeyError

>       monkeypatch.setattr(
            health_module, "_redis_client",
            AsyncMock(side_effect=ConnectionError("redis host has no redis installed")),
        )
E       AttributeError: <module 'app.api.v1.health' ...> has no attribute '_redis_client'
tests\test_health.py:38: AttributeError

>       assert resp.status_code == 503
E       assert 200 == 503
E        +  where 200 = <Response [200 OK]>.status_code
tests\test_health.py:62: AssertionError

>       monkeypatch.setattr(health_module, "PROBE_TIMEOUT_SECONDS", 0.1)
E       AttributeError: <module 'app.api.v1.health' ...> has no attribute 'PROBE_TIMEOUT_SECONDS'
tests\test_health.py:73: AttributeError

4 failed, 2 warnings in 1.37s
```

All 4 new tests failed for the expected reason (no probing, no `_redis_client`,
no `PROBE_TIMEOUT_SECONDS`) — confirmed red before touching the implementation.

## Green (after implementation)

```
tests\test_health.py ....                                                [100%]
======================== 4 passed, 2 warnings in 0.78s ========================
```

Full identity suite:

```
35 passed, 29 warnings in 14.03s
```

## Baseline comparison

- Baseline (before this task, local `.venv`): **31 passed**.
- After this task: **35 passed** (31 pre-existing + 4 new in `test_health.py`).
- No regressions; no baseline test's outcome changed.

Run command used (identity tests need the local Postgres container's real
password, not the compose-file default — pulled via
`docker inspect uniops_postgres`):

```bash
cd identity-api
export TEST_PG_PASSWORD=<uniops_postgres POSTGRES_PASSWORD>
./.venv/Scripts/python.exe -m pytest -q
```

Redis: real local `uniops_redis` container on `localhost:6379` (already
required by the pre-existing `test_mfa_flow_with_real_redis` test — confirmed
running via `docker ps` before starting).

## Compose healthcheck compatibility analysis

`docker-compose.prod.yml` identity-api block (lines ~142-147):

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8009/health').status==200 else 1)"]
  interval: 30s
  timeout: 5s
  retries: 5
  start_period: 20s
```

and (`x-api-common` anchor, line 34-35): `restart: unless-stopped`.

Findings:

- The healthcheck only cares about **HTTP status code 200**, not body
  content. My change makes Redis-down still return 200 (with the failure
  visible only in the JSON body) — **this exact compose config will never
  flag "unhealthy" for a Redis-only outage**, exactly satisfying the
  requirement. Redis failures are now *visible* (in the body, e.g. via
  `docker exec identity-api python -c "..."` or a future monitoring hook)
  without changing container health status.
- DB-down does return 503 → the healthcheck's `CMD` exits 1 → after 5
  consecutive failed retries (`retries: 5`, `interval: 30s` — roughly 2.5
  minutes) Docker marks the container `unhealthy`. This is the intended,
  correct behavior per the task's own stated design ("DB 不可用 →
  identity 干不了任何事,算 unhealthy 合理").
- **Does "unhealthy" trigger a restart here?** Checked: no `autoheal`,
  `watchtower`, or Swarm/`deploy:` block exists anywhere in
  `docker-compose.prod.yml` (`grep -n "autoheal\|watchtower\|docker.sock\|deploy:"`
  found nothing beyond the one `restart: unless-stopped` line). Docker
  Compose's restart policies (`unless-stopped` included) only act on
  **container exit**, not on healthcheck-derived "unhealthy" status — plain
  `docker-compose`/Compose V2 does not itself restart a running-but-unhealthy
  container. So in the current deployment topology, an unhealthy identity
  container is *visible* (`docker ps`, any external monitoring reading health
  status) but not auto-restarted by Docker itself. **No restart-storm risk
  found** — nothing to report as BLOCKED.
- Caveat for the record (not a blocker, just a note for whoever operates
  this): if an external watchdog/monitoring script is later added that
  restarts containers on `unhealthy` (autoheal-style), the DB-down → 503 →
  unhealthy path would then cause a restart — which is fine per this task's
  own stated design (DB down really does warrant it). If such tooling
  exists elsewhere in the deploy that this repo doesn't show (e.g. a
  systemd timer or external autoheal container on the host, outside this
  compose file), it wasn't found by grep and couldn't be verified from the
  repo alone.

## Timeout / hang-safety verification

`test_health_probe_timeout_does_not_hang_forever` patches `_redis_client` to
`await asyncio.sleep(100)` and `PROBE_TIMEOUT_SECONDS` down to `0.1` for test
speed, then asserts the whole `GET /health` call completes within a 5s
`asyncio.wait_for` wrapper, returns 200, and reports `"redis": "fail: timed
out after 0.1s"`. This is the "hard requirement" from the task brief — a
hung dependency cannot hang `/health` itself.

## Files changed

- `identity-api/app/api/v1/health.py` — real DB probe (503 on failure) +
  real Redis probe (200 + degraded body on failure), both concurrent, both
  timeout-guarded.
- `identity-api/tests/test_health.py` — new: 4 tests (all-ok, redis-down,
  db-down, timeout-does-not-hang).

## Concerns / follow-ups (not done here, out of scope per instructions)

- Every other service's `/health` (epms-api, budget-api, mdm-api, vms-api,
  finance-api, approval-api, booking-api, file-api) has the exact same
  false-green problem — hardcoded `{"status": "ok"}` with real probes (where
  they exist at all) sitting behind unused `/health/db` / `/health/redis`
  endpoints the compose healthcheck never calls. Flagged per the task brief
  as a separate future task; **not touched here**.
- Did not modify `docker-compose.prod.yml` (not asked to, and the existing
  config is already compatible with this change — see analysis above).
