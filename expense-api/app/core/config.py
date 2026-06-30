from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Expense API"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://epms:epms_dev@localhost:5432/epms"
    jwt_secret_key: str  # required — no default (fail-closed; set via env/.env)
    jwt_algorithm: str = "HS256"

    # Cross-service URLs
    approval_engine_url: str = "http://localhost:8003/approval/v1"
    file_server_url: str = "http://localhost:8005/files/v1"
    epms_api_url: str = "http://localhost:8000/api/v1"
    budget_api_url: str = "http://localhost:8007"
    finance_api_url: str = "http://localhost:8004"

    # Claude API for OCR
    anthropic_api_key: str = ""

    port: int = 8006
    service_name: str = "expense-api"

    # Browser origins allowed by CORS (override via ALLOWED_ORIGINS env, JSON array)
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
    ]


settings = Settings()
