# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**kkepik.ru** — a Flask-based Telegram Web App for a college/school community. It serves as a Telegram Mini App platform providing schedule lookup, attendance tracking, mini-games, VPN key management, and a clicker game. All user authentication goes through Telegram's `initData` (HMAC-verified via `check_init_data()`).

## Running the Application

- **Python 3.12**, virtualenv at `./venv`
- Install deps: `source venv/bin/activate && pip install -r requirements.txt`
- Dev run: `python app.py` (runs on localhost)
- Production: served via Apache/mod_wsgi through `app.wsgi`
- Restart production: `sudo systemctl restart apache2`
- No test suite exists

## Architecture

### Single-file monolith: `app.py` (~4500 lines)

Everything lives in one Flask app file. Key sections (by line ranges):

| Lines | Module |
|-------|--------|
| 1–100 | Imports, Flask init, API caching helpers |
| 100–206 | CORS, logging, SQLite DB connection (`database.db`) with WAL mode, retry logic |
| 207–455 | `init_db()` — all table creation + migrations (ALTER TABLE wrapped in try/except) |
| 456–715 | User management, rating system, energy/upgrade system, Telegram `initData` verification |
| 717–1230 | Page routes + core APIs: congratulation clicker, game score submission, validation |
| 1231–1700 | Attendance system (groups, students, monthly tracking, admin management) |
| 1700–2030 | VPN key management (SSH to remote server via paramiko, Docker container management) |
| 2030–2560 | Admin panel (session auth, DB table browser, clicker settings, energy management) |
| 2560–2870 | Game anti-cheat: server-side move verification for 2048 and Snake |
| 2870–3380 | Battleship multiplayer (in-memory game state, `battleship_games` dict) |
| 3380–3570 | Schedule reactions, reviews |
| 3570–3680 | Casino mini-game |
| 3680–3860 | Favorites system, subject hours tracking with auto-deduction |
| 3860–4460 | Checkers multiplayer (in-memory), Sudoku generator/solver |

### `schedule_api.py` — Schedule API proxy Blueprint

Proxies schedule requests to the loopback-only FastAPI service at `config.API_URL` (`http://127.0.0.1:8000`). Registered as `schedule_bp`. Rate-limited to 10 req/sec via flask-limiter. Swagger docs at `/api/docs`.

### Database

- **SQLite** file: `database.db` (WAL journal mode)
- Key tables: `users`, `rating_snake`, `rating_2048`, `attendance_groups`, `students`, `attendance`, `vpn_keys`, `game_sessions_2048`, `game_sessions_snake`, `battleship` (in-memory only), `casino_users`, `favorite_entities`, `subject_hours`, `sudoku_games`, `sudoku_scores`, `congratulations`, `user_upgrades`, `schedule_reactions`, `reviews`
- All DB access goes through `get_db()` (Flask `g` context) and `safe_db_operation()` for retry on lock
- Migrations are inline in `init_db()` — new columns added via `ALTER TABLE` in try/except blocks

### Frontend

- Templates in `templates/` — Jinja2 HTML, organized by feature (`games/`, `attendance/`, `vpn/`, `admin/`)
- Static assets in `static/` — per-feature JS and CSS files (`js/snake.js`, `css/snake.css`, etc.)
- Cache-busting via `STATIC_VERSION` constant
- Telegram WebApp JS SDK used for auth and UI integration

### Key Patterns

- **Auth**: Telegram `initData` verified via HMAC (`check_init_data()`). Admin panel uses Flask session auth (`session['admin_authenticated']`). VPN admin has separate session check (`admin_required` decorator).
- **Clicker tokens**: Short-lived bearer tokens (`CLICKER_TOKENS` in-memory dict) bound to IP+UA, with abuse penalty system.
- **Multiplayer games** (Battleship, Checkers): State stored in in-memory Python dicts (`battleship_games`, `checkers_games`), lost on restart.
- **All comments and UI text are in Russian.**

## config.py

Contains only `API_URL` — the address of the external schedule FastAPI backend.
