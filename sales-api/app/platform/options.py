"""Platform options (≈ ABP's Configure<AbpAuditingOptions> / AbpCorrelationIdOptions / AbpExceptionHandlingOptions,
user 2026-11-23): typed, frozen settings of the platform's pieces, set once by the module's composition root (main.py)
with `configure(...)` and read by the pieces with `options(Kind)`. The Options pattern: the pieces never read the
environment or the business code themselves.

Defaults are ABP's, except where a user rule says otherwise (each such field says which rule).
"""
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class AuditingOptions:
    """≈ AbpAuditingOptions (Volo.Abp.Auditing/AbpAuditingOptions.cs)."""
    is_enabled: bool = True
    hide_errors: bool = True                     # a failure to save an audit log is logged, never raised
    is_enabled_for_anonymous_users: bool = True
    is_enabled_for_get_requests: bool = False    # ABP default: GET / HEAD are not saved…
    always_log_on_exception: bool = True         # …unless they fail
    # Routes (templates, fnmatch patterns) always saved, GET too ≈ AlwaysLogSelectors: bulk data handed out.
    always_log_routes: tuple[str, ...] = ()
    # Never saved, not even on failure ≈ AbpAspNetCoreAuditingOptions.IgnoredUrls (the container's health probe).
    ignored_routes: tuple[str, ...] = ()
    # Background work (jobs, outbox sends, intake passes): ABP does not audit it; we save a background scope when it
    # changed data, ran an action or failed.
    save_background_scopes: bool = True
    # User rule "remove nothing": ABP blanks action parameters over 2,000 chars and truncates entity values at 512;
    # None = keep whole.
    max_parameters_length: int | None = None
    application_name: str = ""


@dataclass(frozen=True)
class CorrelationOptions:
    """≈ AbpCorrelationIdOptions: the header (ABP's X-Correlation-Id); the others accepted on the way in."""
    http_header_name: str = "X-Correlation-Id"
    accepted_headers: tuple[str, ...] = ("X-Correlation-Id", "X-Request-Id")
    # Also returned under these names (X-Request-Id kept: callers and tests read it since 2026-11-23).
    response_headers: tuple[str, ...] = ("X-Correlation-Id", "X-Request-Id")


@dataclass(frozen=True)
class ExceptionHandlingOptions:
    """≈ AbpExceptionHandlingOptions: what a client sees and at which level an error is logged (≈ IHasLogLevel)."""
    send_exception_details_to_clients: bool = False
    business_log_level: str = "WARNING"          # BusinessException / validation: Warning in ABP
    unexpected_log_level: str = "ERROR"


@dataclass(frozen=True)
class LoggingOptions:
    """The process's logging (platform/log_levels.py): level, the loud third-party loggers kept quiet, override limits."""
    level: str = "INFO"
    quiet_loggers: tuple[str, ...] = ("httpx", "httpcore", "asyncssh", "paramiko", "urllib3", "aiosmtplib", "asyncio",
                                      "multipart", "sqlalchemy.pool", "sqlalchemy.dialects", "sqlalchemy.orm")
    min_override_minutes: int = 1
    max_override_minutes: int = 24 * 60          # always expires: a forgotten DEBUG must not fill the disk
    default_override_minutes: int = 60
    request_logger: str = "request"              # the module names it, e.g. "<module>.request"


@dataclass(frozen=True)
class AuthenticationOptions:
    """Who may still use a valid token (≈ ABP: an inactive IdentityUser is refused at sign-in, and dynamic claims /
    the security stamp end a live session). A principal's status is read through a short cache: a deactivation takes
    effect within `principal_cache_seconds`, with no read of the users table on every request."""
    principal_cache_seconds: float = 30.0
    refuse_missing_principals: bool = True  # a token for a user who does not exist (deleted) is refused too


@dataclass(frozen=True)
class ClockOptions:
    """≈ AbpClockOptions (Volo.Abp.Timing): what "now" is and which zone the business day is in (platform/clock.py).

    `business_timezone` is read where it is used; SQL expressions built at import time (a model's column_property)
    take it when their module loads, so it is set before the models import — or left at its default."""
    business_timezone: str = "America/Toronto"  # the business day, for documents and lists (product owner, 2026-10-30)
    kind: str = "utc"                           # now() is aware UTC (ABP: DateTimeKind.Utc)
    naive_is: str = "utc"                       # normalize(): a time without a zone is UTC ("business": the business zone)
    provider: Any = None                        # a Clock (tests: FixedClock); None = the system clock

    def __post_init__(self) -> None:
        if self.kind != "utc":
            raise ValueError("the clock is UTC only")
        if self.naive_is not in ("utc", "business"):
            raise ValueError(f"naive_is is utc or business, not {self.naive_is!r}")


@dataclass(frozen=True)
class ResourceOptions:
    """What every list resource offers unless its base class or its service says otherwise (platform/resource.py)."""
    get: bool = True
    suggest: bool = True
    export: bool = True
    export_headers: str = "fields"  # the full export's header row: the field names (an import reads them back)
    history: bool = False           # a record's audit history: a list says so
    history_entries: int = 200      # the newest shown


_options: dict[type, Any] = {}


def configure(*opts: Any) -> None:
    """Set the options (the composition root, once at startup; a test may set its own)."""
    for o in opts:
        _options[type(o)] = o


def options(kind: type[T]) -> T:
    """The configured options of this kind, else its defaults."""
    found = _options.get(kind)
    return found if found is not None else kind()


def snapshot() -> dict[type, Any]:
    """The options as they are (a test keeps what the module configured: `restore`)."""
    return dict(_options)


def restore(before: dict[type, Any]) -> None:
    _options.clear()
    _options.update(before)


def reset() -> None:
    """Back to the defaults (tests)."""
    _options.clear()


__all__ = ["AuditingOptions", "AuthenticationOptions", "ClockOptions", "CorrelationOptions", "ExceptionHandlingOptions", "LoggingOptions", "ResourceOptions",
           "configure", "options",
           "replace", "reset", "restore", "snapshot", "field"]
