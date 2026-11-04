"""Service configuration, read from environment variables or a local `.env` file."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Portal access
    portal_url: str = "https://urja-ops.flockenergy.tech"
    portal_username: str
    portal_password: SecretStr

    # Portal client behaviour
    portal_timeout_seconds: float = 15.0
    portal_max_attempts: int = 3  # per request, for transient failures (timeouts, 502/503/504)
    portal_retry_backoff_seconds: float = 0.5  # doubles on each retry
    portal_max_concurrency: int = 4  # be gentle with the legacy portal
    # The portal's session cookie lives 3600 s. Log in again shortly before that instead of waiting to fail.
    portal_session_max_age_seconds: int = 3300

    # Caching
    metadata_ttl_seconds: int = 900  # meters, hierarchy, transformers (one export + DT list)
    readings_ttl_seconds: int = 300  # per-meter energy readings
    refresh_retry_seconds: int = 30  # after a failed refresh, serve stale data this long before retrying
    warm_cache_on_startup: bool = True
