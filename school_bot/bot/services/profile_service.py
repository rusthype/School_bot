from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import Profile, User, UserRole


async def get_profile_by_user_id(session: AsyncSession, user_id: int) -> Profile | None:
    result = await session.execute(select(Profile).where(Profile.user_id == user_id))
    return result.scalar_one_or_none()


async def get_profile_by_id(session: AsyncSession, profile_id: int) -> Profile | None:
    result = await session.execute(select(Profile).where(Profile.id == profile_id))
    return result.scalar_one_or_none()


async def upsert_profile(
    session: AsyncSession,
    user_id: int,
    first_name: str,
    last_name: str | None,
    phone: str,
) -> Profile:
    profile = await get_profile_by_user_id(session, user_id)

    if profile:
        if profile.is_approved:
            return profile

        profile.first_name = first_name
        profile.last_name = last_name
        profile.phone = phone
        profile.registered_at = datetime.utcnow()
        profile.is_approved = False
        profile.approved_by = None
        profile.approved_at = None
        profile.rejected_at = None
        profile.removed_at = None
        await session.commit()
        await session.refresh(profile)
        return profile

    profile = Profile(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        registered_at=datetime.utcnow(),
        is_approved=False,
        assigned_groups=[],
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def approve_profile(
    session: AsyncSession,
    profile: Profile,
    approved_by_user_id: int,
    assigned_groups: list[str],
) -> Profile:
    profile.is_approved = True
    profile.assigned_groups = assigned_groups
    profile.approved_by = approved_by_user_id
    profile.approved_at = datetime.utcnow()
    profile.rejected_at = None
    profile.removed_at = None

    user = await session.get(User, profile.user_id)
    if user and user.role != UserRole.superuser:
        user.role = UserRole.teacher

    await session.commit()
    await session.refresh(profile)
    return profile


async def revoke_teacher(session: AsyncSession, user_id: int) -> bool:
    profile = await get_profile_by_user_id(session, user_id)
    user = await session.get(User, user_id)

    changed = False
    if profile and profile.is_approved:
        profile.is_approved = False
        profile.assigned_groups = []
        profile.approved_by = None
        profile.approved_at = None
        profile.removed_at = datetime.utcnow()
        changed = True

    if user and user.role == UserRole.teacher:
        user.role = None
        changed = True

    if changed:
        await session.commit()

    return changed


async def reject_profile(session: AsyncSession, profile: Profile) -> None:
    user = await session.get(User, profile.user_id)
    if user and user.role == UserRole.teacher:
        user.role = None

    profile.is_approved = False
    profile.assigned_groups = []
    profile.approved_by = None
    profile.approved_at = None
    profile.rejected_at = datetime.utcnow()
    profile.removed_at = None

    await session.commit()


def can_register_again(profile: Profile) -> bool:
    return profile.rejected_at is not None or profile.removed_at is not None
