"""Private projections of a duty list. Forecasting never changes the real queue."""
from datetime import datetime, timedelta
from pathlib import Path
import json
import sqlite3
import tempfile

from duties.store import MSK, Store, at, clock, settings


def day_payload(store, did, uid):
    data = store.day_view(did, uid)
    day = data['day']
    now = clock().timestamp()
    mine = next((a for a in data['assignments'] if a['uid'] == uid and a['state'] == 'assigned'), None)
    request = next((r for r in data['requests'] if r['uid'] == uid), None)
    open_for_requests = now < day['cutoff'] and data['space']['status'] == 'active'
    return dict(
        id=did, date=day['date'], status=day['status'], revision=day['revision'],
        source_state=day['source_state'], cutoff=day['cutoff'], report_at=day['report_at'],
        can_request=bool(mine and not request and open_for_requests),
        my_request=request['status'] if request else None,
        requests=[dict(member=r['member'], name=r['name'], mine=r['uid'] == uid, reason=r['reason'])
                  for r in data['requests'] if open_for_requests and r['status'] == 'pending'
                  and data['access']['role'] != 'member'],
        offer_declines=data['offer_declines'],
        people=[dict(member=a['member'], name=a['name'], state=a['state'], mine=a['uid'] == uid)
                for a in data['assignments']],
        offers=[dict(id=o['id'], kind=o['kind'], host=o['host_name'], guest=o['guest_name'],
                     expires=o['expires'], answered=bool(o['host_yes'] if o['host_uid'] == uid else o['guest_yes']))
                for o in data['offers'] if uid in (o['host_uid'], o['guest_uid'])],
        can_manage=data['access']['role'] != 'member',
    )


def forecast(store, sid, uid, knowledge_path=None, *, count=28, now=None):
    """Run the actual queue in a disposable SQLite copy, assuming shifts finish.

    Even temporary outbox jobs stay in that copy; no worker or Telegram client
    is started here. An unresolved real result remains unresolved in production.
    """
    now = now or clock()
    data = store.view(sid, uid)
    opts = settings(json.loads(data['space']['settings']))
    actual = {d['date']: d['id'] for d in data['days']}
    if data['space']['status'] != 'active':
        return []
    from duties.worker import schedule_state
    with tempfile.TemporaryDirectory(prefix='kkepik-duty-forecast-') as folder:
        path = Path(folder) / 'forecast.db'
        with sqlite3.connect(f'file:{store.path}?mode=ro', uri=True) as source:
            with sqlite3.connect(path) as target:
                source.backup(target)
        simulation = Store(path)
        # Past unconfirmed shifts are only assumed complete in this projection.
        with simulation.connect() as db:
            overdue = [dict(r) for r in db.execute(
                "SELECT DISTINCT d.* FROM days d JOIN assignments a ON a.day=d.id "
                "WHERE d.space=? AND d.date<? AND a.state IN ('assigned','reported') ORDER BY d.date",
                (sid, now.date().isoformat()))]
        result = []
        days = max(1, min(count, 31))
        for offset in range(days):
            # Today's preview follows the same rules as the morning worker.
            # Only later dates assume outstanding work will be confirmed.
            if offset == 1:
                for day in overdue:
                    simulation.finish(day['id'], data['space']['owner'], now=datetime.fromtimestamp(day['report_at'], MSK))
            date = now.date() + timedelta(days=offset)
            iso = date.isoformat()
            state, revision = schedule_state(Path(knowledge_path), data['space']['group_name'], date) if knowledge_path else ('unknown', None)
            moment = at(iso, opts['time'])
            did = simulation.ensure_day(sid, moment, state, revision)
            if not did:
                continue
            view = simulation.day_view(did, uid)
            # Future replacements in the preview assume both people agree.
            if offset and view['offers']:
                for offer in view['offers']:
                    try:
                        simulation.reply_offer(offer['id'], offer['host_uid'], True, moment)
                        simulation.reply_offer(offer['id'], offer['guest_uid'], True, moment)
                    except ValueError:
                        pass
                view = simulation.day_view(did, uid)
            people = [dict(id=a['member'], name=a['name'], mine=a['uid'] == uid)
                      for a in view['assignments'] if a['state'] in ('assigned','reported','done')]
            result.append(dict(date=iso, id=actual.get(iso), status=view['day']['status'],
                               tentative=iso not in actual, people=people))
            if offset + 1 == days:
                break
            report_at = datetime.fromtimestamp(view['day']['report_at'], MSK)
            simulation.tick(report_at)
            if any(a['state'] in ('assigned','reported') for a in view['assignments']):
                simulation.finish(did, data['space']['owner'], now=report_at)
        return result
