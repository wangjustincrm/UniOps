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

    # NC65 read-only connection (BOM raw mirror sync). All-or-nothing: nc_configured()
    # in services/nc_bom_sync/reader.py hides the feature when any of these is missing.
    # Naming/env-var convention copied from finance-api's settings (nc_host/NC_HOST etc,
    # mapped from NC65_* in compose) — epms-api itself has no NC integration.
    nc_host: str | None = None
    nc_port: int = 1521
    nc_service: str | None = None
    nc_user: str | None = None
    nc_password: str | None = None

    allowed_origins: list[str] = [
        "http://localhost:5173", "http://localhost:5174",
        "http://localhost:5175", "http://localhost:5176", "http://localhost:3000",
        "http://localhost:5177",  # Finance frontend
        "http://localhost:5178",  # Booking frontend
    ]

    model_config = {"env_file": ".env"}


settings = Settings()
