from __future__ import annotations

import asyncio

import asyncpg

from school_bot.bot.config import Settings


def _normalize_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    if dsn.startswith("postgres+asyncpg://"):
        return dsn.replace("postgres+asyncpg://", "postgresql://", 1)
    return dsn


async def add_columns() -> None:
    settings = Settings()
    dsn = _normalize_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)

    statements = [
        "ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS delivery_deadline TIMESTAMPTZ;",
        "ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS escalated BOOLEAN DEFAULT false;",
        "CREATE INDEX IF NOT EXISTS idx_orders_delivery_deadline ON book_orders(delivery_deadline);",
        "CREATE INDEX IF NOT EXISTS idx_orders_escalated ON book_orders(escalated);",
        "UPDATE book_orders SET delivery_deadline = created_at + interval '7 days' "
        "WHERE delivery_deadline IS NULL;",
        "UPDATE book_orders SET escalated = false WHERE escalated IS NULL;",
    ]

    for sql in statements:
        try:
            await conn.execute(sql)
            print(f"✅ {sql}")
        except Exception as exc:
            print(f"❌ {sql}\n   Error: {exc}")

    await conn.close()
    print("\n✅ Overdue columns added/backfilled.")


if __name__ == "__main__":
    asyncio.run(add_columns())
