"""Make the app.tasks background-loop logs actually reach a handler.

Nothing in this service calls `logging.basicConfig()`, so the root logger has
no handler. That means every logger's effective level falls back to the root
default (WARNING), and even a record whose logger *does* have an explicit
level still gets dropped if it finds zero handlers anywhere up the chain —
`logging.lastResort` catches those, and lastResort is itself WARNING-level.
INFO records vanish without a trace either way.

Two of `app.tasks`'s INFO lines have no substitute anywhere else: the
scheduler loop actually starting ("... scheduler started (tick=60s)") and the
loop being switched off by config ("... scheduler disabled
(nc_sync_scheduler_enabled=false)"). Everything else about a sync run is also
in the DB (nc_purchase_sync_runs / nc_sync_runs / erp_sync_state) and
logger.error/.exception already surface via lastResort. Losing those two
INFO lines collapses "the loop died", "it's switched off", and "it's just not
due yet" into the same total silence.

We deliberately do NOT call `logging.basicConfig()` or raise the root
logger's *level* — that would also turn on INFO for SQLAlchemy, httpx, etc.,
and nobody asked for that log volume. Instead, two narrow moves:

* `app.tasks` (the package every background loop in this service logs
  under — the four scheduler modules, nothing else) gets its own level
  raised to INFO. Nothing else does.
* A single handler is attached to the ROOT logger, with the root logger's
  own *level* left untouched. Root already receives every record that
  propagates up from any logger in the tree (default `propagate=True`);
  giving it a handler means those records land somewhere real instead of
  falling through to `lastResort`. Because root's level stays WARNING, this
  is a no-op for loggers we didn't touch: a third-party INFO record is still
  filtered out by `isEnabledFor()` at the origin logger, before any handler
  is ever consulted.

The alternative — attach a handler directly to `app.tasks` and set
`propagate = False` — would also work, but stops those records from
reaching anything else already listening at root (a test's `caplog`, a
handler added later for another reason). The root handler is the more
composable choice, and it also means a given record only ever plays through
one handler here, never two.

Idempotent: safe to call more than once (module import + explicit test
calls) without attaching a second handler, which would print every line
twice.
"""
from __future__ import annotations

import logging

TASK_LOGGER_NAME = "app.tasks"
_HANDLER_ATTR = "_uniops_task_logging_configured"


def configure_task_logging() -> None:
    """Give `app.tasks` a working INFO-level path to stdout, and nothing else."""
    root = logging.getLogger()
    if not getattr(root, _HANDLER_ATTR, False):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._uniops_task_logging_marker = True  # lets tests find/remove exactly this handler
        root.addHandler(handler)
        setattr(root, _HANDLER_ATTR, True)
    logging.getLogger(TASK_LOGGER_NAME).setLevel(logging.INFO)
