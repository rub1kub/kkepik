"""Loopback staging for public knowledge pages, without bot/user endpoints."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SITE = Path('/var/www/kkepik.ru')


def backend():
    sys.path.insert(0, str(ROOT))
    from fastapi import FastAPI
    import uvicorn
    from knowledge_api import router
    application = FastAPI()
    application.include_router(router)
    uvicorn.run(application, host='127.0.0.1', port=18082, log_level='warning')


def site():
    sys.path.insert(0, str(SITE))
    from flask import Flask
    import knowledge_public
    knowledge_public.API_URL = 'http://127.0.0.1:18082'
    application = Flask(__name__, template_folder=str(SITE / 'templates'),
                        static_folder=str(SITE / 'static'))
    application.register_blueprint(knowledge_public.knowledge_public_bp)
    application.run(host='127.0.0.1', port=18083, use_reloader=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('backend', 'site'))
    args = parser.parse_args()
    (backend if args.mode == 'backend' else site)()
