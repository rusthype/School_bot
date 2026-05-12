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
    """Monitoring natijasi bor maktablarni qaytaradi."""
    try:
        rows = await session.execute(text("""
            SELECT DISTINCT
                s.id           AS school_id,
                s.name         AS school_name,
                COUNT(tr.id)   AS result_count
            FROM bot_schools s
            JOIN monitoring_testresult tr
                ON tr.school_id::text = s.alochi_school_id
            GROUP BY s.id, s.name
            ORDER BY s.name
        """))
        return [{"id": r.school_id, "name": r.school_name, "count": r.result_count} for r in rows]
    except Exception:
        # Fallback: just list all schools
        rows = await session.execute(select(School).order_by(School.name))
        return [{"id": s.id, "name": s.name, "count": 0} for s in rows.scalars()]


async def _list_groups_for_school(session: AsyncSession, school_id: int) -> list[dict]:
    """Maktab uchun guruhlarni (monitoring natijasi bor) qaytaradi."""
    try:
        rows = await session.execute(text("""
            SELECT DISTINCT
                g.id           AS group_id,
                g.name         AS group_name,
                g.alochi_group_id,
                COUNT(tr.id)   AS result_count
            FROM bot_groups g
            LEFT JOIN monitoring_testresult tr
                ON tr.group_id::text = g.alochi_group_id::text
            WHERE g.school_id = :sid
            GROUP BY g.id, g.name, g.alochi_group_id
            ORDER BY g.name
        """), {"sid": school_id})
        return [{"id": r.group_id, "name": r.group_name,
                 "alochi_id": str(r.alochi_group_id) if r.alochi_group_id else None,
                 "count": r.result_count} for r in rows]
    except Exception:
        rows = await session.execute(
            select(Group).where(Group.school_id == school_id).order_by(Group.name)
        )
        return [{"id": g.id, "name": g.name,
                 "alochi_id": str(g.alochi_group_id) if g.alochi_group_id else None,
                 "count": 0} for g in rows.scalars()]


async def _get_results(session: AsyncSession,
                       school_id: int | None = None,
                       group_alochi_id: str | None = None) -> list[dict]:
    """Monitoring natijalarini to'g'ridan DB dan oladi."""
    filters = []
    params: dict = {}

    if school_id is not None:
        filters.append("""
            tr.school_id IN (
                SELECT alochi_school_id::uuid FROM bot_schools WHERE id = :school_id
            )
        """)
        params["school_id"] = school_id

    if group_alochi_id:
        filters.append("tr.group_id = :group_id")
        params["group_id"] = group_alochi_id

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    try:
        rows = await session.execute(text(f"""
            SELECT
                tr.id,
                tr.created_at,
                tr.variant,
                tr.math_score,
                tr.eng_score,
                tr.total_pct,
                tr.passed,
                p.title        AS package_title,
                u.first_name   AS first_name,
                u.last_name    AS last_name,
                g.grade        AS grade
            FROM monitoring_testresult tr
            LEFT JOIN monitoring_monitoringpackage p ON p.id = tr.package_id
            LEFT JOIN monitoring_monitoringcred   c ON c.id = tr.cred_id
            LEFT JOIN monitoring_student          u ON u.id = c.student_id
            LEFT JOIN monitoring_studentgroup     g ON g.id = tr.group_id
            {where}
            ORDER BY tr.created_at DESC
            LIMIT 500
        """), params)
        return [dict(r._mapping) for r in rows]
    except Exception as exc:
        return []


