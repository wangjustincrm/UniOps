from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "EPMS API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"   # development | staging | production

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

    # ── Redis ────────────────────────────────────────────────────────────────
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ── JWT ──────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str  # required — no default (fail-closed; set via env/.env)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Email (SMTP) ─────────────────────────────────────────────────────────
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025          # MailHog / local dev default
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = False
    SMTP_FROM: str = "noreply@epms.local"

    # ── MFA (Email OTP) ──────────────────────────────────────────────────────
    MFA_OTP_TTL_SECONDS: int = 600   # 10 minutes

    # ── Approval Engine ───────────────────────────────────────────────────────
    APPROVAL_ENGINE_URL: str = "http://localhost:8003/approval/v1"

    # ── File Server ───────────────────────────────────────────────────────────
    FILE_SERVER_URL: str = "http://localhost:8005/files/v1"

    # ── Budget Microservice ──────────────────────────────────────────────────
    BUDGET_API_URL: str = "http://localhost:8007"

    # ── Finance Core (unified payment executor) ──────────────────────────────
    FINANCE_API_URL: str = "http://localhost:8004"

    # ── Identity service (auth moved there in Phase 0-B4; /auth/* proxies) ───
    IDENTITY_API_URL: str = "http://localhost:8009"

    # ── MDM Microservice ─────────────────────────────────────────────────────
    MDM_API_URL: str = "http://localhost:8002"
    MDM_API_TIMEOUT_SECONDS: float = 30.0

    # ── Legacy PMS (SharePoint) import — used by scripts.import_pms and the ──────
    #    admin "PMS Import" page. Leave blank to disable the extract phase. ───────
    SP_USER: str = ""
    SP_PASSWORD: str = ""
    SP_TENANT: str = "canadaroyalmilk.ca"
    SP_SITE: str = "https://canadaroyalmilk.sharepoint.com/sites/pr2"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# Standard initial password assigned to every new / imported account. Users are
# forced to change it on first login (must_change_password=True). Kept in sync
# with the frontend INITIAL_PASSWORD constant in epms AdminPanel.
INITIAL_PASSWORD = "Feihe12#$"
