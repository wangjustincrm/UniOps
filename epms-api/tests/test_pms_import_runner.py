"""Run-history persistence for the PMS import runner.

Regression: run history used to live only in process memory, so it showed
"No runs yet" after every container restart even though the import had
succeeded. It is now persisted to disk (scripts.import_pms.state) and reloaded
on access. These tests exercise the persistence + restart-recovery logic
without triggering the real (SharePoint-hitting) background import.
"""
import pytest

from app.services import pms_import_runner as runner
from scripts.import_pms import state


@pytest.fixture
def isolated_runs(tmp_path, monkeypatch):
    """Point the history file at a temp path and reset the runner's globals."""
    monkeypatch.setattr(state, "_RUNS_FILE", tmp_path / "_runs.json")
    monkeypatch.setattr(runner, "_runs", None)
    monkeypatch.setattr(runner, "_current", None)
    yield


def test_history_survives_restart(isolated_runs):
    run = runner.SyncRun(id="abc", phase="full", dry_run=False, triggered_by="me")
    run.status, run.step = "success", "done"
    runner._runs = [run]
    runner._persist()

    # Simulate a restart: in-memory state gone, must reload from disk.
    runner._runs = None
    got = runner.list_runs()

    assert [r["id"] for r in got] == ["abc"]
    assert got[0]["status"] == "success"


def test_stale_running_run_recovered_as_error(isolated_runs):
    # A run persisted while still "running" means the process died mid-import.
    state.save_runs([{
        "id": "x", "phase": "full", "dry_run": False, "triggered_by": "me",
        "status": "running", "started_at": "2026-07-02T00:00:00Z", "step": "loading",
    }])
    runner._runs = None

    got = runner.list_runs()
    assert got[0]["status"] == "error"
    assert "Interrupted" in (got[0]["error"] or "")
    assert got[0]["finished_at"] is not None


def test_legacy_rows_with_unknown_fields_are_tolerated(isolated_runs):
    state.save_runs([{
        "id": "y", "phase": "full", "dry_run": False, "triggered_by": "me",
        "status": "success", "started_at": "2026-07-02T00:00:00Z",
        "bogus_removed_field": 123,
    }])
    runner._runs = None

    got = runner.list_runs()
    assert [r["id"] for r in got] == ["y"]
