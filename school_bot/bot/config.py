from __future__ import annotations

from typing import List, Dict
import json

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    database_url: str = Field(alias="DATABASE_URL")

    # Guruhlar JSON formatida: {"1-sinf": -10012345, "2-sinf": -10067890}
    groups_raw: str = Field(default="{}", alias="GROUPS")

    superuser_ids_raw: str = Field(default="", alias="SUPERUSER_IDS")

    @property
    def superuser_ids(self) -> List[int]:
        raw = (self.superuser_ids_raw or "").strip()
        if not raw:
            return []
        parts = [p.strip() for p in raw.split(",")]
        return [int(p) for p in parts if p]

    @property
    def groups(self) -> Dict[str, int]:
        """Guruhlar lug'ati: {nomi: chat_id}"""
        try:
            return json.loads(self.groups_raw)
        except:
            return {}
