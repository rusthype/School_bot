#!/usr/bin/env python3
"""
Migration script to add bot_settings table.
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


async def add_bot_settings() -> None:
    settings = Settings()
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            bot_name VARCHAR(255),
            bot_version VARCHAR(50),
            language VARCHAR(10) DEFAULT 'uz',
            work_start_mon_fri VARCHAR(5) DEFAULT '08:00',
            work_end_mon_fri VARCHAR(5) DEFAULT '18:00',
            work_start_sat VARCHAR(5) DEFAULT '09:00',
            work_end_sat VARCHAR(5) DEFAULT '14:00',
            work_sun BOOLEAN DEFAULT FALSE,
            notify_homework BOOLEAN DEFAULT TRUE,
            notify_announcements BOOLEAN DEFAULT TRUE,
            notify_stats BOOLEAN DEFAULT FALSE,
            notify_marketing BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )

    await conn.execute(
        """
        INSERT INTO bot_settings (
            id,
            bot_version,
            language,
            work_start_mon_fri,
            work_end_mon_fri,
            work_start_sat,
            work_end_sat,
            work_sun,
            notify_homework,
            notify_announcements,
            notify_stats,
            notify_marketing
        )
        VALUES (
            1,
            'v2.1.0',
            'uz',
            '08:00',
            '18:00',
            '09:00',
            '14:00',
            FALSE,
            TRUE,
            TRUE,
            FALSE,
            FALSE
        )
        ON CONFLICT (id) DO NOTHING
        """
    )

    await conn.execute(
        """
        UPDATE bot_settings
        SET
            language = COALESCE(language, 'uz'),
            work_start_mon_fri = COALESCE(work_start_mon_fri, '08:00'),
            work_end_mon_fri = COALESCE(work_end_mon_fri, '18:00'),
            work_start_sat = COALESCE(work_start_sat, '09:00'),
            work_end_sat = COALESCE(work_end_sat, '14:00'),
            work_sun = COALESCE(work_sun, FALSE),
            notify_homework = COALESCE(notify_homework, TRUE),
            notify_announcements = COALESCE(notify_announcements, TRUE),
            notify_stats = COALESCE(notify_stats, FALSE),
            notify_marketing = COALESCE(notify_marketing, FALSE)
        WHERE id = 1
        """
    )

    await conn.close()
    print("✅ bot_settings table created (if missing).")


if __name__ == "__main__":
    asyncio.run(add_bot_settings())
