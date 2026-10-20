"""Loopback-only constructor QA. Isolated users/DB and dummy token; no bot sends."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SITE = Path('/var/www/kkepik.ru')


def backend():
    import sqlite3
    import tempfile
    sys.path.insert(0, str(ROOT))
    import config
    import global_schedules as gs
    from fastapi import FastAPI
    import uvicorn
    from schedules.pdf_to_df import pdf_to_dataframe
    from schedules.pdf_crop import crop_group_screenshots
    from tracked_groups import migrate_legacy_groups
    from schedule_preferences import init_preferences_table

    with tempfile.TemporaryDirectory(prefix='kkepik-constructor-qa-') as directory:
        pdf = Path(config.DATA_DIR) / 'Расписание на 06.10.2026_ГРУППЫ.pdf'
        config.TEST_MODE = False
        config.BOT_TOKEN = 'constructor-qa-only-token'
        config.DB_PATH = str(Path(directory) / 'fixture.sqlite')
        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute('CREATE TABLE users (user_id INTEGER PRIMARY KEY, role TEXT, name_or_group TEXT, is_class_teacher INTEGER, class_group TEXT)')
            conn.executemany('INSERT INTO users VALUES(?,?,?,?,?)', [
                (42, config.ROLE_STUDENT, '103-Д9-3ИНС', 0, None),
                (43, config.ROLE_STUDENT, '151-Д9-1ИНС', 0, None)])
            migrate_legacy_groups(conn)
            init_preferences_table(conn)
        gs.last_groups_df = pdf_to_dataframe(str(pdf))
        gs.last_groups_crop_cache = crop_group_screenshots(str(pdf))
        gs.last_groups_date = '06.10.2026'
        from message_settings_api import router
        app = FastAPI()
        app.include_router(router)
        uvicorn.run(app, host='127.0.0.1', port=18080, log_level='warning')


def site():
    sys.path.insert(0, str(SITE))
    from flask import Flask
    import schedule_api
    import telegram_auth
    schedule_api.API_URL = 'http://127.0.0.1:18080'
    telegram_auth.get_bot_token = lambda: 'constructor-qa-only-token'
    app = Flask(__name__, template_folder=str(SITE / 'templates'), static_folder=str(SITE / 'static'))
    app.register_blueprint(schedule_api.schedule_bp)
    app.run(host='127.0.0.1', port=18081, use_reloader=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('backend', 'site'))
    args = parser.parse_args()
    (backend if args.mode == 'backend' else site)()
