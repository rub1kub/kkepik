import datetime as dt
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from college_calendar import EXCEPTIONS, calendar_date, is_holiday, uses_saturday_hours
from schedules.pair_times import get_pair_time
from schedules.schedule_dates import is_current_schedule
from schedules.knowledge_store import KnowledgeStore
from tests.test_knowledge_store import source_frame
from tests import test_duties as duty_fixture


class CalendarTests(unittest.TestCase):
    def test_only_confirmed_dates_override_weekdays(self):
        self.assertTrue(uses_saturday_hours('03.11.2026'))
        self.assertTrue(uses_saturday_hours(dt.date(2026, 11, 3)))
        self.assertTrue(uses_saturday_hours('2026-11-07'))
        self.assertTrue(is_holiday('04.11.2026'))
        self.assertFalse(is_holiday('2026-11-03'))
        for day in ('2026-11-02', '2026-11-05', '2027-11-03', '2027-11-04'):
            self.assertFalse(is_holiday(day))
            self.assertFalse(uses_saturday_hours(day))
        self.assertFalse(is_holiday('invalid'))
        self.assertIsNone(calendar_date(None))

    def test_all_six_pairs_use_saturday_hours_on_tuesday(self):
        self.assertEqual([get_pair_time(i, '03.11.2026') for i in range(1, 7)], [
            '8:45–10:00', '10:10–11:25', '11:35–12:50',
            '13:00–14:15', '14:25–15:40', '15:50–17:05'])
        self.assertEqual(get_pair_time(2, '05.11.2026'), '10:25–11:45')
        self.assertEqual(get_pair_time(2, '04.11.2026'), '')

    def test_calendar_uses_moscow_for_aware_datetimes(self):
        instant = dt.datetime(2026, 11, 3, 21, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(is_holiday(instant))
        self.assertFalse(is_holiday(instant - dt.timedelta(seconds=1)))

    def test_holiday_is_not_an_automatic_schedule_day(self):
        self.assertFalse(is_current_schedule('04.11.2026', today=dt.date(2026, 11, 3)))
        self.assertTrue(is_current_schedule('03.11.2026', today=dt.date(2026, 11, 2)))
        self.assertTrue(is_current_schedule('05.11.2026', today=dt.date(2026, 11, 4)))

    def test_notification_uses_the_same_bell_times(self):
        from schedules.message_constructor import render_schedule_message
        result = render_schedule_message(['▪️2 пара – Математика – Иванов И.И. – 27'],
                                         '103-Д9-3ИНС', '03.11.2026')
        self.assertIn('10:10–11:25', result)
        self.assertNotIn('10:25', result)

    def test_generated_web_rules_match_python(self):
        from scripts.build_college_calendar import build
        with tempfile.TemporaryDirectory() as folder:
            build(folder)
            root = Path(folder)
            self.assertEqual((root / 'college_calendar.py').read_bytes(),
                             (Path(__file__).resolve().parents[1] / 'college_calendar.py').read_bytes())
            js = (root / 'static/js/college_calendar.js').read_text()
            assertions = '''
const assert = require('node:assert/strict');
assert.deepEqual(window.kkepikCalendar.rules, RULES);
assert.equal(window.kkepikCalendar.isSaturday(new Date('2026-11-03T12:00:00')), true);
assert.equal(window.kkepikCalendar.isHoliday(new Date('2026-11-04T12:00:00')), true);
assert.equal(window.kkepikCalendar.isHoliday('2026-11-04'), true);
assert.equal(window.kkepikCalendar.isSaturday(new Date('2026-11-05T12:00:00')), false);
'''.replace('RULES', json.dumps(EXCEPTIONS))
            subprocess.run(['node', '-e', 'global.window={};\n' + js + assertions], check=True, capture_output=True)


class CalendarKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KnowledgeStore(self.temp.name)
        self.source = Path(self.temp.name) / 'test.pdf'
        self.source.write_bytes(b'%PDF-1.0 test')
        self.store.ingest(self.source, '2026-11-03', source_frame())

    def test_holiday_without_pdf_is_confirmed_but_unknown_dates_stay_missing(self):
        result = self.store.schedule('group', '103-Д9-3ИНС', start='2026-11-03', end='2026-11-05')
        self.assertEqual([day['status'] for day in result['days']], ['available', 'no_lessons', 'missing'])
        day = result['days'][1]
        self.assertEqual(day['lessons'], [])
        self.assertIsNone(day['source'])
        self.assertEqual(day['calendar']['kind'], 'holiday')
        self.assertIn('выходной', day['note'])
        self.assertEqual(self.store.coverage(start='2026-11-04', end='2026-11-04')['days'][0]['status'], 'no_lessons')

    def test_unknown_entity_stays_unknown_on_holiday(self):
        self.assertEqual(self.store.schedule('group', '999-Д9-1ИНС', date='2026-11-04')['days'][0]['status'], 'entity_not_found')

    def test_holiday_applies_to_teachers_and_rooms(self):
        for kind, entity in [('teacher', 'Борисова Е.В.'), ('audience', '108')]:
            self.assertEqual(self.store.schedule(kind, entity, date='2026-11-04')['days'][0]['status'], 'no_lessons')

    def test_existing_immutable_revisions_get_correct_times_on_read(self):
        # Reproduce a revision parsed before the calendar correction.
        with self.store._connect() as db:
            for row in db.execute('SELECT id,payload_json FROM lessons').fetchall():
                value = json.loads(row[1]); value['start_time'], value['end_time'] = '12:05', '13:25'
                db.execute('UPDATE lessons SET payload_json=? WHERE id=?', (json.dumps(value), row[0]))
            db.commit()
        result = self.store.schedule('group', '103-Д9-3ИНС', date='2026-11-03')
        third = next(lesson for lesson in result['days'][0]['lessons'] if lesson['pair_number'] == 3)
        self.assertEqual((third['start_time'], third['end_time']), ('11:35', '12:50'))
        with self.store._connect() as db:
            saved = json.loads(db.execute('SELECT payload_json FROM lessons LIMIT 1').fetchone()[0])
            self.assertEqual(saved['start_time'], '12:05')


class CalendarDutyTests(unittest.TestCase):
    setUp = duty_fixture.DutyStoreTest.setUp
    tearDown = duty_fixture.DutyStoreTest.tearDown
    query = duty_fixture.DutyStoreTest.query

    def test_forecast_skips_holiday_without_mutating_actual_queue(self):
        from duties.views import forecast
        before = self.query('SELECT * FROM pairs')
        result = forecast(self.store, self.sid, 99, count=3,
                          now=self.now.replace(month=11, day=3))
        self.assertEqual([day['date'] for day in result], ['2026-11-03', '2026-11-04', '2026-11-05'])
        self.assertEqual(result[1]['status'], 'no_duty')
        self.assertEqual(result[1]['people'], [])
        self.assertEqual({member['id'] for member in result[0]['people']}, set(self.ids[:2]))
        self.assertEqual({member['id'] for member in result[2]['people']}, set(self.ids[2:4]))
        self.assertEqual(before, self.query('SELECT * FROM pairs'))
        self.assertEqual(self.query('SELECT * FROM days'), [])
        self.assertEqual(self.query('SELECT * FROM outbox'), [])
    def test_holiday_is_quiet_and_preserves_queue_without_schedule_source(self):
        from duties.worker import schedule_state
        holiday = self.now.replace(month=11, day=4, hour=9)
        self.assertEqual(schedule_state(Path(self.tmp.name) / 'absent.db', '116-КД9-3ССА', holiday.date()), ('no_lessons', None))
        before = self.query('SELECT * FROM pairs')
        did = self.store.ensure_day(self.sid, holiday, source_state='unknown')
        self.store.tick(holiday.replace(hour=17))
        self.assertEqual(self.query('SELECT status FROM days WHERE id=?', (did,))[0]['status'], 'no_duty')
        self.assertEqual(self.query('SELECT * FROM assignments WHERE day=?', (did,)), [])
        self.assertEqual(self.query('SELECT * FROM outbox'), [])
        self.assertEqual(before, self.query('SELECT * FROM pairs'))
        following = self.store.ensure_day(self.sid, (holiday + dt.timedelta(days=1)).replace(hour=8, minute=0), source_state='available')
        self.assertEqual({r['member'] for r in self.query('SELECT member FROM assignments WHERE day=?', (following,))}, set(self.ids[:2]))


class CalendarLegacyApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_holiday_does_not_load_or_guess_missing_pdf(self):
        import api
        for kind, entity, model in [('group', '103-Д9-3ИНС', api.GroupScheduleRequest),
                                    ('teacher', 'Борисова Е.В.', api.TeacherScheduleRequest),
                                    ('audience', '108', api.AudienceScheduleRequest)]:
            with self.subTest(kind=kind), patch.object(api, 'get_catalog_values', return_value=[entity]), patch.object(api, 'load_schedule_df') as load:
                result = await getattr(api, f'get_{kind}_schedule')(model(date='04.11.2026', **{kind: entity}))
                self.assertEqual(result['status'], 'no_lessons')
                self.assertEqual(result['schedule'], [])
                load.assert_not_called()

    async def test_unknown_group_remains_an_error(self):
        import api
        with patch.object(api, 'get_catalog_values', return_value=['103-Д9-3ИНС']):
            with self.assertRaises(api.HTTPException) as caught:
                await api.get_group_schedule(api.GroupScheduleRequest(group='999', date='04.11.2026'))
        self.assertEqual(caught.exception.status_code, 404)
