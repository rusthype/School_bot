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

Additional fixes applied (2026-04-02):
- _perform_reject (admin.py): `send_message` to rejected user now wrapped in try/except — TelegramForbiddenError no longer crashes the whole handler and leaves the admin flow in broken state
- button_remove_teacher duplicate definition in common.py: renamed first occurrence to `button_remove_teacher_inline` and second to `button_remove_teacher_fsm` — Python silently shadowed the first with the second, causing the `common_del_teacher_` callback path to never fire from the button

Confirmed NOT broken (investigation findings):
- BUG 1 (bot_tasks.updated_at NULL): models.py already has `server_default=func.now()` on Task.updated_at — no code change needed; issue is in existing DB DDL if it occurs
- BUG 3 (debug CALLBACK_DATA log): not present in local admin.py — was a server-only edit; no local fix needed
- BUG 4 (TeacherSelfEditStates): flow is complete and correct in common.py lines 3279–3459; _SELF_NAME_PATTERN and _SELF_PHONE_RE defined at 3265-3266; all state transitions wired correctly
- BUG 5 (stubs): no silent pass handlers found; "tez orada" text in support.py is legitimate message copy, not stub code

Post-role registration flow implemented (2026-04-02):
- `handle_role_selection` no longer shows "wait" message immediately; instead sets PostRoleRegistrationStates.waiting_name and asks for full name
- Two new FSM states added to registration.py: PostRoleRegistrationStates.waiting_name, PostRoleRegistrationStates.waiting_school
- Name validation: regex ^[\w\s'\-]{2,80}$ (unicode); invalid input re-prompts
- School is free text — stored in Profile.last_name until admin assigns a real school_id during approval
- On completion: upsert_profile(first_name=name, last_name=school_text, phone="", school_id=None) + notify_superadmins_new_registration
- /cancel and "❌ Bekor qilish" work at both steps; state.clear() + ReplyKeyboardRemove on cancel
- notify_superadmins_new_registration updated to show free-text school from last_name when school_id is None
- Existing approved users are unaffected (upsert_profile returns early if is_approved=True)

Keldim/Ketdim attendance fixes applied (2026-04-02, commit fix(bot)):
- teacher_check_in_start / teacher_check_out_start: was silently returning with no user message when is_teacher=False — now sends clear error message
- teacher_check_in_start / teacher_check_out_start: added state.update_data(menu_active=True) before set_state() as defensive guard against edge cases where menu_active might be missing from FSM data
- teacher_check_in_location / teacher_check_out_location: was silently returning when is_teacher=False (could happen mid-flow if role changes) — now calls state.clear() and sends error message
- add_student_phone in common.py (line 1642): state.clear() without menu_active=True when no schools found — teachers would be locked out of MenuGuardMiddleware after this error path; fixed by adding state.update_data(menu_active=True)
- superadmin_attendance.py: missing `import uuid` for _build_report_nav type annotation — harmless at runtime due to from __future__ import annotations, but stale annotation from UUID-era schema; fixed by adding import

Investigation confirmed NOT broken: router registration order (teacher_attendance before common), F.location handler filters, location keyboard request_location=True, haversine geo calculation, DB model schema, MenuGuardMiddleware state bypass (passes through when current_state is not None).

Remaining known issues (not fixed — by design or future work):
- Profile.last_name used as temporary school_name store — a dedicated school_name text column would be cleaner
- In-memory approval state (_APPROVAL_SELECTIONS, _APPROVAL_SCHOOLS) lost on restart; multi-admin concurrent approval unsafe
- Missing .env.example entries: TEACHER_IDS, ADMIN_GROUP_ID, LOG_MAX_SIZE_MB, LOG_CLEANUP_DAYS
- UserContextMiddleware makes live Telegram API call on every update when admin_group_id is set — no caching beyond GroupAdminGuardMiddleware

**Why:** Original code was incomplete and had several refactor leftovers.
**How to apply:** Bot should now start and run. Remaining stubs are UI-only placeholders.
