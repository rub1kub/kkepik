import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from duties.profiles import ProfileDirectory
from duties.store import Store, DutyError, clock, parse_people


class EnrollmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(root / 'duties.db')
        self.profile_db = root / 'profiles.db'
        self.group = '116-КД9-3ССА'
        with sqlite3.connect(self.profile_db) as db:
            db.execute('CREATE TABLE users(user_id INTEGER,role TEXT,name_or_group TEXT)')
            db.executemany('INSERT INTO users VALUES(?,?,?)', [(i, 'Я студент', self.group) for i in (99, 11, 12)])
        for uid, name, username in [(99, 'Староста', 'creator'), (11, '-', 'first_user'), (12, 'Анна', None)]:
            self.store.observe(uid, name, username, self.group)
        self.directory = ProfileDirectory(self.store, self.profile_db)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, text='@first_user + Анна', reserves='', creator=99, existing=()):
        return self.directory.enrollment(creator, self.group, parse_people(text),
                                         parse_people(reserves, reserves=True), existing=existing)

    def test_exact_handle_and_unique_name_in_complete_group(self):
        plan = self.plan()
        self.assertEqual(plan.ids, [11, 12])
        self.assertEqual(plan.reason, '')
        self.assertEqual(self.store.lists(11), [])

    def test_explicit_ids_do_not_need_cached_names(self):
        with self.store.connect(True) as db:
            db.execute('DELETE FROM identities')
        self.assertEqual(self.plan('11 + 12').ids, [11, 12])

    def test_name_is_not_unique_if_directory_incomplete(self):
        with self.store.connect(True) as db:
            db.execute('DELETE FROM identities WHERE uid=11')
        self.assertIsNone(self.plan('11 + Анна').ids)
        self.assertEqual(self.plan('11 + 12').ids, [11, 12])

    def test_namesakes_need_explicit_ids(self):
        self.store.observe(12, '-', None, self.group)
        self.assertIsNone(self.plan('@first_user + -').ids)
        self.assertEqual(self.plan('11 + 12').ids, [11, 12])

    def test_stale_username_or_name_is_not_auto_connected(self):
        self.store.observe(11, '-', 'first_user', self.group, now=clock() - timedelta(minutes=11))
        self.assertIsNone(self.plan().ids)
        self.assertEqual(self.plan('11 + 12').ids, [11, 12])

    def test_current_registration_wins_over_cached_group(self):
        with sqlite3.connect(self.profile_db) as db:
            db.execute("UPDATE users SET name_or_group='103-Д9-3ИНС' WHERE user_id=12")
        self.assertIsNone(self.plan('11 + 12').ids)
        self.assertNotIn('103-Д9-3ИНС', self.plan('11 + 12').reason)

    def test_teacher_and_creator_of_other_group_cannot_autojoin(self):
        with sqlite3.connect(self.profile_db) as db:
            db.execute("UPDATE users SET role='Я преподаватель' WHERE user_id=12")
        self.assertIsNone(self.plan('11 + 12').ids)
        with sqlite3.connect(self.profile_db) as db:
            db.execute("UPDATE users SET name_or_group='103-Д9-3ИНС' WHERE user_id=99")
        self.assertIsNone(self.plan('11 + 99').ids)

    def test_unknown_reserve_disables_automatic_enrollment_for_everyone(self):
        plan = self.plan('11 + 12', reserves='123456789')
        self.assertIsNone(plan.ids)
        self.assertIn('123456789', plan.reason)

    def test_same_person_under_name_and_username_is_rejected(self):
        with self.assertRaisesRegex(DutyError, 'дважды'):
            self.plan('@first_user + -')

    def test_changed_username_does_not_silently_retarget_saved_id(self):
        pairs = parse_people('@first_user + 12')
        pairs[0][0]['telegram_id'] = 11
        self.store.observe(11, '-', 'new_user', self.group)
        self.assertIsNone(self.directory.enrollment(99, self.group, pairs, []).ids)

    def test_registration_failure_disables_automatic_enrollment(self):
        with patch('duties.profiles.sqlite3.connect', side_effect=sqlite3.OperationalError('unavailable')):
            plan = self.plan('11 + 12')
        self.assertIsNone(plan.ids)
        self.assertIn('не удалось проверить', plan.reason)

    def test_addition_checks_existing_roster_too(self):
        old = [{'name': 'Старый участник', 'uid': 12345}]
        self.assertIsNone(self.plan('11 + 12', existing=old).ids)

    def test_create_binds_and_enqueues_atomically_preserving_owner_role(self):
        plan = self.plan('11 + 99')
        sid = self.store.create(99, self.group, plan.pairs, [], auto_link=True)
        self.assertEqual(self.store.view(sid, 99)['access']['role'], 'owner')
        self.assertEqual(self.store.view(sid, 11)['access']['role'], 'member')
        jobs = self.store.pending_jobs()
        self.assertEqual({j['uid'] for j in jobs}, {11, 99})
        self.assertTrue(all(j['kind'] == 'added' for j in jobs))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM invites').fetchone()[0], 0)

    def test_failure_in_automatic_link_rolls_back_list_and_outbox(self):
        with self.assertRaises(DutyError):
            self.store.create(99, self.group, parse_people('11 + Незнакомый'), [], auto_link=True)
        with self.store.connect() as db:
            for table in ('spaces', 'members', 'access', 'outbox'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table)

    def test_append_does_not_reenrol_departed_members(self):
        sid = self.store.create(99, self.group, parse_people('11 + 12'), [], auto_link=True)
        self.store.manage(sid, 11, 'leave')
        self.store.append_roster(sid, 99, reserves=parse_people('99', reserves=True), auto_link=True)
        self.assertIsNone(next(m for m in self.store.view(sid, 99)['members'] if m['telegram_id'] == 11)['uid'])
        self.assertEqual(self.store.lists(11), [])
        self.assertEqual(self.store.view(sid, 99)['access']['role'], 'owner')

    def test_notice_survives_pause_but_not_archive_or_leaving_or_mute(self):
        for action in ('pause', 'archive', 'leave', 'silence'):
            with self.subTest(action=action):
                sid = self.store.create(99, self.group, parse_people('11 + 12'), [], auto_link=True)
                job = next(j for j in self.store.pending_jobs() if j['space'] == sid and j['uid'] == 11)
                self.store.manage(sid, 99 if action in ('pause', 'archive') else 11, action)
                self.assertEqual(bool(self.store.begin_delivery(job['id'])), action == 'pause')
                self.store.manage(sid, 99, 'archive')

