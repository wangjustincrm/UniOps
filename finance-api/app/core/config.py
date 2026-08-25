from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    service_name: str = "finance-core"
    port: int = 8004
    epms_api_url: str = "http://localhost:8000/api/v1"
    budget_api_url: str = "http://localhost:8007"
    # NC65 read-only connection (sync button). All-or-nothing: nc_configured()
    # in services/nc_sync.py hides the feature when any of these is missing.
    nc_host: str | None = None
    nc_port: int = 1521
    nc_service: str | None = None
    nc_user: str | None = None
    nc_password: str | None = None
    # 部署级总开关。关掉 = 这个进程不起调度循环(手动触发不受影响)。
    # 与 epms-api 的同名设置一一对应。
    nc_sync_scheduler_enabled: bool = True
    allowed_origins: list[str] = [
        "http://localhost:5173", "http://localhost:5174",
        "http://localhost:5175", "http://localhost:5176", "http://localhost:3000",
        "http://localhost:5177",  # Finance frontend
        "http://localhost:5178",  # Booking frontend
    ]

    model_config = {"env_file": ".env"}


settings = Settings()
