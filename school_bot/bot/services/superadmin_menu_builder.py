from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder


@dataclass
class SuperAdminOverview:
    total_users: int
    admin_users: int
    teacher_users: int
    student_users: int
    book_count: int
    task_count: int
    db_size_mb: Optional[int]


class SuperAdminMenuBuilder:
    def build_main_keyboard(self) -> ReplyKeyboardMarkup:
        builder = ReplyKeyboardBuilder()
        builder.row(
            KeyboardButton(text="👥 Foydalanuvchilar"),
            KeyboardButton(text="👨‍🏫 O'qituvchilar"),
        )
        builder.row(
            KeyboardButton(text="👑 Adminlar"),
            KeyboardButton(text="📚 Kitoblar"),
        )
        builder.row(
            KeyboardButton(text="📊 Statistika"),
            KeyboardButton(text="💾 Backup"),
        )
        builder.row(
            KeyboardButton(text="📢 Xabarnoma"),
            KeyboardButton(text="⚙️ Bot sozlamalari"),
        )
        builder.row(
            KeyboardButton(text="📋 Loglar"),
            KeyboardButton(text="❓ Yordam"),
        )
        builder.row(KeyboardButton(text="🏠 Bosh menyu"))
        return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")

    def build_dashboard_text(self, overview: SuperAdminOverview) -> str:
        db_size = f"{overview.db_size_mb} MB" if overview.db_size_mb is not None else "N/A"
        return (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👑 SUPERADMIN DASHBOARD\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 SYSTEM OVERVIEW\n"
            "┌─────────────────────────────────────────┐\n"
            f"│  👥 Users:    {overview.total_users} total\n"
            f"│  ├─ 👑 Admin:     {overview.admin_users}\n"
            f"│  ├─ 🎓 Teacher:   {overview.teacher_users}\n"
            f"│  └─ 👤 Student:   {overview.student_users}\n"
            "├─────────────────────────────────────────┤\n"
            f"│  📚 Books:      {overview.book_count}\n"
            f"│  📝 Homework:   {overview.task_count}\n"
            f"│  💾 DB Size:    {db_size}\n"
            "│  🟢 Status:     Online\n"
            "└─────────────────────────────────────────┘\n\n"
            "⚡ QUICK ACTIONS\n"
            "[👥 Foydalanuvchilar] [👨‍🏫 O'qituvchilar]\n"
            "[👑 Adminlar] [📚 Kitoblar]\n"
        )
