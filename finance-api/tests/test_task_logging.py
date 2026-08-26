"""app.tasks INFO logs used to be silently dropped: no handler anywhere in
this process means every INFO record falls through to `logging.lastResort`,
which is itself WARNING-level. Two lines have no substitute anywhere else —
the scheduler loop actually starting, and it being switched off by config
(see app/core/task_logging.py for the full argument and the mechanism).

Shape mirrors epms-api/tests/test_task_logging.py.

These tests assert the real behaviour, not the configuration: that a record
really lands in a handler, that the fix did not quietly turn INFO on for
everything (SQLAlchemy would get noisy), and that the same record cannot be
emitted twice.
"""
import logging

from app.core.task_logging import configure_task_logging
from app.tasks import nc_sync_scheduler as sched


def _reset_task_logging_state():
    """configure_task_logging() mutates the real root logger. Put it back so
    this file doesn't change how the rest of the suite logs."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_uniops_task_logging_marker", False):
            root.removeHandler(h)
    if hasattr(root, "_uniops_task_logging_configured"):
        delattr(root, "_uniops_task_logging_configured")
    logging.getLogger("app.tasks").setLevel(logging.NOTSET)


def setup_function(_fn):
    _reset_task_logging_state()


def teardown_function(_fn):
    _reset_task_logging_state()


def test_scheduler_info_record_is_emitted(caplog):
    """A record logged at INFO by the scheduler module's own logger must
    actually reach a handler once configure_task_logging() has run."""
    configure_task_logging()
    caplog.clear()
    sched.logger.info("JV sync scheduler started (tick=%ss)", sched.TICK_SECONDS)
    matches = [r for r in caplog.records
               if r.name == sched.logger.name and "scheduler started" in r.getMessage()]
    assert len(matches) == 1, caplog.records


async def test_scheduler_disabled_line_is_emitted_via_the_real_code_path(caplog, monkeypatch):
    """The 'switched off by config' line, exercised through the real loop
    function rather than a synthetic log call — this is the only evidence a
    deliberate env flag turned the loop off, as opposed to it dying."""
    configure_task_logging()
    monkeypatch.setattr(sched.settings, "nc_sync_scheduler_enabled", False)
    caplog.clear()
    await sched.nc_sync_loop()
    matches = [r for r in caplog.records
               if r.name == sched.logger.name and "scheduler disabled" in r.getMessage()]
    assert len(matches) == 1, caplog.records


def test_third_party_info_is_not_turned_on(caplog):
    """Fixing app.tasks must not flip INFO on globally — SQLAlchemy (or any
    other third-party logger nobody touched) must stay silent at INFO."""
    configure_task_logging()
    caplog.clear()
    logging.getLogger("sqlalchemy.engine").info(
        "should not be captured — would mean INFO went on globally")
    assert not any("should not be captured" in r.getMessage() for r in caplog.records)


def test_configure_task_logging_is_idempotent():
    """Calling it twice (module import + a test, or two lifespans in the same
    process) must not attach a second handler — a second handler on the same
    logger chain would print, and could double-count, every app.tasks line."""
    root = logging.getLogger()
    configure_task_logging()
    markers_after_first = sum(
        1 for h in root.handlers if getattr(h, "_uniops_task_logging_marker", False))
    configure_task_logging()
    markers_after_second = sum(
        1 for h in root.handlers if getattr(h, "_uniops_task_logging_marker", False))
    assert markers_after_first == 1
    assert markers_after_second == 1


def test_no_duplicate_emission_of_the_same_record(caplog):
    """One logger.info() call must produce exactly one captured record, not
    two — the failure mode a naive fix (handler on app.tasks *and*
    propagate left on, with something else also handling at root) would hit."""
    configure_task_logging()
    caplog.clear()
    sched.logger.info("single emission check for the duplicate-record test")
    matches = [r for r in caplog.records
               if "single emission check for the duplicate-record test" in r.getMessage()]
    assert len(matches) == 1
