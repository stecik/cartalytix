from ipaddress import ip_network
from typing import Literal

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator, model_validator
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
    # A remembered sign-in expires after this many days without activity.
    remember_days: int = Field(default=30, ge=1, le=365)
    login_attempts: int = Field(default=10, ge=1, le=100)
    login_window_seconds: int = Field(default=900, ge=60)
    token_key_file: str | None = None
    bootstrap_password: SecretStr | None = None
    albert_client_username: SecretStr | None = None
    albert_client_password: SecretStr | None = None
    albert_verification_salt: SecretStr | None = None
    albert_login_salt: SecretStr | None = None
    # Sync runs that imported receipts are kept forever; the rest only this many days.
    sync_run_empty_retention_days: int = Field(default=14, ge=1)
    sync_run_failed_retention_days: int = Field(default=90, ge=1)
    # Public origin of this instance; enables the MCP connector. Never derived from Host headers.
    public_url: AnyHttpUrl | None = None

    @property
    def albert_configured(self) -> bool:
        return all(
            (
                self.albert_client_username,
                self.albert_client_password,
                self.albert_verification_salt,
                self.albert_login_salt,
            )
        )

    @field_validator(
        "albert_client_username",
        "albert_client_password",
        "albert_verification_salt",
        "albert_login_salt",
        mode="before",
    )
    @classmethod
    def optional_albert_secret(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("public_url", mode="before")
    @classmethod
    def optional_public_url(cls, value: object) -> object:
        # Compose passes an unset variable as an empty string.
        return None if value == "" else value

    @model_validator(mode="after")
    def secure_production(self) -> Settings:
        if self.public_url is not None:
            url = self.public_url
            if url.path not in (None, "", "/") or url.query or url.fragment or url.username:
                raise ValueError("Public URL must be an origin without path, query or credentials")
            local = url.host in {"localhost", "127.0.0.1", "[::1]"}
            if url.scheme != "https" and (self.environment == "production" or not local):
                raise ValueError("Public URL must use HTTPS")
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
