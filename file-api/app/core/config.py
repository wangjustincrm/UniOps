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
        "http://localhost:5179",  # MRP frontend
        "http://localhost:5180",  # Safety frontend
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
    # Accepted when the upload names that service, on top of the list above.
    # Safety's training material is slides and short videos (round 7:
    # "attaching a 19 meg video returns … Unsupported file type 'video/mp4'").
    # ★ This keeps the other modules' screens from offering these types; it is
    # not a security boundary. `service` is a query parameter the caller sets,
    # so any signed-in user can name "ehs" (raised in review by uniops-55).
    # Accepted as it stands because nothing here can run in a browser: the
    # files are served as downloads under their own names, and the size limit
    # applies as to everything else. Proving the caller is ehs-api would need a
    # service credential, which is a deployment change of its own.
    SERVICE_EXTRA_CONTENT_TYPES: dict[str, list[str]] = {
        "ehs": [
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
            "application/vnd.ms-powerpoint",  # .ppt
            "video/mp4", "video/quicktime", "video/webm",
            "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/wav", "audio/x-wav",
        ],
    }

    @property
    def database_url_sync(self) -> str:
        return self.DATABASE_URL.replace("+asyncpg", "+psycopg2")


@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
