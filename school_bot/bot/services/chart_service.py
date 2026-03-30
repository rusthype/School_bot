from __future__ import annotations

import io
from datetime import datetime, timedelta

import matplotlib
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import Book, BookCategory, BookOrder, Profile, Task, User, UserRole

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


try:
    plt.style.use("seaborn-v0_8-darkgrid")
except OSError:
    plt.style.use("seaborn-darkgrid")

plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["font.size"] = 10
plt.rcParams["figure.figsize"] = (12, 6)
plt.rcParams["figure.dpi"] = 100


async def create_teacher_activity_chart(session: AsyncSession) -> io.BytesIO | None:
    """Eng faol o'qituvchilar (top 10)."""
    result = await session.execute(
        select(User, func.count(Task.id).label("task_count"))
        .join(Task, User.id == Task.teacher_id)
        .where(User.role == UserRole.teacher)
        .group_by(User.id)
        .order_by(func.count(Task.id).desc())
        .limit(10)
    )
    teachers = result.all()
    if not teachers:
        return None

    names: list[str] = []
    counts: list[int] = []
    for user, count in teachers:
        name = user.full_name or f"ID: {user.telegram_id}"
        if len(name) > 20:
            name = f"{name[:17]}..."
        names.append(name)
        counts.append(int(count or 0))

    plt.figure(figsize=(12, 6))
    colors = plt.cm.viridis(np.linspace(0, 1, len(names)))
    bars = plt.barh(names, counts, color=colors)

    for bar, count in zip(bars, counts):
        plt.text(
            bar.get_width() + 0.1,
            bar.get_y() + bar.get_height() / 2,
            str(count),
            va="center",
            fontweight="bold",
        )

    plt.xlabel("Topshiriqlar soni", fontsize=12)
    plt.ylabel("O'qituvchilar", fontsize=12)
    plt.title(
        f"Eng faol o'qituvchilar (Top 10)\n{datetime.now().strftime('%d.%m.%Y')}",
        fontsize=14,
    )
    plt.grid(axis="x", alpha=0.3)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf


async def create_daily_orders_chart(session: AsyncSession) -> io.BytesIO | None:
    """Oxirgi 30 kunlik buyurtmalar."""
    thirty_days_ago = datetime.now() - timedelta(days=30)
    result = await session.execute(
        select(
            func.date(BookOrder.created_at).label("date"),
            func.count().label("count"),
        )
        .where(BookOrder.created_at >= thirty_days_ago)
        .group_by(func.date(BookOrder.created_at))
        .order_by("date")
    )
    data = result.all()
    if not data:
        return None

    dates: list[str] = []
    counts: list[int] = []
    for row in data:
        raw_date = row.date
        if hasattr(raw_date, "strftime"):
            label = raw_date.strftime("%d.%m")
        else:
            label = str(raw_date)
        dates.append(label)
        counts.append(int(row.count or 0))

    plt.figure(figsize=(14, 6))
    plt.plot(
        dates,
        counts,
        marker="o",
        linestyle="-",
        linewidth=2,
        color="#FF6B6B",
        markersize=8,
        markerfacecolor="white",
        markeredgewidth=2,
    )
    plt.fill_between(dates, counts, alpha=0.2, color="#FF6B6B")

    avg_count = sum(counts) / len(counts)
    plt.axhline(
        y=avg_count,
        color="gray",
        linestyle="--",
        alpha=0.7,
        label=f"O'rtacha: {avg_count:.1f}",
    )

    plt.xlabel("Sana", fontsize=12)
    plt.ylabel("Buyurtmalar soni", fontsize=12)
    plt.title("Oxirgi 30 kundagi buyurtmalar", fontsize=14)
    plt.xticks(rotation=45)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf


