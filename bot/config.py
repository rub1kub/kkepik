# config.py
import os
import sqlite3
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Роли пользователей ──
ROLE_STUDENT = "Я студент"
ROLE_TEACHER = "Я преподаватель"

# ── Режим работы ──
TEST_MODE = os.getenv("TEST_MODE", "False").lower() in ("true", "1", "yes")

# ── Токены (из .env) ──
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TEST_BOT_TOKEN = os.getenv("TEST_BOT_TOKEN", "")

# ── Пути к данным ──
APP_NAME = "kkepik_bot"
if os.name == 'nt':
    APP_DATA = os.getenv('LOCALAPPDATA', os.path.expanduser('~'))
    DATA_DIR = os.path.join(APP_DATA, APP_NAME, 'data')
else:
    APP_DATA = os.path.expanduser('~')
    DATA_DIR = os.path.join(APP_DATA, APP_NAME, 'data')

os.makedirs(DATA_DIR, exist_ok=True)

# ── Базы данных ──
DB_PATH = os.path.join(DATA_DIR, "database.db")
TEST_DB_PATH = os.path.join(DATA_DIR, "testdatabase.db")

if TEST_MODE:
    DB_PATH = TEST_DB_PATH

# ── Порты API ──
API_PORT = 8000
TEST_API_PORT = 8080

# ── Администраторы (из .env: ADMINS=id1,id2,id3) ──
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip()]


def get_bot_token():
    return TEST_BOT_TOKEN if TEST_MODE else BOT_TOKEN


def get_db_path():
    return TEST_DB_PATH if TEST_MODE else DB_PATH


def get_api_port():
    return TEST_API_PORT if TEST_MODE else API_PORT


def init_db():
    conn = sqlite3.connect(get_db_path())
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            role TEXT NOT NULL,
            name_or_group TEXT NOT NULL,
            is_class_teacher INTEGER,
            class_group TEXT
        )
    ''')
    cur.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cur.fetchall()]
    if "is_class_teacher" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN is_class_teacher INTEGER")
    if "class_group" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN class_group TEXT")
    conn.commit()
    conn.close()


logger.debug(f"DATA_DIR: {DATA_DIR}")
logger.debug(f"Тестовый режим: {TEST_MODE}")
logger.debug(f"Используемая база данных: {get_db_path()}")
logger.debug(f"Порт API: {get_api_port()}")

init_db()
