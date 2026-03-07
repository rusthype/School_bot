#!/usr/bin/env python3
"""
Migration script to add delivery-related columns to book_orders table.
Run this from the project root directory.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg

# Add the project root directory to Python path
PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from school_bot.bot.config import Settings


def _normalize_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    if dsn.startswith("postgres+asyncpg://"):
        return dsn.replace("postgres+asyncpg://", "postgresql://", 1)
    return dsn


async def add_delivery_columns() -> None:
    print("🚀 Starting migration...")
    settings = Settings()
    dsn = _normalize_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)

    statements = [
        "ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS delivery_deadline TIMESTAMPTZ;",
        "ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS escalated BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;",
        "ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS delivered_by INTEGER;",
        "ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'normal';",
        "ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;",
        "ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS updated_by INTEGER;",
        "CREATE INDEX IF NOT EXISTS idx_orders_delivery_deadline ON book_orders(delivery_deadline);",
        "CREATE INDEX IF NOT EXISTS idx_orders_escalated ON book_orders(escalated);",
        "CREATE INDEX IF NOT EXISTS idx_orders_delivered_at ON book_orders(delivered_at);",
        "CREATE INDEX IF NOT EXISTS idx_orders_delivered_by ON book_orders(delivered_by);",
        "CREATE INDEX IF NOT EXISTS idx_orders_priority ON book_orders(priority);",
        "CREATE INDEX IF NOT EXISTS idx_orders_updated_at ON book_orders(updated_at);",
        "CREATE INDEX IF NOT EXISTS idx_orders_updated_by ON book_orders(updated_by);",
        "CREATE TABLE IF NOT EXISTS order_status_history ("
        "id SERIAL PRIMARY KEY, "
        "order_id INTEGER REFERENCES book_orders(id) ON DELETE CASCADE, "
        "old_status VARCHAR(50) NOT NULL, "
        "new_status VARCHAR(50) NOT NULL, "
        "changed_by INTEGER, "
        "changed_at TIMESTAMPTZ DEFAULT now(), "
        "comment TEXT"
        ");",
        "CREATE INDEX IF NOT EXISTS idx_order_status_history_order ON order_status_history(order_id);",
        "CREATE INDEX IF NOT EXISTS idx_order_status_history_new_status ON order_status_history(new_status);",
        "CREATE INDEX IF NOT EXISTS idx_order_status_history_changed_by ON order_status_history(changed_by);",
        "CREATE INDEX IF NOT EXISTS idx_order_status_history_changed_at ON order_status_history(changed_at);",
        "UPDATE book_orders SET delivery_deadline = created_at + interval '7 days' "
        "WHERE delivery_deadline IS NULL;",
        "UPDATE book_orders SET escalated = false WHERE escalated IS NULL;",
        "UPDATE book_orders SET priority = 'normal' WHERE priority IS NULL;",
        "UPDATE book_orders SET updated_at = created_at WHERE updated_at IS NULL;",
    ]

    for sql in statements:
        try:
            await conn.execute(sql)
            print(f"✅ {sql}")
        except Exception as exc:
            print(f"❌ {sql}\n   Error: {exc}")

    await conn.close()
    print("\n✅ All delivery columns added.")


if __name__ == "__main__":
    asyncio.run(add_delivery_columns())
