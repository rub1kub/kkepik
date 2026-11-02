"""Read the private action journal locally; never expose it through HTTP."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activity_store import ActivityStore, RETENTION_DAYS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', type=int)
    parser.add_argument('--since', help='YYYY-MM-DD, Europe/Moscow')
    parser.add_argument('--limit', type=int, default=200)
    parser.add_argument('--downloads', action='store_true', help='Only file requests and transfers; --user is optional')
    parser.add_argument('--anonymous', action='store_true', help='Public downloads without a Telegram ID')
    parser.add_argument('--prune', action='store_true')
    args = parser.parse_args()
    store = ActivityStore()
    if args.prune:
        with store.connect() as db:
            db.execute("DELETE FROM activity_meta WHERE key='pruned_at'")
            store._prune(db, int(datetime.now(timezone.utc).timestamp() * 1000))
        print(json.dumps(dict(pruned=True, retention_days=RETENTION_DAYS)))
        return
    if args.user is not None and args.user <= 0 or args.user is not None and args.anonymous:
        parser.error('Use a positive --user or --anonymous')
    if args.user is None and not args.downloads and not args.anonymous:
        parser.error('--user, --downloads or --anonymous is required')
    since = 0
    if args.since:
        since = int(datetime.strptime(args.since, '%Y-%m-%d').replace(tzinfo=ZoneInfo('Europe/Moscow')).timestamp() * 1000)
    if not store.path.exists():
        print(json.dumps(dict(user=args.user, events=[], journal_exists=False)))
        return
    with sqlite3.connect(f'file:{store.path}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        where, values = ['received_at_ms>=?'], [since]
        if args.user is not None or args.anonymous:
            where.append('actor_id=?'); values.append(0 if args.anonymous else args.user)
        if args.downloads or args.anonymous:
            where.append("action LIKE 'schedule.download.%'")
        values.append(max(1, min(args.limit, 2000)))
        rows = [dict(r) for r in db.execute('SELECT * FROM activity_events WHERE ' + ' AND '.join(where) +
            ' ORDER BY received_at_ms DESC,id DESC LIMIT ?', values)]
    for row in rows:
        row['at_moscow'] = datetime.fromtimestamp(row['received_at_ms'] / 1000, ZoneInfo('Europe/Moscow')).isoformat()
        row['metadata'] = json.loads(row['metadata'])
    print(json.dumps(dict(user=args.user, retention_days=RETENTION_DAYS, events=rows), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
