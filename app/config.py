from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    trimble_client_id: str = ""
    trimble_client_secret: str = ""
    trimble_scope: str = "openid"
    trimble_redirect_uri: str = "http://localhost:8000/auth/trimble/callback"
    trimble_identity_base: str = "https://id.trimble.com"
    trimble_connect_regions_url: str = "https://app.connect.trimble.com/tc/api/2.0/regions"

    token_encryption_key: str = ""
    session_secret: str = ""

    database_path: str = "trimble_mcp.db"
    frontend_url: str = "http://localhost:5173"
    cookie_secure: bool = False
    # Extra Host header values accepted by /mcp (e.g. "mcp-dev.example.com"); localhost is always allowed
    mcp_allowed_hosts: list[str] = []

    http_timeout_seconds: float = 20.0
    http_max_retries: int = 2
    max_pages: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