async def create_books_by_category_chart(session: AsyncSession) -> io.BytesIO | None:
    """Kategoriyalar bo'yicha kitoblar."""
    result = await session.execute(
        select(BookCategory.name, func.count(Book.id).label("count"))
        .join(Book, BookCategory.id == Book.category_id, isouter=True)
        .group_by(BookCategory.id, BookCategory.name)
        .order_by(func.count(Book.id).desc())
    )
    data = result.all()
    if not data:
        return None

    categories = [row.name for row in data]
    counts = [int(row.count or 0) for row in data]

    if len(categories) > 6:
        other_count = sum(counts[5:])
        categories = categories[:5] + ["Boshqalar"]
        counts = counts[:5] + [other_count]

    plt.figure(figsize=(10, 8))
    colors = plt.cm.Set3(np.linspace(0, 1, len(categories)))
    wedges, texts, autotexts = plt.pie(
        counts,
        labels=categories,
        autopct="%1.1f%%",
        colors=colors,
        startangle=90,
        textprops={"fontsize": 11},
    )
    for autotext in autotexts:
        autotext.set_color("white")
        autotext.set_fontweight("bold")
        autotext.set_fontsize(10)

    plt.title(f"Kategoriyalar bo'yicha kitoblar\nJami: {sum(counts)} ta", fontsize=14)
    plt.axis("equal")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf


async def create_order_status_chart(session: AsyncSession) -> io.BytesIO | None:
    """Buyurtma statuslari."""
    status_meta = [
        ("pending", "Kutilmoqda", "#FFA500"),
        ("confirmed", "Tasdiqlangan", "#4CAF50"),
        ("delivered", "Yetkazilgan", "#2196F3"),
        ("rejected", "Rad etilgan", "#F44336"),
        ("cancelled", "Bekor qilingan", "#9E9E9E"),
    ]

    labels: list[str] = []
    counts: list[int] = []
    colors: list[str] = []

    for status, label, color in status_meta:
        count = await session.scalar(select(func.count()).where(BookOrder.status == status))
        count_value = int(count or 0)
        if count_value:
            labels.append(label)
            counts.append(count_value)
            colors.append(color)

    if not counts:
        return None

    plt.figure(figsize=(10, 8))
    wedges, texts, autotexts = plt.pie(
        counts,
        labels=labels,
        autopct="%1.1f%%",
        colors=colors,
        startangle=90,
        pctdistance=0.85,
        textprops={"fontsize": 11},
    )
    centre_circle = plt.Circle((0, 0), 0.70, fc="white")
    fig = plt.gcf()
    fig.gca().add_artist(centre_circle)

    total = sum(counts)
    plt.text(0, 0, f"Jami\n{total}", ha="center", va="center", fontsize=16, fontweight="bold")
    plt.title("Buyurtma statuslari", fontsize=14)
    plt.axis("equal")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf


async def create_teacher_subjects_chart(session: AsyncSession) -> io.BytesIO | None:
    """O'qituvchilar fanlar bo'yicha (mavjud bo'lmasa, guruhlar bo'yicha)."""
    subjects: dict[str, int] = {}

    result = await session.execute(select(User).where(User.role == UserRole.teacher))
    teachers = result.scalars().all()

    for teacher in teachers:
        profile_result = await session.execute(select(Profile).where(Profile.bot_user_id == teacher.id))
        profile = profile_result.scalar_one_or_none()
        if not profile:
            continue

        profile_subjects = getattr(profile, "subjects", None)
        if profile_subjects:
            for subject in profile_subjects:
                subjects[subject] = subjects.get(subject, 0) + 1
            continue

        if profile.assigned_groups:
            for group_name in profile.assigned_groups:
                subjects[group_name] = subjects.get(group_name, 0) + 1

    if not subjects:
        return None

    names = list(subjects.keys())
    counts = list(subjects.values())

    plt.figure(figsize=(12, 6))
    colors = plt.cm.Paired(np.linspace(0, 1, len(names)))
    bars = plt.bar(names, counts, color=colors)

    for bar, count in zip(bars, counts):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.1,
            str(count),
            ha="center",
            fontweight="bold",
        )

    plt.xlabel("Fanlar / guruhlar", fontsize=12)
    plt.ylabel("O'qituvchilar soni", fontsize=12)
    plt.title("Fanlar bo'yicha o'qituvchilar", fontsize=14)
    plt.xticks(rotation=45)
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf
