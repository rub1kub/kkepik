import sys
import os

# Активация виртуального окружения
venv_path = os.path.join(os.path.dirname(__file__), 'venv', 'bin', 'activate_this.py')
if os.path.exists(venv_path):
    with open(venv_path) as f:
        exec(f.read(), {'__file__': venv_path})

# Добавляем путь к проекту, если нужно
sys.path.insert(0, os.path.dirname(__file__))

from app import app as application 