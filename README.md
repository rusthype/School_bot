# School Task Poll Bot

Telegram bot for managing school tasks and collecting feedback via polls. Built with Python, aiogram v3, PostgreSQL, and async SQLAlchemy.

## Features
- Role-based access: superuser, teacher, regular user
- Teachers can create tasks with topic and description, attach an optional photo, select a target group, and send a poll
- Teachers can place book orders
- Superusers can manage teachers and view stats/users
- Regular users can participate in polls

## Commands
- Common: `/start`, `/help`
- Teacher: `/new_task`, `/order_book`
- Superuser: `/add_teacher`, `/remove_teacher`, `/list_teachers`, `/stats`, `/users`
- Flow controls: `/cancel` during forms, `/skip` for optional steps

## Setup
1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create `.env` from `.env.example` and fill values.

## Environment Variables
| Name | Description | Example |
| --- | --- | --- |
| `BOT_TOKEN` | Telegram bot token from BotFather | `123456:ABCDEF_replace_me` |
| `DATABASE_URL` | Async SQLAlchemy connection string | `postgresql+asyncpg://postgres:postgres@localhost:5432/school_bot` |
| `GROUPS` | JSON map of group name to chat ID | `{"1-A": -1001234567890, "2-B": -1009876543210}` |
| `SUPERUSER_IDS` | Comma-separated Telegram user IDs | `111111111,222222222` |

Example `.env`:

```dotenv
BOT_TOKEN=123456:ABCDEF_replace_me
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/school_bot
GROUPS={"1-A": -1001234567890, "2-B": -1009876543210}
SUPERUSER_IDS=111111111,222222222
```

## Running
```bash
python -m school_bot.main
```

## Notes
- Add the bot to each target group and grant permission to post messages and polls.
- Database tables are created on startup via `Base.metadata.create_all`. Use Alembic for production migrations.
- FSM state uses in-memory storage, so active flows reset on restart.
- Task photos are stored locally in the `photos/` directory.

## Project Structure
- `school_bot/`: application code
- `photos/`: uploaded task photos
- `requirements.txt`: pinned dependencies
