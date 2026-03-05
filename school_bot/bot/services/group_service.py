from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import Group


GROUPS_JSON_PATH = Path(__file__).resolve().parents[2] / "groups.json"


async def list_groups(session: AsyncSession) -> list[Group]:
    result = await session.execute(select(Group).order_by(Group.name))
    return list(result.scalars().all())


async def get_group_by_id(session: AsyncSession, group_id: int) -> Group | None:
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
    result = await session.execute(select(Group).where(Group.name.in_(names)).order_by(Group.name))
    return list(result.scalars().all())


async def add_group(session: AsyncSession, name: str, chat_id: int) -> Group:
    group = Group(name=name, chat_id=chat_id)
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


async def update_group(
    session: AsyncSession,
    group: Group,
    name: str | None = None,
    chat_id: int | None = None,
) -> Group:
    if name is not None:
        group.name = name
    if chat_id is not None:
        group.chat_id = chat_id
    await session.commit()
    await session.refresh(group)
    return group


async def remove_group(session: AsyncSession, group: Group) -> None:
    await session.delete(group)
    await session.commit()


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
