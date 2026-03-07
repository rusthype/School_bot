from __future__ import annotations

import os
import time
import tempfile
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.superadmin_menu_builder import SuperAdminMenuBuilder

router = Router(name="logs")
logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILES = {
    "main": LOG_DIR / "bot.log",
    "error": LOG_DIR / "error.log",
    "backup": LOG_DIR / "backup.log",
    "access": LOG_DIR / "access.log",
    "stats": LOG_DIR / "stats.log",
}
ALT_LOG_FILES = {
    "main": LOG_DIR / "school_bot.log",
    "error": LOG_DIR / "school_bot.error.log",
    "backup": LOG_DIR / "backup.log",
    "access": LOG_DIR / "access.log",
    "stats": LOG_DIR / "stats.log",
}

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 5
_rate_limit: dict[int, deque[float]] = {}


def _ensure_log_files() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for name, log_file in LOG_FILES.items():
        if not log_file.exists():
            log_file.write_text("", encoding="utf-8")


def _seed_sample_logs() -> None:
    sentinel = LOG_DIR / ".sample_seeded"
    if sentinel.exists():
        return

    def _has_real_content(path: Path) -> bool:
        if not path.exists():
            return False
        if path.stat().st_size == 0:
            return False
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as file_obj:
                for line in file_obj:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        return True
        except Exception:
            return True
        return False

    files_empty = True
    for path in list(LOG_FILES.values()) + list(ALT_LOG_FILES.values()):
        if _has_real_content(path):
            files_empty = False
            break

    if not files_empty:
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sample_main = (
        f"{now} - INFO - Bot started successfully\n"
        f"{now} - INFO - Superadmin opened logs panel\n"
        "2024-03-07 10:23:45 - WARNING - Slow response detected (345ms)\n"
        "2024-03-07 11:05:34 - INFO - Homework created by @teacher1\n"
    )
    sample_error = (
        "2024-03-07 10:23:45 - ERROR - Failed to send message to user 12345\n"
        "2024-03-07 11:45:12 - ERROR - Database connection lost\n"
        f"{now} - ERROR - Test error message\n"
    )
    sample_access = (
        f"{now} | ✅ AUTHORIZED | Admin: @superadmin | Action: /start\n"
        "2024-03-07 10:23:45 | ⚠️ UNAUTHORIZED | User: @guest | Tried: /logs\n"
    )
    sample_backup = (
        "2024-03-07 02:00:15 - INFO - Daily backup started\n"
        f"{now} - INFO - Manual backup created\n"
    )

    LOG_FILES["main"].write_text(sample_main, encoding="utf-8")
    LOG_FILES["error"].write_text(sample_error, encoding="utf-8")
    LOG_FILES["access"].write_text(sample_access, encoding="utf-8")
    LOG_FILES["backup"].write_text(sample_backup, encoding="utf-8")
    LOG_FILES["stats"].write_text(f"{now} - INFO - Stats log initialized\n", encoding="utf-8")

    sentinel.write_text(now, encoding="utf-8")


_ensure_log_files()
_seed_sample_logs()


def _rate_limited(user_id: int) -> bool:
    now = time.time()
    bucket = _rate_limit.setdefault(user_id, deque())
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX:
        return True
    bucket.append(now)
    return False


def _parse_args(text: str) -> tuple[str, int, str | None, str | None, str | None]:
    parts = (text or "").split()
    log_type = "main"
    lines = 10
    mode = None
    search = None
    error = None

    if len(parts) <= 1:
        return log_type, lines, mode, search, error

    args = parts[1:]
    if args[0].isdigit():
        lines = min(int(args[0]), 100)
        return log_type, lines, mode, search, error

    if args[0] in ("error", "backup", "main"):
        log_type = args[0]
        if len(args) > 1 and args[1].isdigit():
            lines = min(int(args[1]), 100)
        elif len(args) > 1 and args[1] == "today":
            mode = "today"
        return log_type, lines, mode, search, error

    if args[0] == "today":
        mode = "today"
        return log_type, lines, mode, search, error

    if args[0] == "clear":
        mode = "clear"
        if len(args) > 1 and args[1] in ("error", "backup", "main"):
            log_type = args[1]
        return log_type, lines, mode, search, error

    if args[0] == "search" and len(args) > 1:
        search = " ".join(args[1:])
        mode = "search"
        return log_type, lines, mode, search, error

    error = "❌ Noto'g'ri option. /logs yoki /logs [number]"
    return log_type, lines, mode, search, error


def _resolve_log_path(log_type: str) -> Path:
    path = LOG_FILES.get(log_type, LOG_FILES["main"])
    alt = ALT_LOG_FILES.get(log_type)
    if path.exists() and path.stat().st_size > 0:
        return path
    if alt and alt.exists() and alt.stat().st_size > 0:
        return alt
    if path.exists():
        return path
    if alt and alt.exists():
        return alt
    return path


