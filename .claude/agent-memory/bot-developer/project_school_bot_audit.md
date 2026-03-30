---
name: School_bot audit findings
description: Critical bugs, broken flows, stub features, and missing env vars found in the School_bot Telegram project audit
type: project
---

All startup and critical runtime bugs have been fixed (commit d10cfde, 2026-03-29):

Fixed bugs:
- show_groups_menu, show_users_menu, show_stats_menu — all three exist in common.py (lines 1737, 1557, 1591) and import correctly
- approval_select_school and approval_school_page in admin.py — now assign school_name/requested_str before use
- asyncio.create_task calls moved into dp.startup on_startup handler in main.py
- registration_confirm reads reg_type from FSM state instead of hardcoding "teacher"
- superadmin_attendance cancel handler uses or_f(Command("cancel"), F.text == "❌ Bekor qilish")
- All datetime.utcnow() replaced with datetime.now(timezone.utc) in: order_escalation_service, book_order_service, approval_service, admin, superadmin_orders, superadmin_settings
- Removed duplicate parse_telegram_input import in admin.py
- Removed unreachable code blocks after return in admin.py and common.py

Remaining known issues (not fixed — by design or future work):
- In-memory approval state (_APPROVAL_SELECTIONS, _APPROVAL_SCHOOLS) lost on restart; multi-admin concurrent approval unsafe
- ~15 teacher/student menu buttons are stubs (export, search, add student, broadcast, etc.)
- Missing .env.example entries: TEACHER_IDS, ADMIN_GROUP_ID, LOG_MAX_SIZE_MB, LOG_CLEANUP_DAYS
- UserContextMiddleware makes live Telegram API call on every update when admin_group_id is set — no caching beyond GroupAdminGuardMiddleware

**Why:** Original code was incomplete and had several refactor leftovers.
**How to apply:** Bot should now start and run. Remaining stubs are UI-only placeholders.
