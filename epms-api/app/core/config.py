from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from urllib.parse import quote


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

    # ── Module frontend URLs (task-notification deep-links) ───────────────────
    # The daily-followup / task notifier is SYSTEM-WIDE: the shared tasks table
    # holds EPMS (pr/po/pa/gr/invoice), OA (pa_dir + expense claims), Finance
    # (budget_plan) and VMS (vms_*) tasks. Each email link must point back at its
    # OWN module's frontend, so the notifier resolves the base URL per
    # document_type from these (see services/notification.py:_task_link).
    # Injected from the release env (EPMS_URL/OA_URL/…); defaults are dev ports.
    EPMS_URL: str = "http://localhost:5173"
    OA_URL: str = "http://localhost:5175"
    VMS_URL: str = "http://localhost:5176"
    # Safety frontend — used by _task_link to deep-link Safety notifications.
    SAFETY_URL: str = "http://localhost:5180"
    FINANCE_URL: str = "http://localhost:5177"
    BOOKING_URL: str = "http://localhost:5178"
    PORTAL_URL: str = "http://localhost:5174"

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
    # Optional — empty string (default) means unauthenticated, same as before
    # this field existed. Only set once `requirepass` is actually configured
    # on the Redis instance (see REDIS_URL below for why empty must stay a no-op).
    REDIS_PASSWORD: str = ""

    @property
    def REDIS_URL(self) -> str:
        # ⚠️ 这份逻辑在 identity-api/app/core/config.py 有一份同样的拷贝 —— 改这里必须同时改那里。
        # (2026-07-16 的教训:identity 的 email.py 是 epms 的拷贝,epms 修了 TLS 分流而拷贝没跟上,
        #  结果同一台邮件服务器 epms 发得出、identity 发不出,查了很久。拷贝会漂移。)
        #
        # Backward-compat is load-bearing: when REDIS_PASSWORD is unset/empty,
        # this must produce the exact same unauthenticated URL as before the
        # field existed (2026-07-15 MFA outage — see comment on the field above).
        if not self.REDIS_PASSWORD:
            return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        # quote() escapes @ : / # etc. so a password containing them doesn't
        # break URL parsing (redis.asyncio.from_url must decode back to the
        # original password).
        auth = quote(self.REDIS_PASSWORD, safe="")
        return f"redis://:{auth}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

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

    # ── NC65 Oracle (read-only source for nc_purchase_sync) ─────────────────
    nc_host: str | None = None
    nc_port: int = 1521
    nc_service: str | None = None
    nc_user: str | None = None
    nc_password: str | None = None
    # Earliest NC order date to import ('YYYY-MM-DD HH:MM:SS'). Read by
    # app.services.nc_purchase_sync.service._cutover() — the ONE source of
    # truth for the cutover; None falls back to that module's wide-open
    # default. Do not add a second cutover setting.
    nc_purchase_cutover: str | None = None

    # Whether THIS process runs the NC purchase sync loop. How OFTEN it runs
    # is company config (an admin dial, changeable without a redeploy) — this
    # is the deployment switch: dev containers set it false so a developer
    # machine does not pull NC65 alongside production.
    nc_sync_scheduler_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# Standard initial password assigned to every new / imported account. Users are
# forced to change it on first login (must_change_password=True). Kept in sync
# with the frontend INITIAL_PASSWORD constant in epms AdminPanel.
INITIAL_PASSWORD = "Feihe12#$"
