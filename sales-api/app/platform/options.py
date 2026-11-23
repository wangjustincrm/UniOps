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


_options: dict[type, Any] = {}


def configure(*opts: Any) -> None:
    """Set the options (the composition root, once at startup; a test may set its own)."""
    for o in opts:
        _options[type(o)] = o


def options(kind: type[T]) -> T:
    """The configured options of this kind, else its defaults."""
    found = _options.get(kind)
    return found if found is not None else kind()


def reset() -> None:
    """Back to the defaults (tests)."""
    _options.clear()


__all__ = ["AuditingOptions", "CorrelationOptions", "ExceptionHandlingOptions", "LoggingOptions", "configure", "options",
           "replace", "reset", "field"]
