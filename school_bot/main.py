import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

from school_bot.bot.config import Settings
from school_bot.bot.handlers import admin, common, teacher
from school_bot.bot.middlewares.db_session import DbSessionMiddleware
from school_bot.bot.middlewares.user_context import UserContextMiddleware
from school_bot.database.session import create_session_factory, init_models
from school_bot.bot.services.user_service import seed_superusers
from school_bot.bot.services.group_service import seed_groups


async def set_bot_commands(bot: Bot):
    settings = Settings()

    # Barcha foydalanuvchilar uchun umumiy komandalar
    default_commands = [
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="help", description="Yordam"),
    ]

    # Teacherlar uchun qo'shimcha komandalar
    teacher_commands = [
        BotCommand(command="new_task", description="Yangi topshiriq yaratish"),
        BotCommand(command="order_book", description="Kitob buyurtma qilish"),
    ]

    # Superuserlar uchun qo'shimcha komandalar
    superuser_commands = [
        BotCommand(command="groups", description="Guruhlar ro'yxati"),
        BotCommand(command="add_group", description="Guruh qo'shish"),
        BotCommand(command="edit_group", description="Guruhni tahrirlash"),
        BotCommand(command="remove_group", description="Guruhni o'chirish"),
        BotCommand(command="remove_teacher", description="O'qituvchini olib tashlash"),
        BotCommand(command="list_teachers", description="Barcha o'qituvchilar ro'yxati"),
        BotCommand(command="stats", description="Bot statistikasi"),
        BotCommand(command="users", description="Foydalanuvchilar ro'yxati"),
    ]

    # Default komandalarni o'rnatish (hamma ko'radi)
    await bot.set_my_commands(commands=default_commands, scope=BotCommandScopeDefault())

    # Superuserlar uchun maxsus komandalar
    for user_id in settings.superuser_ids:
        try:
            await bot.set_my_commands(
                commands=default_commands + superuser_commands + teacher_commands,
                scope=BotCommandScopeChat(chat_id=user_id)
            )
            logging.info(f"Superuser komandalari o'rnatildi: {user_id}")
        except Exception as e:
            logging.error(f"Superuser komandalarini o'rnatishda xatolik {user_id}: {e}")

    logging.info("Bot komandalari o'rnatildi")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    settings = Settings()

    engine, session_factory = create_session_factory(settings.database_url)
    await init_models(engine)
    await seed_superusers(session_factory=session_factory, superuser_tg_ids=settings.superuser_ids)
    await seed_groups(session_factory=session_factory, groups_fallback=settings.groups)

    # Bot yaratish
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # Komandalarni o'rnatish
    await set_bot_commands(bot)

    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(DbSessionMiddleware(session_factory=session_factory))
    dp.update.middleware(UserContextMiddleware(superuser_ids=settings.superuser_ids))

    # Routerlarni to'g'ri tartibda ulash (MUHIM!)
    dp.include_router(admin.router)  # Admin router birinchi
    dp.include_router(teacher.router)  # Teacher router ikkinchi
    dp.include_router(common.router)  # Common router eng oxirgi

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
