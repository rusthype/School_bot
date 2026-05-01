import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

from school_bot.bot.config import Settings
from school_bot.bot.handlers import (
    admin,
    common,
    teacher,
    group_join,
    librarian,
    book_categories,
    book_management,
    admin_management,
    book_order_cart,
    support,
    superadmin_orders,
    error_handler,
    superadmin_dashboard,
    logs,
    superadmin_settings,
    teacher_attendance,
    superadmin_attendance,
)
from school_bot.bot.middlewares.db_session import DbSessionMiddleware
from school_bot.bot.middlewares.user_context import UserContextMiddleware
from school_bot.bot.middlewares.group_admin_guard import GroupAdminGuardMiddleware
from school_bot.bot.middlewares.menu_guard import MenuGuardMiddleware
from school_bot.bot.middlewares.rate_limit import RateLimitMiddleware
from school_bot.database.session import create_session_factory, init_models
from school_bot.bot.services.user_service import seed_superadmins
from school_bot.bot.services.group_service import seed_groups
from school_bot.bot.services.school_service import seed_schools
from school_bot.bot.services.book_service import seed_book_categories
from school_bot.bot.services.order_escalation_service import start_overdue_order_watch
from school_bot.bot.services.log_cleanup_service import LogCleanupService
from school_bot.bot.services.teacher_notifier import schedule_pending_digests



