---
name: Bot Login API Contract
description: Exact API contract between school-bot and A'lochi backend for bot auth endpoints — paths, headers, request/response shapes
type: project
---

All bot service endpoints are mounted at `/api/v1/auth/bot/...` in the A'lochi backend (apps/users/urls.py).

**Auth header:** `X-Bot-Service-Key: <BOT_SERVICE_SHARED_SECRET>` — NOT X-Bot-Service-Secret.

**Return contract:** All AlochiApiClient methods return `(dict, int)` — (json_body, http_status_code). Exceptions are only raised for network failures (AlochiNetworkError). Never raise on 4xx/5xx — branch on status code.

## Endpoints

### POST /api/v1/auth/bot/login/
Request: `{ login, password, bot_user_id }`
- 200: `{ success:True, user_id:str(UUID), username, role, first_name, last_name, phone }`
- 401: `{ success:False, reason:"invalid_credentials" }`
- 429: `{ success:False, reason:"rate_limited", wait_seconds:int }`

### POST /api/v1/auth/bot/credentials/teacher/
Request: `{ phone, first_name, last_name, bot_user_id }`
- 201: teacher created → `{ user_id:UUID, login, password }`
- 200: already has credentials
- 404: teacher not found in panel

### POST /api/v1/auth/bot/find-children-by-phone/
Request: `{ phone }`
- 200: `{ children: [{ id, full_name, class, school }] }`
- 404: no children found

### POST /api/v1/auth/bot/credentials/parent-by-phone/
Request: `{ first_name, last_name, phone, bot_user_id, student_ids }`
- 201: parent created → `{ user_id:UUID, login, password }`

### POST /api/v1/auth/bot/credentials/parent-by-invite/
Request: `{ invite_code, first_name, last_name, phone, bot_user_id }`
- 201: parent created → `{ user_id:UUID, login, password }`
- 400: invalid invite code
- 410: invite code expired

## Type notes
- `user_id` from all endpoints is UUID stored as String(36) — Profile.alochi_user_id is String(36).
- `bot_user_id` in requests is the bot's internal User.id (BigInteger).
