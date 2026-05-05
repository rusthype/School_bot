---
name: api-testing
description: >
  Complete reference for testing any A'lochi Django REST API endpoint correctly on the first try.
---

# API Testing Skill — A'lochi Django REST API

Complete reference for testing any A'lochi endpoint correctly on the first try.

---

## 1. AUTHENTICATION

A'lochi supports multiple auth flows depending on role.

### Student Auth (OTP flow)
```bash
# Step 1: Request OTP
curl -s -X POST https://api.alochi.org/auth/otp/send \
  -H "Content-Type: application/json" \
  -d '{"phone": "+998901234567"}'
# → {"detail": "OTP sent"}

# Step 2: Verify OTP → get JWT
curl -s -X POST https://api.alochi.org/auth/otp/verify \
  -H "Content-Type: application/json" \
  -d '{"phone": "+998901234567", "otp": "1234"}'
# → {"access": "eyJ...", "refresh": "eyJ..."}

# Step 3: Use token
TOKEN="eyJ..."
curl -H "Authorization: Bearer $TOKEN" https://api.alochi.org/api/v1/tests/
```

### Username/Password Auth (boss, school)
```bash
# Boss login
curl -s -X POST https://api.alochi.org/auth/boss/login/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "secret"}'

# School admin login
curl -s -X POST https://api.alochi.org/auth/admin/login/ \
  -H "Content-Type: application/json" \
  -d '{"username": "school_admin", "password": "secret"}'

# Generic login
curl -s -X POST https://api.alochi.org/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "user", "password": "pass"}'
```

### CRM Auth (separate scheme)
```bash
# CRM uses "CRM " prefix, not "Bearer "
CRM_TOKEN=$(curl -s -X POST https://api.alochi.org/api/v1/crm/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username": "aliyev", "password": "pass"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access'])")

curl -H "Authorization: CRM $CRM_TOKEN" https://api.alochi.org/api/v1/crm/leads/
```

### Token Refresh
```bash
curl -s -X POST https://api.alochi.org/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh": "eyJ..."}'
# → {"access": "eyJ..."}
```

### JWT Lifetime
- Access token: 15 minutes (configurable via `JWT_ACCESS_LIFETIME`)
- Refresh token: 30 days (configurable via `JWT_REFRESH_LIFETIME`)
- Algorithm: HS256

### Common Auth Errors
| Error | Cause | Fix |
|-------|-------|-----|
| `401 detail: "token_not_valid"` | Expired access token | Refresh with `/auth/refresh` |
| `401 detail: "No active account"` | Wrong credentials | Check username/password |
| `429` | Rate limited (5/min on login) | Wait 60s |
| `403 detail: "permission_denied"` | Wrong role for endpoint | Check user role |
| `401 detail: "Authentication credentials were not provided"` | Missing header | Add `Authorization: Bearer TOKEN` |

---

## 2. ENDPOINT CATALOG

Base URL: `https://api.alochi.org` (or `/api/v1/` prefix — both work)

### Auth
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| POST | `/auth/otp/send` | No | `{"phone": "+998..."}` |
| POST | `/auth/otp/verify` | No | `{"phone", "otp"}` → tokens |
| POST | `/auth/otp/reset` | No | OTP password reset |
| POST | `/auth/login` | No | `{"username", "password"}` |
| POST | `/auth/boss/login/` | No | Boss panel login |
| POST | `/auth/admin/login/` | No | School admin login |
| POST | `/auth/refresh` | No | `{"refresh"}` → new access |
| GET | `/auth/me` | Bearer | Current user profile |

### Tests
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| GET | `/api/v1/tests/` | Bearer | List available tests, filter by grade/subject |
| GET | `/api/v1/tests/catalog` | Bearer | Test catalog |
| GET | `/api/v1/tests/{id}/` | Bearer | Test detail with questions |
| POST | `/api/v1/attempts/` | Bearer | Start a test attempt |
| POST | `/api/v1/attempts/{id}/submit/` | Bearer | Submit answers |

