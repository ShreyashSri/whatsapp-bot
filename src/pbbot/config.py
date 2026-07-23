from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    port: int = 3000
    openwa_webhook_secret: str = ""
    command_prefixes: Annotated[tuple[str, ...], NoDecode] = ("/", "!")

    @field_validator("command_prefixes", mode="before")
    @classmethod
    def parse_command_prefixes(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(prefix.strip() for prefix in value.split(",") if prefix.strip())
        return value

    @property
    def require_webhook_signature(self) -> bool:
        return self.app_env.lower() == "production" or bool(self.openwa_webhook_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
