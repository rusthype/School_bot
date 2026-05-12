"""
superadmin_monitoring.py — Monitoring natijalarini yuklab olish (superadmin uchun)

Oqim:
  /monitoring yoki tugma → Maktab tanlash → Guruh tanlash → Natijalar + Excel yuklash
"""
from __future__ import annotations

import io
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import Group, School

router = Router(name="superadmin_monitoring")


class MonitoringStates(StatesGroup):
    choose_school = State()
    choose_group  = State()


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _list_schools_with_results(session: AsyncSession) -> list[dict]:
    """Monitoring natijasi bor maktablarni qaytaradi (schools jadvalidan)."""
    try:
        rows = await session.execute(text("""
            SELECT
                sch.id::text   AS school_id,
                sch.name       AS school_name,
                COUNT(tr.id)   AS result_count
            FROM monitoring_test_results tr
            JOIN students_student u  ON u.id = tr.student_id
            JOIN schools sch         ON sch.id = u.school_id
            GROUP BY sch.id, sch.name
            ORDER BY sch.name
        """))
        return [{"id": r.school_id, "name": r.school_name, "count": r.result_count}
                for r in rows]
    except Exception:
        await session.rollback()
        return []


async def _list_groups_for_school(session: AsyncSession, school_id: str) -> list[dict]:
    """Maktab uchun guruhlarni qaytaradi (monitoring natijasi bor)."""
    try:
        rows = await session.execute(text("""
            SELECT
                g.id::text     AS group_id,
                g.name         AS group_name,
                COUNT(tr.id)   AS result_count
            FROM monitoring_test_results tr
            JOIN students_student u  ON u.id = tr.student_id
            JOIN schools sch         ON sch.id = u.school_id
            LEFT JOIN groups_group g ON g.id = tr.group_id
            WHERE sch.id::text = :sid
            GROUP BY g.id, g.name
            ORDER BY g.name
        """), {"sid": school_id})
        return [{"id": r.group_id or "NONE", "name": r.group_name or "Guruhsiz",
                 "alochi_id": r.group_id, "count": r.result_count}
                for r in rows]
    except Exception:
        await session.rollback()
        return []


async def _get_results(session: AsyncSession,
                       school_id: str | None = None,
                       group_id: str | None = None) -> list[dict]:
    """Monitoring natijalarini DB dan oladi."""
    filters = []
    params: dict = {}

    if school_id:
        filters.append("sch.id::text = :school_id")
        params["school_id"] = school_id

    if group_id:
        filters.append("tr.group_id::text = :group_id")
        params["group_id"] = group_id

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    try:
        rows = await session.execute(text(f"""
            SELECT
                tr.id,
                tr.synced_at        AS created_at,
                tr.variant,
                tr.math_score,
                tr.eng_score,
                tr.total_pct,
                tr.passed,
                p.title             AS package_title,
                u.first_name,
                u.last_name,
                u.name              AS full_name,
                u.grade,
                g.name              AS group_name
            FROM monitoring_test_results tr
            JOIN students_student u      ON u.id = tr.student_id
            JOIN schools sch             ON sch.id = u.school_id
            LEFT JOIN monitoring_packages p ON p.id = tr.package_id
            LEFT JOIN groups_group g        ON g.id = tr.group_id
            {where}
            ORDER BY tr.synced_at DESC
            LIMIT 500
        """), params)
        return [dict(r._mapping) for r in rows]
    except Exception:
        await session.rollback()
        return []