### Challenges (PvP)
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| GET | `/api/v1/challenges/current/` | Bearer | Current challenge (auto-creates if none) |
| POST | `/api/v1/challenges/answer/` | Bearer | Submit answer |
| GET | `/api/v1/challenges/history/` | Bearer | Past challenges |

### Shop
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| GET | `/api/v1/shop/items/` | Bearer | List items, filter by category |
| GET | `/api/v1/shop/items/{slug}/` | Bearer | Item detail |
| POST | `/api/v1/shop/items/{slug}/purchase/` | Bearer | Buy item (coins/XP) |
| GET | `/api/v1/shop/purchases/` | Bearer | Purchase history |

### Schools
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| GET | `/api/v1/schools/` | No | Public list of partner schools |
| GET | `/api/v1/school/dashboard/` | School Bearer | School admin dashboard |
| GET | `/api/v1/school/students/` | School Bearer | Students list |
| GET | `/api/v1/school/stats/` | School Bearer | School statistics |
| GET | `/api/v1/school/leaderboard/` | School Bearer | School leaderboard |

### Leaderboard
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| GET | `/api/v1/leaderboard/?scope=global` | Bearer | Global leaderboard |
| GET | `/api/v1/leaderboard/?scope=city` | Bearer | City leaderboard |
| GET | `/api/v1/leaderboard/?scope=school` | Bearer | School leaderboard |
| GET | `/api/v1/leaderboard/global` | Bearer | Global (legacy alias) |

### XP & Coins
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| GET | `/api/v1/xp/` | Bearer | XP history |
| GET | `/api/v1/gamification/profile` | Bearer | Full gamification profile |
| GET | `/api/v1/gamification/achievements` | Bearer | Achievements |
| POST | `/api/v1/gamification/daily-login` | Bearer | Claim daily login reward |
| GET | `/api/v1/coins/wallet/` | Bearer | Coin balance |

### Homework & AI
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| GET | `/api/v1/homework/` | Bearer | List homework |
| POST | `/api/v1/homework/` | Bearer | Submit homework |
| POST | `/api/v1/ai/homework/analyze` | Bearer | AI analysis of homework photo |
| POST | `/api/v1/ai/homework/help` | Bearer | AI homework assistant |

### Notifications
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| GET | `/api/v1/notifications/` | Bearer | List notifications |
| GET | `/api/v1/notifications/unread-count/` | Bearer | Unread count |
| POST | `/api/v1/notifications/mark-all-read/` | Bearer | Mark all read |

### CRM (separate auth)
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| POST | `/api/v1/crm/auth/login/` | No | `{"username", "password"}` → CRM token |
| GET | `/api/v1/crm/leads/` | CRM | Role-filtered (agent=own, manager/super_admin=all) |
| POST | `/api/v1/crm/leads/` | CRM | Create lead |
| PATCH | `/api/v1/crm/leads/{id}/` | CRM | Update lead |
| DELETE | `/api/v1/crm/leads/{id}/` | CRM | Delete lead |
| POST | `/api/v1/crm/leads/{id}/calls/` | CRM | Add call log |
| GET | `/api/v1/crm/agents/` | CRM manager+ | List agents |
| POST | `/api/v1/crm/agents/` | CRM super_admin | Create agent |
| GET | `/api/v1/crm/stats/` | CRM | Aggregate stats |
| GET | `/api/v1/crm/stats/agents/` | CRM | Per-agent stats |
| GET | `/api/v1/crm/stats/daily/` | CRM | Daily call counts |

### Boss Panel
| Method | URL | Auth | Notes |
|--------|-----|------|-------|
| GET | `/api/v1/boss/` | Boss Bearer | Boss dashboard |
| GET | `/admin/statistics/` | Session | Admin stats page |

---

