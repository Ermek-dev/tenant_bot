**TenantBot (Telegram)**

- Purpose: Tenants report issues (plumbing, electricity, documents, other) via bot. Reports are saved to DB and sent to a staff chat. A repair person can claim a task once, attach completion photos, and mark it done. The tenant receives an automatic resolution notification.

**Features**

- Polished UX with emoji menus and clear steps.
- Categories via inline buttons: 🔧 Сантехника, 💡 Свет, 📄 Документы, ❓ Другая.
- Text + multiple photos from tenant.
- Enterprises (companies) with invite codes; users bind to a company.
- Staff group receives task with inline buttons.
- One-click claim by a single staff member; tracks assignee.
- Assignee can submit completion text + photo report.
- Tenant gets a resolution notification with photos.
- SQLite storage; easy to deploy.

**Setup**

- Python 3.10+
- Create a bot with @BotFather and obtain `TELEGRAM_BOT_TOKEN`.
- Create a staff group, add your bot, send any message to reveal chat id (or set via command below).

1) Install deps

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Configure env

```
cp .env.example .env
# fill TELEGRAM_BOT_TOKEN
# optionally set STAFF_CHAT_ID or use /setstaffchat in the staff group
```

3) Run

```
python -m app.main
```

**Docker + Backups**

- Build and start with backups:

```
cp .env.example .env
# Fill TELEGRAM_BOT_TOKEN (and optionally STAFF_CHAT_ID, ADMIN_USER_IDS)
docker compose up -d --build
```

- Volumes/paths:
  - `./data` is mounted to `/data` in bot container (`DATABASE_PATH=/data/bot.db`).
  - `./backups` stores periodic SQLite backups created by the backup container.

- Backup details:
  - Interval: every 3600 seconds (1 hour) by default; configure via `BACKUP_INTERVAL_SEC` in `docker-compose.yml`.
  - Retention: files older than 14 days are deleted; configure via `BACKUP_KEEP_DAYS`.
  - Backup command uses `sqlite3 .backup` for consistent snapshots with WAL mode.

- Restore from a backup:

```
# Stop bot
docker compose stop bot

# Choose backup file from ./backups, e.g., bot-20240101T120000Z.db
cp ./backups/bot-YYYYMMDDTHHMMSSZ.db ./data/bot.db

# Start bot
docker compose start bot
```


**How to set staff chat**

- Option A: set `STAFF_CHAT_ID` in `.env`.
- Option B: invite the bot to the staff group and run `/setstaffchat` in that group. Only users in `ADMIN_USER_IDS` can set it.

You can use `/chatid` anywhere to get the current Chat ID and your User ID (useful to populate `STAFF_CHAT_ID` and `ADMIN_USER_IDS`).

**Enterprises (companies)**

- Admins:
  - `/company_create <name> [code]` — create a company and get its invite code.
  - `/company_list` — list companies and member counts.
- Tenants:
  - `/company_join <code>` — bind to a company by invite code, or use menu “🔑 Привязать предприятие”.
- After binding, create issues via “🆕 Новая заявка”.

Notes:
- A user is linked to one company (rebinding overwrites previous).
- All issues include company and show its name in staff notifications.

**Data model (SQLite)**

- companies: id, name, invite_code, created_at.
- user_companies: user_id, company_id, created_at.
- issues: id, user_id, user_name, category, description, status, assignee_user_id, assignee_name, staff_chat_id, staff_message_id, tenant_chat_id, company_id, created_at, updated_at.
- issue_photos: id, issue_id, file_id, is_completion, uploader_user_id, created_at.
- settings: key, value (stores staff_chat_id if set via command).

**Notes**

- The bot enforces that only the assignee can complete a task.
- Buttons disable/adjust after claim/complete.
- Media is handled as multiple photos; albums are accepted as individual photos.
