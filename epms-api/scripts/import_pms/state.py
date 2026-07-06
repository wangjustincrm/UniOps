"""Sync watermark persistence (file-based — no schema change).

Stores the timestamp of the last successful committed sync so incremental runs
can request only rows with ``Modified`` at/after it. Kept as a small JSON file in
the staging ``data/`` dir; mount that dir as a volume in Docker for durability.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_STATE_FILE = Path(__file__).resolve().parent / "data" / "_sync_state.json"
_RUNS_FILE = Path(__file__).resolve().parent / "data" / "_runs.json"


def get_last_sync() -> str | None:
    """Return the last-sync ISO timestamp, or None if never synced."""
    if not _STATE_FILE.exists():
        return None
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8")).get("last_sync")
    except (ValueError, OSError):
        return None


def set_last_sync(ts: str) -> None:
    _STATE_FILE.parent.mkdir(exist_ok=True)
    _STATE_FILE.write_text(json.dumps({"last_sync": ts}, indent=2), encoding="utf-8")


def now_iso() -> str:
    """UTC now as the SharePoint-compatible ISO instant used for filtering."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_runs() -> list[dict]:
    """Return the persisted import-run history (newest first), or [] if none.

    Run history was previously kept only in process memory, so it vanished on
    every container restart/redeploy (the observed "Run history: No runs yet"
    even after a successful import). Persisting it here — same file-based,
    no-schema-change approach as the watermark — makes it survive restarts.
    """
    if not _RUNS_FILE.exists():
        return []
    try:
        data = json.loads(_RUNS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (ValueError, OSError):
        return []


def save_runs(runs: list[dict]) -> None:
    """Persist run history atomically (temp file + replace) to avoid corruption
    if the process dies mid-write."""
    _RUNS_FILE.parent.mkdir(exist_ok=True)
    tmp = _RUNS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(runs, indent=2, default=str), encoding="utf-8")
    tmp.replace(_RUNS_FILE)
