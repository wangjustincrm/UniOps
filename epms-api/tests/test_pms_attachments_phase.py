"""The attachments phase must be accepted by the API schema and the runner guard."""
from app.api.v1.pms_import import RunRequest
from app.services import pms_import_runner as runner


def test_run_request_allows_attachments():
    assert RunRequest(phase="attachments").phase == "attachments"


def test_run_request_rejects_bad_phase():
    import pydantic, pytest
    with pytest.raises(pydantic.ValidationError):
        RunRequest(phase="nonsense")


def test_start_run_accepts_attachments_phase(monkeypatch):
    # Don't actually launch the background coroutine or persist to disk.
    # Stub `background.spawn` — the seam start_run now uses. Stubbing
    # `runner.asyncio.create_task` instead would patch the asyncio module
    # itself, so spawn()'s own create_task(coro, name=...) call would hit the
    # stub and blow up on the unexpected `name` kwarg. start_run imports spawn
    # inside the function, so patching it at its definition site takes effect.
    from app.core import background

    def _no_spawn(coro, *, name=None):
        coro.close()
        return None
    monkeypatch.setattr(background, "spawn", _no_spawn)
    monkeypatch.setattr(runner, "_persist", lambda: None)
    monkeypatch.setattr(runner, "_ensure_loaded", lambda: [])
    runner._current = None
    try:
        out = runner.start_run("attachments", dry_run=True, triggered_by="tester")
        assert out["phase"] == "attachments" and out["status"] == "running"
    finally:
        runner._current = None
