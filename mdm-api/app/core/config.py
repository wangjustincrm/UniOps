from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    service_name: str = "mdm-stub"
    port: int = 8002

    # ERP integration
    erp_base_url: str = "http://10.10.95.66"
    erp_timeout_seconds: float = 60.0

    allowed_origins: list[str] = [
        "http://localhost:5173", "http://localhost:5174",
        "http://localhost:5175", "http://localhost:5176", "http://localhost:3000",
    ]

    model_config = {"env_file": ".env"}


settings = Settings()
