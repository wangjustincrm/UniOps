"""vms-api settings (loaded from env, .env, or docker-compose)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "VMS API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Local timezone for human-facing exports (CFIA visit log). Times are
    # stored in UTC; reports render them in this zone. IANA name.
    REPORT_TIMEZONE: str = "America/Toronto"

    # ── API ──────────────────────────────────────────────────────────────────
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5173",  # EPMS frontend
        "http://localhost:5174",  # UniOps Portal
        "http://localhost:5175",  # OA frontend
        "http://localhost:5176",  # VMS frontend
        "http://localhost:5177",  # Finance frontend
        "http://localhost:3000",
    ]

    # ── Database (shared with all UniOps services) ──────────────────────────
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

    # ── JWT (shared secret with epms-api which issues tokens) ───────────────
    JWT_SECRET_KEY: str  # required — no default (fail-closed; set via env/.env)
    JWT_ALGORITHM: str = "HS256"

    # ── Cross-service URLs ──────────────────────────────────────────────────
    EPMS_API_URL: str = "http://localhost:8000/api/v1"
    APPROVAL_ENGINE_URL: str = "http://localhost:8003/approval/v1"
    FILE_SERVER_URL: str = "http://localhost:8005/files/v1"

    # ── Background scheduler (reminders / no-show / overdue alerts) ──────────
    # In-process asyncio loop started in the FastAPI lifespan. Runs the
    # time-based jobs (VMS-PR-012 / -019, VMS-CO-010 / -011). A Postgres
    # advisory lock guards each tick so multi-worker / multi-container
    # deployments don't double-fire. Disable in tests / one-off CLI runs.
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_INTERVAL_SECONDS: int = 900  # 15 min — fine for ~1h granularity


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
