# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KKEPIK Bot — a Telegram + MAX messenger bot for managing and distributing educational schedules at KKEPIK (Russian educational institution). Built with Python 3.12, aiogram 3.19, FastAPI, and SQLite.

## Running

```bash
# Activate virtualenv
source venv/bin/activate

# Run the bot (starts Telegram polling + FastAPI server)
python main.py

# Database migrations (run automatically on startup, or manually)
python migrate_db.py
```

Set `TEST_MODE=true` in `.env` for test database and alternate API port (8080 instead of 8000).

## Architecture

**Dual-process design:** `main.py` starts the aiogram Telegram bot in the main process and spawns the FastAPI API server (`api.py`) in a separate `multiprocessing.Process`.

### Core flow: Schedule upload → broadcast

1. User uploads `.xlsx` or `.pdf` → `handlers/upload_schedule.py` validates and saves to `data/`
2. `schedules/parser_all.py` loads the file format-agnostically into a pandas DataFrame
3. `schedules/group_schedule.py` and `schedules/teacher_schedule.py` extract per-group and per-teacher schedules
4. `schedules/schedule_comparator.py` detects changes vs. previous version
5. `schedules/schedule_image.py` renders PNG preview cards (Montserrat font, PIL)
6. `handlers/schedule_broadcaster.py` sends schedules to registered users
7. `global_schedules.py` maintains an in-memory cache of the latest DataFrames

### Key modules

- **`config.py`** — env-based config, SQLite init with auto-migrations, admin whitelist, user role management
- **`commands/`** — Telegram command handlers (`/start` registration FSM, `/find` schedule search, `/broadcast` admin messaging, `/reset`, `/app`, `/lastmessage`)
- **`handlers/inline_mode.py`** — inline queries with fuzzy matching (`difflib.get_close_matches`)
- **`max_bot/`** — MAX messenger integration via pyromax; monitors configured chats for schedule file uploads and re-distributes via Telegram
- **`schedules/pdf_to_df.py`** — character-level PDF extraction with pdfplumber; handles multi-group page layouts

### Database

SQLite with two tables:
- `users` — user_id (PK), role (student/teacher), name_or_group, is_class_teacher, class_group
- `rep_clicks` — user_id (PK)

### File naming conventions for schedule uploads

- Groups: `*ГРУППЫ*dd.mm.yyyy*.xlsx` or `.pdf`
- Teachers: `*ПРЕПОДАВАТЕЛИ*dd.mm.yyyy*.xlsx` or `.pdf`
- Dates use `.` or `_` separators

## Key patterns

- All handlers are async. The codebase is async-first throughout.
- Registration uses aiogram `StateGroup` FSM for multi-step dialogs.
- Global schedule cache (`global_schedules.reload_cache()`) minimizes repeated file I/O.
- Admin-only commands check against `ADMINS` env variable (comma-separated Telegram user IDs).
- The bot is in Russian — all user-facing strings, group names, and schedule parsing assume Russian text.
