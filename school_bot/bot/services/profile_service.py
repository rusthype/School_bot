from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.utils.phone_utils import normalize_phone
from school_bot.database.models import Profile, User, UserRole

logger = get_logger(__name__)


async def get_profile_by_user_id(session: AsyncSession, user_id: int) -> Profile | None:
    result = await session.execute(select(Profile).where(Profile.bot_user_id == user_id))
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
    school_id: int | None = None,
    profile_type: str = "teacher",
) -> Profile:
    profile = await get_profile_by_user_id(session, user_id)

    if profile:
        if profile.is_approved:
            return profile

        profile.first_name = first_name
        profile.last_name = last_name
        profile.phone = phone
        profile.school_id = school_id
        profile.profile_type = profile_type
        profile.registered_at = datetime.now(timezone.utc)
        profile.is_approved = False
        profile.approved_by = None
        profile.approved_at = None
        profile.rejected_at = None
        profile.removed_at = None
        await session.commit()
        await session.refresh(profile)
        return profile

    profile = Profile(
        bot_user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        school_id=school_id,
        profile_type=profile_type,
        registered_at=datetime.now(timezone.utc),
        is_approved=False,
        assigned_groups=[],
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def upsert_student_profile(
    session: AsyncSession,
    user_id: int,
    first_name: str,
    last_name: str | None,
    phone: str,
    class_name: str | None,
    school_id: int | None = None,
) -> Profile:
    profile = await get_profile_by_user_id(session, user_id)

    if profile:
        profile.first_name = first_name
        profile.last_name = last_name
        profile.phone = phone
        profile.school_id = school_id
        profile.profile_type = "student"
        profile.assigned_groups = [class_name] if class_name else []
        profile.registered_at = datetime.now(timezone.utc)
        profile.is_approved = True
        profile.approved_by = None
        profile.approved_at = datetime.now(timezone.utc)
        profile.rejected_at = None
        profile.removed_at = None
        await session.commit()
        await session.refresh(profile)
        return profile

    profile = Profile(
        bot_user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        school_id=school_id,
        profile_type="student",
        assigned_groups=[class_name] if class_name else [],
        registered_at=datetime.now(timezone.utc),
        is_approved=True,
        approved_by=None,
        approved_at=datetime.now(timezone.utc),
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
    school_id: int | None = None,
) -> Profile:
    profile.is_approved = True
    profile.assigned_groups = assigned_groups
    profile.profile_type = profile.profile_type or "teacher"
    profile.approved_by = approved_by_user_id
    profile.approved_at = datetime.now(timezone.utc)
    profile.rejected_at = None
    profile.removed_at = None
    if school_id is not None:
        profile.school_id = school_id

    user = await session.get(User, profile.bot_user_id)
    if user and user.role not in (UserRole.superadmin, UserRole.librarian):
        user.role = UserRole.teacher

    await session.commit()
    await session.refresh(profile)
    return profile


async def revoke_teacher(session: AsyncSession, user_id: int) -> bool:
    """Soft-delete a teacher: sets is_active=False and revokes their role/approval.

    The user row is kept in the DB and can be restored by an admin.
    """
    profile = await get_profile_by_user_id(session, user_id)
    user = await session.get(User, user_id)

    changed = False
    if profile and profile.is_approved:
        profile.is_approved = False
        profile.assigned_groups = []
        profile.approved_by = None
        profile.approved_at = None
        profile.removed_at = datetime.now(timezone.utc)
        changed = True

    if user and user.role == UserRole.teacher:
        user.role = None
        user.is_active = False
        changed = True
    elif user and not user.is_active:
        # Already inactive — nothing to change
        pass
    elif user:
        # Non-teacher user being soft-deleted
        user.is_active = False
        changed = True

    if changed:
        await session.commit()

    return changed


async def restore_teacher(session: AsyncSession, user_id: int) -> bool:
    """Restore a previously soft-deleted teacher: sets is_active=True, role=teacher, is_approved=True."""
    profile = await get_profile_by_user_id(session, user_id)
    user = await session.get(User, user_id)

    if not user:
        return False

    changed = False

    user.is_active = True
    user.role = UserRole.teacher
    changed = True

    if profile:
        profile.is_approved = True
        profile.removed_at = None
        profile.rejected_at = None

    if changed:
        await session.commit()

    return changed


async def reject_profile(session: AsyncSession, profile: Profile) -> None:
    # Rol hech qachon o'chirilmaydi — faqat is_approved=False qo'yiladi.
    # Rolni saqlash shart: rad etilgan user /start bossa rol tanlash
    # keyboard ko'rishi va qayta ro'yxatdan o'tishi uchun rol kerak.
    profile.is_approved = False
    profile.assigned_groups = []
    profile.approved_by = None
    profile.approved_at = None
    profile.rejected_at = datetime.now(timezone.utc)
    profile.removed_at = None

    await session.commit()


def can_register_again(profile: Profile) -> bool:
    return profile.rejected_at is not None or profile.removed_at is not None


async def update_teacher_profile(
    session: AsyncSession,
    user_id: int,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
) -> Profile | None:
    """Update Profile fields for a teacher. Only non-None kwargs are applied."""
    profile = await get_profile_by_user_id(session, user_id)
    if not profile:
        return None
    if first_name is not None:
        profile.first_name = first_name
    if last_name is not None:
        profile.last_name = last_name
    if phone is not None:
        profile.phone = phone
    await session.commit()
    await session.refresh(profile)
    return profile


async def update_teacher_groups(
    session: AsyncSession,
    user_id: int,
    assigned_groups: list[str],
) -> Profile | None:
    """Replace the teacher's assigned_groups list."""
    profile = await get_profile_by_user_id(session, user_id)
    if not profile:
        return None
    profile.assigned_groups = assigned_groups
    await session.commit()
    await session.refresh(profile)
    return profile


async def update_teacher_user(
    session: AsyncSession,
    user_id: int,
    *,
    full_name: str | None = None,
    role: "UserRole | None" = None,
) -> User | None:
    """Update User fields for a teacher. Only non-None kwargs are applied."""
    user = await session.get(User, user_id)
    if not user:
        return None
    if full_name is not None:
        user.full_name = full_name
    if role is not None:
        user.role = role
    await session.commit()
    await session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Alochi panel linkage
# ---------------------------------------------------------------------------
#
# The bot and the Alochi panel share a single Postgres database. Alochi owns
# the teachers_teacher table (managed by Django migrations); we only read
# from it here and, when a match is found, write bot_user_id back onto the
# Alochi row so the link is symmetric.


async def find_profile_by_phone(
    session: AsyncSession,
    normalized_phone: str,
    *,
    exclude_bot_user_id: int | None = None,
) -> Profile | None:
    """Return another Profile that already uses this phone, or None.

    Matches against the raw stored phone AND the normalized form, so phones
    saved historically without the '+' prefix still collide. Pass
    ``exclude_bot_user_id`` to skip the current user when re-registering.
    """
    raw_no_plus = normalized_phone.lstrip("+")
    stmt = select(Profile).where(
        (Profile.phone == normalized_phone) | (Profile.phone == raw_no_plus)
    )
    if exclude_bot_user_id is not None:
        stmt = stmt.where(Profile.bot_user_id != exclude_bot_user_id)
    stmt = stmt.limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def try_link_to_alochi_teacher(
    session: AsyncSession,
    profile: Profile,
) -> str | None:
    """Attempt to link ``profile`` to a row in Alochi's teachers_teacher.

    Looks up teachers_teacher by normalized phone. On match:
      - sets profile.alochi_teacher_id
      - writes teachers_teacher.bot_user_id back (symmetric) when that
        Alochi row is currently unlinked
      - commits the transaction

    Returns the Alochi teacher's display name on success, or None when no
    match exists or the phone cannot be normalized. Errors (e.g. the Alochi
    table not yet migrated to include bot_user_id) are logged and swallowed
    — registration must never fail because the link step failed.
    """
    if not profile.phone:
        return None
    normalized = normalize_phone(profile.phone)
    if not normalized:
        return None

    try:
        result = await session.execute(
            text(
                """
                SELECT id, name
                FROM teachers_teacher
                WHERE phone = :phone AND is_deleted = false
                LIMIT 1
                """
            ),
            {"phone": normalized},
        )
        row = result.fetchone()
        if not row:
            return None

        alochi_teacher_id = str(row[0])
        teacher_name = row[1]

        profile.alochi_teacher_id = alochi_teacher_id

        # Symmetric write: set teachers_teacher.bot_user_id when empty.
        # Guarded so this function stays safe if the column has not yet been
        # added on the Alochi side (older DB state). UPDATE is wrapped in
        # its own try/except to avoid poisoning the outer transaction.
        try:
            await session.execute(
                text(
                    """
                    UPDATE teachers_teacher
                    SET bot_user_id = :bot_user_id
                    WHERE id = :teacher_id AND bot_user_id IS NULL
                    """
                ),
                {
                    "bot_user_id": profile.bot_user_id,
                    "teacher_id": alochi_teacher_id,
                },
            )
        except Exception:
            logger.warning(
                "Alochi teachers_teacher.bot_user_id write skipped "
                "(column may not exist yet on this DB)",
                exc_info=True,
            )

        await session.commit()
        await session.refresh(profile)
        logger.info(
            "Profile linked to Alochi teacher",
            extra={
                "bot_user_id": profile.bot_user_id,
                "alochi_teacher_id": alochi_teacher_id,
                "teacher_name": teacher_name,
            },
        )
        return teacher_name
    except Exception:
        logger.error(
            "Failed to link Profile to Alochi teacher",
            exc_info=True,
            extra={"bot_user_id": profile.bot_user_id},
        )
        return None
