"""ehs-api settings (loaded from env, .env, or docker-compose).

Shape mirrors booking-api / vms-api so tooling (check-health.sh, compose
healthchecks, Portal module card) treats Safety like any other UniOps service.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "Safety API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Statutory clocks are computed in Ontario local time (business-day
    # arithmetic must respect the province's holidays and its calendar days).
    # Values are stored as timestamptz; this is the zone they are reasoned in.
    DISPLAY_TIMEZONE: str = "America/Toronto"

    # ── API ──────────────────────────────────────────────────────────────────
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5173",  # EPMS frontend
        "http://localhost:5174",  # UniOps Portal
        "http://localhost:5175",  # OA frontend
        "http://localhost:5176",  # VMS frontend
        "http://localhost:5177",  # Finance frontend
        "http://localhost:5178",  # Booking frontend
        "http://localhost:5179",  # MRP frontend
        "http://localhost:5180",  # Safety frontend
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
    MDM_API_URL: str = "http://localhost:8002/mdm/v1"
    APPROVAL_ENGINE_URL: str = "http://localhost:8003/approval/v1"
    FILE_SERVER_URL: str = "http://localhost:8005/files/v1"
    SAFETY_WEB_URL: str = "http://localhost:5180"

    # ── Statutory deadline scanner ──────────────────────────────────────────
    # In-process asyncio loop started in the FastAPI lifespan. The interval
    # itself lives in ehs_config and is re-read on every tick (0 = disabled);
    # this only controls how often the loop wakes to look.
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_TICK_SECONDS: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
