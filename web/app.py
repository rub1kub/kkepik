from flask import Flask, render_template, request, jsonify, abort, redirect, url_for, send_file
import os
import hmac
import hashlib
import logging
import time
import json
import secrets
from urllib.parse import unquote
import sqlite3
from flask import g
import subprocess
import random
import string
import base64
import paramiko
import socket
import requests
from functools import wraps
from flask import session
from flask_swagger_ui import get_swaggerui_blueprint
from schedule_api import schedule_bp
from config import API_URL
from collections import defaultdict
import uuid

app = Flask(__name__)
app.secret_key = 'super_secret_key_for_sessions'
BOT_TOKEN = os.getenv('BOT_TOKEN', '7446409382:AAHZXyW-JQkiyk2Ln28bJWXe8asS4GGhUmM')

# Простое кэширование для API данных
api_cache = {
    'total_congratulations': {'value': 0, 'timestamp': 0},
    'rating': {'value': [], 'timestamp': 0},
    'stats': {'value': {}, 'timestamp': 0},
    'user_data': {}  # Кэш для пользовательских данных
}
CACHE_DURATION = 10  # секунд
USER_CACHE_DURATION = 5  # секунд для пользовательских данных

def is_event_active():
    """Проверяет, активен ли ивент (после 19:00 МСК 27 сентября 2025)"""
    import datetime
    import pytz
    
    # Создаем московское время
    moscow_tz = pytz.timezone('Europe/Moscow')
    now_moscow = datetime.datetime.now(moscow_tz)
    
    # Дата начала ивента: 27 сентября 2025, 19:00 МСК
    event_start = moscow_tz.localize(datetime.datetime(2025, 9, 27, 19, 0, 0))
    
    is_active = now_moscow >= event_start
    app.logger.info(f"Event check: now={now_moscow}, start={event_start}, active={is_active}")
    
    return is_active

def get_cached_data(key):
    """Получить данные из кэша, если они еще актуальны"""
    current_time = time.time()
    if key in api_cache:
        cache_entry = api_cache[key]
        if current_time - cache_entry['timestamp'] < CACHE_DURATION:
            return cache_entry['value']
    return None

def set_cached_data(key, value):
    """Сохранить данные в кэш"""
    api_cache[key] = {
        'value': value,
        'timestamp': time.time()
    }

def get_cached_user_data(user_id):
    """Получить пользовательские данные из кэша"""
    current_time = time.time()
    if user_id in api_cache['user_data']:
        cache_entry = api_cache['user_data'][user_id]
        if current_time - cache_entry['timestamp'] < USER_CACHE_DURATION:
            return cache_entry['value']
    return None

def set_cached_user_data(user_id, value):
    """Сохранить пользовательские данные в кэш"""
    api_cache['user_data'][user_id] = {
        'value': value,
        'timestamp': time.time()
    }

# CORS support for API endpoints
@app.after_request
def after_request(response):
    # Allow CORS for API endpoints and Telegram WebApp
    if request.path.startswith('/api/') or 'telegram' in request.headers.get('User-Agent', '').lower():
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Telegram-Init-Data')
        response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
        response.headers.add('Access-Control-Allow-Credentials', 'false')
    return response

@app.route('/api/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    response = jsonify({'status': 'ok'})
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Telegram-Init-Data')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# Версия для предотвращения кэширования статических файлов
STATIC_VERSION = "5.0.0"

log_path = os.path.join(os.path.dirname(__file__), 'log')
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    handlers=[
        logging.FileHandler(log_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

app.logger = logging.getLogger(__name__)

DATABASE = os.path.join(os.path.dirname(__file__), 'database.db')

MAX_SCORE_PER_MOVE = 2048  # Максимально возможные очки за один ход
MIN_MOVE_TIME = 0.1  # Минимальное время между ходами в секундах
MAX_TOTAL_SCORE = 200000  # Максимально возможный счет
SECRET_KEY = b'403090WOW'  # Замените на ваш секретный ключ

# Добавим новые константы для змейки
MAX_SNAKE_SPEED = 100  # Минимальное время между ходами в миллисекундах
MAX_SNAKE_SCORE_PER_FOOD = 10  # Максимальные очки за одну еду
MAX_SNAKE_LENGTH = 500  # Максимальная длина змейки (с учетом размера поля)
MAX_SNAKE_TOTAL_SCORE = 400  # Максимально возможный счет

# Добавляем константы для 2048
MAX_2048_MOVE_TIME = 30  # теперь 30 мс между ходами (можно увеличить ещё)
MAX_2048_SCORE_PER_MOVE = 2048  # Максимальный прирост очков за один ход
MAX_2048_TOTAL_SCORE = 200000  # Максимально возможный счет

# Настройка Swagger
SWAGGER_URL = '/api/docs'
API_URL = '/static/swagger.json'

swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={
        'app_name': "API Расписания",
        'docExpansion': 'list',
        'defaultModelsExpandDepth': -1,
        'displayRequestDuration': True,
        'persistAuthorization': True,
        'layout': 'BaseLayout',  # Убираем верхнюю панель
        'url': 'https://kkepik.ru/static/swagger.json',  # Устанавливаем полный URL для документации
        'validatorUrl': None  # Отключаем валидатор
    }
)

# Регистрация Blueprint'ов
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)
app.register_blueprint(schedule_bp)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE, timeout=30.0)
        db.row_factory = sqlite3.Row
        # Настройки для лучшей производительности и предотвращения блокировок
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA cache_size=10000")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA busy_timeout=30000")  # 30 секунд таймаут
        db.execute("PRAGMA foreign_keys=ON")
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        try:
            db.close()
        except Exception as e:
            app.logger.error(f"Error closing database connection: {e}")

