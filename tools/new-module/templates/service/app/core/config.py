from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "%%service_title%%"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    # "test" makes the composition root use in-memory fakes for every gateway.
    ENVIRONMENT: str = "development"

    # ── API ──────────────────────────────────────────────────────────────────
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5174",  # UniOps Portal
        "http://localhost:%%web_port%%",  # %%label%% frontend
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
    %%env_prefix%%_SCHEDULER_ENABLED: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
