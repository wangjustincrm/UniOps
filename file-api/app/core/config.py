from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    DATABASE_URL: str = "postgresql+asyncpg://epms:epms_dev@localhost:5432/epms"
    JWT_SECRET_KEY: str  # required — no default (fail-closed; set via env/.env)
    JWT_ALGORITHM: str = "HS256"
    STORAGE_ROOT: Path = Path("C:/Project/uniops/file-storage")
    SERVICE_NAME: str = "file-server"
    PORT: int = 8005
    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5173", "http://localhost:5174",
        "http://localhost:5175", "http://localhost:5176", "http://localhost:3000",
        "http://localhost:5177",  # Finance frontend
        "http://localhost:5178",  # Booking frontend
    ]
    # Upload content-type allowlist (documents + images for receipts/POs/invoices).
    ALLOWED_CONTENT_TYPES: list[str] = [
        "application/pdf",
        "image/png", "image/jpeg", "image/gif", "image/webp", "image/heic",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
        "application/vnd.ms-excel",  # .xls
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
        "application/msword",  # .doc
        "text/csv", "text/plain",
        # Saved emails — the only evidence for a vendor credit the vendor will
        # not issue a credit note for (they say "deduct it" by email).
        "application/vnd.ms-outlook",  # .msg (Outlook)
        "message/rfc822",  # .eml
    ]

    @property
    def database_url_sync(self) -> str:
        return self.DATABASE_URL.replace("+asyncpg", "+psycopg2")


@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