def safe_db_operation(operation, max_retries=3, retry_delay=0.1):
    """Безопасное выполнение операций с базой данных с retry логикой"""
    for attempt in range(max_retries):
        try:
            db = get_db()
            return operation(db)
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                app.logger.warning(f"Database locked, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
                continue
            else:
                app.logger.error(f"Database operation failed after {max_retries} attempts: {e}")
                raise
        except Exception as e:
            app.logger.error(f"Unexpected database error: {e}")
            raise

def init_db():
    with app.app_context():
        db = get_db()
        
        # Проверяем существование колонки last_attendance_group_id
        cursor = db.cursor()
        columns = cursor.execute("PRAGMA table_info(users)").fetchall()
        has_last_attendance_group = any(column[1] == 'last_attendance_group_id' for column in columns)

        # Базовый SQL скрипт для создания таблиц
        base_script = '''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            tg_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT
        );
        CREATE TABLE IF NOT EXISTS rating_snake (
            user_id INTEGER,
            score INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS rating_2048 (
            user_id INTEGER,
            score INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS history_snake (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            delta INTEGER,
            new_score INTEGER,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS history_2048 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            delta INTEGER,
            new_score INTEGER,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS attendance_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            creator_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(creator_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY(group_id) REFERENCES attendance_groups(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            date DATE NOT NULL,
            absences INTEGER DEFAULT 0,
            excused_absences INTEGER DEFAULT 0,
            FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
            UNIQUE(student_id, date)
        );
        CREATE TABLE IF NOT EXISTS group_admins (
            group_id INTEGER,
            user_id INTEGER,
            FOREIGN KEY(group_id) REFERENCES attendance_groups(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS vpn_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            port INTEGER NOT NULL,
            key_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS game_moves_2048 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_id TEXT NOT NULL,
            move_number INTEGER NOT NULL,
            board_state TEXT NOT NULL,
            score INTEGER NOT NULL,
            timestamp REAL NOT NULL,
            move_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS game_sessions_2048 (
            game_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            start_time REAL NOT NULL,
            last_move_time REAL NOT NULL,
            current_score INTEGER NOT NULL,
            moves_count INTEGER NOT NULL,
            is_finished BOOLEAN NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS game_moves_snake (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_id TEXT NOT NULL,
            move_number INTEGER NOT NULL,
            snake_state TEXT NOT NULL,
            food_position TEXT NOT NULL,
            score INTEGER NOT NULL,
            timestamp REAL NOT NULL,
            move_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS game_sessions_snake (
            game_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            start_time REAL NOT NULL,
            last_move_time REAL NOT NULL,
            current_score INTEGER NOT NULL,
            snake_length INTEGER NOT NULL,
            moves_count INTEGER NOT NULL,
            is_finished BOOLEAN NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS schedule_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            reaction TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            UNIQUE(user_id, date, reaction)
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mark INTEGER NOT NULL,
            review TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS casino_users (
            user_id INTEGER PRIMARY KEY,
            score INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS favorite_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            entity_type TEXT NOT NULL, -- 'group' или 'teacher'
            entity_id TEXT NOT NULL,   -- id группы или ФИО преподавателя
            entity_name TEXT NOT NULL, -- название группы или ФИО преподавателя
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS subject_hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject_name TEXT NOT NULL,
            teacher_name TEXT NOT NULL,
            group_name TEXT NOT NULL,
            planned_hours REAL DEFAULT 0,
            completed_hours REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            UNIQUE(user_id, subject_name, teacher_name, group_name)
        );
        CREATE TABLE IF NOT EXISTS sudoku_games (
            game_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            difficulty TEXT NOT NULL,
            puzzle TEXT NOT NULL,
            solution TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS sudoku_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            first_name TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            completion_time INTEGER NOT NULL,
            hints_used INTEGER DEFAULT 0,
            submitted_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(game_id) REFERENCES sudoku_games(game_id)
        );
        '''
        
        # Выполняем базовый скрипт
        db.executescript(base_script)
        
        # Если колонка last_attendance_group_id не существует, добавляем её
        if not has_last_attendance_group:
            try:
                db.execute('ALTER TABLE users ADD COLUMN last_attendance_group_id INTEGER DEFAULT NULL REFERENCES attendance_groups(id)')
            except Exception as e:
                app.logger.error(f"Error adding last_attendance_group_id column: {e}")
        
        # Создаем таблицу для поздравлений
        db.execute('''
            CREATE TABLE IF NOT EXISTS congratulations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Добавляем поля для системы энергии
        try:
            db.execute('ALTER TABLE users ADD COLUMN energy_current INTEGER DEFAULT 150')
            db.execute('ALTER TABLE users ADD COLUMN energy_max INTEGER DEFAULT 150')
            db.execute('ALTER TABLE users ADD COLUMN energy_regen_rate INTEGER DEFAULT 2')
            db.execute('ALTER TABLE users ADD COLUMN energy_regen_interval INTEGER DEFAULT 8000')
            db.execute('ALTER TABLE users ADD COLUMN energy_last_regen INTEGER DEFAULT 0')
        except Exception as e:
            # Поля уже существуют
            pass
        
        # Обновляем пользователей с energy_last_regen = 0 до текущего времени
        current_time = int(time.time() * 1000)
        db.execute('''
            UPDATE users SET energy_last_regen = ? 
            WHERE energy_last_regen IS NULL OR energy_last_regen = 0
        ''', (current_time,))
        
        
        # Создаем таблицу для апгрейдов пользователей
        db.execute('''
            CREATE TABLE IF NOT EXISTS user_upgrades (
                user_id INTEGER,
                upgrade_type TEXT,
                level INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, upgrade_type),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        
        # Создаем таблицу для трат поздравлений
        db.execute('''
            CREATE TABLE IF NOT EXISTS congratulations_spent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        db.commit()

init_db()

def migrate_existing_upgrades():
    """Применить существующие апгрейды к пользователям (миграция)"""
    try:
        db = get_db()
        users_with_upgrades = db.execute('''
            SELECT DISTINCT user_id FROM user_upgrades
        ''').fetchall()
        
        for user_row in users_with_upgrades:
            user_id = user_row['user_id']
            upgrades = db.execute('''
                SELECT upgrade_type, level FROM user_upgrades WHERE user_id = ?
            ''', (user_id,)).fetchall()
            
            for upgrade in upgrades:
                apply_upgrade_effects(user_id, upgrade['upgrade_type'], upgrade['level'])
    except Exception as e:
        app.logger.error(f"Ошибка миграции апгрейдов: {e}")

def get_or_create_user(tg_id, username, first_name):
    def _get_or_create_user(db):
        user = db.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,)).fetchone()
        if user:
            # Обновляем имя и username при каждом заходе
            db.execute('UPDATE users SET first_name = ?, username = ? WHERE tg_id = ?', (first_name, username, tg_id))
            db.commit()
            return user['id']
        
        # При создании нового пользователя инициализируем энергию
        current_time = int(time.time() * 1000)
        db.execute('''
            INSERT INTO users (tg_id, username, first_name, energy_current, energy_max, 
                              energy_regen_rate, energy_regen_interval, energy_last_regen) 
            VALUES (?, ?, ?, 150, 150, 2, 8000, ?)
        ''', (tg_id, username, first_name, current_time))
        db.commit()
        return db.execute('SELECT id FROM users WHERE tg_id = ?', (tg_id,)).fetchone()['id']
    
    try:
        return safe_db_operation(_get_or_create_user)
    except Exception as e:
        app.logger.warning(f"Failed to create user: {e}")
        return None

def update_rating(game, user_id, delta):
    db = get_db()
    table = f'rating_{game}'
    hist = f'history_{game}'
    cur = db.execute(f'SELECT score FROM {table} WHERE user_id = ?', (user_id,)).fetchone()
    if cur:
        new_score = max(0, cur['score'] + delta)
        db.execute(f'UPDATE {table} SET score = ? WHERE user_id = ?', (new_score, user_id))
    else:
        new_score = max(0, delta)
        db.execute(f'INSERT INTO {table} (user_id, score) VALUES (?, ?)', (user_id, new_score))
    db.execute(f'INSERT INTO {hist} (user_id, delta, new_score) VALUES (?, ?, ?)', (user_id, delta, new_score))
    db.commit()
    return new_score

def get_rating(game):
    db = get_db()
    table = f'rating_{game}'
    return db.execute(f'''SELECT u.id as user_id, u.first_name, u.username, r.score FROM {table} r JOIN users u ON r.user_id = u.id ORDER BY r.score DESC LIMIT 50''').fetchall()

def get_user_energy(user_id):
    """Получить текущую энергию пользователя с учетом регенерации"""
    # Проверяем кэш пользовательских данных
    cached_user_data = get_cached_user_data(user_id)
    if cached_user_data and cached_user_data.get('energy'):
        return cached_user_data['energy']
    
    db = get_db()
    user = db.execute('''
        SELECT energy_current, energy_max, energy_regen_rate, energy_regen_interval, energy_last_regen 
        FROM users WHERE tg_id = ?
    ''', (user_id,)).fetchone()
    
    if not user:
        app.logger.warning(f"User not found in get_user_energy: {user_id}")
        return None
    
    current_time = int(time.time() * 1000)  # миллисекунды
    last_regen = user['energy_last_regen'] or current_time
    time_passed = current_time - last_regen
    
    # Вычисляем сколько энергии восстановилось
    if time_passed >= user['energy_regen_interval']:
        regen_count = time_passed // user['energy_regen_interval']
        new_energy = min(user['energy_max'], user['energy_current'] + regen_count * user['energy_regen_rate'])
        new_last_regen = last_regen + (regen_count * user['energy_regen_interval'])
        
        # Обновляем в БД
        db.execute('''
            UPDATE users SET energy_current = ?, energy_last_regen = ? WHERE tg_id = ?
        ''', (new_energy, new_last_regen, user_id))
        db.commit()
        
        return {
            'current': new_energy,
            'max': user['energy_max'],
            'regen_rate': user['energy_regen_rate'],
            'regen_interval': user['energy_regen_interval'],
            'last_regen': new_last_regen
        }
    
    return {
        'current': user['energy_current'],
        'max': user['energy_max'],
        'regen_rate': user['energy_regen_rate'],
        'regen_interval': user['energy_regen_interval'],
        'last_regen': last_regen
    }

def consume_user_energy(user_id, amount=1):
    """Потратить энергию пользователя"""
    energy = get_user_energy(user_id)
    if not energy or energy['current'] < amount:
        return False
    
    db = get_db()
    db.execute('''
        UPDATE users SET energy_current = energy_current - ? WHERE tg_id = ?
    ''', (amount, user_id))
    db.commit()
    return True

def get_user_upgrades(user_id):
    """Получить апгрейды пользователя"""
    db = get_db()
    # Сначала находим внутренний ID пользователя по tg_id
    user = db.execute('SELECT id FROM users WHERE tg_id = ?', (user_id,)).fetchone()
    if not user:
        return {'capacity': 1, 'speed': 1}
    
    upgrades = db.execute('''
        SELECT upgrade_type, level FROM user_upgrades WHERE user_id = ?
    ''', (user['id'],)).fetchall()
    
    result = {'capacity': 1, 'speed': 1}
    for upgrade in upgrades:
        result[upgrade['upgrade_type']] = upgrade['level']
    
    return result

def get_upgrade_cost(upgrade_type, current_level):
    """Вычислить стоимость апгрейда"""
    base_costs = {'capacity': 50, 'speed': 30}
    if upgrade_type not in base_costs:
        return None
    
    return base_costs[upgrade_type] * (2 ** (current_level - 1))

def apply_upgrade_effects(user_id, upgrade_type, level):
    """Применить эффекты апгрейда к энергии пользователя"""
    db = get_db()
    
    if upgrade_type == 'capacity':
        # Увеличиваем максимальную энергию на 30 за каждый уровень (от базовых 150)
        new_max = 150 + (level - 1) * 30
        
        # Получаем текущую энергию
        current_energy = db.execute('SELECT energy_current FROM users WHERE tg_id = ?', (user_id,)).fetchone()['energy_current']
        
        # Увеличиваем текущую энергию на разницу между новым и старым максимумом
        old_max = 150 + max(0, (level - 2)) * 30  # Предыдущий максимум (минимум 150)
        energy_bonus = new_max - old_max  # Бонус энергии (+30)
        new_current = current_energy + energy_bonus
        
        # Обновляем максимальную и текущую энергию
        db.execute('UPDATE users SET energy_max = ?, energy_current = ? WHERE tg_id = ?', (new_max, new_current, user_id))
    elif upgrade_type == 'speed':
        # Уменьшаем интервал регенерации на 1 секунду за каждый уровень (от базовых 8 сек)
        new_interval = max(3000, 8000 - (level - 1) * 1000)
        db.execute('UPDATE users SET energy_regen_interval = ? WHERE tg_id = ?', (new_interval, user_id))
    
    db.commit()

def buy_upgrade(user_id, upgrade_type):
    """Купить апгрейд"""
    db = get_db()
    
    # Получаем текущий уровень апгрейда
    current_upgrades = get_user_upgrades(user_id)
    current_level = current_upgrades.get(upgrade_type, 1)
    
    # Вычисляем стоимость
    cost = get_upgrade_cost(upgrade_type, current_level)
    if cost is None:
        return {'success': False, 'message': 'Неизвестный тип апгрейда'}
    
    # Проверяем баланс пользователя (поздравления минус траты)
    earned = db.execute('''
        SELECT COUNT(*) as count FROM congratulations WHERE user_id = ?
    ''', (user_id,)).fetchone()['count']
    
    spent = db.execute('''
        SELECT COALESCE(SUM(amount), 0) as total FROM congratulations_spent WHERE user_id = ?
    ''', (user_id,)).fetchone()['total']
    
    current_balance = earned - spent
    
    if current_balance < cost:
        return {'success': False, 'message': 'Недостаточно поздравлений'}
    
    # Записываем трату поздравлений на апгрейд
    db.execute('''
        INSERT INTO congratulations_spent (user_id, amount, reason, timestamp) 
        VALUES (?, ?, ?, ?)
    ''', (user_id, cost, f'upgrade_{upgrade_type}', int(time.time() * 1000)))
    
    # Получаем внутренний ID пользователя
    user = db.execute('SELECT id FROM users WHERE tg_id = ?', (user_id,)).fetchone()
    if not user:
        return {'success': False, 'message': 'Пользователь не найден'}
    
    # Обновляем или создаем запись апгрейда
    new_level = current_level + 1
    db.execute('''
        INSERT OR REPLACE INTO user_upgrades (user_id, upgrade_type, level)
        VALUES (?, ?, ?)
    ''', (user['id'], upgrade_type, new_level))
    
    # Применяем эффекты апгрейда
    apply_upgrade_effects(user_id, upgrade_type, new_level)
    
    db.commit()
    
    return {
        'success': True, 
        'new_level': new_level,
        'new_balance': current_balance - cost
    }

def parse_init_data_params(init_data):
    """Безопасный парсинг init_data параметров"""
    params = {}
    for item in init_data.split('&'):
        if '=' in item:
            key, value = item.split('=', 1)
            params[key] = unquote(value)
    return params

def check_init_data(init_data):
    try:
        secret_key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
        params = parse_init_data_params(init_data)
        hash_ = params.pop('hash', None)
        if not hash_:
            return False
        auth_date = int(params.get('auth_date', 0))
        current_time = int(time.time())
        if abs(current_time - auth_date) > 86400:
            return False
        data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(params.items()))
        hmac_hash = hmac.new(secret_key, data_check_string.encode('utf-8'), hashlib.sha256).hexdigest()
        return hmac_hash == hash_
    except Exception as e:
        app.logger.error(f'Ошибка проверки initData: {e}')
        return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/test')
def test_page():
    return render_template('test.html')

@app.route('/congratulate')
def congratulate_game():
    # Проверяем, активен ли ивент
    if is_event_active():
        return render_template('event_page.html')
    return render_template('congratulate_game.html')

@app.route('/event-end')
def event_end():
    """Страница окончания ивента"""
    return render_template('event_end_page.html')

@app.route('/api/session', methods=['POST'])
def api_session():
    """Создать короткоживущую сессию кликера: initData -> bearer token"""
    try:
        data = request.get_json() or {}
        init_data = request.headers.get('X-Telegram-Init-Data') or data.get('initData')
        if not init_data or not check_init_data(init_data):
            return jsonify({'success': False, 'message': 'auth'}), 403
        params = parse_init_data_params(init_data)
        user_obj = json.loads(params.get('user', '{}'))
        user_id = user_obj.get('id')
        if not user_id:
            return jsonify({'success': False, 'message': 'no user'}), 400
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        ua = request.headers.get('User-Agent', '')
        token = issue_clicker_token(str(user_id), ip, ua, ttl_sec=900)
        return jsonify({'success': True, 'token': token, 'ttl': 900})
    except Exception as e:
        app.logger.error(f"Ошибка в api_session: {e}")
        return jsonify({'success': False, 'message': 'server error'}), 500

@app.route('/api/congratulate', methods=['POST'])
def api_congratulate():
    """API для отправки поздравления"""
    # Проверяем, активен ли ивент
    if is_event_active():
        return jsonify({'success': False, 'message': 'Игра приостановлена для проведения ивента'}), 503
    
    try:
        data = request.get_json() or {}
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        ua = request.headers.get('User-Agent', '')

        # Bearer: Authorization: Bearer <token>
        auth = request.headers.get('Authorization', '')
        token = ''
        if auth.lower().startswith('bearer '):
            token = auth.split(' ', 1)[1].strip()
        user_id = verify_clicker_token(token, ip, ua)
        if not user_id:
            # Fallback: разовая проверка initData (например, для sendBeacon)
            init_data = request.headers.get('X-Telegram-Init-Data') or data.get('initData')
            if not init_data or not check_init_data(init_data):
                return jsonify({'success': False, 'message': 'auth'}), 403
            params = parse_init_data_params(init_data)
            user_obj = json.loads(params.get('user', '{}'))
            user_id = str(user_obj.get('id')) if user_obj.get('id') else None
            user_name = user_obj.get('first_name') or data.get('userName') or 'Аноним'
        else:
            # Если валидный токен — имя можем не использовать
            user_name = data.get('userName', 'Аноним')
        timestamp = int(data.get('timestamp', int(time.time() * 1000)))
        # Поддержка батчинга с клиента
        try:
            count = int(data.get('count', 1))
        except Exception:
            count = 1
        # Санитайз: разумные пределы
        if count < 1:
            count = 1
        if count > 50:
            count = 50
        
        if not user_id:
            return jsonify({'success': False, 'message': 'Неверные данные пользователя'})

        # Лимиты по IP (дополнительно к user rate-limit)
        if not ip:
            ip = '0.0.0.0'
        
        # Проверка на спам (максимум 10 поздравлений в секунду от одного пользователя)
        current_time = int(time.time() * 1000)
        db = get_db()
        
        # Получаем количество поздравлений пользователя за последнюю секунду
        one_second_ago = current_time - 1000
        recent_congratulations = db.execute(
            'SELECT COUNT(*) as count FROM congratulations WHERE user_id = ? AND timestamp > ?',
            (str(user_id), one_second_ago)
        ).fetchone()
        recent = int(recent_congratulations['count'] or 0)
        limit_per_sec = 20
        # Доп. мягкий лимит по IP: до 20 событий/сек на IP
        # Мягкий лимит по IP: если колонки ip нет — не падаем
        try:
            ip_recent = db.execute(
                'SELECT COUNT(*) as count FROM congratulations WHERE ip = ? AND timestamp > ?',
                (ip, one_second_ago)
            ).fetchone()
            ip_allowed = max(0, 20 - int(ip_recent['count'] or 0))
        except Exception:
            app.logger.warning('SELECT по ip недоступен (нет колонки?) — пропускаем IP-лимит')
            ip_allowed = 20
        # Итоговое разрешённое число учитывает оба лимита
        allowed = max(0, limit_per_sec - recent)
        allowed = min(allowed, ip_allowed)
        # Простейшая пеня за аномалии: если юзер под пенальти — урезаем наполовину
        if is_under_penalty(user_id):
            allowed = allowed // 2
        accepted = min(count, allowed)

        if accepted <= 0:
            return jsonify({'success': False, 'message': 'Слишком быстро! Максимум 20 поздравлений в секунду.', 'accepted': 0}), 429

        # Проверяем энергию пользователя для принятого количества кликов
        energy = get_user_energy(user_id)
        if not energy or energy['current'] < accepted:
            # Урезаем количество кликов до доступной энергии
            available_clicks = energy['current'] if energy else 0
            if available_clicks <= 0:
                return jsonify({
                    'success': False, 
                    'message': 'Недостаточно энергии',
                    'energy': energy,
                    'error_type': 'insufficient_energy'
                }), 400
            accepted = min(accepted, available_clicks)

        # Пакетная вставка принятых кликов (с IP, если колонка существует)
        rows = [(str(user_id), user_name, timestamp, ip) for _ in range(accepted)]
        try:
            db.executemany(
                'INSERT INTO congratulations (user_id, user_name, timestamp, ip) VALUES (?, ?, ?, ?)',
                rows
            )
        except Exception as _e:
            app.logger.warning('congratulations.ip недоступна, откат к вставке без IP')
            db.executemany(
                'INSERT INTO congratulations (user_id, user_name, timestamp) VALUES (?, ?, ?)',
                [(u, n, t) for (u, n, t, _ip) in rows]
            )
        db.commit()
        
        # Сбрасываем кэш после добавления поздравлений
        api_cache['total_congratulations']['timestamp'] = 0
        api_cache['rating']['timestamp'] = 0
        api_cache['stats']['timestamp'] = 0
        # Сбрасываем кэш пользовательских данных
        if user_id in api_cache['user_data']:
            api_cache['user_data'][user_id]['timestamp'] = 0
        
        # Тратим энергию
        if not consume_user_energy(user_id, accepted):
            app.logger.warning(f"Не удалось потратить энергию для пользователя {user_id}")
        
        # Получаем общее количество поздравлений
        total = db.execute('SELECT COUNT(*) as count FROM congratulations').fetchone()['count']
        
        # Получаем обновленную энергию
        updated_energy = get_user_energy(user_id)
        
        response = jsonify({
            'success': True,
            'total': total,
            'accepted': accepted,
            'rate_limit_per_sec': limit_per_sec,
            'message': 'Поздравление отправлено!',
            'energy': updated_energy
        })
        
        # Добавляем CORS заголовки
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Telegram-Init-Data')
        response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
        
        return response
        
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            app.logger.error(f"Ошибка в api_congratulate: database is locked")
            return jsonify({'success': False, 'message': 'База данных временно недоступна, попробуйте позже'})
        else:
            app.logger.error(f"Ошибка в api_congratulate: {e}")
            return jsonify({'success': False, 'message': 'Внутренняя ошибка сервера'})
    except Exception as e:
        app.logger.error(f"Ошибка в api_congratulate: {e}")
        return jsonify({'success': False, 'message': 'Внутренняя ошибка сервера'})

@app.route('/api/congratulate-data')
def api_congratulate_data():
    """API для получения данных о поздравлениях"""
    try:
        # Пытаемся получить данные из разных источников
        init_data = request.args.get('tgWebAppData') or request.headers.get('X-Telegram-Init-Data')
        user_id = None
        
        app.logger.info(f"API congratulate-data: initData present: {bool(init_data)}")
        
        if init_data:
            try:
                # Проверяем подпись initData и достаем user_id
                check_result = check_init_data(init_data)
                app.logger.info(f"check_init_data result: {check_result}")
                
                if check_result:
                    params = parse_init_data_params(init_data)
                    user_obj = json.loads(params.get('user', '{}'))
                    if user_obj and user_obj.get('id'):
                        user_id = str(user_obj.get('id'))
                        app.logger.info(f"Extracted user_id: {user_id}")
                    else:
                        app.logger.warning(f"No user ID in parsed data: {user_obj}")
                else:
                    app.logger.warning(f"check_init_data failed for initData: {init_data[:50]}...")
            except Exception as e:
                app.logger.warning(f"Exception parsing initData: {e}")
        else:
            app.logger.warning("No initData provided")
        
        # Если не удалось получить user_id из Telegram данных, 
        # используем заголовок X-User-ID (если есть)
        if not user_id:
            user_id = request.headers.get('X-User-ID')
        
        current_time = int(time.time() * 1000)
        
        db = get_db()
        
        # Получаем общее количество поздравлений (с кэшированием)
        total = get_cached_data('total_congratulations')
        if total is None:
            total = db.execute('SELECT COUNT(*) as count FROM congratulations').fetchone()['count']
            set_cached_data('total_congratulations', total)
        
        # Получаем количество поздравлений пользователя, энергию и апгрейды
        user_count = 0
        user_energy = None
        user_upgrades = None
        
        # Проверяем кэш пользовательских данных
        cached_user_data = get_cached_user_data(user_id) if user_id else None
        if cached_user_data:
            user_count = cached_user_data.get('count', 0)
            user_energy = cached_user_data.get('energy')
            user_upgrades = cached_user_data.get('upgrades')
        elif user_id:
            # Создаем пользователя если его нет в таблице users
            if init_data:
                try:
                    params = parse_init_data_params(init_data)
                    user_obj = json.loads(params.get('user', '{}'))
                    if user_obj:
                        get_or_create_user(
                            int(user_id), 
                            user_obj.get('username', ''), 
                            user_obj.get('first_name', 'Аноним')
                        )
                except Exception as e:
                    app.logger.warning(f"Failed to create user: {e}")
            
            # Вычисляем реальный баланс (заработанные минус потраченные)
            earned = db.execute(
                'SELECT COUNT(*) as count FROM congratulations WHERE user_id = ?',
                (user_id,)
            ).fetchone()['count']
            
            spent = db.execute(
                'SELECT COALESCE(SUM(amount), 0) as total FROM congratulations_spent WHERE user_id = ?',
                (user_id,)
            ).fetchone()['total']
            
            user_count = earned - spent
            
            # Фолбэк: если пользователь есть в congratulations, но нет в users - создаем его
            user_energy = get_user_energy(user_id)
            if not user_energy and earned > 0:
                app.logger.info(f"Creating missing user {user_id} found in congratulations")
                # Получаем имя пользователя из последней записи congratulations
                last_congratulation = db.execute(
                    'SELECT user_name FROM congratulations WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1',
                    (user_id,)
                ).fetchone()
                user_name = last_congratulation['user_name'] if last_congratulation else 'Аноним'
                
                # Создаем пользователя с дефолтными данными
                get_or_create_user(int(user_id), '', user_name)
                user_energy = get_user_energy(user_id)
                
            user_upgrades = get_user_upgrades(user_id)
            
            # Сохраняем в кэш
            set_cached_user_data(user_id, {
                'count': user_count,
                'energy': user_energy,
                'upgrades': user_upgrades
            })
        
        # Получаем рейтинг пользователей (с кэшированием)
        rating_list = get_cached_data('rating')
        if rating_list is None:
            rating = db.execute('''
                SELECT 
                    c.user_id AS user_id,
                    (
                        SELECT c2.user_name 
                        FROM congratulations c2 
                        WHERE c2.user_id = c.user_id 
                        ORDER BY c2.timestamp DESC 
                        LIMIT 1
                    ) AS user_name,
                    COUNT(*) AS count
                FROM congratulations c
                GROUP BY c.user_id
                ORDER BY count DESC
                LIMIT 20
            ''').fetchall()
            
            rating_list = []
            for row in rating:
                rating_list.append({
                    'userId': row['user_id'],
                    'userName': row['user_name'],
                    'count': row['count']
                })
            set_cached_data('rating', rating_list)
        
        # Получаем статистику (с кэшированием)
        stats = get_cached_data('stats')
        if stats is None:
            stats = {}
            
            # Общее количество поздравлений
            stats['total_congratulations'] = total
            
            # Количество уникальных пользователей
            unique_users = db.execute('SELECT COUNT(DISTINCT user_id) as count FROM congratulations').fetchone()['count']
            stats['unique_users'] = unique_users
            
            # Онлайн (пользователи, которые поздравляли за последние 5 минут)
            five_minutes_ago = current_time - (5 * 60 * 1000)
            online_users = db.execute(
                'SELECT COUNT(DISTINCT user_id) as count FROM congratulations WHERE timestamp > ?',
                (five_minutes_ago,)
            ).fetchone()['count']
            stats['online_users'] = online_users
            
            # Поздравлений за последний час
            one_hour_ago = current_time - (60 * 60 * 1000)
            recent_congratulations = db.execute(
                'SELECT COUNT(*) as count FROM congratulations WHERE timestamp > ?',
                (one_hour_ago,)
            ).fetchone()['count']
            stats['recent_congratulations'] = recent_congratulations
            
            # Среднее количество поздравлений на пользователя
            avg_per_user = total / unique_users if unique_users > 0 else 0
            stats['avg_per_user'] = round(avg_per_user, 1)
            
            set_cached_data('stats', stats)
        
        response = jsonify({
            'success': True,
            'total': total,
            'userCount': user_count,
            'rating': rating_list,
            'stats': stats,
            'energy': user_energy,
            'upgrades': user_upgrades
        })
        
        # Добавляем CORS заголовки
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Telegram-Init-Data')
        response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
        
        return response
        
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            app.logger.error(f"Ошибка в api_congratulate_data: database is locked")
            return jsonify({'success': False, 'message': 'База данных временно недоступна, попробуйте позже'})
        else:
            app.logger.error(f"Ошибка в api_congratulate_data: {e}")
            return jsonify({'success': False, 'message': 'Внутренняя ошибка сервера'})
    except Exception as e:
        app.logger.error(f"Ошибка в api_congratulate_data: {e}")
        return jsonify({'success': False, 'message': 'Внутренняя ошибка сервера'})

@app.route('/api/buy-upgrade', methods=['POST'])
def api_buy_upgrade():
    """API для покупки апгрейдов"""
    try:
        data = request.get_json() or {}
        upgrade_type = data.get('upgrade_type')
        
        if not upgrade_type or upgrade_type not in ['capacity', 'speed']:
            return jsonify({'success': False, 'message': 'Неверный тип апгрейда'}), 400
        
        # Получаем user_id из initData
        init_data = request.headers.get('X-Telegram-Init-Data') or data.get('initData')
        user_id = None
        
        if init_data:
            try:
                if check_init_data(init_data):
                    params = parse_init_data_params(init_data)
                    user_obj = json.loads(params.get('user', '{}'))
                    if user_obj and user_obj.get('id'):
                        user_id = str(user_obj.get('id'))
            except Exception:
                pass
        
        if not user_id:
            return jsonify({'success': False, 'message': 'Неверные данные пользователя'}), 403
        
        # Покупаем апгрейд
        result = buy_upgrade(user_id, upgrade_type)
        
        if result['success']:
            # Возвращаем обновленные данные
            updated_energy = get_user_energy(user_id)
            updated_upgrades = get_user_upgrades(user_id)
            
            return jsonify({
                'success': True,
                'message': 'Апгрейд успешно куплен',
                'new_level': result['new_level'],
                'new_balance': result['new_balance'],
                'energy': updated_energy,
                'upgrades': updated_upgrades
            })
        else:
            return jsonify(result), 400
        
    except Exception as e:
        app.logger.error(f"Ошибка в api_buy_upgrade: {e}")
        return jsonify({'success': False, 'message': 'Внутренняя ошибка сервера'}), 500

# Применяем миграцию апгрейдов после определения всех функций
migrate_existing_upgrades()

@app.route('/validate', methods=['POST'])
def validate():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    if not init_data:
        return jsonify({'success': False, 'error': 'tgWebAppData отсутствует'}), 400
    if not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Неверные данные инициализации'}), 403
    params = parse_init_data_params(init_data)
    user_data = params.get('user', '{}')
    try:
        user = json.loads(user_data)
    except Exception:
        user = {}
    first_name = user.get('first_name', 'Пользователь')
    return jsonify({'success': True, 'first_name': first_name})

@app.route('/games/<game_name>')
def game_page(game_name):
    html_file = f'{game_name}.html'
    return render_template(f'games/{html_file}')

@app.route('/api/rating/<game>')
def api_rating(game):
    if game not in ['snake', '2048']:
        return {'error': 'invalid game'}, 400
    rating = get_rating(game)
    return {'rating': [dict(row) for row in rating]}

@app.route('/api/submit/<game>', methods=['POST'])
def api_submit(game):
    if game not in ['snake', '2048']:
        return {'error': 'invalid game'}, 400
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    score = data.get('score')
    if not init_data or score is None:
        return {'error': 'no data'}, 400
    # Проверка initData
    if not check_init_data(init_data):
        return {'error': 'auth'}, 403
    params = parse_init_data_params(init_data)
    user_data = params.get('user', '{}')
    try:
        user = json.loads(user_data)
    except Exception:
        user = {}
    tg_id = user.get('id')
    username = user.get('username', '')
    first_name = user.get('first_name', 'Пользователь')
    if not tg_id:
        return {'error': 'no tg_id'}, 400
    user_id = get_or_create_user(tg_id, username, first_name)
    # Серверная логика: принимаем только лучший результат (например, если score > текущего)
    db = get_db()
    table = f'rating_{game}'
    cur = db.execute(f'SELECT score FROM {table} WHERE user_id = ?', (user_id,)).fetchone()
    if cur and score <= cur['score']:
        return {'ok': True, 'score': cur['score']}
    delta = score - (cur['score'] if cur else 0)
    new_score = update_rating(game, user_id, delta)
    return {'ok': True, 'score': new_score}

@app.route('/attendance')
def attendance_list():
    init_data = request.args.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return render_template('attendance/list_groups.html')

    try:
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        if not tg_id:
            return render_template('attendance/list_groups.html')

        db = get_db()
        user = db.execute('''
            SELECT u.last_attendance_group_id, ag.id as group_exists 
            FROM users u 
            LEFT JOIN attendance_groups ag ON u.last_attendance_group_id = ag.id 
            WHERE u.tg_id = ?
        ''', (tg_id,)).fetchone()

        if user and user['last_attendance_group_id'] and user['group_exists']:
            return redirect(url_for('attendance_view', group_id=user['last_attendance_group_id'], tgWebAppData=init_data))
        
        return render_template('attendance/list_groups.html')
    except Exception as e:
        app.logger.error(f'Error in attendance_list: {e}')
        return render_template('attendance/list_groups.html')

@app.route('/attendance/create')
def attendance_create():
    init_data = request.args.get('tgWebAppData')
    return render_template('attendance/create_group.html', tg_web_app_data=init_data)

@app.route('/attendance/<int:group_id>')
def attendance_view(group_id):
    init_data = request.args.get('tgWebAppData')
    return render_template('attendance/view_attendance.html', tg_web_app_data=init_data)

@app.route('/api/attendance/create_group', methods=['POST'])
def api_create_attendance_group():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})

    try:
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})

        name = data.get('name')
        students = data.get('students', [])
        
        if not name or not students:
            return jsonify({'success': False, 'error': 'Неверные данные'})

        db = get_db()
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        cursor = db.cursor()
        # Создаем группу
        cursor.execute(
            'INSERT INTO attendance_groups (name, creator_id) VALUES (?, ?)',
            (name, user_id)
        )
        group_id = cursor.lastrowid

        # Добавляем студентов
        for student_name in students:
            cursor.execute(
                'INSERT INTO students (group_id, name) VALUES (?, ?)',
                (group_id, student_name)
            )
        
        # Устанавливаем группу как активную для пользователя
        cursor.execute(
            'UPDATE users SET last_attendance_group_id = ? WHERE id = ?',
            (group_id, user_id)
        )
        
        db.commit()
        return jsonify({'success': True, 'group_id': group_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/attendance/get_attendance/<int:group_id>/<int:year>/<int:month>')
def api_get_attendance(group_id, year, month):
    try:
        db = get_db()
        
        # Получаем информацию о группе
        group = db.execute(
            'SELECT name FROM attendance_groups WHERE id = ?',
            (group_id,)
        ).fetchone()
        
        if not group:
            return jsonify({'success': False, 'error': 'Группа не найдена'})

        # Получаем список студентов
        students = []
        student_rows = db.execute(
            'SELECT id, name FROM students WHERE group_id = ? ORDER BY name',
            (group_id,)
        ).fetchall()

        for student in student_rows:
            # Получаем посещаемость для каждого студента
            attendance = {}
            attendance_rows = db.execute('''
                SELECT strftime('%d', date) as day, absences, excused_absences
                FROM attendance 
                WHERE student_id = ? 
                AND strftime('%Y', date) = ? 
                AND strftime('%m', date) = ?
            ''', (student['id'], str(year), str(month).zfill(2))).fetchall()

            total_absences = 0
            total_excused = 0
            
            for row in attendance_rows:
                day = int(row['day'])
                attendance[day] = {
                    'absences': row['absences'],
                    'excused': row['excused_absences']
                }
                total_absences += row['absences']
                total_excused += row['excused_absences']

            students.append({
                'id': student['id'],
                'name': student['name'],
                'attendance': attendance,
                'total_absences': total_absences,
                'total_excused': total_excused
            })

        return jsonify({
            'success': True,
            'group_name': group['name'],
            'students': students
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/attendance/update', methods=['POST'])
def api_update_attendance():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})

    try:
        student_id = data.get('student_id')
        date = data.get('date')
        absences = data.get('absences', 0)
        excused_absences = data.get('excused_absences', 0)

        db = get_db()
        # Обновляем данные о пропусках
        db.execute('''
            INSERT INTO attendance (student_id, date, absences, excused_absences)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(student_id, date) DO UPDATE SET
            absences = ?,
            excused_absences = ?
        ''', (student_id, date, absences, excused_absences, absences, excused_absences))
        
        # Получаем обновленные суммы
        totals = db.execute('''
            SELECT 
                SUM(absences) as total_absences,
                SUM(excused_absences) as total_excused
            FROM attendance
            WHERE student_id = ?
        ''', (student_id,)).fetchone()
        
        db.commit()

        return jsonify({
            'success': True,
            'total_absences': totals['total_absences'] or 0,
            'total_excused': totals['total_excused'] or 0
        })
    except Exception as e:
        app.logger.error(f'Error in api_update_attendance: {e}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/attendance/list_groups')
def api_list_groups():
    init_data = request.args.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    try:
        # Правильно парсим init_data
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        db = get_db()
        
        # Получаем группы, где пользователь является создателем или администратором
        groups = db.execute('''
            SELECT DISTINCT g.id, g.name, g.created_at,
                   (SELECT COUNT(*) FROM students s WHERE s.group_id = g.id) as student_count,
                   CASE WHEN g.creator_id = ? THEN 1 ELSE 0 END as is_owner
            FROM attendance_groups g
            LEFT JOIN group_admins ga ON g.id = ga.group_id
            WHERE g.creator_id = ? OR ga.user_id = ?
            ORDER BY g.created_at DESC
        ''', (user_id, user_id, user_id)).fetchall()
        
        return jsonify({
            'success': True,
            'groups': [{
                'id': g['id'],
                'name': g['name'],
                'created_at': g['created_at'],
                'student_count': g['student_count'],
                'is_owner': g['is_owner'] == 1
            } for g in groups]
        })
    except Exception as e:
        app.logger.error(f'Error in api_list_groups: {str(e)}')  # Добавляем логирование ошибки
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/attendance/get_group/<int:group_id>')
def api_get_group(group_id):
    init_data = request.args.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    try:
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        current_user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        db = get_db()
        
        # Получаем информацию о группе
        group = db.execute('''
            SELECT id, name, creator_id FROM attendance_groups 
            WHERE id = ?
        ''', (group_id,)).fetchone()
        
        if not group:
            return jsonify({'success': False, 'error': 'Группа не найдена'})
        
        # Получаем список студентов
        students = db.execute('''
            SELECT id, name FROM students 
            WHERE group_id = ? 
            ORDER BY name
        ''', (group_id,)).fetchall()
        
        # Получаем список администраторов
        admins = db.execute('''
            SELECT u.id, u.first_name as name, u.username 
            FROM users u 
            JOIN group_admins ga ON u.id = ga.user_id 
            WHERE ga.group_id = ?
        ''', (group_id,)).fetchall()
        
        return jsonify({
            'success': True,
            'group': {
                'id': group['id'],
                'name': group['name'],
                'creator_id': group['creator_id'],
                'students': [dict(s) for s in students],
                'admins': [dict(a) for a in admins]
            }
        })
    except Exception as e:
        app.logger.error(f'Error in api_get_group: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/attendance/update_group/<int:group_id>', methods=['POST'])
def api_update_group(group_id):
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})

    try:
        name = data.get('name')
        students = data.get('students', [])
        
        if not name or not students:
            return jsonify({'success': False, 'error': 'Неверные данные'})

        db = get_db()
        # Обновляем название группы
        db.execute('UPDATE attendance_groups SET name = ? WHERE id = ?', (name, group_id))
        
        # Получаем текущих студентов
        current_students = db.execute('SELECT id, name FROM students WHERE group_id = ?', (group_id,)).fetchall()
        current_names = {s['name']: s['id'] for s in current_students}
        
        # Обновляем студентов
        for student_name in students:
            if student_name in current_names:
                # Студент уже существует
                current_names.pop(student_name)
            else:
                # Добавляем нового студента
                db.execute('INSERT INTO students (group_id, name) VALUES (?, ?)', (group_id, student_name))
        
        # Удаляем оставшихся студентов
        for student_id in current_names.values():
            db.execute('DELETE FROM students WHERE id = ?', (student_id,))
        
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/attendance/delete_group/<int:group_id>', methods=['POST'])
def api_delete_group(group_id):
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})

    try:
        db = get_db()
        # Удаляем группу (каскадное удаление удалит также студентов и посещаемость)
        db.execute('DELETE FROM attendance_groups WHERE id = ?', (group_id,))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/attendance/add_admin/<int:group_id>', methods=['POST'])
def api_add_admin(group_id):
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    try:
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        identifier = data.get('identifier')
        if not identifier:
            return jsonify({'success': False, 'error': 'Не указан идентификатор пользователя'})
        
        db = get_db()
        current_user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        # Проверяем, является ли текущий пользователь создателем группы
        owner = db.execute('''
            SELECT creator_id FROM attendance_groups 
            WHERE id = ?
        ''', (group_id,)).fetchone()
        
        if not owner or owner['creator_id'] != current_user_id:
            return jsonify({'success': False, 'error': 'Недостаточно прав'})
        
        # Ищем пользователя по username или id
        if identifier.startswith('@'):
            username = identifier[1:]  # Убираем @ из начала
            user = db.execute('''
                SELECT id, first_name, username 
                FROM users 
                WHERE username = ? COLLATE NOCASE
            ''', (username,)).fetchone()
        else:
            try:
                user_id = int(identifier)
                user = db.execute('''
                    SELECT id, first_name, username 
                    FROM users 
                    WHERE id = ?
                ''', (user_id,)).fetchone()
            except ValueError:
                return jsonify({'success': False, 'error': 'Неверный формат ID'})
        
        if not user:
            return jsonify({'success': False, 'error': 'Пользователь не найден'})
        
        # Проверяем, не является ли пользователь уже администратором
        existing_admin = db.execute('''
            SELECT 1 FROM group_admins 
            WHERE group_id = ? AND user_id = ?
        ''', (group_id, user['id'])).fetchone()
        
        if existing_admin:
            return jsonify({'success': False, 'error': 'Пользователь уже является администратором'})
        
        # Добавляем пользователя как администратора
        db.execute('''
            INSERT INTO group_admins (group_id, user_id) 
            VALUES (?, ?)
        ''', (group_id, user['id']))
        db.commit()
        
        return jsonify({
            'success': True,
            'admin': {
                'id': user['id'],
                'name': user['first_name'],
                'username': user['username']
            }
        })
    except Exception as e:
        app.logger.error(f'Error in api_add_admin: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/attendance/remove_admin/<int:group_id>', methods=['POST'])
def api_remove_admin(group_id):
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    try:
        # Правильно парсим init_data
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        admin_id = data.get('admin_id')
        if not admin_id:
            return jsonify({'success': False, 'error': 'Не указан ID администратора'})
        
        db = get_db()
        current_user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        # Проверяем, является ли текущий пользователь создателем группы
        owner = db.execute('''
            SELECT creator_id FROM attendance_groups 
            WHERE id = ?
        ''', (group_id,)).fetchone()
        
        if not owner or owner['creator_id'] != current_user_id:
            return jsonify({'success': False, 'error': 'Недостаточно прав'})
        
        # Удаляем администратора
        db.execute('''
            DELETE FROM group_admins 
            WHERE group_id = ? AND user_id = ?
        ''', (group_id, admin_id))
        db.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f'Error in api_remove_admin: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/vpn')
def vpn_page():
    return render_template('vpn/view_vpn.html')

@app.route('/api/vpn/get_user_key', methods=['POST'])
def api_get_user_key():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    try:
        # Получаем данные пользователя
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        # Получаем ID пользователя из базы
        db = get_db()
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        # Проверяем, есть ли уже ключ у пользователя
        key = db.execute('SELECT key_data FROM vpn_keys WHERE user_id = ?', (user_id,)).fetchone()
        
        if key:
            return jsonify({
                'success': True,
                'hasKey': True,
                'key': key['key_data']
            })
        
        return jsonify({
            'success': True,
            'hasKey': False
        })
        
    except Exception as e:
        app.logger.error(f'Error in api_get_user_key: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

def get_container_stats(ssh):
    """Получение статистики использования контейнеров"""
    stdin, stdout, stderr = ssh.exec_command(
        "docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}\t{{.NetIO}}'"
    )
    stats = {}
    for line in stdout:
        name, cpu, mem, net = line.strip().split('\t')
        if name.startswith('outline-'):
            port = name.split('-')[1]
            stats[port] = {
                'cpu': float(cpu.strip('%')),
                'memory': float(mem.strip('%')),
                'network': net
            }
    return stats

def find_optimal_port(ssh):
    """Поиск оптимального порта с наименьшей нагрузкой"""
    stats = get_container_stats(ssh)
    
    # Проверяем общую нагрузку на сервер
    stdin, stdout, stderr = ssh.exec_command("uptime")
    load = float(stdout.read().decode().split('load average:')[1].split(',')[0].strip())
    
    if load > 5.0:  # Если загрузка сервера высокая
        raise Exception("Сервер перегружен, попробуйте позже")
    
    # Если есть существующие контейнеры, выбираем наименее загруженный
    if stats:
        optimal_port = min(stats.items(), key=lambda x: x[1]['cpu'])[0]
        if stats[optimal_port]['cpu'] < 80:  # Если нагрузка на контейнер приемлемая
            return int(optimal_port)
    
    # Если нет контейнеров или все перегружены, создаем новый порт
    port = 8081
    stdin, stdout, stderr = ssh.exec_command('netstat -tuln | grep LISTEN')
    used_ports = stdout.read().decode()
    
    while f":{port}" in used_ports:
        port += 1
    
    return port

@app.route('/api/vpn/generate_key', methods=['POST'])
def api_generate_vpn_key():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    try:
        # Получаем данные пользователя
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        username = user_data.get('username', 'user')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        # Получаем ID пользователя из базы
        db = get_db()
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        # Проверяем, есть ли уже ключ у пользователя
        existing_key = db.execute('SELECT key_data FROM vpn_keys WHERE user_id = ?', (user_id,)).fetchone()
        if existing_key:
            return jsonify({
                'success': True,
                'key': existing_key['key_data']
            })

        # Создаем SSH клиент
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh.connect(
                '87.120.84.187',
                username='root',
                password='403090WOW',
                timeout=20,
                allow_agent=False,
                look_for_keys=False
            )
            
            # Находим оптимальный порт
            try:
                port = find_optimal_port(ssh)
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)})
            
            # Создаем ключ с именем пользователя
            sanitized_username = ''.join(c for c in f"{username}_{tg_id}" if c.isalnum() or c in '_-')
            
            # Проверяем и удаляем существующий контейнер, если он есть
            ssh.exec_command(f'docker stop vless-{port} 2>/dev/null || true')
            ssh.exec_command(f'docker rm vless-{port} 2>/dev/null || true')
            ssh.exec_command(f'rm -f /root/vless/config_{port}.json 2>/dev/null || true')
            
            # Генерируем UUID для пользователя
            user_uuid = str(uuid.uuid4())

            # Создаем конфиг для Xray-core (VLESS)
            config = {
                "inbounds": [
                    {
                        "port": port,
                        "protocol": "vless",
                        "settings": {
                            "clients": [
                                {"id": user_uuid, "level": 0, "email": sanitized_username}
                            ],
                            "decryption": "none"
                        },
                        "streamSettings": {
                            "network": "tcp",
                            "security": "none"
                        }
                    }
                ],
                "outbounds": [
                    {"protocol": "freedom", "settings": {}}
                ]
            }
            config_path = f'/root/vless/config_{port}.json'
            stdin, stdout, stderr = ssh.exec_command(f'cat > {config_path}')
            stdin.write(json.dumps(config, indent=4))
            stdin.close()

            # Запускаем контейнер Xray-core
            cmd = f'''docker run -d \
                --name vless-{port} \
                --restart always \
                --network host \
                -v {config_path}:/etc/xray/config.json \
                teddysun/xray \
                xray -config /etc/xray/config.json'''
            stdin, stdout, stderr = ssh.exec_command(cmd)
            error = stderr.read().decode()
            if error and 'Error response from daemon' in error:
                raise Exception(f"Ошибка при создании контейнера: {error}")

            # Формируем ссылку VLESS
            key = f"vless://{user_uuid}@87.120.84.187:{port}?encryption=none#ВПН_от_РУБИКА_{sanitized_username}"

            # Сохраняем ключ в базу
            db.execute('INSERT INTO vpn_keys (user_id, port, key_data) VALUES (?, ?, ?)',
                      (user_id, port, key))
            db.commit()
            
            return jsonify({
                'success': True,
                'key': key
            })
            
        finally:
            ssh.close()
            
    except Exception as e:
        app.logger.error(f'Error in api_generate_vpn_key: {str(e)}')
        return jsonify({
            'success': False,
            'error': 'Ошибка при создании ключа. Пожалуйста, попробуйте позже.'
        })

@app.route('/api/vpn/list_keys', methods=['POST'])
def api_list_vpn_keys():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh.connect(
                '87.120.84.187',
                username='root',
                password='403090WOW',
                timeout=20,
                allow_agent=False,
                look_for_keys=False
            )
            
            # Получаем список контейнеров
            stdin, stdout, stderr = ssh.exec_command('docker ps --format "{{.Names}}" | grep vless-')
            containers = stdout.read().decode().strip().split('\n')
            
            keys = []
            for container in containers:
                if not container:
                    continue
                port = container.split('-')[1]
                config_path = f'/root/vless/config_{port}.json'
                stdin, stdout, stderr = ssh.exec_command(f'cat {config_path}')
                config = json.loads(stdout.read().decode())
                user_uuid = config['inbounds'][0]['settings']['clients'][0]['id']
                key = f"vless://{user_uuid}@87.120.84.187:{port}?encryption=none#ВПН_от_РУБИКА"
                keys.append({
                    'port': port,
                    'key': key
                })
            return jsonify({
                'success': True,
                'keys': keys
            })
            
        finally:
            ssh.close()
            
    except Exception as e:
        app.logger.error(f'Error in api_list_vpn_keys: {str(e)}')
        return jsonify({
            'success': False,
            'error': 'Ошибка при получении списка ключей'
        })

@app.route('/api/vpn/delete_key', methods=['POST'])
def api_delete_vpn_key():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    try:
        # Получаем данные пользователя
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        # Получаем ID пользователя из базы
        db = get_db()
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        # Получаем ключ пользователя
        key = db.execute('SELECT port FROM vpn_keys WHERE user_id = ?', (user_id,)).fetchone()
        
        if not key:
            return jsonify({'success': False, 'error': 'Ключ не найден'})
            
        # Создаем SSH клиент
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh.connect(
                '87.120.84.187',
                username='root',
                password='403090WOW',
                timeout=20,
                allow_agent=False,
                look_for_keys=False
            )
            
            # Останавливаем и удаляем контейнер
            ssh.exec_command(f'docker stop vless-{key["port"]}')
            ssh.exec_command(f'docker rm vless-{key["port"]}')
            
            # Удаляем конфиг
            ssh.exec_command(f'rm -f /root/vless/config_{key["port"]}.json')
            
            # Удаляем ключ из базы
            db.execute('DELETE FROM vpn_keys WHERE user_id = ?', (user_id,))
            db.commit()
            
            return jsonify({'success': True})
            
        finally:
            ssh.close()
            
    except Exception as e:
        app.logger.error(f'Error in api_delete_vpn_key: {str(e)}')
        return jsonify({
            'success': False,
            'error': 'Ошибка при удалении ключа'
        })

# Декоратор для проверки авторизации
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_authenticated'):
            return redirect(url_for('vpn_admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/vpn/admin', methods=['GET'])
@admin_required
def vpn_admin():
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        ssh.connect(
            '87.120.84.187',
            username='root',
            password='403090WOW',
            timeout=20,
            allow_agent=False,
            look_for_keys=False
        )
        
        # Получаем статистику контейнеров
        stats = get_container_stats(ssh)
        
        # Получаем общую нагрузку на сервер
        stdin, stdout, stderr = ssh.exec_command("uptime")
        server_load = stdout.read().decode().strip()
        
        # Получаем использование диска
        stdin, stdout, stderr = ssh.exec_command("df -h /")
        disk_usage = stdout.read().decode().strip()
        
        # Получаем список активных пользователей
        stdin, stdout, stderr = ssh.exec_command(
            "netstat -tn | grep ESTABLISHED | grep -E ':808[0-9]+' | wc -l"
        )
        active_users = stdout.read().decode().strip()
        
        ssh.close()
        
        return render_template(
            'vpn/admin.html',
            stats=stats,
            server_load=server_load,
            disk_usage=disk_usage,
            active_users=active_users
        )
        
    except Exception as e:
        return f"Ошибка: {str(e)}", 500

@app.route('/vpn/admin/login', methods=['GET', 'POST'])
def vpn_admin_login():
    if request.method == 'POST':
        if request.form.get('password') == '403090WOW':
            session['admin_authenticated'] = True
            return redirect(url_for('vpn_admin'))
        else:
            return render_template('vpn/admin_login.html', error='Неверный пароль')
    return render_template('vpn/admin_login.html')

@app.route('/vpn/admin/logout')
def vpn_admin_logout():
    session.pop('admin_authenticated', None)
    return redirect(url_for('vpn_admin_login'))

# Декоратор для проверки авторизации администратора
def superadmin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('superadmin_authenticated'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == '403090WOW':
            session['superadmin_authenticated'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('admin/login.html', error='Неверный пароль')
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('superadmin_authenticated', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@superadmin_required
def admin_dashboard():
    db = get_db()
    
    # Получаем статистику по всем таблицам
    tables_info = {}
    cursor = db.cursor()
    
    # Получаем список всех таблиц
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    
    for table in tables:
        table_name = table[0]
        # Получаем количество записей
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        # Получаем структуру таблицы
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        
        tables_info[table_name] = {
            'count': count,
            'columns': columns
        }
    
    return render_template('admin/dashboard.html', tables_info=tables_info)

@app.route('/admin/clicker')
@superadmin_required
def admin_clicker():
    """Управление кликером"""
    db = get_db()
    
    # Получаем статистику кликера
    total_congratulations = db.execute('SELECT COUNT(*) as count FROM congratulations').fetchone()['count']
    unique_users = db.execute('SELECT COUNT(DISTINCT user_id) as count FROM congratulations').fetchone()['count']
    users_with_energy = db.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
    total_spent = db.execute('SELECT COALESCE(SUM(amount), 0) as total FROM congratulations_spent').fetchone()['total']
    
    # Топ пользователей
    top_users = db.execute('''
        SELECT c.user_id, c.user_name, COUNT(*) as congratulations_count,
               u.energy_current, u.energy_max,
               COALESCE(capacity.level, 0) as capacity_level,
               COALESCE(speed.level, 0) as speed_level
        FROM congratulations c
        LEFT JOIN users u ON c.user_id = u.id
        LEFT JOIN user_upgrades capacity ON c.user_id = capacity.user_id AND capacity.upgrade_type = 'capacity'
        LEFT JOIN user_upgrades speed ON c.user_id = speed.user_id AND speed.upgrade_type = 'speed'
        GROUP BY c.user_id, c.user_name
        ORDER BY congratulations_count DESC
        LIMIT 10
    ''').fetchall()
    
    stats = {
        'total_congratulations': total_congratulations,
        'unique_users': unique_users,
        'users_with_energy': users_with_energy,
        'total_spent': total_spent,
        'top_users': top_users
    }
    
    return render_template('admin/clicker.html', stats=stats)

@app.route('/admin/clicker/update-settings', methods=['POST'])
@superadmin_required
def admin_clicker_update_settings():
    """Обновить системные настройки кликера"""
    try:
        data = request.get_json()
        setting_type = data.get('type')
        value = data.get('value')
        
        if not setting_type or value is None:
            return jsonify({'success': False, 'message': 'Недостаточно данных'})
        
        db = get_db()
        
        if setting_type == 'base_energy':
            # Обновляем базовую энергию для всех пользователей без апгрейдов
            db.execute('''
                UPDATE users 
                SET energy_max = ?, energy_current = CASE 
                    WHEN energy_current > ? THEN energy_current 
                    ELSE ? 
                END
                WHERE id NOT IN (
                    SELECT DISTINCT user_id FROM user_upgrades WHERE upgrade_type = 'capacity'
                )
            ''', (int(value), int(value), int(value)))
            
        elif setting_type == 'regen_interval':
            # Обновляем время восстановления для всех пользователей без апгрейдов скорости
            db.execute('''
                UPDATE users 
                SET energy_regen_interval = ?
                WHERE id NOT IN (
                    SELECT DISTINCT user_id FROM user_upgrades WHERE upgrade_type = 'speed'
                )
            ''', (int(value),))
            
        elif setting_type == 'regen_rate':
            # Обновляем скорость восстановления для всех пользователей
            db.execute('UPDATE users SET energy_regen_rate = ?', (int(value),))
            
        else:
            return jsonify({'success': False, 'message': 'Неизвестный тип настройки'})
        
        db.commit()
        app.logger.info(f"Admin updated {setting_type} to {value}")
        
        return jsonify({'success': True, 'message': f'Настройка {setting_type} обновлена'})
        
    except Exception as e:
        app.logger.error(f"Error updating clicker settings: {e}")
        return jsonify({'success': False, 'message': 'Ошибка сервера'})

@app.route('/admin/clicker/set-user-energy', methods=['POST'])
@superadmin_required
def admin_clicker_set_user_energy():
    """Установить энергию конкретному пользователю"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        energy_amount = data.get('energy_amount')
        
        if not user_id or energy_amount is None:
            return jsonify({'success': False, 'message': 'Недостаточно данных'})
        
        db = get_db()
        
        # Проверяем существует ли пользователь
        user = db.execute('SELECT * FROM users WHERE id = ?', (str(user_id),)).fetchone()
        if not user:
            return jsonify({'success': False, 'message': 'Пользователь не найден'})
        
        # Устанавливаем энергию
        db.execute('UPDATE users SET energy_current = ? WHERE id = ?', (int(energy_amount), str(user_id)))
        db.commit()
        
        app.logger.info(f"Admin set energy {energy_amount} for user {user_id}")
        
        return jsonify({'success': True, 'message': f'Энергия установлена: {energy_amount}'})
        
    except Exception as e:
        app.logger.error(f"Error setting user energy: {e}")
        return jsonify({'success': False, 'message': 'Ошибка сервера'})

@app.route('/admin/clicker/reset-all-energy', methods=['POST'])
@superadmin_required
def admin_clicker_reset_all_energy():
    """Сбросить всю энергию до максимума"""
    try:
        db = get_db()
        
        # Восстанавливаем энергию всех пользователей до максимума
        db.execute('UPDATE users SET energy_current = energy_max')
        db.commit()
        
        # Получаем количество обновленных пользователей
        updated_count = db.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
        
        app.logger.info(f"Admin reset energy for {updated_count} users")
        
        return jsonify({'success': True, 'message': f'Энергия восстановлена для {updated_count} пользователей'})
        
    except Exception as e:
        app.logger.error(f"Error resetting all energy: {e}")
        return jsonify({'success': False, 'message': 'Ошибка сервера'})

@app.route('/admin/clicker/clear-upgrades', methods=['POST'])
@superadmin_required
def admin_clicker_clear_upgrades():
    """Очистить все апгрейды"""
    try:
        db = get_db()
        
        # Удаляем все апгрейды
        upgrades_count = db.execute('SELECT COUNT(*) as count FROM user_upgrades').fetchone()['count']
        db.execute('DELETE FROM user_upgrades')
        
        # Сбрасываем потраченные поздравления
        spent_count = db.execute('SELECT COUNT(*) as count FROM congratulations_spent').fetchone()['count']
        db.execute('DELETE FROM congratulations_spent')
        
        # Сбрасываем энергию всех пользователей к базовым значениям
        db.execute('''
            UPDATE users 
            SET energy_max = 100, 
                energy_current = 100, 
                energy_regen_interval = 20000
        ''')
        
        db.commit()
        
        app.logger.info(f"Admin cleared {upgrades_count} upgrades and {spent_count} spent records")
        
        return jsonify({
            'success': True, 
            'message': f'Очищено {upgrades_count} апгрейдов и {spent_count} трат'
        })
        
    except Exception as e:
        app.logger.error(f"Error clearing upgrades: {e}")
        return jsonify({'success': False, 'message': 'Ошибка сервера'})

@app.route('/admin/clicker/add-congratulations', methods=['POST'])
@superadmin_required
def admin_clicker_add_congratulations():
    """Добавить поздравления пользователю"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        amount = data.get('amount')
        
        if not user_id or amount is None:
            return jsonify({'success': False, 'message': 'Недостаточно данных'})
        
        db = get_db()
        
        # Проверяем существует ли пользователь
        user = db.execute('SELECT * FROM users WHERE id = ?', (str(user_id),)).fetchone()
        if not user:
            return jsonify({'success': False, 'message': 'Пользователь не найден'})
        
        # Добавляем поздравления
        current_time = int(time.time() * 1000)
        for _ in range(int(amount)):
            db.execute(
                'INSERT INTO congratulations (user_id, user_name, timestamp) VALUES (?, ?, ?)',
                (str(user_id), 'Admin Bonus', current_time)
            )
        
        db.commit()
        
        app.logger.info(f"Admin added {amount} congratulations to user {user_id}")
        
        return jsonify({'success': True, 'message': f'Добавлено {amount} поздравлений'})
        
    except Exception as e:
        app.logger.error(f"Error adding congratulations: {e}")
        return jsonify({'success': False, 'message': 'Ошибка сервера'})

@app.route('/admin/clicker/full-wipe', methods=['POST'])
@superadmin_required
def admin_clicker_full_wipe():
    """ПОЛНЫЙ ВАЙП всех данных кликера - НЕОБРАТИМО!"""
    try:
        db = get_db()
        
        # Получаем статистику перед удалением для логирования
        total_congratulations = db.execute('SELECT COUNT(*) as count FROM congratulations').fetchone()['count']
        total_users = db.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
        total_upgrades = db.execute('SELECT COUNT(*) as count FROM user_upgrades').fetchone()['count']
        total_spent = db.execute('SELECT COUNT(*) as count FROM congratulations_spent').fetchone()['count']
        
        # ПОЛНОЕ УДАЛЕНИЕ ВСЕХ ДАННЫХ
        # Удаляем все поздравления
        db.execute('DELETE FROM congratulations')
        
        # Удаляем всех пользователей (энергия, настройки)
        db.execute('DELETE FROM users')
        
        # Удаляем все апгрейды
        db.execute('DELETE FROM user_upgrades')
        
        # Удаляем все записи о тратах
        db.execute('DELETE FROM congratulations_spent')
        
        # Сбрасываем автоинкремент счетчики
        db.execute('DELETE FROM sqlite_sequence WHERE name IN ("congratulations", "users", "user_upgrades", "congratulations_spent")')
        
        db.commit()
        
        # Логируем критическое действие
        app.logger.critical(f"ПОЛНЫЙ ВАЙП ДАННЫХ КЛИКЕРА! Удалено: {total_congratulations} поздравлений, {total_users} пользователей, {total_upgrades} апгрейдов, {total_spent} записей трат")
        
        return jsonify({
            'success': True, 
            'message': f'ПОЛНЫЙ ВАЙП ЗАВЕРШЕН! Удалено: {total_congratulations} поздравлений, {total_users} пользователей, {total_upgrades} апгрейдов, {total_spent} записей трат'
        })
        
    except Exception as e:
        app.logger.error(f"Error during full wipe: {e}")
        return jsonify({'success': False, 'message': 'Ошибка при выполнении полного вайпа'})

@app.route('/admin/table/<table_name>')
@superadmin_required
def admin_table(table_name):
    db = get_db()
    cursor = db.cursor()
    
    # Получаем информацию о структуре таблицы
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    
    # Получаем информацию о первичном ключе
    cursor.execute(f"PRAGMA index_list({table_name})")
    indexes = cursor.fetchall()
    
    primary_keys = []
    for index in indexes:
        if index[2]:  # Если это первичный ключ
            cursor.execute(f"PRAGMA index_info({index[1]})")
            index_info = cursor.fetchall()
            primary_keys.extend(col[2] for col in index_info)
    
    # Если первичных ключей нет, используем первую колонку
    if not primary_keys:
        primary_keys = [columns[0][1]]
    
    # Получаем данные таблицы
    cursor.execute(f"SELECT * FROM {table_name}")
    rows = cursor.fetchall()
    
    return render_template('admin/table.html', 
                         table_name=table_name,
                         columns=columns,
                         rows=rows,
                         primary_keys=primary_keys)

@app.route('/admin/edit/<table_name>', methods=['GET', 'POST'])
@superadmin_required
def admin_edit_row(table_name):
    db = get_db()
    cursor = db.cursor()
    
    # Получаем информацию о структуре таблицы
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    
    # Получаем информацию о первичном ключе
    cursor.execute(f"PRAGMA index_list({table_name})")
    indexes = cursor.fetchall()
    
    primary_keys = []
    for index in indexes:
        if index[2]:  # Если это первичный ключ
            cursor.execute(f"PRAGMA index_info({index[1]})")
            index_info = cursor.fetchall()
            primary_keys.extend(col[2] for col in index_info)
    
    # Если первичных ключей нет, используем первую колонку
    if not primary_keys:
        primary_keys = [columns[0][1]]
    
    if request.method == 'POST':
        try:
            # Получаем все поля из формы
            fields = {}
            where_values = []
            for column in columns:
                col_name = column[1]
                if col_name in request.form:
                    if col_name in primary_keys:
                        where_values.append(request.form[col_name])
                    else:
                        fields[col_name] = request.form[col_name]
            
            # Формируем SQL запрос для обновления
            set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
            where_clause = " AND ".join([f"{k} = ?" for k in primary_keys])
            sql = f"UPDATE {table_name} SET {set_clause} WHERE {where_clause}"
            
            # Выполняем запрос
            cursor.execute(sql, list(fields.values()) + where_values)
            db.commit()
            
            return redirect(url_for('admin_table', table_name=table_name))
        except Exception as e:
            return f"Ошибка: {str(e)}", 500
    
    # Получаем значения для WHERE из параметров запроса
    where_values = []
    where_conditions = []
    for key in primary_keys:
        value = request.args.get(key)
        if value is not None:
            where_values.append(value)
            where_conditions.append(f"{key} = ?")
    
    # Получаем данные строки
    where_clause = " AND ".join(where_conditions)
    cursor.execute(f"SELECT * FROM {table_name} WHERE {where_clause}", where_values)
    row = cursor.fetchone()
    
    if not row:
        return f"Запись не найдена", 404
    
    return render_template('admin/edit_row.html',
                         table_name=table_name,
                         columns=columns,
                         row=row,
                         primary_keys=primary_keys)

@app.route('/admin/delete/<table_name>', methods=['POST'])
@superadmin_required
def admin_delete_row(table_name):
    try:
        db = get_db()
        cursor = db.cursor()
        
        # Получаем информацию о первичном ключе
        cursor.execute(f"PRAGMA index_list({table_name})")
        indexes = cursor.fetchall()
        
        primary_keys = []
        for index in indexes:
            if index[2]:  # Если это первичный ключ
                cursor.execute(f"PRAGMA index_info({index[1]})")
                index_info = cursor.fetchall()
                primary_keys.extend(col[2] for col in index_info)
        
        # Если первичных ключей нет, используем первую колонку
        if not primary_keys:
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            primary_keys = [columns[0][1]]
        
        # Формируем условие WHERE
        where_values = []
        where_conditions = []
        for key in primary_keys:
            value = request.form.get(key)
            if value is not None:
                where_values.append(value)
                where_conditions.append(f"{key} = ?")
        
        where_clause = " AND ".join(where_conditions)
        cursor.execute(f"DELETE FROM {table_name} WHERE {where_clause}", where_values)
        db.commit()
        
        return redirect(url_for('admin_table', table_name=table_name))
    except Exception as e:
        return f"Ошибка: {str(e)}", 500

@app.route('/api/submit/2048', methods=['POST'])
def api_submit_2048():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    score = data.get('score')
    game_id = data.get('game_id')
    move_number = data.get('move_number')
    board_state = data.get('board_state')
    move_hash = data.get('move_hash')
    
    if not all([init_data, score is not None, game_id, move_number is not None, 
                board_state, move_hash]):
        return {'error': 'missing data'}, 400
    
    # Проверка initData
    if not check_init_data(init_data):
        return {'error': 'auth'}, 403
    
    # Получаем данные пользователя
    try:
        params = parse_init_data_params(init_data)
        user_data = params.get('user', '{}')
        user = json.loads(user_data)
    except:
        return {'error': 'invalid user data'}, 400
    
    tg_id = user.get('id')
    if not tg_id:
        return {'error': 'no tg_id'}, 400
    
    user_id = get_or_create_user(tg_id, user.get('username', ''), user.get('first_name', 'Пользователь'))
    
    # Проверяем валидность хода
    is_valid, error = verify_2048_move(game_id, user_id, board_state, score, move_number)
    if not is_valid:
        return {'error': error}, 400
    
    # Проверяем хеш хода
    message = f"{game_id}:{user_id}:{move_number}:{board_state}:{score}"
    hash_value = 0
    for char in message:
        hash_value = (hash_value * 31 + ord(char)) & 0xFFFFFFFF
    expected_hash = format(hash_value, 'x')
    
    if move_hash != expected_hash:
        return {'error': 'invalid move hash'}, 400
    
    # Сохраняем ход
    db = get_db()
    db.execute(
        'INSERT INTO game_moves_2048 (user_id, game_id, move_number, board_state, score, timestamp, move_hash) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (user_id, game_id, move_number, board_state, score, time.time(), move_hash)
    )
    
    # Обновляем сессию
    db.execute(
        'UPDATE game_sessions_2048 SET last_move_time = ?, current_score = ?, moves_count = ? WHERE game_id = ?',
        (time.time(), score, move_number, game_id)
    )
    
    # Обновляем рейтинг только если это лучший результат
    cur = db.execute('SELECT score FROM rating_2048 WHERE user_id = ?', (user_id,)).fetchone()
    if not cur or score > cur['score']:
        if cur:
            db.execute('UPDATE rating_2048 SET score = ? WHERE user_id = ?', (score, user_id))
        else:
            db.execute('INSERT INTO rating_2048 (user_id, score) VALUES (?, ?)', (user_id, score))
    
    db.commit()
    return {'ok': True, 'score': score}

@app.route('/api/2048/new_game', methods=['POST'])
def new_game_2048():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    
    if not check_init_data(init_data):
        return {'error': 'auth'}, 403
    
    params = parse_init_data_params(init_data)
    user_data = params.get('user', '{}')
    try:
        user = json.loads(user_data)
    except Exception:
        return {'error': 'invalid user data'}, 400
    
    tg_id = user.get('id')
    if not tg_id:
        return {'error': 'no tg_id'}, 400
    
    user_id = get_or_create_user(tg_id, user.get('username', ''), user.get('first_name', 'Пользователь'))
    
    # Создаем новую игровую сессию
    game_id = hashlib.sha256(f"{user_id}:{time.time()}".encode()).hexdigest()
    current_time = time.time()
    
    db = get_db()
    db.execute(
        'INSERT INTO game_sessions_2048 (game_id, user_id, start_time, last_move_time, current_score, moves_count) VALUES (?, ?, ?, ?, 0, 0)',
        (game_id, user_id, current_time, current_time)
    )
    db.commit()
    
    return {'ok': True, 'game_id': game_id, 'user_id': user_id}

# Добавим функцию проверки валидности хода змейки
def verify_snake_move(game_id, user_id, snake_state, food_position, score, move_number):
    db = get_db()
    
    # Проверяем существование игровой сессии
    session = db.execute(
        'SELECT * FROM game_sessions_snake WHERE game_id = ? AND user_id = ?',
        (game_id, user_id)
    ).fetchone()
    
    if not session:
        return False, "НЕ ЛОМАЙ ЗМЕЙКУ ПЖ"
    
    # Проверяем время между ходами
    current_time = time.time()
    if (current_time - session['last_move_time']) * 1000 < MAX_SNAKE_SPEED:
        return False, "НЕ ЛОМАЙ ЗМЕЙКУ ПЖ"
    
    # Проверяем корректность номера хода
    if move_number != session['moves_count'] + 1:
        return False, "НЕ ЛОМАЙ ЗМЕЙКУ ПЖ"
    
    # Проверяем прирост очков
    score_delta = score - session['current_score']
    if score_delta > MAX_SNAKE_SCORE_PER_FOOD or score_delta < 0:
        return False, "НЕ ЛОМАЙ ЗМЕЙКУ ПЖ"
    
    # Проверяем длину змейки
    try:
        snake = json.loads(snake_state)
        if len(snake) > MAX_SNAKE_LENGTH:
            return False, "НЕ ЛОМАЙ ЗМЕЙКУ ПЖ"
    except:
        return False, "НЕ ЛОМАЙ ЗМЕЙКУ ПЖ"
    
    # Проверяем общий счет
    if score > MAX_SNAKE_TOTAL_SCORE:
        return False, "НЕ ЛОМАЙ ЗМЕЙКУ ПЖ"
    
    return True, None

# Обновим route для отправки счета змейки
@app.route('/api/submit/snake', methods=['POST'])
def api_submit_snake():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    score = data.get('score')
    game_id = data.get('game_id')
    move_number = data.get('move_number')
    snake_state = data.get('snake_state')
    food_position = data.get('food_position')
    move_hash = data.get('move_hash')
    
    if not all([init_data, score is not None, game_id, move_number is not None, 
                snake_state, food_position, move_hash]):
        return {'error': 'missing data'}, 400
    
    # Проверка initData
    if not check_init_data(init_data):
        return {'error': 'auth'}, 403
    
    # Получаем данные пользователя
    try:
        params = parse_init_data_params(init_data)
        user_data = params.get('user', '{}')
        user = json.loads(user_data)
    except:
        return {'error': 'invalid user data'}, 400
    
    tg_id = user.get('id')
    if not tg_id:
        return {'error': 'no tg_id'}, 400
    
    user_id = get_or_create_user(tg_id, user.get('username', ''), user.get('first_name', 'Пользователь'))
    
    # Проверяем валидность хода
    is_valid, error = verify_snake_move(game_id, user_id, snake_state, food_position, score, move_number)
    if not is_valid:
        return {'error': error}, 400
    
    # Создаем хеш для проверки
    message = f"{game_id}:{user_id}:{move_number}:{snake_state}:{food_position}:{score}"
    hash_value = 0
    for char in message:
        hash_value = (hash_value * 31 + ord(char)) & 0xFFFFFFFF
    expected_hash = format(hash_value, 'x')
    
    app.logger.info(f"Server hash calculation:")
    app.logger.info(f"Message: {message}")
    app.logger.info(f"Expected hash: {expected_hash}")
    app.logger.info(f"Received hash: {move_hash}")
    
    if move_hash != expected_hash:
        return {'error': 'invalid move hash'}, 400
    
    # Сохраняем ход
    db = get_db()
    db.execute(
        'INSERT INTO game_moves_snake (user_id, game_id, move_number, snake_state, food_position, score, timestamp, move_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (user_id, game_id, move_number, snake_state, food_position, score, time.time(), move_hash)
    )
    
    # Обновляем сессию
    snake = json.loads(snake_state)
    db.execute(
        'UPDATE game_sessions_snake SET last_move_time = ?, current_score = ?, snake_length = ?, moves_count = ? WHERE game_id = ?',
        (time.time(), score, len(snake), move_number, game_id)
    )
    
    # Обновляем рейтинг только если это лучший результат
    cur = db.execute('SELECT score FROM rating_snake WHERE user_id = ?', (user_id,)).fetchone()
    if not cur or score > cur['score']:
        if cur:
            db.execute('UPDATE rating_snake SET score = ? WHERE user_id = ?', (score, user_id))
        else:
            db.execute('INSERT INTO rating_snake (user_id, score) VALUES (?, ?)', (user_id, score))
    
    db.commit()
    return {'ok': True, 'score': score}

# Добавим route для начала новой игры змейки
@app.route('/api/snake/new_game', methods=['POST'])
def new_game_snake():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    
    if not check_init_data(init_data):
        return {'error': 'auth'}, 403
    
    params = parse_init_data_params(init_data)
    user_data = params.get('user', '{}')
    try:
        user = json.loads(user_data)
    except Exception:
        return {'error': 'invalid user data'}, 400
    
    tg_id = user.get('id')
    if not tg_id:
        return {'error': 'no tg_id'}, 400
    
    user_id = get_or_create_user(tg_id, user.get('username', ''), user.get('first_name', 'Пользователь'))
    
    # Создаем новую игровую сессию
    game_id = hashlib.sha256(f"{user_id}:{time.time()}".encode()).hexdigest()
    current_time = time.time()
    
    db = get_db()
    db.execute(
        'INSERT INTO game_sessions_snake (game_id, user_id, start_time, last_move_time, current_score, snake_length, moves_count) VALUES (?, ?, ?, ?, 0, 3, 0)',
        (game_id, user_id, current_time, current_time)
    )
    db.commit()
    
    return {'ok': True, 'game_id': game_id, 'user_id': user_id}

# Добавляем функцию проверки валидности хода 2048
def verify_2048_move(game_id, user_id, board_state, score, move_number):
    db = get_db()
    session = db.execute(
        'SELECT * FROM game_sessions_2048 WHERE game_id = ? AND user_id = ?',
        (game_id, user_id)
    ).fetchone()
    if not session:
        app.logger.warning(f"2048: Invalid game session! user_id={user_id} game_id={game_id}")
        return False, "Invalid game session"
    # Проверяем время между ходами (смягчено)
    current_time = time.time()
    ms_since_last = (current_time - session['last_move_time']) * 1000
    if ms_since_last < MAX_2048_MOVE_TIME:
        app.logger.info(f"2048: Moves too fast! user_id={user_id} move_number={move_number} ms_since_last={ms_since_last}")
        return False, f"Moves too fast ({ms_since_last:.1f} ms)"
    # Разрешаем повторную отправку одного и того же номера хода (идемпотентность)
    if move_number < session['moves_count'] + 1:
        app.logger.info(f"2048: Duplicate move_number user_id={user_id} move_number={move_number}")
        return False, "Duplicate move_number"
    if move_number > session['moves_count'] + 1:
        app.logger.info(f"2048: Invalid move_number user_id={user_id} move_number={move_number} session_moves={session['moves_count']}")
        return False, "Invalid move number"
    # Проверяем прирост очков
    score_delta = score - session['current_score']
    if score_delta > MAX_2048_SCORE_PER_MOVE or score_delta < 0:
        app.logger.info(f"2048: Invalid score increment! user_id={user_id} score_delta={score_delta} score={score} prev={session['current_score']}")
        return False, "Invalid score increment"
    # Проверяем состояние доски
    try:
        board = json.loads(board_state)
        if not isinstance(board, list) or len(board) != 4 or not all(isinstance(row, list) and len(row) == 4 for row in board):
            app.logger.info(f"2048: Invalid board state! user_id={user_id} board_state={board_state}")
            return False, "Invalid board state"
        valid_numbers = {0} | {2 ** i for i in range(1, 18)}
        if not all(all(num in valid_numbers for num in row) for row in board):
            app.logger.info(f"2048: Invalid numbers on board! user_id={user_id} board={board}")
            return False, "Invalid numbers on board"
    except Exception as e:
        app.logger.info(f"2048: Exception in board state! user_id={user_id} error={e} board_state={board_state}")
        return False, "Invalid board state"
    if score > MAX_2048_TOTAL_SCORE:
        app.logger.info(f"2048: Score too high! user_id={user_id} score={score}")
        return False, "Score too high"
    return True, None

@app.route('/schedule_search')
def schedule_search():
    return render_template('schedule_search.html')

# Игра "Морской бой"
import random
import string
from collections import defaultdict

# --------------------
# Clicker session tokens (in-memory) and simple abuse penalties
# --------------------
CLICKER_TOKENS = {}  # token -> { user_id: str, exp: int, ip: str, ua: str }
ABUSE_PENALTY = {}   # user_id -> exp (unix seconds)

def issue_clicker_token(user_id: str, ip: str, ua: str, ttl_sec: int = 900) -> str:
    now = int(time.time())
    token = secrets.token_urlsafe(32)
    CLICKER_TOKENS[token] = {
        'user_id': str(user_id),
        'exp': now + ttl_sec,
        'ip': ip or '',
        'ua': ua or ''
    }
    return token

def verify_clicker_token(token: str, ip: str, ua: str):
    if not token:
        return None
    data = CLICKER_TOKENS.get(token)
    if not data:
        return None
    now = int(time.time())
    if data.get('exp', 0) < now:
        CLICKER_TOKENS.pop(token, None)
        return None
    # Привязка к UA и IP (мягкая). Если меняется — отклоняем
    if data.get('ua') and ua and data['ua'] != ua:
        return None
    if data.get('ip') and ip and data['ip'] != ip:
        return None
    return data.get('user_id')

def is_under_penalty(user_id: str) -> bool:
    exp = ABUSE_PENALTY.get(str(user_id))
    if not exp:
        return False
    now = int(time.time())
    if exp <= now:
        ABUSE_PENALTY.pop(str(user_id), None)
        return False
    return True

def set_penalty(user_id: str, seconds: int = 3):
    ABUSE_PENALTY[str(user_id)] = int(time.time()) + max(1, seconds)

# Хранилище игр
battleship_games = {}

# Генерация случайного кода игры
def generate_game_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# Создание новой игры
@app.route('/api/battleship/create', methods=['POST'])
def create_battleship_game():
    data = request.json
    user_id = str(data.get('userId'))
    username = data.get('username')
    first_name = data.get('first_name')
    if not user_id:
        return jsonify({'success': False, 'error': 'Не указан ID пользователя'})
    # Получаем или создаём пользователя (аналогично другим играм)
    db_user_id = get_or_create_user(user_id, username, first_name)
    # Генерация уникального кода игры
    game_id = generate_game_id()
    while game_id in battleship_games:
        game_id = generate_game_id()
    # Создание новой игры
    battleship_games[game_id] = {
        'status': 'waiting',
        'created_at': time.time(),
        'players': {
            str(db_user_id): {
                'id': str(db_user_id),
                'ready': False,
                'board': None
            }
        },
        'current_player': None,
        'winner': None,
        'boards': {}
    }
    return jsonify({
        'success': True,
        'gameId': game_id,
        'playerId': str(db_user_id)
    })

# Присоединение к игре
@app.route('/api/battleship/join', methods=['POST'])
def join_battleship_game():
    data = request.json
    game_id = data.get('gameId')
    user_id = str(data.get('userId'))
    if not game_id or not user_id:
        return jsonify({'success': False, 'error': 'Не указан код игры или ID пользователя'})
    if game_id not in battleship_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = battleship_games[game_id]
    if game['status'] != 'waiting':
        return jsonify({'success': False, 'error': 'Игра уже началась'})
    if user_id in game['players']:
        return jsonify({'success': False, 'error': 'Вы уже создали эту игру'})
    game['players'][user_id] = {
        'id': user_id,
        'ready': False,
        'board': None
    }
    game['status'] = 'placement'
    return jsonify({
        'success': True,
        'gameId': game_id,
        'playerId': user_id
    })

# Отмена игры
@app.route('/api/battleship/cancel', methods=['POST'])
def cancel_battleship_game():
    data = request.json
    game_id = data.get('gameId')
    player_id = str(data.get('playerId'))
    if not game_id or not player_id:
        return jsonify({'success': False, 'error': 'Не указан код игры или ID игрока'})
    if game_id not in battleship_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = battleship_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    del battleship_games[game_id]
    return jsonify({'success': True})

# Готовность игрока
@app.route('/api/battleship/ready', methods=['POST'])
def ready_battleship_game():
    data = request.json
    game_id = data.get('gameId')
    player_id = str(data.get('playerId'))
    board = data.get('board')
    if not game_id or not player_id or not board:
        return jsonify({'success': False, 'error': 'Не указан код игры, ID игрока или доска'})
    if game_id not in battleship_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = battleship_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    if game['status'] != 'placement':
        return jsonify({'success': False, 'error': 'Игра не в состоянии расстановки кораблей'})
    if not validate_board(board):
        return jsonify({'success': False, 'error': 'Некорректная расстановка кораблей'})
    game['players'][player_id]['ready'] = True
    game['boards'][player_id] = board
    all_ready = all(player['ready'] for player in game['players'].values())
    if all_ready and len(game['players']) == 2:
        game['status'] = 'playing'
        game['current_player'] = random.choice(list(game['players'].keys()))
    return jsonify({
        'success': True,
        'gameState': game
    })

# Получение состояния игры
@app.route('/api/battleship/state/<game_id>/<player_id>')
def get_battleship_game_state(game_id, player_id):
    player_id = str(player_id)
    if game_id not in battleship_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = battleship_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    if game['status'] == 'finished':
        return jsonify({
            'success': True,
            'gameState': game
        })
    if game['status'] == 'waiting' and time.time() - game['created_at'] > 3600:
        del battleship_games[game_id]
        return jsonify({'success': False, 'error': 'Время ожидания истекло'})
    return jsonify({
        'success': True,
        'gameState': game
    })

# Вскрытие клеток вокруг потопленного корабля
def reveal_around_sunk_ship(board, row, col):
    # Найти все клетки корабля (горизонтально или вертикально)
    ship_cells = [(row, col)]
    # Проверяем горизонтальное направление
    c = col - 1
    while c >= 0 and board[row][c] == 2:
        ship_cells.append((row, c))
        c -= 1
    c = col + 1
    while c < 10 and board[row][c] == 2:
        ship_cells.append((row, c))
        c += 1
    # Проверяем вертикальное направление
    r = row - 1
    while r >= 0 and board[r][col] == 2:
        ship_cells.append((r, col))
        r -= 1
    r = row + 1
    while r < 10 and board[r][col] == 2:
        ship_cells.append((r, col))
        r += 1
    # Для каждой клетки корабля вскрываем все соседние клетки
    for (r, c) in ship_cells:
        for dr in [-1, 0, 1]:
            for dc in [-1, 0, 1]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < 10 and 0 <= nc < 10:
                    # Вскрываем только если клетка пуста (0)
                    if board[nr][nc] == 0:
                        board[nr][nc] = 3  # Промах

# Ход игрока
@app.route('/api/battleship/move', methods=['POST'])
def make_battleship_move():
    data = request.json
    game_id = data.get('gameId')
    player_id = str(data.get('playerId'))
    row = data.get('row')
    col = data.get('col')
    if not game_id or not player_id or row is None or col is None:
        return jsonify({'success': False, 'error': 'Не указан код игры, ID игрока или координаты'})
    if game_id not in battleship_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = battleship_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    if game['status'] != 'playing':
        return jsonify({'success': False, 'error': 'Игра не в процессе'})
    if game['current_player'] != player_id:
        return jsonify({'success': False, 'error': 'Сейчас не ваш ход'})
    if row < 0 or row >= 10 or col < 0 or col >= 10:
        return jsonify({'success': False, 'error': 'Некорректные координаты'})
    opponent_id = next(id for id in game['players'].keys() if id != player_id)
    if game['boards'][opponent_id][row][col] in [2, 3]:
        return jsonify({'success': False, 'error': 'Вы уже стреляли в эту клетку'})
    if game['boards'][opponent_id][row][col] == 1:
        game['boards'][opponent_id][row][col] = 2
        if is_ship_sunk(game['boards'][opponent_id], row, col):
            # Вскрываем клетки вокруг потопленного корабля
            reveal_around_sunk_ship(game['boards'][opponent_id], row, col)
            if are_all_ships_sunk(game['boards'][opponent_id]):
                game['status'] = 'finished'
                game['winner'] = player_id
    else:
        game['boards'][opponent_id][row][col] = 3
        game['current_player'] = opponent_id
    return jsonify({
        'success': True,
        'gameState': game
    })

# Выход из игры
@app.route('/api/battleship/exit', methods=['POST'])
def exit_battleship_game():
    data = request.json
    game_id = data.get('gameId')
    player_id = str(data.get('playerId'))
    reason = data.get('reason', 'player_exit')
    
    if not game_id or not player_id:
        return jsonify({'success': False, 'error': 'Не указан код игры или ID игрока'})
    
    if game_id not in battleship_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    
    game = battleship_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    
    # Если игра еще не закончена, отмечаем победителем другого игрока
    if game['status'] != 'finished':
        game['status'] = 'finished'
        opponent_id = next(id for id in game['players'].keys() if id != player_id)
        game['winner'] = opponent_id
        game['exit_reason'] = reason
    
    return jsonify({
        'success': True,
        'gameState': game
    })

# Сдача игры
@app.route('/api/battleship/surrender', methods=['POST'])
def surrender_battleship_game():
    data = request.json
    game_id = data.get('gameId')
    player_id = str(data.get('playerId'))
    reason = data.get('reason', 'surrender')
    
    if not game_id or not player_id:
        return jsonify({'success': False, 'error': 'Не указан код игры или ID игрока'})
    
    if game_id not in battleship_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    
    game = battleship_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    
    # Если игра еще не закончена, отмечаем победителем другого игрока
    if game['status'] != 'finished':
        game['status'] = 'finished'
        opponent_id = next(id for id in game['players'].keys() if id != player_id)
        game['winner'] = opponent_id
        game['exit_reason'] = reason
    
    return jsonify({
        'success': True,
        'gameState': game
    })

# Проверка доски на корректность
def validate_board(board):
    # Проверка размеров доски
    if len(board) != 10 or any(len(row) != 10 for row in board):
        return False
    
    # Подсчет кораблей
    ship_counts = defaultdict(int)
    
    # Проверка каждой клетки
    for row in range(10):
        for col in range(10):
            if board[row][col] == 1:  # Клетка с кораблем
                # Проверка, что корабль не прилегает к другому кораблю
                for dr in [-1, 0, 1]:
                    for dc in [-1, 0, 1]:
                        if dr == 0 and dc == 0:
                            continue
                        
                        nr, nc = row + dr, col + dc
                        if 0 <= nr < 10 and 0 <= nc < 10 and board[nr][nc] == 1:
                            # Проверка, что это часть того же корабля
                            if not is_same_ship(board, row, col, nr, nc):
                                return False
    
    # Подсчет кораблей
    for row in range(10):
        for col in range(10):
            if board[row][col] == 1:
                # Проверка, не посчитан ли уже этот корабль
                if not is_part_of_counted_ship(board, row, col):
                    # Подсчет размера корабля
                    ship_size = get_ship_size(board, row, col)
                    ship_counts[ship_size] += 1
    
    # Проверка количества кораблей
    if ship_counts[4] != 1 or ship_counts[3] != 2 or ship_counts[2] != 3 or ship_counts[1] != 4:
        return False
    
    return True

# Проверка, является ли клетка частью того же корабля
def is_same_ship(board, row1, col1, row2, col2):
    # Проверка, что клетки находятся в одной строке или столбце
    if row1 != row2 and col1 != col2:
        return False
    
    # Проверка, что между клетками нет пустых клеток
    if row1 == row2:
        # Горизонтальный корабль
        min_col, max_col = min(col1, col2), max(col1, col2)
        for col in range(min_col, max_col + 1):
            if board[row1][col] != 1:
                return False
    else:
        # Вертикальный корабль
        min_row, max_row = min(row1, row2), max(row1, row2)
        for row in range(min_row, max_row + 1):
            if board[row][col1] != 1:
                return False
    
    return True

# Проверка, является ли клетка частью уже посчитанного корабля
def is_part_of_counted_ship(board, row, col):
    # Проверка соседних клеток
    for dr in [-1, 0, 1]:
        for dc in [-1, 0, 1]:
            if dr == 0 and dc == 0:
                continue
            
            nr, nc = row + dr, col + dc
            if 0 <= nr < 10 and 0 <= nc < 10 and board[nr][nc] == 1:
                # Проверка, что это часть того же корабля
                if is_same_ship(board, row, col, nr, nc):
                    # Проверка, что соседняя клетка находится выше или левее
                    if nr < row or (nr == row and nc < col):
                        return True
    
    return False

# Получение размера корабля
def get_ship_size(board, row, col):
    # Проверка, горизонтальный или вертикальный корабль
    is_horizontal = False
    is_vertical = False
    
    # Проверка соседних клеток
    if col > 0 and board[row][col - 1] == 1:
        is_horizontal = True
    elif col < 9 and board[row][col + 1] == 1:
        is_horizontal = True
    elif row > 0 and board[row - 1][col] == 1:
        is_vertical = True
    elif row < 9 and board[row + 1][col] == 1:
        is_vertical = True
    
    # Подсчет размера корабля
    size = 1
    
    if is_horizontal:
        # Подсчет влево
        c = col - 1
        while c >= 0 and board[row][c] == 1:
            size += 1
            c -= 1
        
        # Подсчет вправо
        c = col + 1
        while c < 10 and board[row][c] == 1:
            size += 1
            c += 1
    elif is_vertical:
        # Подсчет вверх
        r = row - 1
        while r >= 0 and board[r][col] == 1:
            size += 1
            r -= 1
        
        # Подсчет вниз
        r = row + 1
        while r < 10 and board[r][col] == 1:
            size += 1
            r += 1
    
    return size

# Проверка, потоплен ли корабль
def is_ship_sunk(board, row, col):
    # Проверка, что клетка содержит корабль
    if board[row][col] != 2:  # 2 - попадание
        return False
    
    # Проверка, горизонтальный или вертикальный корабль
    is_horizontal = False
    is_vertical = False
    
    # Проверка соседних клеток
    if col > 0 and board[row][col - 1] in [1, 2]:
        is_horizontal = True
    elif col < 9 and board[row][col + 1] in [1, 2]:
        is_horizontal = True
    elif row > 0 and board[row - 1][col] in [1, 2]:
        is_vertical = True
    elif row < 9 and board[row + 1][col] in [1, 2]:
        is_vertical = True
    
    # Проверка, все ли клетки корабля поражены
    if is_horizontal:
        # Проверка влево
        c = col - 1
        while c >= 0 and board[row][c] in [1, 2]:
            if board[row][c] == 1:  # Есть неповрежденная клетка
                return False
            c -= 1
        
        # Проверка вправо
        c = col + 1
        while c < 10 and board[row][c] in [1, 2]:
            if board[row][c] == 1:  # Есть неповрежденная клетка
                return False
            c += 1
    elif is_vertical:
        # Проверка вверх
        r = row - 1
        while r >= 0 and board[r][col] in [1, 2]:
            if board[r][col] == 1:  # Есть неповрежденная клетка
                return False
            r -= 1
        
        # Проверка вниз
        r = row + 1
        while r < 10 and board[r][col] in [1, 2]:
            if board[r][col] == 1:  # Есть неповрежденная клетка
                return False
            r += 1
    
    return True

# Проверка, потоплены ли все корабли
def are_all_ships_sunk(board):
    # Проверка каждой клетки
    for row in range(10):
        for col in range(10):
            if board[row][col] == 1:  # Есть неповрежденная клетка корабля
                return False
    
    return True

@app.route('/games/battleship')
def battleship():
    return render_template('games/battleship.html')

# Получение списка доступных игр
@app.route('/api/battleship/games', methods=['GET'])
def get_battleship_games():
    # Фильтруем только игры в статусе 'waiting'
    available_games = {}
    
    for game_id, game in battleship_games.items():
        if game['status'] == 'waiting':
            # Получаем ID создателя игры
            host_id = list(game['players'].keys())[0] if game['players'] else None
            
            # Получаем имя или username создателя игры из базы данных
            host_name = "Игрок"
            if host_id:
                try:
                    db = get_db()
                    user = db.execute('SELECT username, first_name FROM users WHERE id = ?', (host_id,)).fetchone()
                    if user:
                        if user['username']:
                            host_name = f"@{user['username']}"
                        elif user['first_name']:
                            host_name = user['first_name']
                except Exception as e:
                    app.logger.error(f'Error getting host name: {e}')
            
            available_games[game_id] = {
                'id': game_id,
                'created_at': game['created_at'],
                'host_id': host_id,
                'host_name': host_name,
                'status': game['status']
            }
    
    return jsonify({
        'success': True,
        'games': available_games
    })

# API для работы с реакциями на расписание
@app.route('/api/schedule/reactions', methods=['GET'])
def get_schedule_reactions():
    init_data = request.args.get('tgWebAppData')
    date = request.args.get('date')
    
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    if not date:
        return jsonify({'success': False, 'error': 'Не указана дата'})
    
    try:
        # Получаем данные пользователя
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
        
        # Получаем ID пользователя из базы
        db = get_db()
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        # Получаем все реакции на расписание за указанную дату
        reactions = db.execute('''
            SELECT reaction, COUNT(*) as count 
            FROM schedule_reactions 
            WHERE date = ? 
            GROUP BY reaction
        ''', (date,)).fetchall()
        
        # Получаем реакции текущего пользователя
        user_reactions = db.execute('''
            SELECT reaction 
            FROM schedule_reactions 
            WHERE user_id = ? AND date = ?
        ''', (user_id, date)).fetchall()
        
        # Формируем список реакций пользователя
        user_reactions_list = [r['reaction'] for r in user_reactions]
        
        # Формируем список всех реакций с количеством
        reactions_list = [{'reaction': r['reaction'], 'count': r['count']} for r in reactions]
        
        return jsonify({
            'success': True,
            'reactions': reactions_list,
            'user_reactions': user_reactions_list
        })
    except Exception as e:
        app.logger.error(f'Error in get_schedule_reactions: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/schedule/reactions/toggle', methods=['POST'])
def toggle_schedule_reaction():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    date = data.get('date')
    reaction = data.get('reaction')
    
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    
    if not date or not reaction:
        return jsonify({'success': False, 'error': 'Не указана дата или реакция'})
    
    try:
        # Получаем данные пользователя
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
        
        # Получаем ID пользователя из базы
        db = get_db()
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        # Проверяем, есть ли уже такая реакция у пользователя
        existing_reaction = db.execute('''
            SELECT id FROM schedule_reactions 
            WHERE user_id = ? AND date = ? AND reaction = ?
        ''', (user_id, date, reaction)).fetchone()
        
        if existing_reaction:
            # Если реакция уже есть, удаляем её
            db.execute('''
                DELETE FROM schedule_reactions 
                WHERE user_id = ? AND date = ? AND reaction = ?
            ''', (user_id, date, reaction))
            action = 'removed'
        else:
            # Если реакции нет, сначала удаляем все существующие реакции пользователя
            db.execute('''
                DELETE FROM schedule_reactions 
                WHERE user_id = ? AND date = ?
            ''', (user_id, date))
            
            # Затем добавляем новую реакцию
            db.execute('''
                INSERT INTO schedule_reactions (user_id, date, reaction) 
                VALUES (?, ?, ?)
            ''', (user_id, date, reaction))
            action = 'added'
        
        db.commit()
        
        # Получаем обновленные данные о реакциях
        reactions = db.execute('''
            SELECT reaction, COUNT(*) as count 
            FROM schedule_reactions 
            WHERE date = ? 
            GROUP BY reaction
        ''', (date,)).fetchall()
        
        # Получаем реакции текущего пользователя
        user_reactions = db.execute('''
            SELECT reaction 
            FROM schedule_reactions 
            WHERE user_id = ? AND date = ?
        ''', (user_id, date)).fetchall()
        
        # Формируем список реакций пользователя
        user_reactions_list = [r['reaction'] for r in user_reactions]
        
        # Формируем список всех реакций с количеством
        reactions_list = [{'reaction': r['reaction'], 'count': r['count']} for r in reactions]
        
        return jsonify({
            'success': True,
            'action': action,
            'reactions': reactions_list,
            'user_reactions': user_reactions_list
        })
    except Exception as e:
        app.logger.error(f'Error in toggle_schedule_reaction: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/review', methods=['POST'])
def api_add_review():
    data = request.get_json()
    mark = data.get('mark')
    review = data.get('review', '').strip()
    if not isinstance(mark, int) or not (1 <= mark <= 10):
        return jsonify({'success': False, 'error': 'Оценка должна быть от 1 до 10'}), 400
    if not review:
        return jsonify({'success': False, 'error': 'Текст отзыва обязателен'}), 400
    db = get_db()
    db.execute('INSERT INTO reviews (mark, review) VALUES (?, ?)', (mark, review))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/casino/balance', methods=['POST'])
def casino_balance():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'}), 403
    params = parse_init_data_params(init_data)
    user_data = params.get('user', '{}')
    try:
        user = json.loads(user_data)
    except Exception:
        user = {}
    tg_id = user.get('id')
    username = user.get('username', '')
    first_name = user.get('first_name', 'Пользователь')
    if not tg_id:
        return jsonify({'success': False, 'error': 'Нет tg_id'}), 400
    user_id = get_or_create_user(tg_id, username, first_name)
    db = get_db()
    row = db.execute('SELECT score FROM casino_users WHERE user_id = ?', (user_id,)).fetchone()
    if not row:
        db.execute('INSERT INTO casino_users (user_id, score) VALUES (?, 10)', (user_id,))
        db.commit()
        score = 10
    else:
        score = row['score']
    return jsonify({'success': True, 'score': score})

@app.route('/api/casino/rating', methods=['GET'])
def casino_rating():
    db = get_db()
    rows = db.execute('''
        SELECT u.first_name, u.username, c.score FROM casino_users c
        JOIN users u ON c.user_id = u.id
        ORDER BY c.score DESC, c.user_id ASC LIMIT 10
    ''').fetchall()
    rating = [dict(row) for row in rows]
    return jsonify({'success': True, 'rating': rating})

@app.route('/api/casino/bet', methods=['POST'])
def casino_bet():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    bet = data.get('bet')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'}), 403
    if not isinstance(bet, int) or bet < 1:
        return jsonify({'success': False, 'error': 'Ставка должна быть положительным числом'}), 400
    params = parse_init_data_params(init_data)
    user_data = params.get('user', '{}')
    try:
        user = json.loads(user_data)
    except Exception:
        user = {}
    tg_id = user.get('id')
    username = user.get('username', '')
    first_name = user.get('first_name', 'Пользователь')
    if not tg_id:
        return jsonify({'success': False, 'error': 'Нет tg_id'}), 400
    user_id = get_or_create_user(tg_id, username, first_name)
    db = get_db()
    row = db.execute('SELECT score FROM casino_users WHERE user_id = ?', (user_id,)).fetchone()
    if not row:
        db.execute('INSERT INTO casino_users (user_id, score) VALUES (?, 10)', (user_id,))
        db.commit()
        score = 10
    else:
        score = row['score']
    if score > 10:
        max_bet = score
    else:
        max_bet = 10
    if bet > max_bet:
        return jsonify({'success': False, 'error': f'Максимальная ставка: {max_bet}'}), 400
    import random
    # 1 из 20 — супервыигрыш
    if random.randint(1, 20) == 1:
        win_amount = bet * 5
        new_score = score + win_amount
        db.execute('UPDATE casino_users SET score = ? WHERE user_id = ?', (new_score, user_id))
        db.commit()
        return jsonify({'success': True, 'result': 'superwin', 'type': 'superwin', 'score': new_score, 'win_amount': win_amount, 'delta': win_amount, 'max_bet': max_bet})
    # Шанс выигрыша
    if score <= 10:
        win_chance = 0.7
    else:
        win_chance = 0.6
    win = random.random() < win_chance
    if win:
        new_score = score + bet
        db.execute('UPDATE casino_users SET score = ? WHERE user_id = ?', (new_score, user_id))
        db.commit()
        return jsonify({'success': True, 'result': 'win', 'type': 'win', 'score': new_score, 'win_amount': bet, 'delta': bet, 'max_bet': max_bet})
    else:
        loss = bet // 2
        new_score = score - loss
        db.execute('UPDATE casino_users SET score = ? WHERE user_id = ?', (new_score, user_id))
        db.commit()
        return jsonify({'success': True, 'result': 'lose', 'type': 'lose', 'score': new_score, 'loss_amount': loss, 'delta': -loss, 'max_bet': max_bet})

@app.route('/api/favorites', methods=['GET'])
def api_get_favorites():
    init_data = request.args.get('tgWebAppData')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    try:
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
        db = get_db()
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        rows = db.execute('SELECT id, entity_type, entity_id, entity_name, created_at FROM favorite_entities WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
        favorites = [dict(row) for row in rows]
        return jsonify({'success': True, 'favorites': favorites})
    except Exception as e:
        app.logger.error(f'Error in api_get_favorites: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/favorites/add', methods=['POST'])
def api_add_favorite():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    entity_type = data.get('entity_type')  # 'group' или 'teacher'
    entity_id = data.get('entity_id')
    entity_name = data.get('entity_name')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    if not entity_type or not entity_id or not entity_name:
        return jsonify({'success': False, 'error': 'Не все параметры переданы'})
    try:
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
        db = get_db()
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        # Проверяем, нет ли уже такого избранного
        exists = db.execute('SELECT 1 FROM favorite_entities WHERE user_id = ? AND entity_type = ? AND entity_id = ?', (user_id, entity_type, entity_id)).fetchone()
        if exists:
            return jsonify({'success': False, 'error': 'Уже в избранном'})
        db.execute('INSERT INTO favorite_entities (user_id, entity_type, entity_id, entity_name) VALUES (?, ?, ?, ?)', (user_id, entity_type, str(entity_id), entity_name))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f'Error in api_add_favorite: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/favorites/remove', methods=['POST'])
def api_remove_favorite():
    data = request.get_json()
    init_data = data.get('tgWebAppData')
    entity_type = data.get('entity_type')
    entity_id = data.get('entity_id')
    if not init_data or not check_init_data(init_data):
        return jsonify({'success': False, 'error': 'Ошибка авторизации'})
    if not entity_type or not entity_id:
        return jsonify({'success': False, 'error': 'Не все параметры переданы'})
    try:
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
        db = get_db()
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        db.execute('DELETE FROM favorite_entities WHERE user_id = ? AND entity_type = ? AND entity_id = ?', (user_id, entity_type, str(entity_id)))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f'Error in api_remove_favorite: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

# === API для работы с часами предметов ===

@app.route('/api/subject-hours', methods=['POST'])
def api_save_subject_hours():
    """Сохранение часов по предмету"""
    try:
        data = request.get_json()
        init_data = data.get('tgWebAppData')
        
        if not init_data or not check_init_data(init_data):
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
        
        # Получаем данные
        subject_name = data.get('subject_name', '').strip()
        teacher_name = data.get('teacher_name', '').strip()
        group_name = data.get('group_name', '').strip()
        planned_hours = float(data.get('planned_hours', 0))
        completed_hours = float(data.get('completed_hours', 0))
        
        # Валидация
        if not subject_name or not teacher_name:
            return jsonify({'success': False, 'error': 'Не указан предмет или преподаватель'})
        
        if planned_hours < 0 or completed_hours < 0:
            return jsonify({'success': False, 'error': 'Часы не могут быть отрицательными'})
        
        if planned_hours > 1000 or completed_hours > 1000:
            return jsonify({'success': False, 'error': 'Слишком большое значение часов'})
        
        # Получаем пользователя
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        db = get_db()
        
        # Используем INSERT OR REPLACE для обновления существующих записей
        db.execute('''
            INSERT OR REPLACE INTO subject_hours 
            (user_id, subject_name, teacher_name, group_name, planned_hours, completed_hours, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, subject_name, teacher_name, group_name, planned_hours, completed_hours))
        
        db.commit()
        
        return jsonify({'success': True, 'message': 'Часы сохранены'})
        
    except ValueError as e:
        return jsonify({'success': False, 'error': 'Неверный формат числа'})
    except Exception as e:
        app.logger.error(f'Error in api_save_subject_hours: {str(e)}')
        return jsonify({'success': False, 'error': 'Ошибка сервера'})

@app.route('/api/subject-hours', methods=['GET'])
def api_get_subject_hours():
    """Получение всех часов пользователя"""
    try:
        init_data = request.args.get('tgWebAppData')
        
        if not init_data or not check_init_data(init_data):
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
        
        # Получаем пользователя
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        db = get_db()
        cursor = db.execute('''
            SELECT subject_name, teacher_name, group_name, planned_hours, completed_hours, updated_at
            FROM subject_hours 
            WHERE user_id = ?
            ORDER BY updated_at DESC
        ''', (user_id,))
        
        hours = []
        for row in cursor.fetchall():
            hours.append({
                'subject_name': row['subject_name'],
                'teacher_name': row['teacher_name'],
                'group_name': row['group_name'],
                'planned_hours': row['planned_hours'],
                'completed_hours': row['completed_hours'],
                'updated_at': row['updated_at']
            })
        
        return jsonify({'success': True, 'hours': hours})
        
    except Exception as e:
        app.logger.error(f'Error in api_get_subject_hours: {str(e)}')
        return jsonify({'success': False, 'error': 'Ошибка сервера'})

@app.route('/api/subject-hours/auto-deduct', methods=['POST'])
def api_auto_deduct_hours():
    """Автоматическое списание часов при проведении пары"""
    try:
        data = request.get_json()
        init_data = data.get('tgWebAppData')
        
        if not init_data or not check_init_data(init_data):
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
        
        # Получаем данные о проведенной паре
        subject_name = data.get('subject_name', '').strip()
        teacher_name = data.get('teacher_name', '').strip()
        group_name = data.get('group_name', '').strip()
        lesson_duration = float(data.get('lesson_duration', 2))  # По умолчанию 2 часа (1 пара)
        
        # Валидация
        if not subject_name or not teacher_name:
            return jsonify({'success': False, 'error': 'Не указан предмет или преподаватель'})
        
        if lesson_duration <= 0 or lesson_duration > 8:
            return jsonify({'success': False, 'error': 'Неверная продолжительность пары'})
        
        # Получаем пользователя
        params = parse_init_data_params(init_data)
        user_data = json.loads(params.get('user', '{}'))
        tg_id = user_data.get('id')
        
        if not tg_id:
            return jsonify({'success': False, 'error': 'Ошибка авторизации'})
            
        user_id = get_or_create_user(tg_id, user_data.get('username'), user_data.get('first_name'))
        
        db = get_db()
        
        # Получаем текущие данные о часах
        cursor = db.execute('''
            SELECT planned_hours, completed_hours FROM subject_hours 
            WHERE user_id = ? AND subject_name = ? AND teacher_name = ? AND group_name = ?
        ''', (user_id, subject_name, teacher_name, group_name))
        
        row = cursor.fetchone()
        if not row:
            # Если записи нет, создаем с нулевыми планируемыми часами
            planned_hours = 0
            completed_hours = lesson_duration
        else:
            planned_hours = row['planned_hours']
            completed_hours = row['completed_hours'] + lesson_duration
        
        # Обновляем запись
        db.execute('''
            INSERT OR REPLACE INTO subject_hours 
            (user_id, subject_name, teacher_name, group_name, planned_hours, completed_hours, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, subject_name, teacher_name, group_name, planned_hours, completed_hours))
        
        db.commit()
        
        remaining_hours = max(0, planned_hours - completed_hours)
        
        return jsonify({
            'success': True, 
            'message': f'Списано {lesson_duration} ч.',
            'planned_hours': planned_hours,
            'completed_hours': completed_hours,
            'remaining_hours': remaining_hours
        })
        
    except ValueError as e:
        return jsonify({'success': False, 'error': 'Неверный формат числа'})
    except Exception as e:
        app.logger.error(f'Error in api_auto_deduct_hours: {str(e)}')
        return jsonify({'success': False, 'error': 'Ошибка сервера'})

# === Онлайн-шашки ===
checkers_games = {}

@app.route('/api/checkers/create', methods=['POST'])
def create_checkers_game():
    data = request.json
    user_id = str(data.get('userId'))
    username = data.get('username')
    first_name = data.get('first_name')
    if not user_id:
        return jsonify({'success': False, 'error': 'Не указан ID пользователя'})
    db_user_id = get_or_create_user(user_id, username, first_name)
    game_id = generate_game_id()
    while game_id in checkers_games:
        game_id = generate_game_id()
    checkers_games[game_id] = {
        'status': 'waiting',
        'created_at': time.time(),
        'players': {
            str(db_user_id): {
                'id': str(db_user_id),
                'color': 'white',
                'ready': False
            }
        },
        'current_player': None,
        'winner': None,
        'board': None,
        'move_history': []
    }
    return jsonify({'success': True, 'gameId': game_id, 'playerId': str(db_user_id)})

@app.route('/api/checkers/join', methods=['POST'])
def join_checkers_game():
    data = request.json
    game_id = data.get('gameId')
    user_id = str(data.get('userId'))
    if not game_id or not user_id:
        return jsonify({'success': False, 'error': 'Не указан код игры или ID пользователя'})
    if game_id not in checkers_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = checkers_games[game_id]
    if game['status'] != 'waiting':
        return jsonify({'success': False, 'error': 'Игра уже началась'})
    if user_id in game['players']:
        return jsonify({'success': False, 'error': 'Вы уже создали эту игру'})
    # Второй игрок получает цвет 'black'
    game['players'][user_id] = {
        'id': user_id,
        'color': 'black',
        'ready': False
    }
    game['status'] = 'playing'
    game['current_player'] = random.choice(list(game['players'].keys()))
    # Инициализация доски (8x8, стандартная расстановка)
    game['board'] = init_checkers_board()
    return jsonify({'success': True, 'gameId': game_id, 'playerId': user_id})

@app.route('/api/checkers/cancel', methods=['POST'])
def cancel_checkers_game():
    data = request.json
    game_id = data.get('gameId')
    player_id = str(data.get('playerId'))
    if not game_id or not player_id:
        return jsonify({'success': False, 'error': 'Не указан код игры или ID игрока'})
    if game_id not in checkers_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = checkers_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    del checkers_games[game_id]
    return jsonify({'success': True})

@app.route('/api/checkers/state/<game_id>/<player_id>')
def get_checkers_game_state(game_id, player_id):
    player_id = str(player_id)
    if game_id not in checkers_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = checkers_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    if game['status'] == 'finished':
        return jsonify({'success': True, 'gameState': game})
    if game['status'] == 'waiting' and time.time() - game['created_at'] > 3600:
        del checkers_games[game_id]
        return jsonify({'success': False, 'error': 'Время ожидания истекло'})
    return jsonify({'success': True, 'gameState': game})

@app.route('/api/checkers/exit', methods=['POST'])
def exit_checkers_game():
    data = request.json
    game_id = data.get('gameId')
    player_id = str(data.get('playerId'))
    reason = data.get('reason', 'player_exit')
    if not game_id or not player_id:
        return jsonify({'success': False, 'error': 'Не указан код игры или ID игрока'})
    if game_id not in checkers_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = checkers_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    if game['status'] != 'finished':
        game['status'] = 'finished'
        opponent_id = next(id for id in game['players'].keys() if id != player_id)
        game['winner'] = opponent_id
        game['exit_reason'] = reason
    return jsonify({'success': True, 'gameState': game})

@app.route('/api/checkers/games', methods=['GET'])
def get_checkers_games():
    available_games = {}
    for game_id, game in checkers_games.items():
        if game['status'] == 'waiting':
            host_id = list(game['players'].keys())[0] if game['players'] else None
            host_name = "Игрок"
            if host_id:
                try:
                    db = get_db()
                    user = db.execute('SELECT username, first_name FROM users WHERE id = ?', (host_id,)).fetchone()
                    if user:
                        if user['username']:
                            host_name = f"@{user['username']}"
                        elif user['first_name']:
                            host_name = user['first_name']
                except Exception as e:
                    app.logger.error(f'Error getting host name: {e}')
            available_games[game_id] = {
                'id': game_id,
                'created_at': game['created_at'],
                'host_id': host_id,
                'host_name': host_name,
                'status': game['status']
            }
    return jsonify({'success': True, 'games': available_games})

def init_checkers_board():
    # 8x8, 0 - пусто, 1 - белая шашка, 2 - черная шашка, 3 - белая дамка, 4 - черная дамка
    board = [[0 for _ in range(8)] for _ in range(8)]
    for row in range(3):
        for col in range(8):
            if (row + col) % 2 == 1:
                board[row][col] = 2  # черные шашки сверху
    for row in range(5, 8):
        for col in range(8):
            if (row + col) % 2 == 1:
                board[row][col] = 1  # белые шашки снизу
    return board

def get_possible_moves(board, row, col, color, must_capture_only=False):
    """
    Возвращает список возможных ходов и взятий для шашки (или дамки) на позиции row, col.
    Если must_capture_only=True, возвращает только взятия.
    Формат: [{'to': [row, col], 'capture': [row, col] or None}]
    """
    piece = board[row][col]
    moves = []
    captures = []
    directions = []
    if piece in [1, 3] and color == 'white':
        directions = [(-1, -1), (-1, 1)]
    if piece in [2, 4] and color == 'black':
        directions = [(1, -1), (1, 1)]
    if piece in [3, 4]:  # дамка
        directions = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    if piece in [3, 4]:  # дамка: ходит по диагонали на любое расстояние
        for dr, dc in directions:
            r, c = row + dr, col + dc
            found_capture = False
            while 0 <= r < 8 and 0 <= c < 8:
                if board[r][c] == 0 and not found_capture and not must_capture_only:
                    moves.append({'to': [r, c], 'capture': None})
                elif board[r][c] != 0:
                    # Если шашка противника и за ней пусто — взятие
                    if board[r][c] % 2 != piece % 2:
                        r2, c2 = r + dr, c + dc
                        while 0 <= r2 < 8 and 0 <= c2 < 8 and board[r2][c2] == 0:
                            captures.append({'to': [r2, c2], 'capture': [r, c]})
                            # Дамка может встать на любую пустую после взятия
                            r2 += dr
                            c2 += dc
                        found_capture = True
                    break  # Препятствие
                else:
                    break
                r += dr
                c += dc
    else:
        for dr, dc in directions:
            r, c = row + dr, col + dc
            # Обычный ход
            if 0 <= r < 8 and 0 <= c < 8 and board[r][c] == 0 and not must_capture_only:
                moves.append({'to': [r, c], 'capture': None})
            # Взятие
            r2, c2 = row + 2*dr, col + 2*dc
            if 0 <= r2 < 8 and 0 <= c2 < 8 and board[r][c] != 0 and board[r][c] % 2 != piece % 2 and board[r2][c2] == 0:
                captures.append({'to': [r2, c2], 'capture': [r, c]})
    return captures if captures else moves

def has_any_capture(board, color):
    for row in range(8):
        for col in range(8):
            piece = board[row][col]
            if (color == 'white' and piece in [1, 3]) or (color == 'black' and piece in [2, 4]):
                if get_possible_moves(board, row, col, color, must_capture_only=True):
                    return True
    return False

@app.route('/api/checkers/moves', methods=['POST'])
def checkers_moves():
    data = request.json
    game_id = data.get('gameId')
    player_id = str(data.get('playerId'))
    row = data.get('row')
    col = data.get('col')
    if not all([game_id, player_id, row is not None, col is not None]):
        return jsonify({'success': False, 'error': 'Не все параметры переданы'})
    if game_id not in checkers_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = checkers_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    color = game['players'][player_id]['color']
    board = game['board']
    must_capture = has_any_capture(board, color)
    moves = get_possible_moves(board, row, col, color, must_capture_only=must_capture)
    return jsonify({'success': True, 'moves': moves})

@app.route('/api/checkers/move', methods=['POST'])
def checkers_move():
    data = request.json
    game_id = data.get('gameId')
    player_id = str(data.get('playerId'))
    from_row = data.get('fromRow')
    from_col = data.get('fromCol')
    to_row = data.get('toRow')
    to_col = data.get('toCol')
    if not all([game_id, player_id, from_row is not None, from_col is not None, to_row is not None, to_col is not None]):
        return jsonify({'success': False, 'error': 'Не все параметры переданы'})
    if game_id not in checkers_games:
        return jsonify({'success': False, 'error': 'Игра не найдена'})
    game = checkers_games[game_id]
    if player_id not in game['players']:
        return jsonify({'success': False, 'error': 'Вы не являетесь участником этой игры'})
    if game['status'] != 'playing':
        return jsonify({'success': False, 'error': 'Игра не в процессе'})
    if game['current_player'] != player_id:
        return jsonify({'success': False, 'error': 'Сейчас не ваш ход'})
    board = game['board']
    color = game['players'][player_id]['color']
    piece = board[from_row][from_col]
    # Проверка, что игрок ходит своей шашкой
    if color == 'white' and piece not in [1, 3]:
        return jsonify({'success': False, 'error': 'Можно ходить только своими шашками'})
    if color == 'black' and piece not in [2, 4]:
        return jsonify({'success': False, 'error': 'Можно ходить только своими шашками'})
    # Проверка, что целевая клетка пуста
    if board[to_row][to_col] != 0:
        return jsonify({'success': False, 'error': 'Клетка занята'})
    # Проверка возможных ходов
    must_capture = has_any_capture(board, color)
    possible_moves = get_possible_moves(board, from_row, from_col, color, must_capture_only=must_capture)
    found = None
    for move in possible_moves:
        if move['to'] == [to_row, to_col]:
            found = move
            break
    if not found:
        return jsonify({'success': False, 'error': 'Недопустимый ход'})
    # Если это взятие
    if found['capture']:
        cap_row, cap_col = found['capture']
        board[cap_row][cap_col] = 0
    board[to_row][to_col] = piece
    board[from_row][from_col] = 0
    # Превращение в дамку
    if color == 'white' and to_row == 0 and piece == 1:
        board[to_row][to_col] = 3
    if color == 'black' and to_row == 7 and piece == 2:
        board[to_row][to_col] = 4
    # Проверка на множественное взятие
    if found['capture']:
        more_captures = get_possible_moves(board, to_row, to_col, color, must_capture_only=True)
        if more_captures:
            game['current_player'] = player_id
            game['move_history'].append({'from': [from_row, from_col], 'to': [to_row, to_col], 'player': player_id, 'capture': found['capture']})
            return jsonify({'success': True, 'gameState': game, 'moreCapture': True})
    # Смена хода
    next_player = next(pid for pid in game['players'] if pid != player_id)
    game['current_player'] = next_player
    game['move_history'].append({'from': [from_row, from_col], 'to': [to_row, to_col], 'player': player_id, 'capture': found['capture'] if found else None})
    # Проверка окончания игры (нет ходов у соперника)
    opp_color = 'black' if color == 'white' else 'white'
    has_moves = False
    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if (opp_color == 'white' and p in [1, 3]) or (opp_color == 'black' and p in [2, 4]):
                if get_possible_moves(board, r, c, opp_color):
                    has_moves = True
    if not has_moves:
        game['status'] = 'finished'
        game['winner'] = player_id
    return jsonify({'success': True, 'gameState': game})

@app.route('/sudoku')
def sudoku():
    return render_template('sudoku.html')



# === API для Судоку ===
@app.route('/api/sudoku/new_game', methods=['POST'])
def sudoku_new_game():
    try:
        data = request.get_json()
        tgWebAppData = data.get('tgWebAppData')
        difficulty = data.get('difficulty', 'easy')
        
        if not tgWebAppData:
            return jsonify({'success': False, 'error': 'No Telegram data'}), 400
        
        # Валидация Telegram WebApp данных
        if not check_init_data(tgWebAppData):
            return jsonify({'success': False, 'error': 'Invalid Telegram data'}), 400
        
        # Получаем данные пользователя
        params = parse_init_data_params(tgWebAppData)
        user_data = json.loads(params.get('user', '{}'))
        user_id = user_data.get('id')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'No user ID'}), 400
        
        # Генерируем судоку
        puzzle, solution = generate_sudoku_puzzle(difficulty)
        
        # Создаем игру в базе данных
        game_id = str(uuid.uuid4())
        
        db = get_db()
        db.execute('''
            INSERT INTO sudoku_games (game_id, user_id, difficulty, puzzle, solution, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (game_id, user_id, difficulty, json.dumps(puzzle), json.dumps(solution), int(time.time())))
        db.commit()
        
        return jsonify({
            'success': True,
            'game_id': game_id,
            'user_id': user_id,
            'puzzle': puzzle,
            'solution': solution,
            'difficulty': difficulty
        })
        
    except Exception as e:
        app.logger.error(f"Error creating sudoku game: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/sudoku/submit_score', methods=['POST'])
def sudoku_submit_score():
    try:
        data = request.get_json()
        tgWebAppData = data.get('tgWebAppData')
        game_id = data.get('game_id')
        completion_time = data.get('completion_time')
        difficulty = data.get('difficulty')
        hints_used = data.get('hints_used', 0)
        
        if not tgWebAppData or not game_id or completion_time is None:
            return jsonify({'success': False, 'error': 'Missing required data'}), 400
        
        # Валидация Telegram WebApp данных
        if not check_init_data(tgWebAppData):
            return jsonify({'success': False, 'error': 'Invalid Telegram data'}), 400
        
        # Получаем данные пользователя
        params = parse_init_data_params(tgWebAppData)
        user_data = json.loads(params.get('user', '{}'))
        user_id = user_data.get('id')
        first_name = user_data.get('first_name', 'Игрок')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'No user ID'}), 400
        
        # Валидация времени (защита от накрутки)
        if completion_time < 10 or completion_time > 3600:  # от 10 секунд до 1 часа
            return jsonify({'success': False, 'error': 'Invalid completion time'}), 400
        
        # Проверяем, что игра существует и принадлежит пользователю
        db = get_db()
        game = db.execute('''
            SELECT * FROM sudoku_games 
            WHERE game_id = ? AND user_id = ?
        ''', (game_id, user_id)).fetchone()
        
        if not game:
            return jsonify({'success': False, 'error': 'Game not found'}), 404
        
        # Проверяем, не был ли уже отправлен результат
        existing_score = db.execute('''
            SELECT * FROM sudoku_scores 
            WHERE game_id = ?
        ''', (game_id,)).fetchone()
        
        if existing_score:
            return jsonify({'success': False, 'error': 'Score already submitted'}), 400
        
        # Сохраняем результат
        db.execute('''
            INSERT INTO sudoku_scores (game_id, user_id, first_name, difficulty, completion_time, hints_used, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (game_id, user_id, first_name, difficulty, completion_time, hints_used, int(time.time())))
        db.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        app.logger.error(f"Error submitting sudoku score: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/sudoku/rating')
def sudoku_rating():
    try:
        db = get_db()
        
        # Получаем лучшие результаты по каждой сложности
        rating = db.execute('''
            SELECT first_name, difficulty, MIN(completion_time) as best_time
            FROM sudoku_scores
            GROUP BY user_id, difficulty
            ORDER BY best_time ASC
            LIMIT 50
        ''').fetchall()
        
        return jsonify({
            'success': True,
            'rating': [dict(row) for row in rating]
        })
        
    except Exception as e:
        app.logger.error(f"Error getting sudoku rating: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

def generate_sudoku_puzzle(difficulty):
    """Генерирует судоку заданной сложности"""
    # Создаем полное решение
    board = [[0 for _ in range(9)] for _ in range(9)]
    solution = [[0 for _ in range(9)] for _ in range(9)]
    
    # Заполняем диагональные блоки 3x3
    fill_diagonal_boxes(board)
    
    # Решаем остальные клетки
    solve_sudoku(board)
    
    # Копируем решение
    for i in range(9):
        for j in range(9):
            solution[i][j] = board[i][j]
    
    # Удаляем числа в зависимости от сложности
    cells_to_remove = {'easy': 40, 'medium': 50, 'hard': 60}.get(difficulty, 40)
    
    removed = 0
    while removed < cells_to_remove:
        row = random.randint(0, 8)
        col = random.randint(0, 8)
        
        if board[row][col] != 0:
            board[row][col] = 0
            removed += 1
    
    return board, solution

def fill_diagonal_boxes(board):
    """Заполняет диагональные блоки 3x3"""
    for box in range(0, 9, 3):
        fill_box(board, box, box)

def fill_box(board, row, col):
    """Заполняет блок 3x3 случайными числами"""
    numbers = list(range(1, 10))
    random.shuffle(numbers)
    
    index = 0
    for i in range(3):
        for j in range(3):
            board[row + i][col + j] = numbers[index]
            index += 1

def solve_sudoku(board):
    """Решает судоку используя backtracking"""
    for row in range(9):
        for col in range(9):
            if board[row][col] == 0:
                for num in range(1, 10):
                    if is_valid_sudoku_move(board, row, col, num):
                        board[row][col] = num
                        if solve_sudoku(board):
                            return True
                        board[row][col] = 0
                return False
    return True

def is_valid_sudoku_move(board, row, col, num):
    """Проверяет, можно ли поставить число в данную позицию"""
    # Проверяем строку (исключая текущую позицию)
    for c in range(9):
        if c != col and board[row][c] == num:
            return False
    
    # Проверяем столбец (исключая текущую позицию)
    for r in range(9):
        if r != row and board[r][col] == num:
            return False
    
    # Проверяем блок 3x3 (исключая текущую позицию)
    box_row = (row // 3) * 3
    box_col = (col // 3) * 3
    for r in range(box_row, box_row + 3):
        for c in range(box_col, box_col + 3):
            if (r != row or c != col) and board[r][c] == num:
                return False
    
    return True

if __name__ == '__main__':
    app.run(debug=True) 