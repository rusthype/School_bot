from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import BookCategory, Book

ALLOWED_CATEGORY_NAMES = {"1-sinf", "2-sinf", "3-sinf", "4-sinf"}


# Categories
async def list_categories(session: AsyncSession) -> list[BookCategory]:
    result = await session.execute(
        select(BookCategory)
        .where(BookCategory.name.in_(ALLOWED_CATEGORY_NAMES))
        .order_by(BookCategory.display_order, BookCategory.name)
    )
    return list(result.scalars().all())


async def get_category_by_id(session: AsyncSession, category_id: int) -> BookCategory | None:
    result = await session.execute(
        select(BookCategory)
        .where(BookCategory.id == category_id, BookCategory.name.in_(ALLOWED_CATEGORY_NAMES))
    )
    return result.scalar_one_or_none()


async def get_category_by_name(session: AsyncSession, name: str) -> BookCategory | None:
    if name not in ALLOWED_CATEGORY_NAMES:
        return None
    result = await session.execute(select(BookCategory).where(BookCategory.name == name))
    return result.scalar_one_or_none()


async def add_category(session: AsyncSession, name: str, display_order: int = 0) -> BookCategory:
    if name not in ALLOWED_CATEGORY_NAMES:
        raise ValueError("allowed_categories_only")
    if display_order == 0:
        result = await session.execute(select(func.max(BookCategory.display_order)))
        max_order = result.scalar()
        display_order = (int(max_order) if max_order is not None else 0) + 1
    category = BookCategory(name=name, display_order=display_order)
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category


async def update_category(session: AsyncSession, category: BookCategory, name: str | None = None) -> BookCategory:
    if name is not None:
        category.name = name
    await session.commit()
    await session.refresh(category)
    return category


async def count_books_in_category(session: AsyncSession, category_id: int) -> int:
    count = await session.scalar(
        select(func.count()).select_from(Book).where(Book.category_id == category_id)
    )
    return int(count or 0)


async def remove_category(session: AsyncSession, category: BookCategory) -> None:
    await session.delete(category)
    await session.commit()


async def seed_book_categories(session_factory) -> None:
    async with session_factory() as session:
        extras = await session.execute(
            select(BookCategory).where(~BookCategory.name.in_(ALLOWED_CATEGORY_NAMES))
        )
        for category in extras.scalars().all():
            count = await session.scalar(
                select(func.count()).select_from(Book).where(Book.category_id == category.id)
            )
            if int(count or 0) == 0:
                await session.delete(category)

        result = await session.execute(
            select(BookCategory).where(BookCategory.name.in_(ALLOWED_CATEGORY_NAMES))
        )
        existing = {category.name for category in result.scalars().all()}
        missing = [name for name in sorted(ALLOWED_CATEGORY_NAMES) if name not in existing]
        if not missing:
            return
        for idx, name in enumerate(missing, 1):
            session.add(BookCategory(name=name, display_order=idx))
        await session.commit()


# Books
async def list_books_by_category(session: AsyncSession, category_id: int) -> list[Book]:
    """Return only available books for teacher-facing browse/cart flows."""
    result = await session.execute(
        select(Book)
        .where(Book.category_id == category_id, Book.is_available.is_(True))
        .order_by(Book.title)
    )
    return list(result.scalars().all())


async def get_book_by_id(session: AsyncSession, book_id: int) -> Book | None:
    """Fetch a book by PK with no availability filter.

    Use this in admin/management paths where unavailable books must
    still be visible and editable. Use get_available_book_by_id for
    any teacher-facing cart lookup.
    """
    result = await session.execute(select(Book).where(Book.id == book_id))
    return result.scalar_one_or_none()


async def get_available_book_by_id(session: AsyncSession, book_id: int) -> Book | None:
    """Fetch a book by PK, returning None if it is marked unavailable.

    Use this in teacher-facing cart add flows so a book that was made
    unavailable after the browse list was fetched cannot be silently
    added to an order.
    """
    result = await session.execute(
        select(Book).where(Book.id == book_id, Book.is_available.is_(True))
    )
    return result.scalar_one_or_none()


async def add_book(
    session: AsyncSession,
    category_id: int,
    title: str,
    author: str | None = None,
    description: str | None = None,
    cover_image: str | None = None,
) -> Book:
    book = Book(
        category_id=category_id,
        title=title,
        author=author,
        description=description,
        cover_image=cover_image,
        is_available=True,
    )
    session.add(book)
    await session.commit()
    await session.refresh(book)
    return book


async def update_book(
    session: AsyncSession,
    book: Book,
    title: str | None = None,
    author: str | None = None,
    description: str | None = None,
    cover_image: str | None = None,
    is_available: bool | None = None,
) -> Book:
    if title is not None:
        book.title = title
    if author is not None:
        book.author = author
    if description is not None:
        book.description = description
    if cover_image is not None:
        book.cover_image = cover_image
    if is_available is not None:
        book.is_available = is_available

    await session.commit()
    await session.refresh(book)
    return book


async def remove_book(session: AsyncSession, book: Book) -> None:
    await session.delete(book)
    await session.commit()
