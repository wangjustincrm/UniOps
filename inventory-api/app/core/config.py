from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "UniOps Inventory API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # ── API ──────────────────────────────────────────────────────────────────
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5174",  # UniOps Portal
        "http://localhost:5179",  # MRP frontend
        "http://localhost:5181",  # Inventory frontend
        "http://localhost:3000",
    ]

    # ── Database ─────────────────────────────────────────────────────────────
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

    # ── NC65 Oracle read-only connection (thin mode) ─────────────────────────
    # Same variable names as epms-api / mdm-api / finance-api, so compose can
    # hand every service the one NC65_* block from .env.
    nc_host: str | None = None
    nc_port: int = 1521
    nc_service: str | None = None
    nc_user: str | None = None
    nc_password: str | None = None

    # Which slice of NC is "Canada". Three modules, three different keys for
    # the same company (verified 2026-10-14, see the design doc §4.1):
    #   IC (库存管理) filters on the stock organisation,
    #   IA (存货核算) on the cost region,
    #   GL on the accounting book — which IA also carries as pk_book.
    # ★ WMS's CUSTOMERID '10024' is NOT an NC organisation code.
    NC_STOCK_ORG: str = "0001A1100000000034BL"
    NC_COST_REGION: str = "1001A1100000003CFXTH"
    NC_ACCOUNTING_BOOK: str = "1001A1100000003CGCBX"
    # Inventory-type GL accounts, by code prefix. 1471 (跌价准备) is mirrored
    # but shown as its own line, never compared per material (design D7).
    NC_GL_INVENTORY_ACCOUNT_PREFIXES: list[str] = [
        "1402", "1403", "1404", "1405", "1406", "1407", "1408", "1471",
    ]

    # ── WMS (Flux) Oracle read-only connection — THICK mode ─────────────────
    # Flux runs Oracle <=11g; python-oracledb thin mode refuses it (DPY-3010),
    # hence the Instant Client baked into the Dockerfile (same as mrp-api).
    # inventory-api reads only the transaction log and lot attributes here;
    # the current-stock mirror stays mrp-api's (design D2/D2').
    wms_host: str | None = None
    wms_port: int = 1521
    wms_service: str | None = None
    wms_user: str | None = None
    wms_password: str | None = None
    oracle_client_lib: str = "/opt/oracle/instantclient_19_28"
    WMS_WAREHOUSE: str = "CANADA"
    WMS_CUSTOMER: str = "10024"

    # ── NC sync scheduler ────────────────────────────────────────────────────
    # The interval is the `nc_sync_interval_minutes` parameter (Settings page),
    # not an env var — same split as mrp-api's WMS scheduler. These two are
    # deployment concerns: dev stacks switch the loop off so every developer
    # machine does not pull from NC alongside production.
    INV_NC_SYNC_SCHEDULER_ENABLED: bool = True
    INV_NC_SYNC_TICK_SECONDS: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
