#!/usr/bin/env python3
"""
Migration script to add profile_type column to profiles table.
Run this from the project root directory.
"""
from __future__ import annotations

import sys
from pathlib import Path
import asyncio
import asyncpg


project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from school_bot.bot.config import Settings  # noqa: E402


async def add_profile_type() -> None:
    settings = Settings()
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)

    await conn.execute(
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS profile_type TEXT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profiles_profile_type ON profiles(profile_type)"
    )

    await conn.close()
    print("✅ profile_type column added (if missing).")


if __name__ == "__main__":
    asyncio.run(add_profile_type())
