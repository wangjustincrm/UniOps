"""Log levels raised for a while, without a restart (≈ ABP's Serilog MinimumLevel + Override with reloadOnChange,
user 2026-11-23: "change it to gather more details for the time we have errors").

- `configure()` once at startup: one JSON line per record on stdout (a message that is itself a JSON object is merged
  in, e.g. the request log), the root at the server's default level, and the noisy third-party loggers (`QUIET`) at
  WARNING — so raising the root or a module's logger never switches them on unless one is named.
- An override is (logger, level, until): it ALWAYS expires (1 minute to 24 hours, default 60): a forgotten DEBUG must
  not fill the disk (the container logs have no rotation). "" is the root (everything not quiet).
- `apply(overrides, now)` sets the levels: each logger an override touched goes back to what it was before the first
  override (e.g. what SQLAlchemy's echo set), then the overrides still running are applied. Called on every change and
  by a short tick in each process, so an expiry needs nobody.

Only stdout: nothing here writes to a database table, so a debug session cannot flood one.
"""
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from app.platform.options import LoggingOptions, options

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
# The defaults (LoggingOptions; a composition root may configure others): read through options() at run time.
_D = LoggingOptions()
MIN_MINUTES, MAX_MINUTES, DEFAULT_MINUTES = _D.min_override_minutes, _D.max_override_minutes, _D.default_override_minutes
QUIET = _D.quiet_loggers  # third-party loggers very loud at DEBUG / INFO: WARNING unless an override names them
_NAME = re.compile(r"^[A-Za-z0-9_.]{0,100}$")
_baseline: dict[str, int] = {}  # a logger's own level before an override first touched it
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        line: dict[str, Any] = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                                "level": record.levelname, "logger": record.name}
        body = None
        if msg.startswith("{"):
            try:
                body = json.loads(msg)
            except ValueError:
                body = None
        line.update(body if isinstance(body, dict) else {"message": msg})
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, separators=(",", ":"), default=str)


def configure(level: str = "INFO", *, stream: Any = None) -> None:
    """The process's logging, once: JSON lines to stdout at `level`; the quiet loggers at WARNING."""
    global _configured
    root = logging.getLogger()
    if not _configured:
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler.set_name("json")
        root.addHandler(handler)
        _configured = True
    root.setLevel(level if level in LEVELS else "INFO")
    for name in options(LoggingOptions).quiet_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)


def check(logger: str, level: str, minutes: int) -> str | None:
    """Why this override cannot be set; None: it can."""
    if not _NAME.match(logger or ""):
        return "a logger is a dotted name (letters, digits, _ and .), or empty for everything"
    if level not in LEVELS:
        return f"the level is one of {', '.join(LEVELS)}"
    o = options(LoggingOptions)
    if not o.min_override_minutes <= minutes <= o.max_override_minutes:
        return f"an override lasts {o.min_override_minutes} minute to {o.max_override_minutes // 60} hours (it always expires)"
    return None


def parse(raw: Any) -> list[dict[str, Any]]:
    """The stored overrides; raises ValueError on anything malformed (the registry then reads its safe value)."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError("a list of overrides")
    out = []
    for o in raw:
        if not isinstance(o, dict) or check(o.get("logger", ""), o.get("level", ""), MIN_MINUTES):
            raise ValueError(f"not an override: {o!r}")
        until = datetime.fromisoformat(str(o["until"]))
        if until.tzinfo is None:
            raise ValueError("until needs a time zone")
        out.append({"logger": o["logger"], "level": o["level"], "until": until.isoformat(), "by": o.get("by"),
                    "reason": o.get("reason")})
    return out


def override(logger: str, level: str, minutes: int, now: datetime, by: str | None, reason: str | None) -> dict[str, Any]:
    return {"logger": logger, "level": level, "until": (now + timedelta(minutes=minutes)).isoformat(), "by": by, "reason": reason}


def running(overrides: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    return [o for o in overrides if datetime.fromisoformat(o["until"]) > now]


def apply(overrides: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Set every logger's level from the overrides still running; the others back to their own. Returns those running."""
    live = running(overrides, now)
    wanted = {o["logger"]: o["level"] for o in live}
    for name in list(_baseline):
        if name not in wanted:
            logging.getLogger(name or None).setLevel(_baseline.pop(name))
    for name, level in wanted.items():
        lg = logging.getLogger(name or None)
        _baseline.setdefault(name, lg.level)
        lg.setLevel(level)
    return live


def effective(names: list[str]) -> dict[str, str]:
    """The level each of these loggers logs at now ("" = the root)."""
    return {n: logging.getLevelName(logging.getLogger(n or None).getEffectiveLevel()) for n in names}