def _build_html(results: list[dict], school_name: str, group_label: str) -> bytes:
    """Chiroyli HTML hisobot — brauzerda ochish mumkin."""
    from datetime import datetime as _dt
    passed  = sum(1 for r in results if r.get("passed"))
    avg_pct = int(sum(r.get("total_pct", 0) for r in results) / len(results)) if results else 0

    rows_html = ""
    for i, r in enumerate(results, 1):
        name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or "—"
        sana = r.get("created_at")
        sana_str = sana.strftime("%d.%m.%Y %H:%M") if isinstance(sana, _dt) else str(sana or "—")
        passed_html = (
            '<span class="badge pass">✅ O\'tdi</span>' if r.get("passed")
            else '<span class="badge fail">❌ O\'tmadi</span>'
        )
        row_class = "row-pass" if r.get("passed") else "row-fail"
        rows_html += f"""
        <tr class="{row_class}">
          <td class="center">{i}</td>
          <td class="name">{name}</td>
          <td class="center">{r.get("grade", "—")}-sinf</td>
          <td class="center">{r.get("variant", "—")}</td>
          <td class="center score">{r.get("math_score", 0)}</td>
          <td class="center score">{r.get("eng_score", 0)}</td>
          <td class="center bold">{r.get("total_pct", 0)}%</td>
          <td class="center">{passed_html}</td>
          <td class="pkg">{r.get("package_title", "—")}</td>
          <td class="center dt">{sana_str}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monitoring Natijalar — {school_name}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Inter', system-ui, sans-serif; font-size: 13px;
         background: #f5f4f1; color: #18181b; }}
  .page {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px; }}

  /* Header */
  .header {{ background: linear-gradient(135deg, #f97316, #ea580c);
             border-radius: 16px; padding: 24px 28px; margin-bottom: 20px;
             color: #fff; display: flex; align-items: center; gap: 20px; }}
  .header-icon {{ width: 52px; height: 52px; background: rgba(255,255,255,.2);
                  border-radius: 12px; display: flex; align-items: center;
                  justify-content: center; font-size: 26px; flex-shrink: 0; }}
  .header-title {{ font-size: 22px; font-weight: 700; letter-spacing: -.02em; }}
  .header-sub   {{ font-size: 13px; opacity: .8; margin-top: 2px; }}

  /* Stats */
  .stats {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 20px; }}
  .stat {{ background: #fff; border: 1px solid rgba(0,0,0,.08); border-radius: 12px;
           padding: 14px 16px; }}
  .stat-val {{ font-size: 26px; font-weight: 800; color: #18181b; line-height: 1; }}
  .stat-lbl {{ font-size: 11px; color: #71717a; margin-top: 3px; font-weight: 500; }}
  .stat.orange .stat-val {{ color: #f97316; }}
  .stat.green  .stat-val {{ color: #16a34a; }}
  .stat.blue   .stat-val {{ color: #2563eb; }}

  /* Table */
  .table-wrap {{ background: #fff; border: 1px solid rgba(0,0,0,.08);
                 border-radius: 14px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,.06); }}
  table {{ width: 100%; border-collapse: collapse; }}
  thead tr {{ background: #f97316; }}
  thead th {{ padding: 11px 10px; color: #fff; font-weight: 700; font-size: 11px;
              text-transform: uppercase; letter-spacing: .06em; text-align: center; }}
  thead th.name {{ text-align: left; }}
  tbody tr {{ border-bottom: 1px solid #f4f4f5; transition: background .1s; }}
  tbody tr:last-child {{ border-bottom: none; }}
  tbody tr:hover {{ background: #fafafa; }}
  tbody tr.row-pass {{ background: #f0fdf4; }}
  tbody tr.row-pass:hover {{ background: #dcfce7; }}
  tbody td {{ padding: 10px 10px; vertical-align: middle; }}
  td.center {{ text-align: center; }}
  td.name {{ font-weight: 600; }}
  td.score {{ font-family: 'SF Mono', 'Consolas', monospace; font-weight: 700; }}
  td.bold {{ font-weight: 800; font-family: monospace; }}
  td.pkg {{ color: #52525b; font-size: 11px; }}
  td.dt {{ color: #a1a1aa; font-size: 11px; white-space: nowrap; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 20px;
            font-size: 11px; font-weight: 700; }}
  .badge.pass {{ background: #dcfce7; color: #15803d; }}
  .badge.fail {{ background: #fee2e2; color: #dc2626; }}

  /* Footer */
  .footer {{ text-align: center; margin-top: 18px; font-size: 11px; color: #a1a1aa; }}

  @media print {{
    body {{ background: #fff; }}
    .page {{ padding: 0; max-width: 100%; }}
    .header {{ border-radius: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .table-wrap {{ box-shadow: none; border-radius: 0; }}
  }}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div class="header-icon">📊</div>
    <div>
      <div class="header-title">{school_name}</div>
      <div class="header-sub">
        {group_label} &nbsp;·&nbsp;
        {_dt.now().strftime("%d.%m.%Y %H:%M")} &nbsp;·&nbsp;
        Monitoring natijalari
      </div>
    </div>
  </div>

  <div class="stats">
    <div class="stat orange">
      <div class="stat-val">{len(results)}</div>
      <div class="stat-lbl">Jami topshirdi</div>
    </div>
    <div class="stat green">
      <div class="stat-val">{passed}</div>
      <div class="stat-lbl">O'tdi</div>
    </div>
    <div class="stat">
      <div class="stat-val">{len(results) - passed}</div>
      <div class="stat-lbl">O'tmadi</div>
    </div>
    <div class="stat blue">
      <div class="stat-val">{avg_pct}%</div>
      <div class="stat-lbl">O'rtacha ball</div>
    </div>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th class="name">Ism Familiya</th>
          <th>Sinf</th>
          <th>Variant</th>
          <th>Matematika</th>
          <th>Ingliz tili</th>
          <th>Jami %</th>
          <th>Natija</th>
          <th class="name">Paket</th>
          <th>Sana</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <div class="footer">
    A'lochi Monitoring tizimi &nbsp;·&nbsp; {_dt.now().strftime("%Y")}
  </div>
</div>
</body>
</html>"""
    return html.encode("utf-8")


