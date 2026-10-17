from ipaddress import ip_network
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CARTALYTIX_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = Field(
        default="postgresql+psycopg://cartalytix@localhost/cartalytix",
        validation_alias=AliasChoices("CARTALYTIX_DATABASE_URL", "DATABASE_URL", "database_url"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("CARTALYTIX_REDIS_URL", "REDIS_URL", "redis_url"),
    )
    allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]
    trusted_proxy_hosts: list[str] = []
    cookie_secure: bool = True
    session_hours: int = Field(default=24, ge=1, le=168)
    login_attempts: int = Field(default=10, ge=1, le=100)
    login_window_seconds: int = Field(default=900, ge=60)
    token_key_file: str | None = None

    @model_validator(mode="after")
    def secure_production(self) -> Settings:
        for proxy in self.trusted_proxy_hosts:
            network = ip_network(proxy, strict=False)
            if network.prefixlen == 0:
                raise ValueError("Proxy trust must name specific networks or addresses")
        if self.environment == "production" and (
            not self.cookie_secure
            or any(not origin.startswith("https://") for origin in self.allowed_origins)
        ):
            raise ValueError("Production requires secure cookies and HTTPS origins")
        if "cjf_prod" in self.database_url.casefold():
            raise ValueError("Forbidden database")
        return self
