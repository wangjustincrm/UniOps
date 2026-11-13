from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "UniOps Sales API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    # "test" makes the composition root use in-memory fakes for every gateway.
    ENVIRONMENT: str = "development"

    # ── API ──────────────────────────────────────────────────────────────────
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5174",  # UniOps Portal
        "http://localhost:5182",  # Sales frontend
        "http://localhost:3000",
    ]

    # ── Database (POSTGRES_* only — DATABASE_URL is derived) ─────────────────
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "epms"
    POSTGRES_PASSWORD: str = "epms_dev"
    POSTGRES_DB: str = "epms"

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def DATABASE_URL_SYNC(self) -> str:
        """Sync URL used by Alembic migrations."""
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ── JWT (shared with other services) ──────────────────────────────────────
    JWT_SECRET_KEY: str  # required — no default (fail-closed; set via env/.env)
    JWT_ALGORITHM: str = "HS256"

    # ── Scheduler (deployment switch; dev stacks keep it off) ────────────────
    SALES_SCHEDULER_ENABLED: bool = False

    # ── Prod-only outbound switches (design §5.5) ────────────────────────────
    # Both false everywhere except .env.prod. While false, every external
    # email and every partner write (SFTP put/move/delete, Shopify write) is
    # refused and logged as "would have sent". Read ONLY by the gateway guard
    # (app/container.py wires the names into app.platform's GuardedGateway).
    SALES_EXTERNAL_EMAIL_ENABLED: bool = False
    SALES_PARTNER_WRITES_ENABLED: bool = False
    # Internal email (user 2026-11-09): mail whose every recipient is in SALES_INTERNAL_EMAIL_DOMAINS
    # (the company's own domains, comma-separated). Its own switch, so a test stack can mail staff
    # (invoice copies, notifications) while partners and customers get nothing. Empty domains: none.
    SALES_INTERNAL_EMAIL_ENABLED: bool = False
    SALES_INTERNAL_EMAIL_DOMAINS: str = ""
    # Where the Sales front end is reached from a mail client (a notification's link); empty: no link.
    SALES_PUBLIC_URL: str = ""
    # The company's logo on printed documents: epms-api's public branding (what the sidebar shows).
    # Empty: documents print without a logo. Read at most every 10 minutes; unreachable → no logo.
    SALES_BRANDING_URL: str = "http://epms-api:8000/api/v1/config/public/branding?module=sales"

    # ── SFTP hosts this stack may connect to (fail closed, every environment) ─
    # Comma-separated host names. Empty or unset: no SFTP connection at all —
    # also in production, which lists its partner hosts explicitly (product
    # owner, 2026-10-29). Not keyed on ENVIRONMENT, so a mis-set ENVIRONMENT
    # cannot open it; dev stacks list only their test container.
    SALES_SFTP_ALLOWED_HOSTS: str = ""

    @property
    def sftp_allowed_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.SALES_SFTP_ALLOWED_HOSTS.split(",") if h.strip())

    # ── SFTP hosts a put / move / delete may touch (fail closed) ─────────────
    # The third guard on partner writes, beside SALES_PARTNER_WRITES_ENABLED and
    # the connection's Write tick (P4, 2026-10-30). Empty or unset: no SFTP write
    # anywhere. A host may be readable (SALES_SFTP_ALLOWED_HOSTS) and still not
    # writable: dev lists only its test container; production lists Radiant at
    # release (DEPLOYMENT.md).
    SALES_SFTP_WRITE_HOSTS: str = ""
    # Shadow run (§5.4): a read-only folder in the container holding files
    # Zoho sent (940s, later 810s / 943s), copied there by hand. Each is
    # stored once as a ZOHO_REFERENCE document — never parsed into orders,
    # never sent. Empty = the import step is off.
    SALES_ZOHO_REFERENCE_DIR: str = ""
    # P6: the external mail account (spec X3, revised: env; the password is
    # the SALES_SECRET_SMTP variable, read when sending). Empty host = no mail
    # server: a send fails NotAccepted (and the switch is off outside prod).
    SALES_SMTP_HOST: str = ""
    SALES_SMTP_PORT: int = 587
    SALES_SMTP_USER: str = ""
    SALES_SMTP_FROM: str = ""
    SALES_SMTP_STARTTLS: bool = True

    @property
    def internal_email_domains(self) -> frozenset[str]:
        return frozenset(d.strip().lower().lstrip("@") for d in self.SALES_INTERNAL_EMAIL_DOMAINS.split(",") if d.strip())

    @property
    def sftp_write_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.SALES_SFTP_WRITE_HOSTS.split(",") if h.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
