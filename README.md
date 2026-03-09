# 📚 **ClassPulse Bot** — Telegram Bot for School & Class Management

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Aiogram](https://img.shields.io/badge/Aiogram-3.x-2ea44f)](https://docs.aiogram.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-336791)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

A production-ready Telegram bot for school/class management. It supports role-based menus, teacher workflows, student registration, group‑safe behavior, and robust logging.

---

## ✨ **Key Features**

- 👥 **Multi‑role system** — Superadmin, Teacher, Librarian, Student
- 📝 **Student registration** — Name, surname, phone, class
- 🔐 **Role‑based menus** — Different keyboards per user type
- 🧑‍🏫 **Teacher tools** — Create tasks, view votes, manage orders
- 📚 **Book module** — Categories, books, ordering, cover images
- 🧾 **Order workflow** — Pending → Processing → Confirmed → Delivered
- 🛡️ **Group safety** — Only group admins can use commands in groups
- 🧩 **FSM flows** — Clean multi‑step interactions
- 🐘 **PostgreSQL + asyncpg** — Async DB I/O
- 🐳 **Docker ready** — Easy deployment with Compose
- 🧾 **Logging & rotation** — Daily rotation + cleanup

---

## 📸 **Screenshots (Placeholders)**

> Add screenshots here later (start screen, student registration, admin menu, librarian orders, etc.)

```
/screenshots
  - start.png
  - registration.png
  - admin-menu.png
  - student-menu.png
  - orders.png
```

---

## 🚀 **Quick Start**

### ✅ Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Telegram Bot Token from **@BotFather**

### ✅ Installation

```bash
# 1) Clone
https://github.com/yourusername/your-repo.git
cd School_bot

# 2) Create venv
python -m venv .venv
source .venv/bin/activate

# 3) Install dependencies
pip install -r requirements.txt
```

### ✅ Environment variables

Create a `.env` file in the project root:

```env
BOT_TOKEN=123456:ABCDEF
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/school_bot
SUPERADMIN_IDS=123456789,987654321
TEACHER_IDS=111111111,222222222
ADMIN_GROUP_ID=-1001234567890
GROUPS={"7-A": -1001234567, "7-B": -1007654321}
LOG_MAX_SIZE_MB=10
LOG_CLEANUP_DAYS=30
```

### ✅ Database migrations (one‑time)

```bash
python scripts/add_profile_type.py
python scripts/add_delivery_columns.py
python scripts/add_indexes.py
```

### ✅ Run the bot

```bash
python -m school_bot.main
```

---

## ⚙️ **Configuration**

| Variable | Description | Example |
|---------|-------------|---------|
| `BOT_TOKEN` | Bot token from @BotFather | `123456:ABCDEF...` |
| `DATABASE_URL` | PostgreSQL async DSN | `postgresql+asyncpg://user:pass@host/db` |
| `SUPERADMIN_IDS` | Comma‑separated superadmin IDs | `123,456` |
| `TEACHER_IDS` | Comma‑separated teacher IDs | `111,222` |
| `ADMIN_GROUP_ID` | Group ID to check admin status | `-1001234567890` |
| `GROUPS` | JSON mapping of groups | `{"7-A": -100...}` |
| `LOG_MAX_SIZE_MB` | Max log size before trim | `10` |
| `LOG_CLEANUP_DAYS` | Log retention days | `30` |

---

## 🧭 **Usage Guide**

### 👨‍🎓 Students
1. Send `/start` in private chat
2. Register: name → surname → phone → class
3. Access student menu:
   - 📚 Kitoblar
   - 📘 Topshiriqlar
   - 📊 Baholar
   - ❓ Yordam

### 👨‍🏫 Teachers / Admins
- `/start` shows full admin/teacher menu
- Create tasks, manage books, handle orders

### 👥 Groups
- Bot is **silent for regular group members**
- **Only group admins** can use commands/buttons

---

## 🧩 **Commands**

| Command | Who | Description |
|--------|-----|-------------|
| `/start` | All | Start / menu / registration
| `/help` | All | Help
| `/stop` | All | Hide menu
| `/admin_orders` | Superadmin | Orders management
| `/pending_orders` | Librarian | Pending orders

---

## 🐳 **Docker Deployment**

### Example `docker-compose.yml`

```yaml
version: "3.8"

services:
  bot:
    build: .
    restart: always
    environment:
      BOT_TOKEN: ${BOT_TOKEN}
      DATABASE_URL: ${DATABASE_URL}
      SUPERADMIN_IDS: ${SUPERADMIN_IDS}
      TEACHER_IDS: ${TEACHER_IDS}
      ADMIN_GROUP_ID: ${ADMIN_GROUP_ID}
      GROUPS: ${GROUPS}
    depends_on:
      - db

  db:
    image: postgres:14
    restart: always
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: school_bot
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

---

## 📁 **Project Structure**

```
School_bot/
├── school_bot/
│   ├── bot/
│   │   ├── handlers/        # Command + message handlers
│   │   ├── middlewares/     # Access control, DB sessions
│   │   ├── services/        # Business logic, helpers
│   │   ├── states/          # FSM states
│   │   └── config.py        # Settings / env
│   ├── database/            # SQLAlchemy models
│   └── main.py              # App entry point
├── scripts/                 # Migrations and maintenance
├── logs/                    # Log files
├── requirements.txt
└── README.md
```

---

## 🤝 **Contributing**

1. Fork the repository
2. Create a feature branch: `git checkout -b codex/feature-name`
3. Commit your changes
4. Open a Pull Request

---

## 📄 **License**

MIT License — see `LICENSE` for details.

---

## 📬 **Contact**

If you want support or custom development, open an issue or contact the maintainer.

---

⭐ If you find this project useful, consider starring it.