def _schools_keyboard(schools: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for s in schools[:24]:
        cnt = f" ({s['count']})" if s.get("count") else ""
        rows.append([InlineKeyboardButton(
            text=f"🏫 {s['name']}{cnt}",
            callback_data=f"mon_sch:{s['id']}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="mon_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _groups_keyboard(groups: list[dict], school_id: str) -> InlineKeyboardMarkup:
    rows = []
    # "Hammasi" button
    rows.append([InlineKeyboardButton(
        text="📥 Barcha guruhlar (hammasi)",
        callback_data=f"mon_grp:{school_id}:ALL",
    )])
    for g in groups[:20]:
        cnt = f" ({g['count']})" if g.get("count") else ""
        rows.append([InlineKeyboardButton(
            text=f"👥 {g['name']}{cnt}",
            callback_data=f"mon_grp:{school_id}:{g['alochi_id'] or g['id']}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="mon_back")])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="mon_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Entry points ─────────────────────────────────────────────────────────────

@router.message(Command("monitoring"))
async def monitoring_cmd(message: Message, session: AsyncSession,
                          state: FSMContext, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await message.answer("⛔ Ruxsat yo'q.")
        return
    await _show_schools(message, session, state)


async def _show_schools(target: Message | CallbackQuery,
                        session: AsyncSession, state: FSMContext) -> None:
    schools = await _list_schools_with_results(session)
    if not schools:
        text_msg = "📭 Hozircha monitoring natijalari yo'q."
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text_msg)
        else:
            await target.answer(text_msg)
        return

    await state.set_state(MonitoringStates.choose_school)
    await state.update_data(schools={str(s["id"]): s for s in schools})

    kb = _schools_keyboard(schools)
    text_msg = "🏫 <b>Maktabni tanlang:</b>\n<i>Monitoring natijalarini yuklab olish uchun</i>"
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text_msg, reply_markup=kb, parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text_msg, reply_markup=kb, parse_mode="HTML")


@router.callback_query(MonitoringStates.choose_school, F.data.startswith("mon_sch:"))
async def school_selected(callback: CallbackQuery, session: AsyncSession,
                           state: FSMContext, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    school_id = callback.data.split(":")[1]
    data = await state.get_data()
    schools = data.get("schools", {})
    school_name = schools.get(str(school_id), {}).get("name", f"#{school_id}")

    groups = await _list_groups_for_school(session, school_id)
    if not groups:
        await callback.message.edit_text(
            f"📭 <b>{school_name}</b> maktabida guruh topilmadi.",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.set_state(MonitoringStates.choose_group)
    await state.update_data(school_id=school_id, school_name=school_name)

    kb = _groups_keyboard(groups, school_id)
    await callback.message.edit_text(
        f"🏫 <b>{school_name}</b>\n👥 <b>Guruhni tanlang:</b>",
        reply_markup=kb, parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(MonitoringStates.choose_group, F.data.startswith("mon_grp:"))
async def group_selected(callback: CallbackQuery, session: AsyncSession,
                          state: FSMContext, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    parts = callback.data.split(":")
    school_id = parts[1]
    group_key = parts[2]  # UUID or "ALL"

    data = await state.get_data()
    school_name = data.get("school_name", "—")

    await callback.message.edit_text("⏳ Natijalar yuklanmoqda...")
    await callback.answer()

    group_id = None if group_key == "ALL" else group_key
    group_label = "Barcha guruhlar" if group_key == "ALL" else group_key

    results = await _get_results(
        session,
        school_id=school_id,
        group_id=group_id,
    )

    if not results:
        await callback.message.edit_text(
            f"📭 <b>{school_name}</b> — {group_label}\nNatijalar topilmadi.",
            parse_mode="HTML",
        )
        await state.clear()
        return

    # Summary message
    passed = sum(1 for r in results if r.get("passed"))
    avg_pct = int(sum(r.get("total_pct", 0) for r in results) / len(results))
    summary = (
        f"📊 <b>{school_name}</b>\n"
        f"👥 {group_label}\n"
        f"━━━━━━━━━━━\n"
        f"Jami: <b>{len(results)}</b> ta natija\n"
        f"O'tdi: <b>{passed}</b> / {len(results)}\n"
        f"O'rtacha: <b>{avg_pct}%</b>\n"
        f"━━━━━━━━━━━\n"
        f"📥 Excel fayl tayyorlanmoqda..."
    )
    await callback.message.edit_text(summary, parse_mode="HTML")

    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    prefix = f"natijalar_{school_name[:12]}_{stamp}"
    caption = (
        f"📊 <b>{school_name}</b> — {group_label}\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"📝 {len(results)} ta natija · {passed} o'tdi · o'rt. {avg_pct}%"
    )

    # 1. HTML fayl
    html_bytes = _build_html(results, school_name, group_label)
    await callback.message.answer_document(
        document=BufferedInputFile(html_bytes, filename=f"{prefix}.html"),
        caption=caption + "\n\n🌐 <i>HTML: brauzerda oching, chop etish mumkin</i>",
        parse_mode="HTML",
    )

    # 2. PDF fayl
    pdf_bytes = _build_pdf(results, school_name, group_label)
    if pdf_bytes:
        await callback.message.answer_document(
            document=BufferedInputFile(pdf_bytes, filename=f"{prefix}.pdf"),
            caption="📄 PDF versiya",
        )
    else:
        await callback.message.answer(
            "⚠️ PDF yaratishda xato (reportlab o'rnatilmagan). "
            "HTML faylni brauzerda ochib, Ctrl+P → PDF sifatida saqlang."
        )

    # 3. Excel
    excel_bytes = _build_excel(results, school_name)
    await callback.message.answer_document(
        document=BufferedInputFile(excel_bytes, filename=f"{prefix}.xlsx"),
        caption="📊 Excel versiya",
    )

    await state.clear()


@router.callback_query(F.data == "mon_back")
async def monitoring_back(callback: CallbackQuery, session: AsyncSession,
                           state: FSMContext, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    await _show_schools(callback, session, state)


@router.callback_query(F.data == "mon_cancel")
async def monitoring_cancel(callback: CallbackQuery, state: FSMContext,
                             is_superadmin: bool = False) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()


# ── Keyboard button handler ───────────────────────────────────────────────────

@router.message(F.text == "📋 Monitoring Natijalar")
async def monitoring_btn_handler(message: Message, session: AsyncSession,
                                  state: FSMContext, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    await _show_schools(message, session, state)
