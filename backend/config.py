"""Application configuration powered by Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed runtime settings for the BI Data Agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Autonomous BI & Data Analysis Agent")
    app_version: str = Field(default="1.0.0")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.8-flash", validation_alias="GEMINI_MODEL")
    database_path: Path = Field(
        default=Path("data/warehouse.db"),
        validation_alias="DATABASE_PATH",
    )
    max_query_rows: int = Field(default=500, ge=1, le=10000)
    max_agent_turns: int = Field(default=8, ge=2, le=20)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