def _read_last_lines(
    path: Path,
    lines: int,
    today_only: bool,
    level_filter: str | None = None,
    search: str | None = None,
) -> tuple[list[str], str | None, int]:
    if not path.exists():
        return [], "📁 Log fayli topilmadi", 0
    if not os.access(path, os.R_OK):
        return [], "🔒 Ruxsat yo'q", 0

    if path.stat().st_size == 0:
        return [], "📭 Log fayli bo'sh. Bot ishlaganda avtomatik yoziladi.", 0

    size = path.stat().st_size
    if size > 10 * 1024 * 1024:
        lines = min(lines, 50)

    result: deque[str] = deque(maxlen=lines)
    today_str = datetime.now().strftime("%Y-%m-%d")

    with path.open("r", encoding="utf-8", errors="ignore") as file_obj:
        for line in file_obj:
            if today_only and today_str not in line:
                continue
            if level_filter == "error" and "ERROR" not in line:
                continue
            if search and search.lower() not in line.lower():
                continue
            result.append(line.rstrip("\n"))

    return list(result), None, size


def _format_line(line: str) -> str:
    if " - ERROR - " in line or " ERROR " in line:
        emoji = "❌"
    elif " - WARNING - " in line or " WARNING " in line:
        emoji = "⚠️"
    elif " - INFO - " in line or " INFO " in line:
        emoji = "✅"
    else:
        emoji = "ℹ️"

    ts = ""
    content = line
    if len(line) >= 19 and line[4] == "-" and line[7] == "-" and line[10] == " ":
        ts = line[:19]
        content = line[19:].lstrip(" -")

    if ts:
        return f"🕒 {ts} {emoji} {content}"
    return f"{emoji} {content}"


def _format_output(lines: Iterable[str], log_type: str, total_size: int, filename: str) -> str:
    header = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 SYSTEM LOGS ({filename})\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    lines_list = list(lines)
    formatted = "\n".join(_format_line(line) for line in lines_list)
    size_mb = total_size / (1024 * 1024) if total_size else 0
    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {len(lines_list)} lines | Size: {size_mb:.2f} MB | {datetime.now().strftime('%H:%M:%S')}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return header + (formatted or "📭 Loglar bo'sh") + footer


def _build_actions(log_type: str, lines: int, mode: str | None) -> InlineKeyboardMarkup:
    mode_val = mode or ""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Refresh",
                    callback_data=f"logs_refresh:{log_type}:{lines}:{mode_val}",
                ),
                InlineKeyboardButton(
                    text="📥 Download",
                    callback_data=f"logs_download:{log_type}:{lines}:{mode_val}",
                ),
                InlineKeyboardButton(
                    text="🗑️ Clear",
                    callback_data=f"logs_clear:{log_type}",
                ),
            ]
        ]
    )
    return keyboard


def _build_logs_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📄 Asosiy", callback_data="logs_open:main"),
                InlineKeyboardButton(text="❌ Xatolar", callback_data="logs_open:error"),
            ],
            [
                InlineKeyboardButton(text="💾 Backup", callback_data="logs_open:backup"),
                InlineKeyboardButton(text="🔐 Kirish", callback_data="logs_open:access"),
            ],
            [
                InlineKeyboardButton(text="📊 Statistika", callback_data="logs_open:stats"),
                InlineKeyboardButton(text="📅 Bugungi", callback_data="logs_open_today:main"),
            ],
            [
                InlineKeyboardButton(text="⚠️ Xatolar (filter)", callback_data="logs_open_error:main"),
                InlineKeyboardButton(text="🔍 Qidirish", callback_data="logs_search_hint"),
            ],
            [
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="logs_back_menu"),
                InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="logs_home"),
                InlineKeyboardButton(text="🔄 Yangilash", callback_data="logs_menu_refresh"),
            ],
        ]
    )


async def _send_log_output(
    target: Message | CallbackQuery,
    log_type: str,
    lines: int,
    mode: str | None,
    search: str | None = None,
    level_filter: str | None = None,
) -> None:
    path = _resolve_log_path(log_type)
    today_only = mode == "today"

    log_lines, error, size = _read_last_lines(path, lines, today_only, level_filter, search)
    if error:
        if isinstance(target, CallbackQuery):
            await target.message.answer(error)
        else:
            await target.answer(error)
        return

    output = _format_output(log_lines, log_type, size, path.name)
    keyboard = _build_actions(log_type, lines, mode)

    if len(output) > 3800:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as tmp:
            tmp.write("\n".join(log_lines).encode("utf-8", errors="ignore"))
            tmp_path = tmp.name
        try:
            if isinstance(target, CallbackQuery):
                await target.message.answer_document(FSInputFile(tmp_path), caption=f"📋 {log_type} logs")
            else:
                await target.answer_document(FSInputFile(tmp_path), caption=f"📋 {log_type} logs")
        finally:
            os.unlink(tmp_path)
        return

    if isinstance(target, CallbackQuery):
        await target.message.answer(output, reply_markup=keyboard)
    else:
        await target.answer(output, reply_markup=keyboard)


