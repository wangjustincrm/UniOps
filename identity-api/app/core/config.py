"""Identity service settings — shares DB / JWT secret / Redis with the stack."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Identity API"
    SERVICE_NAME: str = "identity-api"
    PORT: int = 8009

    DATABASE_URL: str = "postgresql+asyncpg://epms:epms_dev@localhost:5432/epms"

    JWT_SECRET_KEY: str  # required — no default (fail-closed; set via env/.env)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5173", "http://localhost:5174",
        "http://localhost:5175", "http://localhost:5176", "http://localhost:3000",
    ]

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # SMTP fallbacks — DB-stored company_config overrides win (same as epms)
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = False
    SMTP_FROM: str = "noreply@uniops.local"

    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


settings = Settings()