async def set_bot_commands(bot: Bot, superadmin_ids: list[int]) -> None:
    base_commands = [
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="help", description="Yordam"),
        BotCommand(command="stop", description="Menyuni yopish"),
        BotCommand(command="support", description="Admin bilan bog'lanish"),
        BotCommand(command="order_books", description="Kitob buyurtma qilish"),
        BotCommand(command="my_orders", description="Mening buyurtmalarim"),
    ]

    superadmin_commands = [
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="help", description="Yordam"),
        BotCommand(command="stop", description="Menyuni yopish"),
        BotCommand(command="pending_approvals", description="Tasdiqlanmagan o'qituvchilar"),
        BotCommand(command="kutayotganlar", description="Tasdiqlanmagan o'qituvchilar"),
        BotCommand(command="order_books", description="Kitob buyurtma qilish"),
        BotCommand(command="my_orders", description="Mening buyurtmalarim"),
        BotCommand(command="poll_voters", description="Topshiriq ovozlarini ko'rish"),
        BotCommand(command="orders", description="Buyurtmalar ro'yxati"),
        BotCommand(command="order_stats", description="Buyurtmalar statistikasi"),
        BotCommand(command="pending_orders", description="Kutilayotgan buyurtmalar"),
        BotCommand(command="add_category", description="Kategoriya qo'shish"),
        BotCommand(command="list_categories", description="Kategoriyalar ro'yxati"),
        BotCommand(command="edit_category", description="Kategoriya tahrirlash"),
        BotCommand(command="remove_category", description="Kategoriya o'chirish"),
        BotCommand(command="add_book", description="Kitob qo'shish"),
        BotCommand(command="list_books", description="Kitoblar ro'yxati"),
        BotCommand(command="edit_book", description="Kitob tahrirlash"),
        BotCommand(command="remove_book", description="Kitob o'chirish"),
        BotCommand(command="add_admin", description="Admin qo'shish"),
        BotCommand(command="remove_admin", description="Admin o'chirish"),
        BotCommand(command="list_admins", description="Adminlar ro'yxati"),
        BotCommand(command="edit_admin_role", description="Admin roli"),
        BotCommand(command="all_polls", description="Barcha ovozlar"),
        BotCommand(command="add_teacher_manual", description="O'qituvchi qo'shish (manual)"),
        BotCommand(command="support", description="Admin bilan bog'lanish"),
        BotCommand(command="reply", description="Murojaatga javob berish"),
        BotCommand(command="add_group", description="Guruh qo'shish"),
        BotCommand(command="groups_ids", description="Guruh chat IDlari"),
        BotCommand(command="pending_groups", description="Kutilayotgan guruhlar"),
        BotCommand(command="users", description="Foydalanuvchilar menyusi"),
        BotCommand(command="admin_orders", description="Buyurtmalar"),
        BotCommand(command="attendance_today", description="Bugungi davomat"),
        BotCommand(command="attendance_school", description="Maktab bo'yicha davomat"),
        BotCommand(command="logs", description="Loglarni ko'rish"),
    ]
    try:
        await bot.set_my_commands(commands=base_commands, scope=BotCommandScopeDefault())
        for admin_id in superadmin_ids:
            await bot.set_my_commands(
                commands=superadmin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        logging.info("Commands set successfully")
    except Exception as e:
        logging.warning(f"Could not set commands: {e}")

async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    settings = Settings()

    engine, session_factory = create_session_factory(settings.alochi_db_url)
    await init_models(engine)
    await seed_superadmins(session_factory=session_factory, superadmin_tg_ids=settings.superadmin_ids)
    await seed_schools(session_factory=session_factory)
    await seed_groups(session_factory=session_factory, groups_fallback={})
    await seed_book_categories(session_factory=session_factory)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    async def on_startup(bot: Bot) -> None:
        asyncio.create_task(set_bot_commands(bot, settings.superadmin_ids))
        asyncio.create_task(start_overdue_order_watch(bot=bot, session_factory=session_factory))
        asyncio.create_task(start_log_cleanup_watch(settings))
        # Recover any 24h teacher digests that were scheduled before
        # the previous shutdown. Reads bot_tasks rows where
        # notify_scheduled_at is set AND teacher_notif_message_id is
        # still NULL, then re-schedules an asyncio sleep for the
        # remaining time (or fires immediately if the deadline has
        # already passed). Safe to call alongside fresh scheduling —
        # the per-task fire path is idempotent on
        # teacher_notif_message_id.
        asyncio.create_task(schedule_pending_digests(bot, session_factory))

    dp.startup.register(on_startup)

    dp.message.middleware(RateLimitMiddleware(limit=30, window=60))
    dp.update.middleware(GroupAdminGuardMiddleware())
    dp.update.middleware(DbSessionMiddleware(session_factory=session_factory))
    dp.update.middleware(
        UserContextMiddleware(
            superadmin_ids=settings.superadmin_ids,
            teacher_ids=settings.teacher_ids,
            admin_group_id=settings.admin_group_id,
        )
    )
    dp.update.middleware(MenuGuardMiddleware())

    dp.include_router(admin.router)
    dp.include_router(admin_management.router)
    dp.include_router(teacher.router)
    dp.include_router(librarian.router)
    dp.include_router(book_categories.router)
    dp.include_router(book_management.router)
    dp.include_router(book_order_cart.router)
    dp.include_router(superadmin_orders.router)
    dp.include_router(superadmin_dashboard.router)
    dp.include_router(superadmin_settings.router)
    dp.include_router(logs.router)
    dp.include_router(teacher_attendance.router)
    dp.include_router(superadmin_attendance.router)
    dp.include_router(error_handler.router)
    dp.include_router(support.router)
    dp.include_router(group_join.router)
    dp.include_router(common.router)

    logging.info("Starting bot...")
    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "callback_query",
            "poll_answer",
            "poll",
            "my_chat_member",
            "chat_member",
            "inline_query",
            "chosen_inline_result",
        ]
    )


async def start_log_cleanup_watch(settings: Settings) -> None:
    cleanup_service = LogCleanupService(
        log_dir="logs",
        max_size_mb=settings.log_max_size_mb,
    )

    while True:
        try:
            await cleanup_service.check_and_cleanup()
            await cleanup_service.cleanup_old_files(days=settings.log_cleanup_days)
        except Exception as exc:
            logging.error("Log cleanup failed: %s", exc)
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
