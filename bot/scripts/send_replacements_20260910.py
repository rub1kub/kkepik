"""Explicit, resumable private student notice for the authorized September 10 groups."""
import argparse
import asyncio
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter
from message_registry import record_message

DATE = '10.09.2026'
PERSONAL_GROUP = '103-Д9-3ИНС'
GROUPS = {PERSONAL_GROUP, '117-КД9-3ИНС', '122-Д9-2КСК', '135-КД9-2ССА',
          '151-КД9-1КСК', '152-КД9-1КСК', '153-КД9-1ССА', '154-КД9-1ССА', '98-Д9-4ИНБ'}
PERSONAL = 'Уважаемый 3й курс веб разработчиков, 2 пара в 22 кабинете.'
GENERAL = '''Замена на 10.09.2026

Вместо Лосевой Л.А.
1 пара - 151 гр - Чуев Ю.М. - 48 ауд.
2 пара - 152 гр - Чуев Ю.М. - 48 ауд.

3 пара - 153 гр - нет
3 пара - 151 гр - нет

4 пара - 152 гр - Шанайда С.В. - 34 ауд

Замена аудитории
2 пара - 103 гр - Дмитриева А.С. - 22 ауд.
3 пара - 117 гр - Дмитриева А.С. - 22 ауд.

1 пара - 153 гр - Чуев Ю.М. - 49 ауд.
2 пара - 154 гр - Чуев Ю.М. - 49 ауд.

1 пара - 98 гр - Шушунов В.В. - 25 ауд.
2 пара - 122 гр - Шушунов В.В. - 25 ауд.
3 пара - 135 гр - Шушунов В.В. - 25 ауд.'''


def recipients():
    result = {}
    with sqlite3.connect(f'file:{ROOT}/data/database.db?mode=ro', uri=True) as db:
        for uid, role, primary, tracked in db.execute('''
            SELECT u.user_id,u.role,u.name_or_group,t.group_name FROM users u
            LEFT JOIN tracked_groups t ON t.user_id=u.user_id WHERE u.role='Я студент'
        '''):
            if uid <= 0:
                continue
            matched = {g for g in (primary, tracked) if g in GROUPS}
            if matched:
                result.setdefault(uid, set()).update(matched)
    return [{'user_id': uid, 'groups': sorted(groups),
             'variant': 'personal' if PERSONAL_GROUP in groups else 'general',
             'text': PERSONAL if PERSONAL_GROUP in groups else GENERAL}
            for uid, groups in sorted(result.items(), key=lambda item: (item[0] != 1084693264, item[0]))]


async def main(apply):
    load_dotenv(ROOT / '.env')
    targets = recipients()
    assert targets and len(targets) == len({t['user_id'] for t in targets})
    assert all(set(t['groups']) <= GROUPS and t['user_id'] > 0 for t in targets)
    print(json.dumps({'recipients': len(targets), 'variants': dict(Counter(t['variant'] for t in targets)),
                      'groups': dict(Counter(g for t in targets for g in t['groups'])), 'apply': apply},
                     ensure_ascii=False), flush=True)
    if not apply:
        return
    audit = ROOT / 'audits/replacements-20260910'
    audit.mkdir(exist_ok=True, mode=0o700)
    with (audit / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = audit / 'results.jsonl'
        previous = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
        # Never retry a send with an uncertain outcome: Telegram has no send idempotency key.
        attempted = {r['user_id'] for r in previous}
        (audit / 'plan.json').write_text(json.dumps(targets, ensure_ascii=False, indent=2))
        counts = Counter()
        registry = os.getenv('MESSAGE_REGISTRY_PATH') or str(ROOT / 'data/message_registry.db')
        with journal.open('a', buffering=1) as log:
            def append(data):
                log.write(json.dumps(data, ensure_ascii=False) + '\n')
                log.flush()
                os.fsync(log.fileno())

            async with Bot(token=os.environ['BOT_TOKEN']) as bot:
                for target in targets:
                    uid = target['user_id']
                    if uid in attempted:
                        counts['already_attempted'] += 1
                        continue
                    current = next((r for r in recipients() if r['user_id'] == uid), None)
                    if not current or current['variant'] != target['variant']:
                        counts['subscription_changed'] += 1
                        continue
                    append({'user_id': uid, 'status': 'attempting', 'variant': target['variant']})
                    try:
                        for attempt in range(3):
                            try:
                                msg = await bot.send_message(chat_id=uid, text=target['text'], parse_mode=None)
                                break
                            except TelegramRetryAfter as exc:
                                if attempt == 2:
                                    raise
                                await asyncio.sleep(exc.retry_after + 1)
                        assert msg.chat.type == 'private' and msg.chat.id == uid
                        append({'user_id': uid, 'status': 'sent', 'message_id': msg.message_id,
                                'variant': target['variant'], 'text': msg.text})
                        record_message(platform='telegram', direction='outgoing', chat_id=uid,
                                       message_id=msg.message_id, message_kind='targeted_replacement_notice',
                                       telegram_method='SendMessage', schedule_date=DATE,
                                       schedule_type='groups', entity=','.join(target['groups']),
                                       content=msg.text, db_path=registry)
                        counts[target['variant'] + '_sent'] += 1
                    except TelegramNetworkError:
                        append({'user_id': uid, 'status': 'unknown_network_outcome'})
                        counts['unknown'] += 1
                    except TelegramAPIError as exc:
                        append({'user_id': uid, 'status': 'failed', 'error': exc.message})
                        counts['failed'] += 1
                    await asyncio.sleep(0.08)
                    if sum(counts.values()) % 50 == 0:
                        print(json.dumps(dict(counts)), flush=True)
        print(json.dumps({'result': dict(counts), 'audit': str(audit)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args().apply))
