from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import Task


async def create_task(
    session: AsyncSession,
    teacher_id: int,
    topic: str,
    description: str,
    poll_message_id: int | None,
    poll_id: str | None = None,
) -> Task:
    task = Task(
        teacher_id=teacher_id,
        topic=topic,
        description=description,
        poll_message_id=poll_message_id,
        poll_id=poll_id,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task
