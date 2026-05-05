# School Bot — Gemini CLI Project Context

## Project Overview
Telegram bot for Uzbek primary schools (maktab). Handles teacher registration,
student management, attendance, book orders, polls/tasks, and group management.
Connected to Alochi panel (Django backend at api.alochi.org).

## Stack
- Python 3.11
- aiogram 3.26 (Telegram bot framework)
- SQLAlchemy 2 async + asyncpg (ORM)
- Alembic (migrations)
- PostgreSQL (shared with alochi Django backend — same DB)
- Redis (FSM state storage)
- aiohttp (HTTP client)
- Pillow (image processing)
- Docker + docker-compose

## Project Structure
```
school_bot/
  main.py                    — bot entry point, dispatcher setup, router registration
  database/
    models.py                — ALL SQLAlchemy models (User, Profile, School, Group, TeacherAttendance, ...)
    session.py               — async session factory
  bot/
    handlers/
      common.py              — main handler (teacher/admin/librarian keyboards, /start, registration flow)
      teacher.py             — teacher-specific commands
      teacher_attendance.py  — teacher GPS check-in/check-out (Keldim/Ketdim)
      admin.py               — admin commands
      admin_management.py    — user/school/group management
      book_management.py     — book CRUD (1467 lines — needs refactoring)
      book_order_cart.py     — shopping cart flow
      book_search.py         — book search
      book_categories.py     — category management
      librarian.py           — librarian order management
      group_join.py          — auto-register groups middleware
      superadmin_attendance.py — GPS geofence management
      superadmin_dashboard.py  — stats dashboard
      superadmin_orders.py     — order management
      superadmin_settings.py   — bot settings
      error_handler.py       — global error handler (captures to Sentry)
      support.py             — support flow
      logs.py                — log viewing
    services/
      attendance_service.py  — GPS attendance logic
      approval_service.py    — teacher approval notifications
      book_service.py        — book CRUD service
      book_order_service.py  — order service
      profile_service.py     — profile upsert, Alochi panel link
      school_service.py      — school CRUD
      user_service.py        — get_or_create_user (NOTE: has race condition bug — needs ON CONFLICT upsert)
      bot_settings_service.py — bot settings
      logger_service.py      — structured logging
      pagination.py          — school pagination helper
      superadmin_menu_builder.py — superadmin menu UI builder
    states/
      attendance.py          — TeacherAttendanceStates, SuperadminAttendanceStates
      registration.py        — RegistrationStates
      admin_states.py        — admin FSM states
      book_states.py         — book management states
      book_order.py          — order flow states
      dashboard_states.py    — search/broadcast states
      group_management.py    — group management states
      new_task.py            — new task states
      support_states.py      — support states
    utils/
      phone_utils.py         — phone normalization
      telegram.py            — send_chunked_message
      subscription.py        — channel subscription check
    config.py                — Settings (pydantic-settings, reads from .env)
    middlewares/
      group_registration.py  — auto-registers unknown groups on any message
  scripts/
    test_sentry.py           — Sentry smoke test

alembic/
  versions/
    20260501_01 ... 20260501_04  — existing migrations (last: bot_groups_alochi_group_id)
  env.py, alembic.ini

tests/
  test_group_registration_middleware.py  — reference test style
  test_book_search_service.py

Dockerfile                   — python:3.11-slim base, apt-get: gcc libpq-dev libjpeg libpng libgl
docker-compose.yml           — services: bot, postgres, redis
requirements.txt             — aiogram, sqlalchemy, asyncpg, aiohttp, pillow, sentry-sdk, etc.
.env.example                 — env var documentation
```

## Database Tables (bot uses alochi Django DB)
- `bot_users` — Telegram users (id, telegram_id UNIQUE, full_name, username, role)
- `bot_profiles` — registration profiles (bot_user_id, first_name, last_name, phone, profile_type, assigned_groups JSON, school_id, is_approved, alochi_teacher_id)
- `bot_schools` — schools (id, number UNIQUE, name, latitude, longitude, radius_m, alochi_school_id)
- `bot_groups` — Telegram groups (id, chat_id UNIQUE, name, status, school_id, alochi_group_id UUID)
- `bot_teacher_attendance` — teacher GPS check-in/out (teacher_id, school_id, action, lat/lon, distance_m, is_inside, attendance_date)
- `bot_tasks` — teacher poll tasks
- `bot_poll_votes` — poll votes

## Key Patterns

### Models
```python
class SomeModel(Base):
    __tablename__ = "table_name"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
```

### Handlers
```python
@router.message(F.text == "Button text")
async def handler(message: Message, session: AsyncSession, db_user, profile, is_teacher: bool = False) -> None:
    if not is_teacher:
        await message.answer("⛔ Bu funksiya faqat tasdiqlangan o'qituvchilar uchun.")
        return
```

### FSM
```python
await state.set_state(SomeStates.some_state)
data = await state.get_data()
await state.update_data(key=value)
await state.clear()
```

### Alembic migration naming: `YYYYMMDD_NN_description.py`
Last migration: `20260501_04_bot_groups_alochi_group_id.py`

## Critical Rules
- No Co-Authored-By in commits
- No emoji in Python code or comments
- Uzbek text in bot UI messages
- English in code, variable names, docstrings, comments
- No secrets/API keys in code — only os.environ.get()
- All external API calls: wrap in try/except, never raise to user
- Blocking operations (pytesseract, image processing): run in asyncio.run_in_executor
- New routers: register in main.py BEFORE common.router (last)
- MessageNotModified on edit_reply_markup: silently ignore
- Test pattern: see tests/test_group_registration_middleware.py

## Known Pending Bugs (do NOT accidentally touch these in unrelated PRs)
- Bug #11: get_or_create_user race condition — needs INSERT ... ON CONFLICT upsert
- Bug #12: schools UUID query — WHERE id = ANY(:uuids::uuid[]) may fail in some contexts

## Deployment
- GitHub Actions does NOT auto-deploy this repo
- Manual deploy: ssh alochi-deployer@198.163.206.64 -p 2222
  cd ~/school-bot && git pull && docker compose build bot && docker compose up -d bot
- Check logs: docker compose logs bot --tail 100
