from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable

from sqlalchemy import select, func, or_
from sqlalchemy.exc import ProgrammingError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import Group




async def _execute_with_status_fallback(session: AsyncSession, stmt, fallback_stmt):
    try:
        return await session.execute(stmt)
    except (ProgrammingError, OperationalError) as e:
        msg = str(e).lower()
        if "status" in msg and "column" in msg:
            return await session.execute(fallback_stmt)
        raise


GROUPS_JSON_PATH = Path(__file__).resolve().parents[2] / "groups.json"


async def list_groups(session: AsyncSession, include_pending: bool = False) -> list[Group]:
    stmt = select(Group)
    if not include_pending:
        stmt = stmt.where(or_(Group.status.is_(None), Group.status != "pending"))
    result = await _execute_with_status_fallback(
        session,
        stmt.order_by(Group.name),
        select(Group).order_by(Group.name),
    )
    return list(result.scalars().all())


async def list_pending_groups(session: AsyncSession) -> list[Group]:
    try:
        result = await session.execute(
            select(Group)
            .where(Group.status == "pending")
            .order_by(Group.created_at.desc())
        )
        return list(result.scalars().all())
    except (ProgrammingError, OperationalError) as e:
        msg = str(e).lower()
        if "status" in msg and "column" in msg:
            return []
        raise


async def get_group_by_id(session: AsyncSession, group_id: uuid.UUID) -> Group | None:
    result = await session.execute(select(Group).where(Group.id == group_id))
    return result.scalar_one_or_none()


async def get_group_by_name(session: AsyncSession, name: str) -> Group | None:
    result = await session.execute(select(Group).where(Group.name == name))
    return result.scalar_one_or_none()


async def get_group_by_chat_id(session: AsyncSession, chat_id: int) -> Group | None:
    result = await session.execute(select(Group).where(Group.chat_id == chat_id))
    return result.scalar_one_or_none()


async def get_groups_by_names(session: AsyncSession, names: Iterable[str]) -> list[Group]:
    names = [n for n in names if n]
    if not names:
        return []
    result = await _execute_with_status_fallback(
        session,
        select(Group)
        .where(
            Group.name.in_(names),
            or_(Group.status.is_(None), Group.status != "pending"),
        )
        .order_by(Group.name),
        select(Group).where(Group.name.in_(names)).order_by(Group.name),
    )
    return list(result.scalars().all())


async def list_groups_by_school(session: AsyncSession, school_id: uuid.UUID) -> list[Group]:
    result = await _execute_with_status_fallback(
        session,
        select(Group)
        .where(
            Group.school_id == school_id,
            or_(Group.status.is_(None), Group.status != "pending"),
        )
        .order_by(Group.name),
        select(Group).where(Group.school_id == school_id).order_by(Group.name),
    )
    return list(result.scalars().all())


async def add_group(
    session: AsyncSession,
    name: str,
    chat_id: int,
    invite_link: str | None = None,
    school_id: uuid.UUID | None = None,
    status: str = "active",
) -> Group:
    group = Group(
        name=name,
        chat_id=chat_id,
        invite_link=invite_link,
        school_id=school_id,
        status=status,
    )
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


async def update_group(
    session: AsyncSession,
    group: Group,
    name: str | None = None,
    chat_id: int | None = None,
    invite_link: str | None = None,
    school_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Group:
    if name is not None:
        group.name = name
    if chat_id is not None:
        group.chat_id = chat_id
    if invite_link is not None:
        group.invite_link = invite_link
    if school_id is not None:
        group.school_id = school_id
    if status is not None:
        group.status = status
    await session.commit()
    await session.refresh(group)
    return group


async def set_invite_link(session: AsyncSession, group: Group, invite_link: str | None) -> Group:
    return await update_group(session, group, invite_link=invite_link)


async def remove_group(session: AsyncSession, group: Group) -> None:
    await session.delete(group)
    await session.commit()


async def update_group_chat_id(session: AsyncSession, group: Group, new_chat_id: int) -> Group:
    group.chat_id = new_chat_id
    await session.commit()
    await session.refresh(group)
    return group


async def seed_groups(session_factory, groups_fallback: dict[str, int]) -> None:
    async with session_factory() as session:
        existing = await session.scalar(select(func.count()).select_from(Group))
        if existing and existing > 0:
            return

        groups_data: dict[str, int] = {}

        if GROUPS_JSON_PATH.exists():
            try:
                groups_data = json.loads(GROUPS_JSON_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                groups_data = {}

        if not groups_data:
            groups_data = groups_fallback

        if not groups_data:
            return

        for name, chat_id in groups_data.items():
            session.add(Group(name=name, chat_id=chat_id))

        await session.commit()
