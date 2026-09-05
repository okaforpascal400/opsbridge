"""Application settings for OpsBridge.

Every environment-specific value is read here and nowhere else. Modules import
`get_settings()` instead of reading `os.environ` directly, so there is exactly
one place to look when a value is wrong.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+psycopg2://opsbridge:opsbridge@localhost:5432/opsbridge"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"


class Settings(BaseSettings):
    """Settings sourced from the process environment, falling back to .env.

    Field names map to upper-case environment variables: `database_url` reads
    `DATABASE_URL`. Matching is case-insensitive, so no aliases are needed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = DEFAULT_DATABASE_URL
    anthropic_api_key: str = ""
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once per process."""
    return Settings()
