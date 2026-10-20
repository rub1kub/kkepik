"""Authenticated, read-only access to the anonymous finished-event archive."""
import json
import os
from pathlib import Path
import re
import time
from contextlib import closing

from flask import jsonify, request, send_file

EXPORT_ID = re.compile(r'battle-[0-9]{8}-[a-f0-9]{12}\Z')


def register_replay(app, connect, event_status):
    app.config.setdefault('CANVAS_REPLAY_DIRECTORY', os.environ.get(
        'KEPIK_CANVAS_REPLAY_DIRECTORY', '/var/lib/kkepik-pixels/replay'))

    def archive():
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            event = event_status(db, time.time_ns()//1_000_000)
        if not event or not event['finished']:
            return None, None, (jsonify(success=False, error='Игра ещё не завершена.'), 423)
        root = Path(app.config['CANVAS_REPLAY_DIRECTORY'])
        try:
            export_id = request.args.get('export_id')
            if export_id is None:
                export_id = json.loads((root/'current.json').read_text())['export_id']
            if not isinstance(export_id, str) or not EXPORT_ID.fullmatch(export_id):
                return None, None, (jsonify(success=False, error='Неверный архив.'), 400)
            folder = root/export_id
            manifest = json.loads((folder/'manifest.json').read_text())
            if manifest['export_id'] != export_id or manifest['ends_at_ms'] != event['ends_at_ms']:
                return None, None, (jsonify(success=False, error='Итоги готовятся.'), 404)
        except FileNotFoundError:
            return None, None, (jsonify(success=False, error='Итоги готовятся.'), 404)
        except (OSError, ValueError, KeyError, TypeError):
            return None, None, (jsonify(success=False, error='Архив временно недоступен.'), 503)
        return folder, manifest, None

    @app.get('/replay')
    def replay_manifest():
        _folder, manifest, error = archive()
        return error if error else jsonify(manifest)

    @app.get('/replay/data')
    def replay_data():
        folder, _manifest, error = archive()
        if error:
            return error
        if not (folder/'history.bin.gz').is_file():
            return jsonify(success=False, error='Итоги готовятся.'), 404
        response = send_file(folder/'history.bin.gz', mimetype='application/octet-stream',
                             conditional=False, etag=False)
        response.headers['Content-Encoding'] = 'gzip'
        return response