def _build_pdf(results: list[dict], school_name: str, group_label: str) -> bytes | None:
    """ReportLab bilan PDF hisobot yaratadi."""
    try:
        import io as _io
        from datetime import datetime as _dt
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph,
            Spacer, HRFlowable,
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT

        buf = _io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=landscape(A4),
            leftMargin=1.5*cm, rightMargin=1.5*cm,
            topMargin=2*cm, bottomMargin=2*cm,
            title=f"Monitoring — {school_name}",
        )

        styles = getSampleStyleSheet()
        brand  = colors.HexColor("#f97316")
        green  = colors.HexColor("#16a34a")
        red    = colors.HexColor("#dc2626")
        ink2   = colors.HexColor("#52525b")
        gray   = colors.HexColor("#f4f4f5")

        h1 = ParagraphStyle("h1", fontSize=16, fontName="Helvetica-Bold",
                             textColor=brand, spaceAfter=4)
        h2 = ParagraphStyle("h2", fontSize=10, fontName="Helvetica",
                             textColor=ink2, spaceAfter=12)
        cell_style = ParagraphStyle("cell", fontSize=8, fontName="Helvetica",
                                    leading=10, alignment=TA_LEFT)

        passed  = sum(1 for r in results if r.get("passed"))
        avg_pct = int(sum(r.get("total_pct", 0) for r in results) / len(results)) if results else 0

        flowables = [
            Paragraph(school_name, h1),
            Paragraph(
                f"{group_label}  ·  {_dt.now().strftime('%d.%m.%Y %H:%M')}  ·  "
                f"Jami: <b>{len(results)}</b>  O'tdi: <b>{passed}</b>  "
                f"O'rtacha: <b>{avg_pct}%</b>", h2,
            ),
            HRFlowable(width="100%", thickness=1, color=brand, spaceAfter=10),
        ]

        # Table header
        headers = ["#", "Ism Familiya", "Sinf", "Variant",
                   "Matematika", "Ingliz tili", "Jami %", "Natija", "Paket", "Sana"]
        table_data = [headers]

        for i, r in enumerate(results, 1):
            name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or "—"
            sana = r.get("created_at")
            sana_str = sana.strftime("%d.%m.%Y %H:%M") if isinstance(sana, _dt) else str(sana or "—")
            table_data.append([
                str(i),
                Paragraph(name, cell_style),
                f"{r.get('grade', '—')}-sinf",
                str(r.get("variant", "—")),
                str(r.get("math_score", 0)),
                str(r.get("eng_score", 0)),
                f"{r.get('total_pct', 0)}%",
                "✅ O'tdi" if r.get("passed") else "❌ O'tmadi",
                Paragraph(r.get("package_title", "—") or "—", cell_style),
                sana_str,
            ])

        col_widths = [1*cm, 5.5*cm, 2*cm, 2*cm, 2.5*cm, 2.5*cm, 2*cm, 3*cm, 4.5*cm, 3.5*cm]
        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)

        style_cmds = [
            ("BACKGROUND",  (0,0), (-1,0), brand),
            ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
            ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",    (0,0), (-1,0), 8),
            ("ALIGN",       (0,0), (-1,0), "CENTER"),
            ("BOTTOMPADDING",(0,0),(-1,0), 7),
            ("TOPPADDING",  (0,0), (-1,0), 7),
            ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
            ("FONTSIZE",    (0,1), (-1,-1), 8),
            ("ALIGN",       (0,1), (0,-1), "CENTER"),
            ("ALIGN",       (2,1), (7,-1), "CENTER"),
            ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, gray]),
            ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#e4e4e7")),
            ("BOTTOMPADDING",(0,1),(-1,-1), 5),
            ("TOPPADDING",  (0,1),(-1,-1), 5),
        ]
        # Green rows for passed
        for row_i, r in enumerate(results, 1):
            if r.get("passed"):
                style_cmds.append(("BACKGROUND", (0, row_i), (-1, row_i), colors.HexColor("#f0fdf4")))
                style_cmds.append(("TEXTCOLOR",  (6, row_i), (6, row_i), green))
            else:
                style_cmds.append(("TEXTCOLOR",  (6, row_i), (6, row_i), red))

        tbl.setStyle(TableStyle(style_cmds))
        flowables.append(tbl)
        flowables.append(Spacer(1, 0.5*cm))
        flowables.append(Paragraph(
            f"A'lochi Monitoring tizimi · {_dt.now().strftime('%Y')}",
            ParagraphStyle("footer", fontSize=7, textColor=ink2, alignment=TA_CENTER),
        ))

        doc.build(flowables)
        return buf.getvalue()
    except Exception as exc:
        return None



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


def _groups_keyboard(groups: list[dict], school_id: int) -> InlineKeyboardMarkup:
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

    school_id = int(callback.data.split(":")[1])
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
    school_id = int(parts[1])
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
        group_alochi_id=group_id,
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
