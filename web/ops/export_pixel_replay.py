"""Publish a frozen, anonymous replay and a full-board MP4 from a SQLite snapshot."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pixel_canvas import PALETTE, CANVAS_BOUNDS

MAGIC = b'KKEPBR01'
RECORD = struct.Struct('<9i')
SOURCES = {'player': 0, 'admin': 1, 'baseline': 2, 'rollback': 3}


def export(database, archive_dir, video_dir, speed=3000, fps=30):
    db = sqlite3.connect('file:' + str(Path(database).resolve()) + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    event = dict(db.execute('SELECT * FROM canvas_event WHERE id=1').fetchone())
    if not event['ends_at_ms']:
        raise ValueError('The event has no deadline')
    rows = db.execute('SELECT id,revision,at_ms,x0,y0,x1,y1,color,source FROM canvas_history ORDER BY id').fetchall()
    if not rows:
        raise ValueError('There is no history')
    started = rows[0]['at_ms']
    payload = bytearray(MAGIC)
    baseline = 0
    groups = []
    for index, row in enumerate(rows):
        source = SOURCES[row['source']]
        payload.extend(RECORD.pack(row['id'], row['revision'], row['at_ms']-started,
                                   row['x0'], row['y0'], row['x1'], row['y1'], row['color'], source))
        if source == 2:
            baseline += 1
        if source == 3 and groups and rows[groups[-1][0]]['source'] == 'rollback' and rows[groups[-1][0]]['revision'] == row['revision']:
            groups[-1][1] = index+1
        else:
            groups.append([index, index+1])
    if any(r['source'] != 'baseline' for r in rows[:baseline]):
        raise ValueError('Baseline must precede edits')
    digest = hashlib.sha256(payload).hexdigest()
    day = datetime.fromtimestamp(event['ends_at_ms']/1000, timezone.utc)
    export_id = 'battle-' + day.strftime('%Y%m%d') + '-' + digest[:12]
    folder = Path(archive_dir) / export_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o755)
    (folder/'history.bin.gz').write_bytes(gzip.compress(payload, compresslevel=6, mtime=0))
    width = CANVAS_BOUNDS['x1'] - CANVAS_BOUNDS['x0']
    height = CANVAS_BOUNDS['y1'] - CANVAS_BOUNDS['y0']
    rgb = [bytes.fromhex(color[1:]) for color in PALETTE]
    board = bytearray(rgb[0] * (width*height))
    occupied = bytearray(width*height)

    def apply(row):
        x0 = max(CANVAS_BOUNDS['x0'], row['x0']) - CANVAS_BOUNDS['x0']
        x1 = min(CANVAS_BOUNDS['x1'], row['x1']) - CANVAS_BOUNDS['x0']
        y0 = max(CANVAS_BOUNDS['y0'], row['y0']) - CANVAS_BOUNDS['y0']
        y1 = min(CANVAS_BOUNDS['y1'], row['y1']) - CANVAS_BOUNDS['y0']
        strip = rgb[max(0, row['color'])] * (x1-x0)
        mark = bytes([int(row['color'] >= 0)]) * (x1-x0)
        for y in range(y0, y1):
            pos = y*width+x0
            board[pos*3:(pos+x1-x0)*3] = strip
            occupied[pos:pos+x1-x0] = mark

    for row in rows[:baseline]:
        apply(row)
    video_dir = Path(video_dir)
    video_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    video_name = 'pixel-battle-' + day.strftime('%Y-%m-%d') + '-' + digest[:12] + '.mp4'
    temporary = video_dir / (video_name + '.part.mp4')
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
           '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{width}x{height}',
           '-framerate', str(fps), '-i', 'pipe:0', '-an',
           '-vf', f'scale={width*6}:{height*6}:flags=neighbor',
           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-threads', '1',
           '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(temporary)]
    baseline_groups = sum(1 for start, _end in groups if rows[start]['source'] == 'baseline')
    changes = len(groups) - baseline_groups
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for _ in range(fps):
            process.stdin.write(board)
        cursor = baseline_groups
        frames = math.ceil(changes*fps/speed)
        for frame in range(frames):
            target = baseline_groups + min(changes, math.floor((frame+1)*speed/fps))
            while cursor < target:
                start, end = groups[cursor]
                for row in rows[start:end]:
                    apply(row)
                cursor += 1
            process.stdin.write(board)
        for _ in range(fps*5):
            process.stdin.write(board)
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError('Video encoder failed')
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait()
        temporary.unlink(missing_ok=True)
        raise
    # The final replay must be byte-for-byte equivalent to the frozen live board.
    expected = bytearray(rgb[0]*(width*height))
    expected_occupied = bytearray(width*height)
    for row in db.execute('SELECT x,y,color FROM pixels'):
        pos = (row['y']-CANVAS_BOUNDS['y0'])*width + row['x']-CANVAS_BOUNDS['x0']
        expected[pos*3:pos*3+3] = rgb[row['color']]
        expected_occupied[pos] = 1
    if board != expected or occupied != expected_occupied:
        temporary.unlink(missing_ok=True)
        raise ValueError('History does not reproduce the final board')
    temporary.replace(video_dir/video_name)
    participants = db.execute("SELECT COUNT(DISTINCT actor_id) FROM canvas_history WHERE source='player' AND actor_id IS NOT NULL").fetchone()[0]
    manifest = {
        'success': True, 'format': 'kkepik-pixel-history-v1', 'export_id': export_id,
        'stride': 9, 'record_count': len(rows), 'baseline_records': baseline,
        'history_started_at_ms': started, 'started_at_ms': event['started_at_ms'],
        'ends_at_ms': event['ends_at_ms'], 'head': rows[-1]['id'], 'revision': rows[-1]['revision'],
        'bounds': CANVAS_BOUNDS, 'palette': PALETTE, 'sha256': digest,
        'default_speed': speed, 'stats': {'participants': participants, 'changes': changes,
        'pixel_writes': sum(r['source'] == 'player' for r in rows),
        'filled_pixels': sum(occupied), 'total_pixels': width*height},
        'data_url': '/api/pixels/replay/data?export_id='+export_id,
        'video_url': '/static/exports/'+video_name,
        'video': {'width': width*6, 'height': height*6, 'fps': fps,
                  'duration_seconds': (frames+fps*6)/fps, 'size_bytes': (video_dir/video_name).stat().st_size},
    }
    db.close()
    (folder/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False)+'\n')
    # Keep old immutable versions available for clients that already fetched them.
    pointer = Path(archive_dir)/'current.json.tmp'
    pointer.write_text(json.dumps({'export_id': export_id})+'\n')
    pointer.replace(Path(archive_dir)/'current.json')
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('database')
    parser.add_argument('--archive-dir', default='/var/lib/kkepik-pixels/replay')
    parser.add_argument('--video-dir', default='/var/www/kkepik.ru/static/exports')
    parser.add_argument('--speed', type=int, default=3000)
    options = parser.parse_args()
    if not 1 <= options.speed <= 10000:
        parser.error('Speed must be between 1 and 10000')
    export(options.database, options.archive_dir, options.video_dir, options.speed)