## 3. TESTING PATTERNS

### pytest Setup (conftest.py)
```python
# conftest.py
import pytest
from django.test import Client
from rest_framework.test import APIClient
from apps.users.models import CustomUser

@pytest.fixture
def api_client():
    return APIClient()

@pytest.fixture
def student_user(db):
    user = CustomUser.objects.create_user(
        username='teststudent',
        phone='+998901111111',
        password='TestPass123',
        role='student',
        grade=5,
    )
    return user

@pytest.fixture
def student_token(student_user):
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(student_user)
    return str(refresh.access_token)

@pytest.fixture
def auth_client(api_client, student_token):
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {student_token}')
    return api_client

@pytest.fixture
def school_user(db):
    return CustomUser.objects.create_user(
        username='testschool',
        password='TestPass123',
        role='school',
    )
```

### Test Class Pattern
```python
import pytest
from django.urls import reverse

@pytest.mark.django_db
class TestShopEndpoints:
    def test_list_items_requires_auth(self, api_client):
        response = api_client.get('/api/v1/shop/items/')
        assert response.status_code == 401

    def test_list_items_authenticated(self, auth_client):
        response = auth_client.get('/api/v1/shop/items/')
        assert response.status_code == 200
        assert 'results' in response.data or isinstance(response.data, list)

    def test_purchase_insufficient_coins(self, auth_client, db):
        response = auth_client.post('/api/v1/shop/items/test-item/purchase/')
        assert response.status_code in (400, 402, 404)
```

### Mocking Groq AI Calls
```python
from unittest.mock import patch, MagicMock

def mock_groq_response(content: str):
    mock = MagicMock()
    mock.choices[0].message.content = content
    return mock

@pytest.mark.django_db
def test_homework_ai_help(auth_client):
    with patch('apps.core.utils.ai_client.client.chat.completions.create') as mock_groq:
        mock_groq.return_value = mock_groq_response('Bu masalani hal qilish uchun...')
        response = auth_client.post('/api/v1/ai/homework/help', {
            'question': 'x + 5 = 10 ni yeching',
        })
    assert response.status_code == 200
    assert 'answer' in response.data or 'result' in response.data
```

### Mocking Pollinations.ai Image Generation
```python
from unittest.mock import patch

@pytest.mark.django_db
def test_generate_question_image(db):
    with patch('apps.core.image_service.urllib.request.urlretrieve') as mock_dl:
        with patch('apps.core.image_service.urllib.request.urlopen') as mock_fetch:
            mock_fetch.return_value.__enter__ = lambda s: s
            mock_fetch.return_value.__exit__ = MagicMock(return_value=False)
            mock_fetch.return_value.read.return_value = b'fake_image_data'
            # call the service
            from apps.core.image_service import generate_image_bytes
            result = generate_image_bytes('test prompt')
    assert result is not None or result is None  # graceful None is OK
```

### Factory Boy Patterns
```python
# factories.py
import factory
from apps.users.models import CustomUser
from apps.tests.models import Test, Question

class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CustomUser
    username = factory.Sequence(lambda n: f'user{n}')
    phone = factory.Sequence(lambda n: f'+99890{n:07d}')
    role = 'student'
    grade = 5

class TestFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Test
    title = factory.Sequence(lambda n: f'Test {n}')
    status = 'published'
    grade = 5
    subject = 'math'

class QuestionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Question
    test = factory.SubFactory(TestFactory)
    question_text = factory.Sequence(lambda n: f'Question {n}?')
    correct_answer = 'A'
```

### Transaction Rollback Pattern
```python
# Use @pytest.mark.django_db(transaction=True) only when testing signals/transactions
# Default @pytest.mark.django_db uses atomic rollback — preferred

@pytest.mark.django_db
def test_purchase_deducts_coins(auth_client, student_user):
    from apps.coins.models import CoinWallet
    wallet = CoinWallet.objects.create(user=student_user, balance=1000)
    auth_client.post('/api/v1/shop/items/test-item/purchase/')
    wallet.refresh_from_db()
    assert wallet.balance < 1000
    # DB automatically rolled back after test
```

