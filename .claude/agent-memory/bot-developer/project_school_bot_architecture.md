---
name: School_bot architecture
description: Architecture overview of the School_bot Telegram bot project — stack, middleware order, router registration, background tasks
type: project
---

Stack: aiogram 3.26, SQLAlchemy 2.0 async (asyncpg), PostgreSQL 15, Redis (FSM storage), Python 3.14.

Entry point: `school_bot/main.py` — creates engine + session factory, runs seeds, creates Bot, wires middlewares, registers routers, starts polling.

Middleware order (outer to inner):
1. `GroupAdminGuardMiddleware` — blocks non-admins in group chats (with 60s TTL cache)
2. `DbSessionMiddleware` — injects `session: AsyncSession` into handler data
3. `UserContextMiddleware` — upserts user, resolves role flags (is_superadmin, is_teacher, is_librarian, is_student, is_group_admin, profile)
4. `MenuGuardMiddleware` — blocks unauthorized private chat messages unless in FSM state or /start

Router registration order (important for priority):
admin, admin_management, teacher, librarian, book_categories, book_management, book_order_cart, superadmin_orders, superadmin_dashboard, superadmin_settings, logs, teacher_attendance, superadmin_attendance, error_handler, support, group_join, common

Background tasks (asyncio.create_task):
- `set_bot_commands` — sets per-user command menus for superadmins
- `start_overdue_order_watch` — hourly overdue book order check + superadmin notification
- `start_log_cleanup_watch` — hourly log file size/age cleanup

DB schema key models: User, Profile, Task, PollVote, BookCategory, Book, BookOrder, BookOrderItem, OrderStatusHistory, SupportTicket, School, TeacherAttendance, Group, BotSettings

FSM states are in `school_bot/bot/states/`: registration, new_task, book_order, book_states, attendance, group_management, admin_states, support_states.

Service layer in `school_bot/bot/services/`: user_service, profile_service, approval_service, attendance_service, book_service, book_catalog_service, book_order_service, group_service, school_service, poll_service, task_service, order_escalation_service, bot_settings_service, chart_service, logger_service, log_cleanup_service, pagination, superadmin_menu_builder, order_status.

Approval selections for teacher approval flow stored in module-level dicts in `approval_service.py` — not persistent across restarts.

Photos stored in local `photos/` directory (not cloud). Book covers in `covers/`. Logs in `logs/`.
