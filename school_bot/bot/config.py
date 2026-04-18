from __future__ import annotations

from typing import List
import json

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    alochi_db_url: str = Field(alias="ALOCHI_DB_URL")
    # DEPRECATED: DATABASE_URL was the old standalone school_bot_db connection.
    # Kept as optional fallback during transition. Remove after migration is confirmed stable.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    superadmin_ids_raw: str = Field(default="", alias="SUPERADMIN_IDS")
    teacher_ids_raw: str = Field(default="", alias="TEACHER_IDS")
    admin_group_id: int | None = Field(default=None, alias="ADMIN_GROUP_ID")

    # Mandatory channel users must subscribe to before using /start.
    # Leave empty string to disable the subscription gate. Bot MUST be admin in this channel.
    required_channel: str = Field(default="@alochi_offline", alias="REQUIRED_CHANNEL")

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    log_max_size_mb: int = Field(default=10, alias="LOG_MAX_SIZE_MB")
    log_cleanup_days: int = Field(default=30, alias="LOG_CLEANUP_DAYS")

    @property
    def superadmin_ids(self) -> List[int]:
        raw = (self.superadmin_ids_raw or "").strip()
        if not raw:
            return []
        parts = [p.strip() for p in raw.split(",")]
        return [int(p) for p in parts if p]

    @property
    def teacher_ids(self) -> List[int]:
        raw = (self.teacher_ids_raw or "").strip()
        if not raw:
            return []
        parts = [p.strip() for p in raw.split(",")]
        return [int(p) for p in parts if p]

    # Groups are managed in the database; no GROUPS env parsing needed.