---

## 4. PERFORMANCE TESTING

### N+1 Query Detection
```python
from django.test import TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext

class TestLeaderboardQueries(TestCase):
    def setUp(self):
        # Create 20 users
        [UserFactory() for _ in range(20)]

    def test_leaderboard_no_n_plus_1(self):
        with self.assertNumQueries(3):  # adjust expected count
            response = self.client.get('/api/v1/leaderboard/?scope=global')
        self.assertEqual(response.status_code, 200)

    def test_capture_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/api/v1/tests/')
        for query in ctx.captured_queries:
            print(query['sql'][:100])
        self.assertLess(len(ctx.captured_queries), 10, "Too many queries")
```

### django-debug-toolbar (local only)
```python
# settings/local.py
INSTALLED_APPS += ['debug_toolbar']
MIDDLEWARE = ['debug_toolbar.middleware.DebugToolbarMiddleware'] + MIDDLEWARE
INTERNAL_IPS = ['127.0.0.1']
```

### Profiling Slow Endpoints
```bash
# Using cProfile via management command
python manage.py shell -c "
import cProfile
import pstats
from django.test import RequestFactory
from apps.leaderboard.views import GlobalLeaderboardView

factory = RequestFactory()
request = factory.get('/api/v1/leaderboard/')
request.user = CustomUser.objects.first()

with cProfile.Profile() as pr:
    GlobalLeaderboardView.as_view()(request)

stats = pstats.Stats(pr)
stats.sort_stats('cumulative')
stats.print_stats(20)
"
```

### Query Optimization Checklist
- [ ] Use `select_related()` for ForeignKey traversals
- [ ] Use `prefetch_related()` for ManyToMany / reverse FK
- [ ] Add `db_index=True` on frequently filtered fields (grade, status, user)
- [ ] Use `values()` / `values_list()` when full model not needed
- [ ] Use `only()` to exclude large text fields from list views
- [ ] Check `explain()` output on complex querysets in production
- [ ] Avoid `.count()` in loops — use aggregation instead
- [ ] Cache leaderboard endpoints with `cache_page(60 * 5)`

---

## 5. SECURITY TESTING

### JWT Manipulation Tests
```python
import jwt
import pytest

@pytest.mark.django_db
def test_tampered_role_rejected(api_client, student_user):
    """Tampered role claim must be rejected"""
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(student_user)
    token_data = jwt.decode(str(refresh.access_token), options={"verify_signature": False})
    token_data['role'] = 'admin'
    # Re-signing with wrong key — must fail
    fake_token = jwt.encode(token_data, 'wrong_secret', algorithm='HS256')
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {fake_token}')
    response = api_client.get('/api/v1/boss/')
    assert response.status_code == 401

@pytest.mark.django_db
def test_expired_token_rejected(api_client, student_user):
    from datetime import datetime, timedelta
    from rest_framework_simplejwt.tokens import RefreshToken
    import time
    refresh = RefreshToken.for_user(student_user)
    token = refresh.access_token
    token.set_exp(lifetime=timedelta(seconds=-1))  # expired
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(token)}')
    response = api_client.get('/api/v1/tests/')
    assert response.status_code == 401
```

### IDOR Tests
```python
@pytest.mark.django_db
def test_student_cannot_read_other_profile(db):
    user_a = UserFactory(username='student_a')
    user_b = UserFactory(username='student_b')
    client = APIClient()
    from rest_framework_simplejwt.tokens import RefreshToken
    token_a = str(RefreshToken.for_user(user_a).access_token)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token_a}')

    # Try to access user B's data
    response = client.get(f'/api/v1/students/{user_b.id}/profile/')
    assert response.status_code in (403, 404)  # must not be 200
```

