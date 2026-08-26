import sys
import os

# Активация виртуального окружения
venv_path = os.path.join(os.path.dirname(__file__), 'venv', 'bin', 'activate_this.py')
if os.path.exists(venv_path):
    with open(venv_path) as f:
        exec(f.read(), {'__file__': venv_path})

env_file = "/etc/kkepik/kkepik.ru.env"
if os.path.exists(env_file):
    with open(env_file, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()

# Добавляем путь к проекту, если нужно
sys.path.insert(0, os.path.dirname(__file__))

from app import app as application
