import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from duties.store import Store, DutyError, AmbiguousPerson, MSK, parse_people, validate_roster, settings, person


class DutyStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'duties.db'
        self.store = Store(self.path)
        self.now = datetime(2026, 10, 19, 7, 30, tzinfo=MSK)
        self.pairs = parse_people('Иван + Анна\nПётр + Мария')
        self.reserves = parse_people('Максим\nОльга', reserves=True)
        self.sid = self.store.create(99, '116-КД9-3ССА', self.pairs, self.reserves, now=self.now)
        self.people = self.store.view(self.sid, 99)['members']
        self.ids = [m['id'] for m in self.people]
        with self.store.connect(True) as db:
            for uid, member in enumerate(self.people, 11):
                db.execute('UPDATE members SET uid=? WHERE id=?', (uid, member['id']))
                db.execute("INSERT INTO access(space,uid,role) VALUES(?,?,'member')", (self.sid, uid))

    def tearDown(self):
        self.tmp.cleanup()

    def time(self, hour=8, minute=0, days=0):
        return self.now.replace(hour=hour, minute=minute) + timedelta(days=days)

    def day(self, **kwargs):
        return self.store.ensure_day(self.sid, self.time(), source_state='available', **kwargs)

    def query(self, sql, args=()):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def offer(self, did):
        rows = self.query("SELECT * FROM offers WHERE day=? AND state='pending'", (did,))
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_three_step_roster_parser_preserves_explicit_pairs_and_nicks(self):
        pairs = parse_people('1. Иван @first_user + Анна @second_user\n2. Пётр | Мария')
        self.assertEqual(pairs[0][0], {'name':'Иван', 'username':'first_user'})
        self.assertEqual(pairs[0][1]['name'], 'Анна')
        self.assertEqual(parse_people('Иван\nАнна')[0][1]['name'], 'Анна')
        with self.assertRaises(DutyError):
            parse_people('Иван\nАнна\nПётр')
        with self.assertRaises(DutyError):
            validate_roster(parse_people('Иван + Анна'), parse_people('Иван', reserves=True))

    def test_schedule_boundary_is_moscow_and_day_creation_is_idempotent(self):
        self.assertIsNone(self.store.ensure_day(self.sid, self.time(7, 59)))
        did = self.day()
        self.assertEqual(self.store.ensure_day(self.sid, self.time(8, 1)), did)
        self.assertEqual(len(self.query('SELECT * FROM assignments WHERE day=?', (did,))), 2)
        jobs = self.query("SELECT uid FROM outbox WHERE kind='daily'")
        self.assertEqual({r['uid'] for r in jobs}, {99,11,12})
        self.assertEqual(len(jobs), 3)

    def test_punctuation_names_and_user_ids_parse_without_placeholder_people(self):
        for name in ('-', '—', '–', '()', '.', '-,()'):
            self.assertEqual(person(name), {'name':name,'username':None})
        pairs = parse_people('@first_user + -\nID: 123456789 + 234567890')
        self.assertEqual(pairs[0][1]['name'],'-')
        self.assertEqual(pairs[1][0]['telegram_id'],123456789)
        self.assertEqual(pairs[1][1]['telegram_id'],234567890)
        self.assertEqual(parse_people('123456789\n-',reserves=True)[0]['telegram_id'],123456789)
        validate_roster(pairs,[])

    def test_invalid_ids_and_blank_entries_report_physical_line(self):
        for value in ('0','-1001234567890','4503599627370496','ID: wrong','9'*200):
            with self.subTest(value=value[:30]), self.assertRaisesRegex(DutyError,'Строка 3:'):
                parse_people('@first_user + Анна\n\n@second_user + '+value)
        with self.assertRaisesRegex(DutyError,'Строка 2:'):
            parse_people('@first_user + Анна\n@second_user + ')

    def test_target_id_stays_separate_from_verified_membership(self):
        self.store.observe(123456789,'-',None,'116-КД9-3ССА')
        pairs,_ = self.store.fill_profile_names('116-КД9-3ССА',parse_people('123456789 + @first_user'),[])
        sid = self.store.create(100,'116-КД9-3ССА',pairs,[],now=self.now)
        member = next(m for m in self.store.view(sid,100)['members'] if m['telegram_id'])
        self.assertEqual(member['name'],'- (ID 123456789)')
        self.assertIsNone(member['uid'])
        self.assertEqual(len(self.query('SELECT * FROM access WHERE space=?',(sid,))),1)
        token = self.store.invite(sid,100,member['id'],now=self.now)
        with self.assertRaises(DutyError):
            self.store.claim(token,777,'',now=self.now)
        self.assertEqual(self.store.invitation(token,now=self.now)['used'],0)
        cid = self.store.claim(token,123456789,'',now=self.now)
        self.store.resolve_claim(cid,100,member['id'])
        self.assertEqual(self.store.view(sid,123456789)['access']['role'],'member')

    def test_wrong_account_cannot_claim_id_by_same_profile_name(self):
        self.store.observe(123456789,'-',None,'116-КД9-3ССА')
        pairs,_ = self.store.fill_profile_names('116-КД9-3ССА',parse_people('123456789 + @first_user'),[])
        pairs[0][0]['name'] = '-'
        sid = self.store.create(100,'116-КД9-3ССА',pairs,[],now=self.now)
        member = next(m for m in self.store.view(sid,100)['members'] if m['telegram_id'])
        token = self.store.invite(sid,100,now=self.now)
        cid = self.store.claim(token,777,'-',now=self.now)
        with self.assertRaisesRegex(DutyError,'ID'):
            self.store.resolve_claim(cid,100,member['id'])
        self.assertIsNone(next(m for m in self.store.view(sid,100)['members'] if m['id']==member['id'])['uid'])

    def test_handle_and_id_of_same_person_are_rejected_as_duplicate(self):
        self.store.observe(123456789,'Анна','first_user','116-КД9-3ССА')
        pairs,_ = self.store.fill_profile_names('116-КД9-3ССА',parse_people('@first_user + 123456789'),[])
        with self.assertRaises(DutyError):
            validate_roster(pairs,[])
        sid = self.store.create(100,'116-КД9-3ССА',parse_people('123456789 + Анна'),[],now=self.now)
        with self.assertRaises(DutyError):
            self.store.append_roster(sid,100,reserves=parse_people('ID:123456789',reserves=True))
        # Existing legacy verified accounts also prevent an ID alias duplicate.
        with self.assertRaises(DutyError):
            self.store.append_roster(self.sid,99,reserves=parse_people('11',reserves=True))

    def test_ambiguous_names_require_id_and_ids_distinguish_namesakes(self):
        self.store.observe(201,'-',None,'116-КД9-3ССА')
        self.store.observe(202,'-',None,'116-КД9-3ССА')
        with self.assertRaisesRegex(AmbiguousPerson,'несколько профилей') as caught:
            self.store.fill_profile_names('116-КД9-3ССА',parse_people('@first_user + -'),[])
        self.assertEqual(caught.exception.path, ['pairs', 0, 1])
        self.assertEqual(caught.exception.pairs[0][1]['name'], '-')
        pairs,_ = self.store.fill_profile_names('116-КД9-3ССА',parse_people('201 + 202'),[])
        validate_roster(pairs,[])
        self.assertEqual(pairs[0][0]['name'],'- (ID 201)')
        self.assertEqual(pairs[0][1]['name'],'- (ID 202)')

    def test_ambiguity_carries_all_resolved_entries_without_mutating_input(self):
        self.store.observe(201, '-', None, '116-КД9-3ССА')
        self.store.observe(202, '-', None, '116-КД9-3ССА')
        self.store.observe(203, 'Иван', 'first_user', '116-КД9-3ССА')
        pairs = parse_people('- + @first_user')
        with self.assertRaises(AmbiguousPerson) as caught:
            self.store.fill_profile_names('116-КД9-3ССА', pairs, [])
        self.assertEqual(caught.exception.pairs[0][1]['telegram_id'], 203)
        self.assertNotIn('telegram_id', pairs[0][1])
        with self.assertRaises(AmbiguousPerson) as caught:
            self.store.fill_profile_names('116-КД9-3ССА', [], parse_people('@first_user\n-', reserves=True))
        self.assertEqual(caught.exception.path, ['reserves', 1])
        self.assertEqual(caught.exception.reserves[0]['telegram_id'], 203)

    def test_v1_database_migrates_without_losing_members_or_setup(self):
        self.store.set_session(99,'setup_pairs',{'group':'116-КД9-3ССА'})
        with self.store.connect(True) as db:
            db.execute('DROP INDEX member_telegram_target')
            db.execute('ALTER TABLE members DROP COLUMN telegram_id')
            db.execute('PRAGMA user_version=1')
        upgraded = Store(self.path)
        self.assertEqual(len(upgraded.view(self.sid,99)['members']),6)
        self.assertEqual(upgraded.session(99)[0],'setup_pairs')
        self.assertEqual(self.query('PRAGMA user_version')[0]['user_version'],3)
        Store(self.path)
        self.assertEqual(len(upgraded.view(self.sid,99)['members']),6)

    def test_profile_names_are_scoped_keep_aliases_and_never_bind(self):
        self.store.observe(201, 'Иван', 'first_user', '116-КД9-3ССА')
        self.store.observe(202, 'Иван', 'second_user', '116-КД9-3ССА')
        self.store.observe(203, 'Чужое имя', 'third_user', '103-Д9-3ИНС')
        self.store.observe(204, 'Старое имя', 'stale_user', '116-КД9-3ССА', now=self.now-timedelta(days=30))
        pairs, reserves = self.store.fill_profile_names('116-КД9-3ССА',
            parse_people('@first_user + @second_user\n@third_user + @stale_user'),
            parse_people('Подписанное имя @first_user', reserves=True))
        self.assertEqual(pairs[0][0]['name'], 'Иван (@first_user)')
        self.assertEqual(pairs[0][1]['name'], 'Иван (@second_user)')
        self.assertEqual(pairs[1][0]['name'], '@third_user')
        self.assertEqual(pairs[1][1]['name'], '@stale_user')
        self.assertEqual(reserves[0]['name'], 'Подписанное имя')
        validate_roster(pairs, [])
        sid = self.store.create(100, '116-КД9-3ССА', pairs, [])
        self.assertTrue(all(m['uid'] is None for m in self.store.view(sid,100)['members']))

    def test_username_reassignment_does_not_leak_other_groups_profile(self):
        self.store.observe(201, 'Прежний владелец', 'first_user', '116-КД9-3ССА')
        self.store.observe(202, 'Новый владелец', 'first_user', '103-Д9-3ИНС')
        pairs, _ = self.store.fill_profile_names('116-КД9-3ССА', parse_people('@first_user + Анна'), [])
        self.assertEqual(pairs[0][0]['name'], '@first_user')

    def test_creator_in_roster_gets_one_morning_message(self):
        with self.store.connect(True) as db:
            db.execute('UPDATE members SET uid=99 WHERE id=?', (self.ids[0],))
        self.day()
        self.assertEqual(len(self.query("SELECT * FROM outbox WHERE kind='daily' AND uid=99")), 1)

    def test_silence_does_not_cancel_original_pair_or_mark_done(self):
        did = self.day()
        self.store.tick(self.time(8, 31))
        self.assertEqual([a['state'] for a in self.query('SELECT * FROM assignments WHERE day=?', (did,))], ['assigned','assigned'])
        self.assertEqual(self.query('SELECT status FROM days WHERE id=?', (did,))[0]['status'], 'assigned')
        self.assertEqual(sum(m['completed'] for m in self.store.view(self.sid, 99)['members']), 0)

    def test_reserve_needs_both_consents_and_never_changes_permanent_pairs(self):
        did = self.day()
        self.store.decline(did, 11, self.time(8, 1))
        offer = self.offer(did)
        self.assertEqual((offer['host'], offer['guest']), (self.ids[1], self.ids[4]))
        self.store.reply_offer(offer['id'], 12, True, self.time(8, 2))
        self.assertEqual(self.offer(did)['guest_yes'], 0)
        self.assertFalse(self.query("SELECT * FROM assignments WHERE day=? AND member=? AND state='assigned'", (did, self.ids[4])))
        self.store.reply_offer(offer['id'], 15, True, self.time(8, 3))
        active = self.query("SELECT member FROM assignments WHERE day=? AND state='assigned'", (did,))
        self.assertEqual({a['member'] for a in active}, {self.ids[1], self.ids[4]})
        new_people = self.store.view(self.sid, 99)['members']
        self.assertEqual({m['id']:m['pair'] for m in new_people}, {m['id']:m['pair'] for m in self.people})

    def test_completed_partner_is_spared_next_day_and_reserve_gets_credit(self):
        did = self.day()
        self.store.decline(did, 11, self.time(8, 1))
        offer = self.offer(did)
        self.store.reply_offer(offer['id'], 12, True, self.time(8, 2))
        self.store.reply_offer(offer['id'], 15, True, self.time(8, 3))
        self.store.finish(did, 99, now=self.time(16))
        people = {m['id']:m for m in self.store.view(self.sid,99)['members']}
        self.assertEqual(people[self.ids[1]]['credit'], 1)
        self.assertEqual(people[self.ids[4]]['completed'], 1)
        tomorrow = self.store.ensure_day(self.sid, self.time(days=1), source_state='available')
        offer2 = self.offer(tomorrow)
        self.assertEqual(offer2['host'], self.ids[0])
        self.assertEqual(offer2['guest'], self.ids[5])
        self.assertFalse(self.query('SELECT * FROM assignments WHERE day=? AND member=?', (tomorrow,self.ids[1])))
        self.store.reply_offer(offer2['id'], 11, True, self.time(8,1,1))
        self.store.reply_offer(offer2['id'], 16, True, self.time(8,2,1))
        self.store.finish(tomorrow, 99, now=self.time(16,0,1))
        later = self.store.ensure_day(self.sid, self.time(days=2), source_state='available')
        assigned = self.query("SELECT member FROM assignments WHERE day=? AND state='assigned'", (later,))
        self.assertEqual({r['member'] for r in assigned}, set(self.ids[2:4]))

    def test_partner_rejects_reserve_pair_is_postponed_and_next_pair_asked(self):
        did = self.day()
        self.store.decline(did, 11, self.time(8,1))
        self.store.reply_offer(self.offer(did)['id'], 12, False, self.time(8,2))
        next_offer = self.offer(did)
        self.assertEqual(next_offer['kind'], 'pair')
        self.assertEqual({next_offer['host'],next_offer['guest']}, set(self.ids[2:4]))
        self.assertEqual(self.query('SELECT pending_since FROM members WHERE id=?', (self.ids[1],))[0]['pending_since'], '2026-10-19')

    def test_reserve_rejection_requires_new_named_consent(self):
        did = self.day()
        self.store.decline(did, 11, self.time(8,1))
        first = self.offer(did)
        self.store.reply_offer(first['id'], 12, True, self.time(8,2))
        self.store.reply_offer(first['id'], 15, False, self.time(8,3))
        second = self.offer(did)
        self.assertEqual(second['guest'], self.ids[5])
        self.assertEqual(second['host_yes'], 0)
        with self.assertRaises(DutyError):
            self.store.reply_offer(first['id'], 15, True, self.time(8,4))

    def test_no_reply_reminded_once_then_timeout_and_no_forced_assignment(self):
        did = self.day()
        self.store.decline(did, 11, self.time(8,1))
        self.store.tick(self.time(8,6))
        self.store.tick(self.time(8,7))
        self.assertEqual(len(self.query("SELECT * FROM outbox WHERE kind='offer_reminder'")), 2)
        self.store.tick(self.time(8,12))
        self.assertEqual(self.offer(did)['kind'], 'pair')
        self.store.tick(self.time(8,31))
        self.assertEqual(self.query('SELECT status FROM days WHERE id=?', (did,))[0]['status'], 'unfilled')
        self.assertFalse(self.query("SELECT * FROM offers WHERE day=? AND state='pending'", (did,)))
        self.assertFalse(self.query("SELECT * FROM assignments WHERE day=? AND state='assigned'", (did,)))

    def test_guest_silence_after_host_consent_tries_next_reserve(self):
        did = self.day()
        self.store.decline(did,11,self.time(8,1))
        self.store.reply_offer(self.offer(did)['id'],12,True,self.time(8,2))
        self.store.tick(self.time(8,12))
        self.assertEqual(self.offer(did)['guest'],self.ids[5])

    def test_double_click_and_two_declines_cannot_create_two_open_offers(self):
        did = self.day()
        self.store.decline(did,11,self.time(8,1))
        with self.assertRaises(DutyError):
            self.store.decline(did,11,self.time(8,1))
        self.store.decline(did,12,self.time(8,2))
        self.assertEqual(self.offer(did)['kind'],'pair')
        self.assertEqual(len(self.query("SELECT * FROM assignments WHERE day=? AND state='declined'", (did,))),2)

    def test_pair_mode_skips_without_reserve_offer(self):
        self.store.update_settings(self.sid,99,{'mode':'pair'})
        did = self.day()
        self.store.decline(did,11,self.time(8,1))
        self.assertEqual(self.offer(did)['kind'],'pair')

    def test_stale_buttons_unauthorized_user_and_outsider_completion_denied(self):
        did = self.day()
        for operation in (lambda:self.store.decline(did,11,self.time(days=1)),
                          lambda:self.store.decline(did,13,self.time()),
                          lambda:self.store.finish(did,11,now=self.time(16)),
                          lambda:self.store.view(self.sid,12345)):
            with self.assertRaises(DutyError):
                operation()

    def test_missing_source_not_holiday_and_confirmed_no_lessons_skips(self):
        did = self.store.ensure_day(self.sid,self.time(),source_state='unknown')
        self.assertEqual(self.query('SELECT status FROM days WHERE id=?',(did,))[0]['status'],'assigned')
        tomorrow = self.store.ensure_day(self.sid,self.time(days=1),source_state='no_lessons')
        self.assertEqual(self.query('SELECT status FROM days WHERE id=?',(tomorrow,))[0]['status'],'no_duty')
        self.assertFalse(self.query('SELECT * FROM assignments WHERE day=?',(tomorrow,)))

    def test_no_historical_catchup_or_silent_late_assignment(self):
        did = self.store.ensure_day(self.sid,self.time(12),source_state='available')
        self.assertEqual(self.query('SELECT status FROM days WHERE id=?',(did,))[0]['status'],'unfilled')
        self.assertFalse(self.query('SELECT * FROM assignments'))
        self.assertEqual(len(self.query('SELECT * FROM days')),1)

    def test_notifications_can_all_be_disabled_without_disabling_queue(self):
        self.store.update_settings(self.sid,99,dict(owner_daily=False,audience='off',requests=False,owner_events=False))
        did = self.day()
        self.store.decline(did,11,self.time(8,1))
        self.store.tick(self.time(16))
        self.assertFalse(self.query('SELECT * FROM outbox'))
        self.assertTrue(self.query('SELECT * FROM days WHERE id=?',(did,)))

    def test_delivery_rechecks_preferences_and_no_replay_after_restart(self):
        self.day()
        job = self.query("SELECT * FROM outbox WHERE kind='daily' AND uid=11")[0]
        self.store.manage(self.sid,11,'mute')
        self.assertIsNone(self.store.begin_delivery(job['id'],self.time()))
        owner_job = self.query("SELECT * FROM outbox WHERE kind='daily' AND uid=99")[0]
        self.assertIsNotNone(self.store.begin_delivery(owner_job['id'],self.time()))
        other = Store(self.path)
        other.recover_outbox()
        self.assertEqual(self.query('SELECT status FROM outbox WHERE id=?',(owner_job['id'],))[0]['status'],'unknown')
        self.assertIsNone(other.begin_delivery(owner_job['id'],self.time(8,1)))

    def test_completion_is_explicit_idempotent_and_correctable(self):
        did = self.day()
        self.store.report_done(did,11,self.time(16))
        self.assertEqual(sum(m['completed'] for m in self.store.view(self.sid,99)['members']),0)
        self.store.finish(did,99,now=self.time(16))
        with self.assertRaises(DutyError):
            self.store.finish(did,99,now=self.time(16))
        self.assertEqual(sum(m['completed'] for m in self.store.view(self.sid,99)['members']),2)
        self.store.correct_result(did,self.ids[0],99,'missed',self.time(16))
        people = {m['id']:m for m in self.store.view(self.sid,99)['members']}
        self.assertEqual(people[self.ids[0]]['credit'],0)
        self.assertEqual(people[self.ids[1]]['credit'],1)
        self.assertEqual(sum(m['completed'] for m in people.values()),1)

    def test_unconfirmed_previous_result_does_not_reassign_same_people(self):
        self.day()
        tomorrow = self.store.ensure_day(self.sid,self.time(days=1),source_state='available')
        active = self.query("SELECT member FROM assignments WHERE day=? AND state='assigned'",(tomorrow,))
        self.assertEqual({a['member'] for a in active},set(self.ids[2:4]))

    def test_late_refusal_does_not_cancel_partner_who_may_be_working(self):
        did = self.day()
        self.store.decline(did,11,self.time(10))
        remaining = self.query("SELECT * FROM assignments WHERE day=? AND member=?",(did,self.ids[1]))[0]
        self.assertEqual(remaining['state'],'assigned')
        self.store.finish(did,99,self.ids[1],now=self.time(16))
        self.assertEqual(self.query('SELECT credit FROM members WHERE id=?',(self.ids[1],))[0]['credit'],1)

    def test_personal_invitation_needs_both_confirmations_and_cannot_be_reused(self):
        sid = self.store.create(100,'116-КД9-3ССА',parse_people('Тёзка + Другой'),[],now=self.now)
        mid = self.store.view(sid,100)['members'][0]['id']
        token = self.store.invite(sid,100,mid,self.now)
        cid = self.store.claim(token,200,'',self.now)
        with self.assertRaises(DutyError):
            self.store.view(sid,200)
        with self.assertRaises(DutyError):
            self.store.claim(token,201,'',self.now)
        with self.assertRaises(DutyError):
            self.store.resolve_claim(cid,99,mid)
        self.store.resolve_claim(cid,100,mid)
        self.assertEqual(self.store.view(sid,200)['access']['role'],'member')

    def test_general_invitation_cannot_bind_a_different_person(self):
        sid = self.store.create(100,'116-КД9-3ССА',parse_people('Иван + Анна'),[],now=self.now)
        token = self.store.invite(sid,100,now=self.now)
        cid = self.store.claim(token,200,'Иван',self.now)
        members = self.store.view(sid,100)['members']
        with self.assertRaises(DutyError):
            self.store.resolve_claim(cid,100,members[1]['id'])
        self.store.resolve_claim(cid,100,members[0]['id'])

    def test_invite_expiry_revocation_and_username_reuse_do_not_rebind(self):
        token = self.store.invite(self.sid,99,now=self.now)
        self.store.invite(self.sid,99,now=self.now)
        with self.assertRaises(DutyError):
            self.store.invitation(token,self.now)
        token = self.store.invite(self.sid,99,now=self.now)
        with self.assertRaises(DutyError):
            self.store.invitation(token,self.now+timedelta(days=8))
        self.store.observe(11,'Иван','same_name',now=self.now)
        self.store.observe(200,'Другой','same_name',now=self.now)
        self.assertEqual(self.query('SELECT uid FROM members WHERE id=?',(self.ids[0],))[0]['uid'],11)

    def test_owner_can_only_create_one_unarchived_list_including_pause(self):
        for paused in (False, True):
            with self.subTest(paused=paused):
                if paused:
                    self.store.manage(self.sid, 99, 'pause')
                with self.assertRaisesRegex(DutyError, 'уже настроены'):
                    self.store.create(99, '103-Д9-3ИНС', self.pairs, [], now=self.now)
                self.assertEqual(len(self.store.lists(99)), 1)
                self.assertEqual(len(self.query('SELECT * FROM members')), len(self.people))

    def test_archive_allows_new_list_but_stale_resume_cannot_restore_second(self):
        self.store.manage(self.sid, 99, 'archive')
        sid2 = self.store.create(99, '103-Д9-3ИНС', self.pairs, [], now=self.now)
        self.assertEqual([s['id'] for s in self.store.lists(99)], [sid2])
        for action in ('resume', 'pause'):
            with self.subTest(action=action), self.assertRaisesRegex(DutyError, 'другой список'):
                self.store.manage(self.sid, 99, action)
        self.assertEqual(self.store.view(self.sid, 99)['space']['status'], 'archived')
        self.assertEqual(len(self.store.view(self.sid, 99)['members']), len(self.people))

    def test_concurrent_creation_cannot_bypass_single_owner_limit(self):
        def create(_):
            try:
                return self.store.create(200, '103-Д9-3ИНС', self.pairs, [], now=self.now)
            except DutyError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(sum(sid is not None for sid in results), 1)
        self.assertEqual(len(self.store.lists(200)), 1)

    def test_transfer_cannot_give_owner_a_second_list(self):
        sid2 = self.store.create(11, '103-Д9-3ИНС', self.pairs, [], now=self.now)
        with self.assertRaisesRegex(DutyError, 'уже ведёт'):
            self.store.manage(self.sid, 99, 'transfer', 11)
        self.assertEqual(self.store.view(self.sid, 99)['access']['role'], 'owner')
        self.assertEqual(self.store.view(self.sid, 11)['access']['role'], 'member')
        self.store.manage(sid2, 11, 'archive')
        self.store.manage(self.sid, 99, 'transfer', 11)
        self.assertEqual(self.store.view(self.sid, 11)['access']['role'], 'owner')

    def test_same_group_can_have_independent_lists_and_cross_access_is_denied(self):
        sid2 = self.store.create(200,'116-КД9-3ССА',self.pairs,[],now=self.now)
        with self.assertRaises(DutyError):
            self.store.update_settings(sid2,99,{'owner_daily':False})
        self.assertEqual(len(self.store.lists(200)),1)

    def test_transfer_and_deputy_are_self_service(self):
        self.store.manage(self.sid,99,'manager',11)
        self.store.update_settings(self.sid,11,{'owner_daily':False})
        with self.assertRaises(DutyError):
            self.store.manage(self.sid,11,'transfer',12)
        self.store.manage(self.sid,99,'transfer',11)
        self.assertEqual(self.store.view(self.sid,11)['access']['role'],'owner')

    def test_participant_can_leave_and_queued_messages_are_cancelled(self):
        self.day()
        job = self.query("SELECT * FROM outbox WHERE kind='daily' AND uid=11")[0]
        self.store.manage(self.sid,11,'leave')
        self.assertIsNone(self.store.begin_delivery(job['id'],self.time()))
        self.assertFalse(self.store.lists(11))

    def test_draft_and_pending_offer_survive_restart(self):
        self.store.set_session(99,'setup_reserves',{'pairs':self.pairs})
        did = self.day()
        self.store.decline(did,11,self.time(8,1))
        other = Store(self.path)
        self.assertEqual(other.session(99)[0],'setup_reserves')
        other.reply_offer(self.offer(did)['id'],12,True,self.time(8,2))
        self.assertEqual(self.offer(did)['host_yes'],1)

    def test_settings_reject_inverted_deadlines(self):
        with self.assertRaises(DutyError):
            settings({'time':'09:00'})
        with self.assertRaises(DutyError):
            settings({'days':[]})

    def test_reconsider_does_not_revert_an_already_confirmed_replacement(self):
        did = self.day()
        self.store.decline(did,11,self.time(8,1))
        offer = self.offer(did)
        self.store.reply_offer(offer['id'],12,True,self.time(8,2))
        self.store.reply_offer(offer['id'],15,True,self.time(8,3))
        self.store.reconsider(did,11,self.time(8,4))
        active = self.query("SELECT member FROM assignments WHERE day=? AND state='assigned'",(did,))
        self.assertEqual({a['member'] for a in active},{self.ids[1],self.ids[4]})
        self.assertNotIn(self.ids[0],json_load(self.query('SELECT excluded FROM days WHERE id=?',(did,))[0]['excluded']))

    def test_manual_offline_replacement_preserves_pairs_and_records_actor(self):
        did = self.day()
        with self.store.connect(True) as db:
            db.execute('UPDATE members SET uid=NULL WHERE id=?',(self.ids[4],))
        self.store.manual_reserve(did,99,self.ids[0],self.ids[4],self.time(9))
        self.store.finish(did,99,now=self.time(16))
        people = {m['id']:m for m in self.store.view(self.sid,99)['members']}
        self.assertEqual(people[self.ids[0]]['credit'],1)
        self.assertEqual(people[self.ids[4]]['completed'],1)
        self.assertEqual(people[self.ids[0]]['pair'],self.people[0]['pair'])
        event = self.query("SELECT actor FROM events WHERE action='manual_reserve_confirmed'")[0]
        self.assertEqual(event['actor'],99)

    def test_pending_reserve_identity_cannot_be_swapped_under_consent(self):
        did = self.day()
        self.store.decline(did,11,self.time(8,1))
        offer = self.offer(did)
        self.store.reply_offer(offer['id'],15,True,self.time(8,2))
        with self.assertRaises(DutyError):
            self.store.edit_member(self.ids[4],99,'uid',None)

    def test_cancel_day_releases_waiting_and_keeps_completed_work(self):
        did = self.day()
        self.store.finish(did,99,self.ids[0],now=self.time(16))
        self.store.cancel_day(did,99,self.time(16))
        rows = self.query('SELECT member,state FROM assignments WHERE day=?',(did,))
        self.assertEqual({r['member']:r['state'] for r in rows},{self.ids[0]:'done',self.ids[1]:'cancelled'})
        self.assertEqual(self.query('SELECT credit FROM members WHERE id=?',(self.ids[0],))[0]['credit'],1)

    def test_changing_permanent_pairs_requires_closed_turns(self):
        self.store.swap_partners(self.ids[0],self.ids[2],99)
        members = {m['id']:m for m in self.store.view(self.sid,99)['members']}
        self.assertEqual(members[self.ids[0]]['pair'],self.people[2]['pair'])
        self.day()
        with self.assertRaises(DutyError):
            self.store.swap_partners(self.ids[0],self.ids[2],99)

    def test_unresolved_result_older_than_two_weeks_remains_accessible(self):
        did = self.day()
        for offset in range(1,20):
            self.store.ensure_day(self.sid,self.time(days=offset),source_state='no_lessons')
        self.assertIn(did,{d['id'] for d in self.store.view(self.sid,99)['days']})

    def test_exemption_and_cross_list_double_booking_are_respected(self):
        did = self.day()
        sid2 = self.store.create(200,'116-КД9-3ССА',parse_people('Другой Иван + Другая Анна'),[],now=self.now)
        with self.store.connect(True) as db:
            people = db.execute('SELECT * FROM members WHERE space=? ORDER BY id',(sid2,)).fetchall()
            for uid,m in zip((11,12),people):
                db.execute('UPDATE members SET uid=? WHERE id=?',(uid,m['id']))
                db.execute("INSERT INTO access(space,uid,role) VALUES(?,?,'member')",(sid2,uid))
        other = self.store.ensure_day(sid2,self.time(),source_state='available')
        self.assertEqual(self.query('SELECT status FROM days WHERE id=?',(other,))[0]['status'],'unfilled')
        self.assertEqual(self.query('SELECT status FROM days WHERE id=?',(did,))[0]['status'],'assigned')


    def test_sundays_and_confirmed_days_off_never_queue_announcements(self):
        for moment, source in ((self.time(days=6), 'available'), (self.time(days=1), 'no_lessons')):
            did = self.store.ensure_day(self.sid, moment, source)
            self.assertEqual(self.store.day_view(did, 99)['day']['status'], 'no_duty')
            self.assertFalse(self.query('SELECT * FROM outbox WHERE ref=?', (did,)))
            with self.store.connect(True) as db:
                self.store._job(db, self.sid, 99, 'daily', did, f'old-day-off:{did}')
            job = self.query('SELECT * FROM outbox WHERE ref=?', (did,))[0]
            self.assertIsNone(self.store.begin_delivery(job['id'], moment))

    def test_legacy_all_audience_is_migrated_and_never_broadcasts_to_observers(self):
        with self.store.connect(True) as db:
            opts = json_load(db.execute('SELECT settings FROM spaces WHERE id=?', (self.sid,)).fetchone()[0])
            opts['audience'] = 'all'
            import json
            db.execute('UPDATE spaces SET settings=?', (json.dumps(opts),))
            db.execute('PRAGMA user_version=2')
        Store(self.path)
        self.assertEqual(json_load(self.store.view(self.sid, 99)['space']['settings'])['audience'], 'assigned')
        did = self.day()
        self.assertEqual({r['uid'] for r in self.query("SELECT uid FROM outbox WHERE kind='daily'")}, {99,11,12})
        with self.store.connect(True) as db:
            self.store._job(db, self.sid, 13, 'daily', did, 'legacy-observer')
            db.execute("UPDATE outbox SET status='sent',message_id=55 WHERE dedup='legacy-observer:13'")
            self.store._job(db, self.sid, 13, 'update', did, 'legacy-observer-update')
        job = self.query("SELECT * FROM outbox WHERE dedup='legacy-observer-update:13'")[0]
        self.assertIsNone(self.store.begin_delivery(job['id'], self.time()))

    def test_reserve_and_paired_member_can_exchange_places_in_both_directions(self):
        paired, reserve = self.ids[0], self.ids[4]
        original = self.people[0]['pair']
        self.store.swap_partners(reserve, paired, 99)
        members = {m['id']: m for m in self.store.view(self.sid,99)['members']}
        self.assertEqual(members[reserve]['pair'], original)
        self.assertIsNone(members[paired]['pair'])
        self.assertEqual(members[reserve]['uid'], 15)
        self.assertEqual(members[paired]['uid'], 11)
        self.store.swap_partners(reserve, paired, 99)
        members = {m['id']: m for m in self.store.view(self.sid,99)['members']}
        self.assertEqual(members[paired]['pair'], original)
        self.assertIsNone(members[reserve]['pair'])

    def test_reserve_swap_is_atomic_when_a_partner_has_an_unfinished_shift(self):
        self.day()
        before = self.store.view(self.sid,99)['members']
        with self.assertRaises(DutyError):
            self.store.swap_partners(self.ids[4], self.ids[0], 99)
        self.assertEqual(self.store.view(self.sid,99)['members'], before)

    def test_forecast_uses_queue_without_changing_real_assignments_or_notifications(self):
        from duties.views import forecast
        self.day()
        with self.store.connect() as db:
            before = '\n'.join(db.iterdump())
        days = forecast(self.store, self.sid, 99, now=self.time(), count=8)
        self.assertEqual([p['id'] for p in days[0]['people']], self.ids[:2])
        self.assertEqual([p['id'] for p in days[1]['people']], self.ids[2:4])
        self.assertEqual(days[6]['status'], 'no_duty')
        with self.store.connect() as db:
            self.assertEqual('\n'.join(db.iterdump()), before)


def json_load(value):
    import json
    return json.loads(value)


if __name__ == '__main__':
    unittest.main()