### Rate Limiting
```bash
# Test login rate limit (5/minute)
for i in $(seq 1 7); do
  STATUS=$(curl -so /dev/null -w "%{http_code}" -X POST \
    https://api.alochi.org/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"nonexistent","password":"wrong"}')
  echo "Attempt $i: $STATUS"
done
# Expected: 401 x5, then 429 x2
```

---

## 6. CI/CD TESTING

### GitHub Actions Config
Tests run automatically on every push to `main` via `.github/workflows/`.

```bash
# Run all tests locally (mirrors CI)
cd alochi_backend
python -m pytest apps/ -v --tb=short

# Run specific app tests
python -m pytest apps/tests/ -v
python -m pytest apps/challenges/ -v
python -m pytest apps/crm/ -v

# Run with coverage
python -m pytest apps/ --cov=apps --cov-report=html --cov-report=term-missing

# Run only fast tests (skip slow/integration)
python -m pytest apps/ -m "not slow" -v

# Parallel execution (4 workers)
python -m pytest apps/ -n 4
```

### Coverage Requirements
- Minimum: 70% coverage on new code
- Critical paths (auth, purchase, XP award) should be 90%+

```bash
# Generate coverage report
coverage run -m pytest apps/
coverage report --fail-under=70
coverage html  # Opens in browser: htmlcov/index.html
```

### Running in Docker (matches production environment)
```bash
docker exec alochi-backend python manage.py test apps.crm
docker exec alochi-backend python -m pytest apps/tests/ -v
```

---

## 7. COMMON ERRORS

### 400 Bad Request
- **Missing required field**: Check serializer `required=True` fields. Add all required fields to request body.
- **UniqueTogetherValidator fail**: Trying to create duplicate record. Use `update_or_create()` in tests or use a unique fixture per test.
  ```python
  # Workaround in tests:
  existing = Model.objects.filter(user=user, subject='math').first()
  if existing:
      existing.delete()
  ```
- **BooleanField default issue**: DRF sends `null` for missing boolean → `None` fails `blank=False`. Fix: `serializer.BooleanField(default=False)`.

### 401 Unauthorized
- Token expired → refresh it
- Missing `Bearer ` prefix (note the space)
- CRM endpoint called with `Bearer` instead of `CRM` prefix
- Token belongs to wrong user type (e.g., CRM token on regular API)

### 403 Forbidden
- Student accessing boss/school endpoint
- CRM agent accessing super_admin endpoint
- Missing `IsAuthenticated` permission on view (returns 403 not 401 for some DRF configs)

### 404 Not Found
- Object doesn't exist in DB for the test environment
- `get_object_or_404` with wrong filter (e.g., filtering by `user=request.user` when ownership differs)
- URL typo — check trailing slash consistency (A'lochi is inconsistent: some endpoints have `/`, some don't)

### 500 Internal Server Error
Most common causes in A'lochi:
1. FK pointing to deleted/nonexistent record
2. Missing env var (GROQ_API_KEY, JWT_SECRET)
3. Celery task failing silently (check Redis connection)
4. Database migration not applied after deploy
5. Pollinations.ai timeout (image generation) — check retry logic

```bash
# Debug 500 in Docker
docker compose logs backend --tail=50 | grep -i "error\|exception\|traceback"
docker exec alochi-backend python manage.py check
```

### DRF-Specific Pitfalls
```python
# 1. Never use .is_valid() without raise_exception=True in views
serializer.is_valid(raise_exception=True)  # ✅

# 2. Pagination — check if response is paginated
# {"count": 10, "results": [...]} vs plain list [...]
# A'lochi uses DRF's PageNumberPagination for most list endpoints

# 3. CRM uses custom CRMAgentAuthentication, not JWT
# authentication_classes = [CRMAgentAuthentication]
# Never mix CRM and regular auth in the same view
```