async def send_logs_menu(message: Message) -> None:
    await message.answer(
        "📋 LOGLAR PANELI\n\nQaysi loglarni ko'rmoqchisiz?",
        reply_markup=_build_logs_menu(),
    )


@router.message(Command("logs"))
async def cmd_logs(message: Message, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await message.answer("Kechirasiz, bu buyruq faqat superadmin uchun")
        return

    if _rate_limited(message.from_user.id):
        await message.answer("⏳ Iltimos, biroz kuting")
        return

    log_type, lines, mode, search, error = _parse_args(message.text or "")
    if error:
        await message.answer(error)
        return

    if mode == "clear":
        await message.answer(
            "🗑️ Loglarni tozalashni tasdiqlaysizmi?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"logs_clear_confirm:{log_type}"),
                        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="logs_clear_cancel"),
                    ]
                ]
            ),
        )
        return

    await _send_log_output(message, log_type, lines, mode, search=search)


@router.callback_query(F.data.startswith("logs_refresh:"))
async def logs_refresh(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    _, log_type, lines, mode = callback.data.split(":", 3)
    mode = mode or None
    await _send_log_output(callback, log_type, int(lines), mode)
    await callback.answer()


@router.callback_query(F.data.startswith("logs_download:"))
async def logs_download(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    _, log_type, lines, mode = callback.data.split(":", 3)
    path = _resolve_log_path(log_type)
    today_only = mode == "today"

    log_lines, error, _ = _read_last_lines(path, int(lines), today_only)
    if error:
        await callback.message.answer(error)
        await callback.answer()
        return

    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{log_type}.log") as tmp:
        tmp.write("\n".join(log_lines).encode("utf-8", errors="ignore"))
        tmp_path = tmp.name

    try:
        await callback.message.answer_document(FSInputFile(tmp_path), caption=f"📋 {log_type} logs")
    finally:
        os.unlink(tmp_path)
    await callback.answer()


@router.message(F.text == "📋 Loglar")
async def logs_button_handler(message: Message, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await message.answer("❌ Kechirasiz, bu bo'lim faqat superadmin uchun")
        return
    await send_logs_menu(message)


@router.callback_query(F.data == "logs_menu_refresh")
async def logs_menu_refresh(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    await callback.message.answer(
        "📋 LOGLAR PANELI\n\nQaysi loglarni ko'rmoqchisiz?",
        reply_markup=_build_logs_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "logs_back_menu")
async def logs_back_menu(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    await callback.message.answer(
        "📋 LOGLAR PANELI\n\nQaysi loglarni ko'rmoqchisiz?",
        reply_markup=_build_logs_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "logs_home")
async def logs_home(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    builder = SuperAdminMenuBuilder()
    await callback.message.answer("🏠 Asosiy menyu", reply_markup=builder.build_main_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("logs_open:"))
async def logs_open(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    _, log_type = callback.data.split(":", 1)
    await _send_log_output(callback, log_type, 20, None)
    await callback.answer()


@router.callback_query(F.data.startswith("logs_open_today:"))
async def logs_open_today(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    _, log_type = callback.data.split(":", 1)
    await _send_log_output(callback, log_type, 20, "today")
    await callback.answer()


@router.callback_query(F.data.startswith("logs_open_error:"))
async def logs_open_error(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    _, log_type = callback.data.split(":", 1)
    await _send_log_output(callback, log_type, 20, None, level_filter="error")
    await callback.answer()


@router.callback_query(F.data == "logs_search_hint")
async def logs_search_hint(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    await callback.message.answer(
        "🔍 Qidirish uchun: /logs search MATN\n"
        "Masalan: /logs search ERROR",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("logs_clear:"))
async def logs_clear_request(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    _, log_type = callback.data.split(":", 1)
    await callback.message.answer(
        "🗑️ Loglarni tozalashni tasdiqlaysizmi?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"logs_clear_confirm:{log_type}"),
                    InlineKeyboardButton(text="❌ Bekor qilish", callback_data="logs_clear_cancel"),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("logs_clear_confirm:"))
async def logs_clear_confirm(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    _, log_type = callback.data.split(":", 1)

    targets = [LOG_FILES.get(log_type, LOG_FILES["main"])]
    for path in targets:
        try:
            path.write_text("")
        except Exception:
            pass

    await callback.message.answer("✅ Loglar tozalandi")
    await callback.answer()


@router.callback_query(F.data == "logs_clear_cancel")
async def logs_clear_cancel(callback: CallbackQuery) -> None:
    await callback.answer()
