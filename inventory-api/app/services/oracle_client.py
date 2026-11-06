"""One Oracle client mode per process.

python-oracledb runs either THIN or THICK, decided by the first connection
the process makes and never switchable afterwards (DPY-2019). NC65 is fine
either way; Flux WMS is Oracle <=11g and only works THICK (DPY-3010). The
scheduler syncs NC and then WMS in the same process, so if NC connected thin
first, every WMS sync after it failed — found on dev1, 2026-10-14; the unit
tests fake the extract and could not see it.

So both readers call `ensure_mode()` before connecting: when the Instant
Client is present (the image ships it), the process goes THICK on its very
first Oracle connection, whichever source that is.
"""
from __future__ import annotations

import os
import threading

from app.core.config import settings

_lock = threading.Lock()
_done = False


def ensure_mode() -> None:
    global _done
    if _done:
        return
    with _lock:
        if _done:
            return
        lib = settings.oracle_client_lib
        if lib and os.path.isdir(lib):
            import oracledb
            oracledb.init_oracle_client(lib_dir=lib)
        _done = True
