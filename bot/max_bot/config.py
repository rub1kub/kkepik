# max_bot/config.py
"""
Конфигурация MAX messenger (pyromax).
"""

import os

# Включить/выключить интеграцию с MAX
MAX_ENABLED = True

# ID чатов, которые мониторить на файлы расписания (из .env: MAX_WATCH_CHAT_IDS=-123,456)
# Пустой список = мониторить все чаты (не рекомендуется)
MAX_WATCH_CHAT_IDS: list[int] = [
    int(x) for x in os.getenv("MAX_WATCH_CHAT_IDS", "").split(",") if x.strip()
]
