"""Durable completion receipt for an explicitly armed advertising campaign."""
from datetime import datetime, timezone
import fcntl
from pathlib import Path
from zoneinfo import ZoneInfo

from admin_post_capture import AdminPostCapture, write_private_json


def schedule_broadcast_completed(schedule_date, schedule_type, is_update, notified_count, *, data_dir=None, now=None):
    if schedule_type != 'groups' or is_update or notified_count <= 0:
        return False
    if data_dir is None:
        import config
        data_dir = config.DATA_DIR
    base = Path(data_dir) / 'admin_campaigns'
    if not (base / 'active.json').exists():
        return False
    capture = AdminPostCapture.active(base)
    campaign = capture.campaign()
    trigger = campaign.get('trigger', {}) if campaign else {}
    now = now or datetime.now(timezone.utc)
    if (not campaign or campaign['phase'] not in ('awaiting_post', 'collecting', 'preview_attempting', 'ready')
            or trigger.get('type') != 'schedule_complete' or trigger.get('schedule_date') != schedule_date
            or trigger.get('broadcast_day') != now.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()):
        return False
    receipt = capture.directory / 'schedule-complete.json'
    with (capture.directory / 'trigger.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if receipt.exists():
            return False
        write_private_json(receipt, dict(campaign=campaign['id'], schedule_date=schedule_date,
            schedule_type=schedule_type, is_update=False, notified_count=notified_count,
            completed_at=now.isoformat()))
    return True
