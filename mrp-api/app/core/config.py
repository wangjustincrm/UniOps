from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "UniOps MRP API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # ── API ──────────────────────────────────────────────────────────────────
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5173",  # EPMS frontend
        "http://localhost:5174",  # UniOps Portal
        "http://localhost:5175",  # OA frontend
        "http://localhost:5176",  # VMS frontend
        "http://localhost:5177",  # Finance frontend
        "http://localhost:5178",  # Booking frontend
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

    # ── Cross-service URLs ────────────────────────────────────────────────────
    APPROVAL_API_URL: str = "http://localhost:8003/approval/v1"
    EPMS_API_URL: str = "http://localhost:8000"

    # ── WMS (Flux) Oracle read-only connection ──────────────────────────────────
    # The Flux WMS Oracle server is <=11g — python-oracledb's thin mode refuses
    # to connect to it (DPY-3010), so mrp-api's WMS sync must use thick mode via
    # an Instant Client (see Dockerfile / oracle_client_lib below).
    wms_host: str | None = None
    wms_port: int = 1521
    wms_service: str | None = None
    wms_user: str | None = None
    wms_password: str | None = None

    oracle_client_lib: str = "/opt/oracle/instantclient_19_28"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
