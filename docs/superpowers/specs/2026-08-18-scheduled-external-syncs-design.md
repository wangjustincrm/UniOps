# Scheduled external syncs — WMS and NC purchase

2026-08-18. Ships in `feature/mrp-wms-sync-schedule`.

## The problem

Every mirror UniOps keeps of an external system moved only when a person
remembered to move it. There was no scheduler anywhere in the repo for a sync
job — `booking-api` and `vms-api` have background loops, but nothing that
refreshes data from WMS, NC65 or the ERP.

Two consequences, both already paid for:

* **2026-08-18, MRP Inventory release.** The new `wms_inventory_lots.uom`
  column and the new `wms_lot_locations` table shipped, and nobody ran the
  sync. Production showed no units and "No location recorded" for two days.
* **The Inventory screen could not tell anybody.** The only date on the page
  was `as of <today>` — the *shelf-life reference date*, computed as the
  server's today. A mirror last synced three weeks earlier still printed
  today's date next to every quantity.

## What ships

### mrp-api — WMS snapshot

| | |
|---|---|
| Schedule | `app/services/wms_sync/scheduler.py`, an asyncio task started in lifespan |
| Interval | planning parameter `wms_sync_interval_minutes`, **default 5**, 0 = off, max 1440 |
| Single flight | Postgres session advisory lock `0x574D535F53594E43`, shared with the manual trigger (which now 409s rather than overlapping) |
| Status | `GET /api/v1/admin/wms-sync/status`, gated `mrp.report.view` |
| Migration | `mrp16_sync_state_last_success` |

`mrp_sync_state` gains **`last_success_at`**. `last_synced_at` keeps meaning
"last attempt" — it is written on failures too — so with WMS down the two
diverge, and everything that renders freshness reads `last_success_at`.
Without the split, an outage makes the screen look freshly synced while the
numbers age.

Due-ness is measured from the last **attempt**: a failing WMS is retried once
per interval, not once per tick.

### mrp front-end — Inventory

* A freshness strip beside the page title: *"WMS data 6 min ago"*, amber past
  twice the interval (floor 15 min), red when the last run failed, plus a
  **Refresh** button gated `mrp.param.write`.
* The misleading `as of <date>` labels now read *"expiry checked against
  <date>"* / *"shelf life measured from <date>"*, and `as_of` carries a comment
  saying what it is and what it is not.
* `freshness.ts` is import-free of the api client so `freshness.verify.ts` can
  assert the regression under plain node: a fresh attempt over an old snapshot
  must never render as fresh.

### epms-api — NC65 purchase mirror

| | |
|---|---|
| Schedule | `app/tasks/nc_purchase_sync_scheduler.py`, asyncio task in lifespan, ticks every 60s |
| Interval | `company_config.nc_purchase_sync_interval_minutes`, **NULL → default 60**, 0 = off |
| Mode | **incremental only** — `full` deletes and rebuilds the mirror and stays behind the typed confirmation |
| Single flight | already solved by `service.start_run`'s live-run gate; the scheduler treats `SyncAlreadyRunning` as "fine" |
| Status | `GET /admin/nc-purchase-sync/status` now returns `interval_minutes` + `next_due_at`; `PATCH /interval` (system_admin) |
| Migration | `ai01_nc_sync_interval` |

### Portal Admin

* New **WMS Sync** section: snapshot age, batches, last attempt + error, next
  run, the interval field, and *Sync now*.
* **NC Purchase Sync** section gains the same interval control next to the
  cutover date.
* Portal now talks to mrp-api, which needs `VITE_MRP_API_URL` — added to
  `portal/Dockerfile` (ARG + ENV), `docker-compose.prod.yml` (from the existing
  `${MRP_API_URL}`) and `docker-compose.dev.yml`. All four places, because a
  missing build arg here degrades silently to `localhost:8011`.

### Dev machines do not sync

Both loops are off in `docker-compose.dev.yml`
(`WMS_SYNC_SCHEDULER_ENABLED=false`, `NC_SYNC_SCHEDULER_ENABLED=false`). The
root `.env` carries real WMS and NC65 credentials, so without the switch every
developer container would pull the live warehouse and the live ERP on the same
schedule as production. The manual buttons still work in dev.

## Decisions worth keeping

**The interval is data, not an env var.** Both live in a table an admin edits
in Portal, re-read every tick, so changing the schedule needs no redeploy.

**A short tick plus a due-check, not a long sleep.** Sleeping for the interval
would make a change from 60 minutes to 5 apply only after the current sleep,
and would tie the schedule to process uptime. Asking "is it due?" against the
stored run history makes restarts and redeploys invisible to the schedule.

**Not real-time, and not Redis.** Settled 2026-08-18 and not re-litigated here:
Oracle reads are fast (0.2s) but every filter, sort, page and aggregate on the
Inventory screen is a cross-database join against Postgres, and the planning
engine must see a still snapshot. Redis would not change either — the Postgres
mirror *is* the cache. Its one real use would be a distributed lock if mrp-api
ever runs multiple replicas; the advisory lock added here covers that.

## Deploying this

1. Migrations: mrp-api `mrp16`, epms-api `ai01` — both in `migrate-prod.sh`'s
   normal path.
2. **Rebuild `portal-web`** — it carries a new build arg. Confirm
   `VITE_MRP_API_URL` is non-empty in `.env` before building (`MRP_API_URL` is
   already there for `mrp-web`).
3. After deploy, both schedulers start on their own: WMS every 5 minutes, NC
   purchase hourly. Nothing needs to be pressed — which is the point — but the
   first WMS run happens within one tick, so watch `mrp_sync_state` once.
4. Set the intervals to taste in Portal → Admin → WMS Sync / NC Purchase Sync.
