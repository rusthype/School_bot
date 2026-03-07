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


async def add_indexes() -> None:
    settings = Settings()
    dsn = _normalize_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)

    indexes = [
        # Users
        "CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);",
        "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);",
        "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);",
        "CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);",
        "CREATE INDEX IF NOT EXISTS ix_users_role_created ON users(role, created_at);",
        # Profiles
        "CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON profiles(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_profiles_school_id ON profiles(school_id);",
        "CREATE INDEX IF NOT EXISTS idx_profiles_is_approved ON profiles(is_approved);",
        "CREATE INDEX IF NOT EXISTS idx_profiles_registered_at ON profiles(registered_at);",
        # Tasks
        "CREATE INDEX IF NOT EXISTS idx_tasks_teacher_id ON tasks(teacher_id);",
        "CREATE INDEX IF NOT EXISTS idx_tasks_poll_message_id ON tasks(poll_message_id);",
        "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);",
        # Book orders
        "CREATE INDEX IF NOT EXISTS idx_orders_teacher_id ON book_orders(teacher_id);",
        "CREATE INDEX IF NOT EXISTS idx_orders_status ON book_orders(status);",
        "CREATE INDEX IF NOT EXISTS idx_orders_created_at ON book_orders(created_at);",
        "CREATE INDEX IF NOT EXISTS idx_orders_delivery_date ON book_orders(delivery_date);",
        "CREATE INDEX IF NOT EXISTS idx_orders_delivery_deadline ON book_orders(delivery_deadline);",
        "CREATE INDEX IF NOT EXISTS idx_orders_escalated ON book_orders(escalated);",
        "CREATE INDEX IF NOT EXISTS idx_orders_delivered_at ON book_orders(delivered_at);",
        "CREATE INDEX IF NOT EXISTS idx_orders_delivered_by ON book_orders(delivered_by);",
        "CREATE INDEX IF NOT EXISTS idx_orders_priority ON book_orders(priority);",
        "CREATE INDEX IF NOT EXISTS idx_orders_updated_at ON book_orders(updated_at);",
        "CREATE INDEX IF NOT EXISTS idx_orders_updated_by ON book_orders(updated_by);",
        "CREATE INDEX IF NOT EXISTS ix_orders_status_created ON book_orders(status, created_at);",
        # Poll votes
        "CREATE INDEX IF NOT EXISTS idx_poll_votes_poll_message_id ON poll_votes(poll_message_id);",
        "CREATE INDEX IF NOT EXISTS idx_poll_votes_poll_id ON poll_votes(poll_id);",
        "CREATE INDEX IF NOT EXISTS idx_poll_votes_user_id ON poll_votes(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_poll_votes_task_id ON poll_votes(task_id);",
        "CREATE INDEX IF NOT EXISTS idx_poll_votes_option_id ON poll_votes(option_id);",
        "CREATE INDEX IF NOT EXISTS idx_poll_votes_voted_at ON poll_votes(voted_at);",
        "CREATE INDEX IF NOT EXISTS ix_poll_votes_user_poll ON poll_votes(user_id, poll_id);",
        "CREATE INDEX IF NOT EXISTS ix_poll_votes_task_option ON poll_votes(task_id, option_id);",
        # Schools
        "CREATE INDEX IF NOT EXISTS idx_schools_number ON schools(number);",
        # Groups
        "CREATE INDEX IF NOT EXISTS idx_groups_name ON groups(name);",
        "CREATE INDEX IF NOT EXISTS idx_groups_school_id ON groups(school_id);",
        # Books
        "CREATE INDEX IF NOT EXISTS idx_books_category_id ON books(category_id);",
        "CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);",
        "CREATE INDEX IF NOT EXISTS idx_books_is_available ON books(is_available);",
        # Categories
        "CREATE INDEX IF NOT EXISTS idx_categories_name ON book_categories(name);",
        # Support tickets
        "CREATE INDEX IF NOT EXISTS idx_tickets_number ON support_tickets(ticket_number);",
        "CREATE INDEX IF NOT EXISTS idx_tickets_user_id ON support_tickets(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_tickets_status ON support_tickets(status);",
        "CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON support_tickets(created_at);",
        # Order status history
        "CREATE INDEX IF NOT EXISTS idx_order_status_history_order ON order_status_history(order_id);",
        "CREATE INDEX IF NOT EXISTS idx_order_status_history_new_status ON order_status_history(new_status);",
        "CREATE INDEX IF NOT EXISTS idx_order_status_history_changed_by ON order_status_history(changed_by);",
        "CREATE INDEX IF NOT EXISTS idx_order_status_history_changed_at ON order_status_history(changed_at);",
    ]

    for ddl in indexes:
        try:
            await conn.execute(ddl)
            print(f"✅ {ddl}")
        except Exception as exc:
            print(f"❌ {ddl}\n   Error: {exc}")

    await conn.close()
    print("\n✅ Barcha indekslar qo'shildi!")


if __name__ == "__main__":
    asyncio.run(add_indexes())
