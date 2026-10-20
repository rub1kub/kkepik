import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramForbiddenError
from aiogram.methods import SendMessage, EditMessageText, GetMe, GetChat

from commands.duty import DutyBot, keyboard, roster_lines
from duties.store import Store, MSK, parse_people
from duties.worker import DutyWorker, schedule_state

NOW = datetime(2026,10,19,8,0,tzinfo=MSK)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.error = None
        self.profiles = {}
        self.profile_error = None

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return types.User(id=123456,is_bot=True,first_name='ККЭПик',username='kkepik_bot')
        if isinstance(method, GetChat):
            if self.profile_error:
                raise self.profile_error(method)
            return self.profiles.get(int(method.chat_id), types.Chat(id=int(method.chat_id), type='private', first_name='Тест'))
        if isinstance(method,(SendMessage,EditMessageText)):
            if self.error:
                error, self.error = self.error, None
                raise error(method)
            return types.Message(message_id=getattr(method,'message_id',None) or len(self.calls),
                date=NOW,chat=types.Chat(id=int(method.chat_id),type='private'),text=method.text)
        return True


class DutyBotTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'duties.db')
        profile = self.root/'profiles.db'
        with sqlite3.connect(profile) as db:
            db.execute('CREATE TABLE users(user_id INTEGER,role TEXT,name_or_group TEXT,class_group TEXT)')
            db.execute("INSERT INTO users VALUES(99,'Я студент','116-КД9-3ССА',NULL)")
        self.app = DutyBot(self.store,profile,self.root)
        self.session = FakeSession()
        self.bot = Bot('123456:local_test_token_only',session=self.session)
        self.dp = Dispatcher()
        self.app.register(self.dp)
        self.fallback_calls = []
        async def ordinary_start(message):
            self.fallback_calls.append(message.text)
        self.dp.message.register(ordinary_start,CommandStart())
        self.counter = 0
        self.patches = [patch('commands.duty.clock',return_value=NOW),patch('duties.worker.clock',return_value=NOW)]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.bot.session.close()
        await self.dp.storage.close()
        self.tmp.cleanup()

    async def update(self, text=None, callback=None, uid=99):
        self.counter += 1
        user = types.User(id=uid,is_bot=False,first_name='Тест',username=f'test_user_{uid}')
        message = types.Message(message_id=self.counter,date=NOW,chat=types.Chat(id=uid,type='private'),from_user=user,text=text or 'Панель')
        if callback:
            update = types.Update(update_id=self.counter,callback_query=types.CallbackQuery(id=str(self.counter),
                from_user=user,chat_instance='test',message=message,data=callback))
        else:
            update = types.Update(update_id=self.counter,message=message)
        await self.dp.feed_update(self.bot,update)

    def fixture(self, *, reserves='Максим', **options):
        sid = self.store.create(99,'116-КД9-3ССА',parse_people('Иван + Анна\nПётр + Мария'),
            parse_people(reserves,reserves=True),options,now=NOW-timedelta(hours=1))
        with self.store.connect(True) as db:
            for uid,m in enumerate(db.execute('SELECT * FROM members WHERE space=? ORDER BY id',(sid,)).fetchall(),11):
                db.execute('UPDATE members SET uid=? WHERE id=?',(uid,m['id']))
                db.execute("INSERT INTO access(space,uid,role) VALUES(?,?,'member')",(sid,uid))
        return sid

    def row(self, sql, args=()):
        with self.store.connect() as db:
            return dict(db.execute(sql,args).fetchone())

    async def test_real_dispatcher_three_step_setup_then_command_reopens_list(self):
        await self.update('/duty')
        await self.update(callback='du:create')
        self.assertEqual(self.store.session(99)[0],'setup_pairs')
        await self.update('Иван @ivan_test + Анна\nПётр + Мария')
        self.assertEqual(self.store.session(99)[0],'setup_reserves')
        await self.update('Максим')
        self.assertEqual(self.store.session(99)[0],'setup_options')
        await self.update(callback='du:wopt:owner_daily')
        await self.update(callback='du:wpreview')
        self.assertEqual(self.store.session(99)[0],'setup_preview')
        await self.update(callback='du:wpublish')
        self.assertIsNone(self.store.session(99))
        spaces = self.store.lists(99)
        self.assertEqual(len(spaces),1)
        self.assertFalse(json.loads(spaces[0]['settings'])['owner_daily'])
        self.assertEqual(len(self.store.view(spaces[0]['id'],99)['members']),5)
        await self.update(callback='du:wpublish')
        self.assertEqual(len(self.store.lists(99)),1)
        await self.update('/duty')
        self.assertIn('Дежурства 116-КД9-3ССА',self.session.calls[-1].text)
        self.assertNotIn('Выберите свой список', self.session.calls[-1].text)
        self.assertNotIn('Все мои списки', str(self.session.calls[-1].reply_markup))
        self.assertTrue(all(int(m.chat_id)==99 for m in self.session.calls if isinstance(m,(SendMessage,EditMessageText))))

    async def test_duty_opens_existing_list_without_create_or_selector(self):
        sid = self.fixture()
        for uid in (99, 11):
            await self.update('/duty', uid=uid)
            message = self.session.calls[-1]
            self.assertIn('Дежурства 116-КД9-3ССА', message.text)
            self.assertNotIn('список ' + str(sid), message.text)
            callbacks = [b.callback_data for row in message.reply_markup.inline_keyboard for b in row]
            self.assertNotIn('du:create', callbacks)
            self.assertNotIn('du:home', callbacks)
        # An old setup button opens the existing roster instead of a new wizard.
        await self.update(callback='du:create')
        self.assertIsNone(self.store.session(99))
        self.assertEqual(len(self.store.lists(99)), 1)
        self.assertIn('Дежурства 116-КД9-3ССА', self.session.calls[-1].text)
        self.store.manage(sid, 99, 'pause')
        await self.update(callback='du:create')
        self.assertIn('На паузе', self.session.calls[-1].text)
        self.assertIsNone(self.store.session(99))

    async def test_setup_remains_available_after_archive(self):
        sid = self.fixture()
        self.store.manage(sid, 99, 'archive')
        await self.update('/duty')
        self.assertIn('Настроить дежурства', str(self.session.calls[-1].reply_markup))
        await self.update(callback='du:create')
        self.assertEqual(self.store.session(99)[0], 'setup_pairs')

    async def test_existing_delegated_rosters_remain_accessible_without_extra_creation(self):
        sid = self.fixture()
        other = self.store.create(200, '103-Д9-3ИНС', parse_people('Другой + Ещё один'), [], now=NOW)
        with self.store.connect(True) as db:
            db.execute("INSERT INTO access(space,uid,role) VALUES(?,99,'manager')", (other,))
        text, rows = self.app.home(99)
        self.assertIn('Какие дежурства открыть', text)
        self.assertEqual({data for row in rows for _, data in row if data.startswith('open:')},
                         {f'open:{sid}', f'open:{other}'})
        self.assertNotIn('create', str(rows))

    async def test_roster_prompts_require_telegram_identity_and_explain_opt_in(self):
        await self.update(callback='du:create')
        text = self.session.calls[-1].text
        self.assertIn('Укажите @username', text)
        self.assertIn('Если их нет — имя из профиля Telegram', text)
        self.assertIn('Telegram user ID числом', text)
        self.assertIn('Имена по известным username бот подставит сам', text)
        self.assertNotIn('Во второй строке', text)
        self.assertNotIn('Можно добавить @username', text)
        self.assertNotIn('приглашени', text)
        self.assertLess(len(text), 600)
        self.assertIn('@ivan_ivanov + @anna_petrova', text)
        await self.update('@ivan_ivanov + Анна Петрова')
        text = self.session.calls[-1].text
        self.assertIn('Telegram user ID числом или имя из профиля Telegram', text)
        self.assertIn('Если запасных нет, нажмите «Без запасных»', text)
        self.assertEqual(self.store.session(99)[1]['pairs'][0][0]['username'], 'ivan_ivanov')
        self.assertEqual(self.store.session(99)[1]['pairs'][0][1]['name'], 'Анна Петрова')
        await self.update(callback='du:wskip')
        await self.update(callback='du:wpreview')
        text = self.session.calls[-1].text
        self.assertIn('отправьте участникам приглашения', text)
        self.assertIn('вы подтвердите заявки', text)
        self.assertIn('На замену должны согласиться оба', text)
        self.assertIn('вручную', text)

    async def test_add_roster_prompts_also_explain_telegram_identity(self):
        sid = self.fixture()
        for action in ('addpairs', 'addreserves'):
            await self.update(callback=f'du:{action}:{sid}')
            text = self.session.calls[-1].text
            self.assertIn('Если их нет — имя из профиля Telegram', text)
            self.assertIn('Telegram user ID числом', text)
            self.assertIn('отправьте приглашение и подтвердите их заявки', text)

    async def test_known_group_profiles_fill_names_without_sending_or_enrolling(self):
        with sqlite3.connect(self.app.profile_db) as db:
            db.executemany('INSERT INTO users VALUES(?,?,?,NULL)', [
                (11,'Я студент','116-КД9-3ССА'), (12,'Я студент','116-КД9-3ССА'),
                (13,'Я студент','103-Д9-3ИНС'), (14,'Я преподаватель','116-КД9-3ССА')])
        self.session.profiles = {
            11: types.Chat(id=11, type='private', first_name='Иван', last_name='Иванов', username='ivan_ivanov'),
            12: types.Chat(id=12, type='private', first_name='Анна', last_name='Петрова', username='anna_petrova')}
        await self.update(callback='du:create')
        await self.update('@ivan_ivanov + @anna_petrova')
        pairs = self.store.session(99)[1]['pairs']
        self.assertEqual(pairs[0][0]['name'], 'Иван Иванов (@ivan_ivanov)')
        self.assertEqual(pairs[0][1]['name'], 'Анна Петрова (@anna_petrova)')
        lookups = [m.chat_id for m in self.session.calls if isinstance(m, GetChat)]
        self.assertEqual(lookups, [11, 12])
        await self.update('@backup_user')
        self.assertEqual([m.chat_id for m in self.session.calls if isinstance(m, GetChat)], lookups)
        await self.update(callback='du:wpreview')
        self.assertIn('Иван Иванов (@ivan_ivanov)', self.session.calls[-1].text)
        await self.update(callback='du:wpublish')
        sid = self.store.lists(99)[0]['id']
        self.assertTrue(all(m['uid'] is None for m in self.store.view(sid,99)['members']))
        self.assertTrue(all(int(m.chat_id) == 99 for m in self.session.calls if isinstance(m,(SendMessage, EditMessageText))))
        self.assertEqual(self.row('SELECT count(*) AS n FROM outbox')['n'], 0)
        token = self.store.invite(sid, 99, now=NOW)
        with patch('duties.store.clock', return_value=NOW):
            await self.update('/start duty_' + token, uid=11)
            await self.update('@ivan_ivanov', uid=11)
        claim = self.store.view(sid,99)['claims'][0]
        _, rows = self.app.claim_panel(claim['id'],99)
        mid = self.store.view(sid,99)['members'][0]['id']
        self.assertIn(f'approve:{claim["id"]}:{mid}', str(rows))
        await self.update(callback=f'du:approve:{claim["id"]}:{mid}')
        self.assertEqual(self.store.view(sid,11)['access']['role'], 'member')

    async def test_profile_lookup_failure_keeps_username_and_setup_usable(self):
        with sqlite3.connect(self.app.profile_db) as db:
            db.execute("INSERT INTO users VALUES(11,'Я студент','116-КД9-3ССА',NULL)")
        self.session.profile_error = lambda m: TelegramNetworkError(method=m, message='offline')
        await self.update(callback='du:create')
        await self.update('@ivan_ivanov + Анна')
        step, data = self.store.session(99)
        self.assertEqual(step, 'setup_reserves')
        self.assertEqual(data['pairs'][0][0]['name'], '@ivan_ivanov')

    async def test_profile_rate_limit_is_respected_across_groups(self):
        with sqlite3.connect(self.app.profile_db) as db:
            db.executemany("INSERT INTO users VALUES(?,'Я студент',?,NULL)", [(11,'116-КД9-3ССА'),(12,'103-Д9-3ИНС')])
        self.session.profile_error = lambda m: TelegramRetryAfter(method=m,message='slow down',retry_after=30)
        await self.app.profiles.refresh(self.bot,'116-КД9-3ССА')
        await self.app.profiles.refresh(self.bot,'103-Д9-3ИНС')
        self.assertEqual(len([m for m in self.session.calls if isinstance(m,GetChat)]),1)

    async def test_cancellation_during_profile_lookup_does_not_restore_setup(self):
        async def refresh(*args):
            self.store.set_session(99)
        await self.update(callback='du:create')
        with patch.object(self.app.profiles, 'refresh', side_effect=refresh):
            await self.update('@ivan_ivanov + Анна')
        self.assertIsNone(self.store.session(99))

    async def test_general_invitation_accepts_username_only_roster_entry(self):
        sid = self.store.create(99, '116-КД9-3ССА', parse_people('@ivan_ivanov + Анна Петрова'), [], now=NOW)
        member = next(m for m in self.store.view(sid, 99)['members'] if m['username'] == 'ivan_ivanov')
        token = self.store.invite(sid, 99, now=NOW)
        with patch('duties.store.clock', return_value=NOW):
            await self.update('/start duty_' + token, uid=11)
            self.assertIn('Пришлите свой @username, Telegram user ID или имя', self.session.calls[-1].text)
            await self.update('@ivan_ivanov', uid=11)
        self.assertIsNone(next(m for m in self.store.view(sid, 99)['members'] if m['id'] == member['id'])['uid'])
        claim = self.store.view(sid, 99)['claims'][0]
        await self.update(callback=f'du:approve:{claim["id"]}:{member["id"]}')
        self.assertEqual(self.store.view(sid, 11)['access']['role'], 'member')

    async def test_invitation_start_does_not_enter_ordinary_registration(self):
        sid = self.store.create(99,'116-КД9-3ССА',parse_people('Иван + Анна'),[],now=NOW)
        mid = self.store.view(sid,99)['members'][0]['id']
        token = self.store.invite(sid,99,mid,now=NOW)
        # Core also validates TTL using its own clock; use the fixture timestamp.
        with patch('duties.store.clock',return_value=NOW):
            await self.update('/start duty_'+token,uid=11)
            self.assertFalse(self.fallback_calls)
            self.assertIn('Это вы?',self.session.calls[-1].text)
            await self.update(callback='du:join:'+token,uid=11)
        claims = self.store.view(sid,99)['claims']
        self.assertEqual(len(claims),1)
        await self.update(callback=f'du:approve:{claims[0]["id"]}:{mid}')
        self.assertEqual(self.store.view(sid,11)['access']['role'],'member')
        await self.update('/start',uid=12)
        self.assertEqual(self.fallback_calls,['/start'])

    async def test_other_command_clears_duty_input_and_start_still_works(self):
        await self.update(callback='du:create')
        self.assertIsNotNone(self.store.session(99))
        await self.update('/start')
        self.assertIsNone(self.store.session(99))
        self.assertEqual(self.fallback_calls,['/start'])

    async def test_id_setup_resolves_dash_profile_and_join_requires_same_account(self):
        target = 123456789
        with sqlite3.connect(self.app.profile_db) as db:
            db.execute("INSERT INTO users VALUES(?,'Я студент','116-КД9-3ССА',NULL)",(target,))
        self.session.profiles[target] = types.Chat(id=target,type='private',first_name='-')
        await self.update(callback='du:create')
        await self.update('@first_user + 123456789')
        step, data = self.store.session(99)
        self.assertEqual(step,'setup_reserves')
        self.assertEqual(data['pairs'][0][1]['name'],'- (ID 123456789)')
        self.assertEqual(data['pairs'][0][1]['telegram_id'],target)
        await self.update(callback='du:wskip')
        await self.update(callback='du:wpreview')
        self.assertIn('- (ID 123456789)',self.session.calls[-1].text)
        await self.update(callback='du:wpublish')
        sid = self.store.lists(99)[0]['id']
        member = next(m for m in self.store.view(sid,99)['members'] if m['telegram_id'])
        self.assertIsNone(member['uid'])
        token = self.store.invite(sid,99,now=NOW)
        with patch('duties.store.clock',return_value=NOW):
            await self.update('/start duty_'+token,uid=11)
            await self.update(str(target),uid=11)
            self.assertIn('не ваш',self.session.calls[-1].text)
            self.assertEqual(self.store.view(sid,99)['claims'],[])
            await self.update('/start duty_'+token,uid=target)
            await self.update(str(target),uid=target)
        claim = self.store.view(sid,99)['claims'][0]
        _, rows = self.app.claim_panel(claim['id'],99)
        self.assertIn(f'approve:{claim["id"]}:{member["id"]}',str(rows))
        await self.update(callback=f'du:approve:{claim["id"]}:{member["id"]}')
        self.assertEqual(self.store.view(sid,target)['access']['role'],'member')

    async def test_dash_profile_name_is_valid_without_username(self):
        self.store.observe(11,'-',None,'116-КД9-3ССА')
        await self.update(callback='du:create')
        await self.update('@first_user + -')
        step,data = self.store.session(99)
        self.assertEqual(step,'setup_reserves')
        self.assertEqual(data['pairs'][0][1]['name'],'-')
        self.assertIsNone(data['pairs'][0][1].get('telegram_id'))

    async def ambiguous_profiles(self):
        self.store.observe(11,'-',None,'116-КД9-3ССА')
        self.store.observe(12,'-',None,'116-КД9-3ССА')
        await self.update(callback='du:create')

    async def test_same_profile_names_save_roster_then_accept_one_id(self):
        await self.ambiguous_profiles()
        lines = [f'@person_{i:02d} + @partner_{i:02d}' for i in range(12)]
        lines[9] = '@first_user + -'
        await self.update('\n'.join(lines))
        step, data = self.store.session(99)
        self.assertEqual(step, 'roster_clarify')
        self.assertEqual(data['path'], ['pairs', 9, 1])
        self.assertEqual(len(data['pairs']), 12)
        self.assertIn('Пара 10: @first_user + -', self.session.calls[-1].text)
        self.assertIn('несколько профилей', self.session.calls[-1].text)
        self.assertIn('только @username или Telegram user ID', self.session.calls[-1].text)
        await self.update('11')
        step, resolved = self.store.session(99)
        self.assertEqual(step, 'setup_reserves')
        self.assertEqual(resolved['pairs'][9][1]['telegram_id'], 11)
        self.assertEqual(resolved['pairs'][9][1]['name'], '- (ID 11)')
        self.assertEqual(resolved['pairs'][:9], data['pairs'][:9])
        self.assertEqual(resolved['pairs'][10:], data['pairs'][10:])
        self.assertEqual(resolved['pairs'][9][0], data['pairs'][9][0])
        self.assertEqual(self.row('SELECT count(*) AS n FROM members')['n'], 0)
        self.assertEqual(self.row('SELECT count(*) AS n FROM outbox')['n'], 0)
        self.assertTrue(all(int(m.chat_id) == 99 for m in self.session.calls if isinstance(m, (SendMessage, EditMessageText))))

    async def test_clarification_survives_store_reload_and_duty_command(self):
        await self.ambiguous_profiles()
        await self.update('@first_user + -')
        saved = self.store.session(99)
        self.app.store = Store(self.root / 'duties.db')
        await self.update('/duty')
        self.assertEqual(self.store.session(99), saved)
        self.assertIn('Пара 1: @first_user + -', self.session.calls[-1].text)
        await self.update('11')
        self.assertEqual(self.store.session(99)[0], 'setup_reserves')

    async def test_invalid_or_duplicate_clarification_keeps_original_entry(self):
        await self.ambiguous_profiles()
        self.store.observe(13, 'Иван', 'first_user', '116-КД9-3ССА')
        await self.update('@first_user + -')
        saved = self.store.session(99)
        for text in ('-', '0', '-100123', 'Иван', '@first_user', '13', '11\n12', '@first_user + 11'):
            await self.update(text)
            self.assertEqual(self.store.session(99), saved, text)
            self.assertIn('Остальной список сохранён', self.session.calls[-1].text)
        await self.update('11')
        self.assertEqual(self.store.session(99)[0], 'setup_reserves')

    async def test_multiple_clarifications_keep_answers_and_reject_old_reset(self):
        await self.ambiguous_profiles()
        self.store.observe(21, '.', 'second_user', '116-КД9-3ССА')
        self.store.observe(22, '.', None, '116-КД9-3ССА')
        await self.update('@first_user + -\n@third_user + .')
        saved = self.store.session(99)
        await self.update('11')
        step, data = self.store.session(99)
        self.assertEqual(step, 'roster_clarify')
        self.assertEqual(data['path'], ['pairs', 1, 1])
        self.assertEqual(data['pairs'][0][1]['telegram_id'], 11)
        await self.update(callback=f'du:rreset:{saved[1]["token"]}')
        self.assertEqual(self.store.session(99), (step, data))
        await self.update('@second_user')
        step, data = self.store.session(99)
        self.assertEqual(step, 'setup_reserves')
        self.assertEqual(data['pairs'][1][1]['telegram_id'], 21)
        self.assertEqual(data['pairs'][0][1]['telegram_id'], 11)

    async def test_duplicate_before_next_ambiguity_can_be_corrected_in_place(self):
        await self.ambiguous_profiles()
        self.store.observe(21, '.', None, '116-КД9-3ССА')
        self.store.observe(22, '.', None, '116-КД9-3ССА')
        self.store.observe(13, 'Иван', 'first_user', '116-КД9-3ССА')
        await self.update('@first_user + -\n@third_user + .')
        saved = self.store.session(99)
        await self.update('13')
        self.assertEqual(self.store.session(99), saved)
        self.assertIn('указан дважды', self.session.calls[-1].text)
        await self.update('11')
        self.assertEqual(self.store.session(99)[1]['path'], ['pairs', 1, 1])

    async def test_pending_clarification_cannot_be_bypassed_and_can_reset(self):
        await self.ambiguous_profiles()
        await self.update('@first_user + -')
        saved = self.store.session(99)
        for callback in ('du:wskip', 'du:wpreview', 'du:wpublish', 'du:wpairs'):
            await self.update(callback=callback)
            self.assertEqual(self.store.session(99), saved)
        await self.update(callback=f'du:rreset:{saved[1]["token"]}')
        self.assertEqual(self.store.session(99)[0], 'setup_pairs')
        await self.update('@first_user + @second_user')
        self.assertEqual(self.store.session(99)[0], 'setup_reserves')

    async def test_cancel_during_initial_ambiguity_lookup_does_not_restore_draft(self):
        await self.ambiguous_profiles()
        async def cancel(*args):
            self.store.set_session(99)
        with patch.object(self.app.profiles, 'refresh', side_effect=cancel):
            await self.update('@first_user + -')
        self.assertIsNone(self.store.session(99))

    async def test_cancel_and_navigation_during_clarification_do_not_restore_draft(self):
        await self.ambiguous_profiles()
        await self.update('@first_user + -')
        async def cancel(*args):
            self.store.set_session(99)
        with patch.object(self.app.profiles, 'refresh', side_effect=cancel):
            await self.update('11')
        self.assertIsNone(self.store.session(99))
        await self.update(callback='du:create')
        await self.update('@first_user + -')
        await self.update('/cancel')
        await self.update('11')
        self.assertIsNone(self.store.session(99))

    async def test_newer_answer_during_lookup_is_not_overwritten(self):
        await self.ambiguous_profiles()
        await self.update('@first_user + -')
        saved = self.store.session(99)
        newer = ('setup_pairs', {'group': '116-КД9-3ССА', 'newer': True})
        async def navigate(*args):
            self.store.set_session(99, *newer)
        with patch.object(self.app.profiles, 'refresh', side_effect=navigate):
            await self.update('11')
        self.assertNotEqual(saved, newer)
        self.assertEqual(self.store.session(99), newer)

    async def test_reserve_clarification_keeps_pairs_and_continues_options(self):
        await self.ambiguous_profiles()
        await self.update('@first_user + @second_user')
        pairs = self.store.session(99)[1]['pairs']
        await self.update('@backup_user\n-')
        self.assertEqual(self.store.session(99)[1]['path'], ['reserves', 1])
        self.assertIn('Запасной № 2: -', self.session.calls[-1].text)
        await self.update('11')
        step, data = self.store.session(99)
        self.assertEqual(step, 'setup_options')
        self.assertEqual(data['pairs'], pairs)
        self.assertEqual(len(data['reserves']), 2)
        self.assertEqual(data['reserves'][1]['telegram_id'], 11)

    async def test_adding_pairs_and_reserves_clarifies_before_writing(self):
        sid = self.fixture()
        self.store.observe(21, '-', None, '116-КД9-3ССА')
        self.store.observe(22, '-', None, '116-КД9-3ССА')
        await self.update(callback=f'du:addpairs:{sid}')
        await self.update('@first_user + -')
        self.assertEqual(self.store.session(99)[0], 'roster_clarify')
        self.assertEqual(len(self.store.view(sid, 99)['members']), 5)
        await self.update('11')  # Already bound in the existing list.
        self.assertEqual(self.store.session(99)[0], 'roster_clarify')
        self.assertIn('указан дважды', self.session.calls[-1].text)
        await self.update('21')
        self.assertIsNone(self.store.session(99))
        self.assertEqual(len(self.store.view(sid, 99)['members']), 7)
        await self.update(callback=f'du:addreserves:{sid}')
        await self.update('-')
        saved = self.store.session(99)
        await self.update('/duty')
        self.assertEqual(self.store.session(99), saved)
        await self.update('22')
        self.assertIsNone(self.store.session(99))
        self.assertEqual(len(self.store.view(sid, 99)['members']), 8)
        self.assertIsNone(self.store.view(sid, 99)['members'][-1]['uid'])

    async def test_append_clarification_rechecks_permission_after_lookup(self):
        sid = self.fixture()
        self.store.manage(sid, 99, 'manager', 11)
        self.store.observe(21, '-', None, '116-КД9-3ССА')
        self.store.observe(22, '-', None, '116-КД9-3ССА')
        await self.update(callback=f'du:addreserves:{sid}', uid=11)
        await self.update('-', uid=11)
        saved = self.store.session(11)
        async def revoke(*args):
            self.store.manage(sid, 99, 'manager', 11)
        with patch.object(self.app.profiles, 'refresh', side_effect=revoke):
            await self.update('21', uid=11)
        self.assertEqual(self.store.session(11), saved)
        self.assertEqual(len(self.store.view(sid, 99)['members']), 5)

    async def test_bad_entry_points_to_line_and_leaves_setup_open(self):
        await self.update(callback='du:create')
        await self.update('@first_user + Анна\n@second_user + ')
        self.assertEqual(self.store.session(99)[0],'setup_pairs')
        self.assertIn('Строка 2:',self.session.calls[-1].text)

    def registered_group(self):
        with sqlite3.connect(self.app.profile_db) as db:
            db.executemany("INSERT INTO users VALUES(?,'Я студент','116-КД9-3ССА',NULL)", [(11,), (12,), (13,)])
        self.store.observe(11, 'Иван', 'first_user', '116-КД9-3ССА')
        self.store.observe(12, 'Анна', None, '116-КД9-3ССА')
        self.store.observe(13, 'Запасной', 'backup_user', '116-КД9-3ССА')

    async def auto_preview(self):
        self.registered_group()
        await self.update(callback='du:create')
        await self.update('@first_user + Анна')
        await self.update('@backup_user')
        await self.update(callback='du:wpreview')

    async def test_same_group_preview_publish_and_single_notifications_without_invites(self):
        await self.auto_preview()
        self.assertEqual(self.store.session(99)[1]['auto_preview'], [11, 12, 13])
        self.assertIn('Приглашения не нужны', self.session.calls[-1].text)
        self.assertEqual(self.row('SELECT count(*) AS n FROM outbox')['n'], 0)
        await self.update(callback='du:wpublish')
        sid = self.store.lists(99)[0]['id']
        self.assertEqual({m['uid'] for m in self.store.view(sid, 99)['members']}, {11, 12, 13})
        self.assertNotIn('Пригласить участников', str(self.session.calls[-1].reply_markup))
        self.assertEqual(self.row('SELECT count(*) AS n FROM claims')['n'], 0)
        self.assertEqual(self.row('SELECT count(*) AS n FROM invites')['n'], 0)
        worker = DutyWorker(self.app, self.bot)
        for job in self.store.pending_jobs(NOW):
            await worker.deliver(job['id'], NOW)
            await worker.deliver(job['id'], NOW)
        notices = [m for m in self.session.calls if isinstance(m, SendMessage) and m.chat_id in (11, 12, 13)]
        self.assertEqual(len(notices), 3)
        self.assertIn('Вас добавили в список дежурств группы 116-КД9-3ССА', notices[0].text)
        self.assertIn('Список создал @test_user_99.', notices[0].text)
        self.assertNotIn('Предложения о подмене', notices[0].text)
        self.assertIn('В дни дежурств в 08:00 МСК бот будет сообщать, кто дежурит.', notices[0].text)
        self.assertIn('Посмотреть список и настроить уведомления можно в /duty.', notices[0].text)
        self.assertIn('Ваш напарник: Анна', notices[0].text)
        self.assertIn('в списке запасных', notices[2].text)
        self.assertIn(f'du:silence:{sid}', str(notices[0].reply_markup))
        self.assertIn(f'du:leaveask:{sid}', str(notices[0].reply_markup))
        await self.update(callback='du:wpublish')
        self.assertEqual(self.row('SELECT count(*) AS n FROM spaces')['n'], 1)
        self.assertEqual(self.row("SELECT count(*) AS n FROM outbox WHERE kind='added'")['n'], 3)

    async def test_group_change_between_preview_and_publish_requires_new_confirmation(self):
        await self.auto_preview()
        with sqlite3.connect(self.app.profile_db) as db:
            db.execute("UPDATE users SET name_or_group='103-Д9-3ИНС' WHERE user_id=12")
        await self.update(callback='du:wpublish')
        self.assertEqual(self.store.lists(99), [])
        self.assertIsNone(self.store.session(99)[1]['auto_preview'])
        self.assertIn('Автоподключения не будет', self.session.calls[-1].text)
        self.assertNotIn('103-Д9-3ИНС', self.session.calls[-1].text)
        await self.update(callback='du:wpublish')
        sid = self.store.lists(99)[0]['id']
        self.assertTrue(all(m['uid'] is None for m in self.store.view(sid, 99)['members']))
        self.assertEqual(self.row('SELECT count(*) AS n FROM outbox')['n'], 0)

    async def test_legacy_preview_never_silently_enrols_people(self):
        await self.auto_preview()
        step, data = self.store.session(99)
        del data['auto_preview']
        self.store.set_session(99, step, data)
        await self.update(callback='du:wpublish')
        self.assertEqual(self.store.lists(99), [])
        self.assertIn('Приглашения не нужны', self.session.calls[-1].text)
        await self.update(callback='du:wpublish')
        self.assertEqual(len(self.store.lists(11)), 1)

    async def test_cancel_during_publish_check_never_creates_or_sends(self):
        await self.auto_preview()
        async def cancel(*args):
            self.store.set_session(99)
        with patch.object(self.app.profiles, 'refresh', side_effect=cancel):
            await self.update(callback='du:wpublish')
        self.assertIsNone(self.store.session(99))
        self.assertEqual(self.store.lists(99), [])
        self.assertEqual(self.row('SELECT count(*) AS n FROM outbox')['n'], 0)

    async def test_added_notice_ignores_daily_audience_but_respects_personal_optout(self):
        await self.auto_preview()
        step, data = self.store.session(99)
        data['options'].update(audience='off', requests=False, owner_daily=False, owner_events=False)
        self.store.set_session(99, step, data)
        await self.update(callback='du:wpublish')
        sid = self.store.lists(99)[0]['id']
        worker = DutyWorker(self.app, self.bot)
        await self.update(callback=f'du:silence:{sid}', uid=11)
        await self.update(callback=f'du:silence:{sid}', uid=11)
        self.assertEqual(self.store.view(sid, 11)['access']['muted'], 1)
        await self.update(callback=f'du:leave:{sid}', uid=12)
        for job in self.store.pending_jobs(NOW):
            await worker.deliver(job['id'], NOW)
        notices = [m for m in self.session.calls if isinstance(m, SendMessage) and 'Вас добавили' in m.text]
        self.assertEqual([m.chat_id for m in notices], [13])
        self.assertIn('Утренняя рассылка в этом списке выключена', notices[0].text)
        self.assertEqual(self.store.lists(12), [])
        self.assertIsNone(next(m for m in self.store.view(sid, 99)['members'] if m['telegram_id'] == 12)['uid'])

    async def test_added_notice_delivery_failure_alerts_creator_without_unbinding(self):
        await self.auto_preview()
        await self.update(callback='du:wpublish')
        sid = self.store.lists(99)[0]['id']
        job = self.row("SELECT * FROM outbox WHERE kind='added' AND uid=11")
        worker = DutyWorker(self.app, self.bot)
        self.session.error = lambda m: TelegramForbiddenError(method=m, message='bot blocked')
        await worker.deliver(job['id'], NOW)
        self.assertEqual(self.row('SELECT status FROM outbox WHERE id=?', (job['id'],))['status'], 'failed')
        self.assertEqual(self.store.view(sid, 11)['access']['role'], 'member')
        self.assertEqual(self.row("SELECT count(*) AS n FROM outbox WHERE kind='delivery_issue' AND uid=99")['n'], 1)

    async def test_added_notice_unknown_delivery_not_resent_after_restart(self):
        await self.auto_preview()
        await self.update(callback='du:wpublish')
        job = self.row("SELECT * FROM outbox WHERE kind='added' AND uid=11")
        worker = DutyWorker(self.app, self.bot)
        self.session.error = lambda m: TelegramNetworkError(method=m, message='timeout')
        await worker.deliver(job['id'], NOW)
        count = len(self.session.calls)
        self.store.recover_outbox()
        await worker.deliver(job['id'], NOW)
        self.assertEqual(len(self.session.calls), count)
        self.assertEqual(self.row('SELECT status FROM outbox WHERE id=?', (job['id'],))['status'], 'unknown')

    async def test_added_people_are_connected_only_if_entire_list_matches_group(self):
        await self.auto_preview()
        await self.update(callback='du:wpublish')
        sid = self.store.lists(99)[0]['id']
        with sqlite3.connect(self.app.profile_db) as db:
            db.executemany("INSERT INTO users VALUES(?,'Я студент','116-КД9-3ССА',NULL)", [(21,), (22,)])
        await self.update(callback=f'du:addpairs:{sid}')
        await self.update('21 + 22')
        self.assertEqual({m['uid'] for m in self.store.view(sid, 99)['members']}, {11, 12, 13, 21, 22})
        with sqlite3.connect(self.app.profile_db) as db:
            db.execute("UPDATE users SET name_or_group='103-Д9-3ИНС' WHERE user_id=11")
        await self.update(callback=f'du:addreserves:{sid}')
        await self.update('99')
        self.assertIsNone(self.store.view(sid, 99)['members'][-1]['uid'])

    async def test_notice_names_are_escaped(self):
        await self.auto_preview()
        self.store.observe(11, '<Иван &>', 'first_user', '116-КД9-3ССА')
        step, data = self.store.session(99)
        data['pairs'][0][0]['name'] = '<Иван &>'
        self.store.set_session(99, step, data)
        await self.update(callback='du:wpublish')
        job = self.row("SELECT * FROM outbox WHERE kind='added' AND uid=12")
        text, _ = DutyWorker(self.app, self.bot).render(job)
        self.assertIn('&lt;Иван &amp;&gt;', text)
        self.assertNotIn('<Иван', text)

    async def test_unknown_account_can_be_corrected_alone_from_preview(self):
        self.registered_group()
        await self.update(callback='du:create')
        await self.update('@first_user + UnknownClassmate')
        await self.update('@backup_user')
        await self.update(callback='du:wpreview')
        step, data = self.store.session(99)
        self.assertIsNone(data['auto_preview'])
        self.assertEqual(data['auto_issue']['path'], ['pairs', 0, 1])
        self.assertIn('Уточнить аккаунт', str(self.session.calls[-1].reply_markup))
        await self.update(callback=f'du:rclarify:{data["auto_token"]}')
        self.assertIn('UnknownClassmate', self.session.calls[-1].text)
        self.assertEqual(self.store.session(99)[0], 'roster_clarify')
        await self.update('12')
        step, corrected = self.store.session(99)
        self.assertEqual(step, 'setup_preview')
        self.assertEqual(corrected['auto_preview'], [11, 12, 13])
        self.assertEqual(corrected['pairs'][0][0], data['pairs'][0][0])
        self.assertEqual(corrected['reserves'], data['reserves'])
        await self.update(callback=f'du:rclarify:{data["auto_token"]}')
        self.assertEqual(self.store.session(99), (step, corrected))
        await self.update(callback='du:wpublish')
        self.assertEqual(len(self.store.lists(12)), 1)

    async def test_id_only_connected_member_does_not_get_invitation_prompt(self):
        sid = self.store.create(99, '116-КД9-3ССА', parse_people('11 + 12'), [], auto_link=True)
        member = self.store.view(sid, 99)['members'][0]
        text, rows = self.app.member_panel(member['id'], 99)
        self.assertIn('Аккаунт подключён', text)
        self.assertNotIn('приглашение', text)

    async def test_reserves_and_added_pairs_accept_numeric_user_ids(self):
        await self.update(callback='du:create')
        await self.update('@first_user + -')
        await self.update('123456789')
        self.assertEqual(self.store.session(99)[1]['reserves'][0]['telegram_id'],123456789)
        await self.update(callback='du:wpreview')
        await self.update(callback='du:wpublish')
        sid = self.store.lists(99)[0]['id']
        await self.update(callback=f'du:addpairs:{sid}')
        await self.update('234567890 + @second_user')
        members = self.store.view(sid,99)['members']
        self.assertEqual({m['telegram_id'] for m in members if m['telegram_id']},{123456789,234567890})
        self.assertTrue(all(m['uid'] is None for m in members))

    async def test_worker_sends_creator_daily_once_and_updates_existing_card(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid,NOW,'available')
        job = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=99")
        worker = DutyWorker(self.app,self.bot)
        await worker.deliver(job['id'],NOW)
        first_count = len([m for m in self.session.calls if isinstance(m,SendMessage)])
        await worker.deliver(job['id'],NOW)
        self.assertEqual(len([m for m in self.session.calls if isinstance(m,SendMessage)]),first_count)
        self.store.decline(did,11,NOW+timedelta(minutes=1))
        update = self.row("SELECT * FROM outbox WHERE kind='update' AND uid=99 ORDER BY id DESC LIMIT 1")
        await worker.deliver(update['id'],NOW+timedelta(minutes=1))
        self.assertIsInstance(self.session.calls[-1],EditMessageText)
        self.assertEqual(self.row('SELECT status FROM outbox WHERE id=?',(update['id'],))['status'],'sent')

    async def test_morning_notice_is_personal_with_only_decline_button(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid, NOW, 'available')
        worker = DutyWorker(self.app, self.bot)
        for uid, partner in ((11, 'Анна'), (12, 'Иван')):
            with self.subTest(uid=uid):
                job = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=?", (uid,))
                await worker.deliver(job['id'], NOW)
                message = self.session.calls[-1]
                self.assertIsInstance(message, SendMessage)
                self.assertEqual(int(message.chat_id), uid)
                self.assertEqual(message.text, f'Ты сегодня дежуришь.\nС тобой в паре — {partner}.\n\n'
                    'Отвечать на это сообщение не нужно. '
                    'Если не можешь отдежурить — нажми «Сегодня не могу».')
                self.assertEqual(message.reply_markup, keyboard([[('Сегодня не могу', f'decline:{did}')]]))
        # The detailed card stays available explicitly through /duty.
        text, rows = self.app.day_panel(did, 11)
        self.assertIn('Дежурства 116-КД9-3ССА', text)
        self.assertIn('Открыть список', str(rows))

    async def test_morning_notice_does_not_assign_observers_or_owner(self):
        sid = self.fixture()
        self.store.ensure_day(sid, NOW, 'available')
        worker = DutyWorker(self.app, self.bot)
        for uid in (13, 15):
            with self.subTest(uid=uid):
                job = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=?", (uid,))
                text, rows = worker.render(job)
                self.assertNotIn('Ты сегодня дежуришь', text)
                self.assertIn('Иван — дежурит', text)
                self.assertNotIn('Сегодня не могу', str(rows))

    async def test_owner_gets_short_morning_notice_even_when_participant_broadcast_is_off(self):
        sid = self.fixture(audience='off')
        did = self.store.ensure_day(sid, NOW, 'available')
        job = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=99")
        self.assertEqual(self.row("SELECT count(*) AS n FROM outbox WHERE kind='daily'")['n'], 1)
        await DutyWorker(self.app, self.bot).deliver(job['id'], NOW)
        message = self.session.calls[-1]
        self.assertEqual(int(message.chat_id), 99)
        self.assertEqual(message.text, 'Сегодня дежурят:\n• Иван\n• Анна')
        self.assertEqual(message.reply_markup, keyboard([[('Открыть дежурство', f'day:{did}')]]))

    async def test_owner_daily_setting_and_personal_mute_still_apply(self):
        sid = self.fixture(audience='off', owner_daily=False)
        self.store.ensure_day(sid, NOW, 'available')
        self.assertEqual(self.row("SELECT count(*) AS n FROM outbox WHERE kind='daily'")['n'], 0)
        self.store.update_settings(sid, 99, {'owner_daily': True})
        self.store.ensure_day(sid, NOW + timedelta(days=1), 'no_lessons')
        job = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=99")
        self.store.manage(sid, 99, 'silence')
        with patch('duties.worker.clock', return_value=NOW + timedelta(days=1)):
            await DutyWorker(self.app, self.bot).deliver(job['id'], NOW + timedelta(days=1))
        self.assertFalse(self.session.calls)
        self.assertEqual(self.row('SELECT status FROM outbox WHERE id=?', (job['id'],))['status'], 'cancelled')

    async def test_owner_no_duty_notice_and_escaped_names(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid, NOW, 'no_lessons')
        text, rows = self.app.day_panel(did, 99, notification=True)
        self.assertEqual(text, 'Сегодня дежурства нет.')
        self.assertEqual(rows, [[('Открыть дежурство', f'day:{did}')]])
        with self.store.connect(True) as db:
            db.execute("UPDATE members SET name='<Иван> & друг' WHERE space=? AND uid=11", (sid,))
        tomorrow = NOW + timedelta(days=1)
        did = self.store.ensure_day(sid, tomorrow, 'available')
        with patch('commands.duty.clock', return_value=tomorrow):
            text, _ = self.app.day_panel(did, 99, notification=True)
        self.assertIn('• &lt;Иван&gt; &amp; друг', text)

    async def test_morning_notice_for_owner_who_is_also_on_duty(self):
        sid = self.fixture()
        with self.store.connect(True) as db:
            db.execute('UPDATE members SET uid=99 WHERE space=? AND uid=11', (sid,))
        did = self.store.ensure_day(sid, NOW, 'available')
        job = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=99")
        text, rows = DutyWorker(self.app, self.bot).render(job)
        self.assertTrue(text.startswith('Ты сегодня дежуришь.\nС тобой в паре — Анна.'))
        self.assertEqual(rows, [[('Сегодня не могу', f'decline:{did}')]])

    async def test_morning_notice_escapes_partner_and_keeps_configured_instructions(self):
        sid = self.fixture(place='Кабинет <10>', tasks='Полить цветы & убрать столы')
        with self.store.connect(True) as db:
            db.execute("UPDATE members SET name='<Анна> & @anna_test',uid=NULL WHERE space=? AND uid=12", (sid,))
        did = self.store.ensure_day(sid, NOW, 'available')
        text, rows = self.app.day_panel(did, 11, notification=True)
        self.assertIn('С тобой в паре — &lt;Анна&gt; &amp; @anna_test.', text)
        self.assertIn('Место: Кабинет &lt;10&gt;', text)
        self.assertIn('Что нужно сделать: Полить цветы &amp; убрать столы', text)
        self.assertEqual(len(rows), 1)

    async def test_unknown_schedule_does_not_add_manager_diagnostics_to_personal_notice(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid, NOW, 'unknown')
        text, _ = self.app.day_panel(did, 11, notification=True)
        self.assertTrue(text.startswith('Ты сегодня дежуришь'))
        self.assertNotIn('Не удалось проверить', text)
        manager_text, _ = self.app.day_panel(did, 99, notification=True)
        self.assertIn('Не удалось проверить расписание занятий', manager_text)

    async def test_morning_update_stays_personal_when_pair_unchanged(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid, NOW, 'available')
        worker = DutyWorker(self.app, self.bot)
        daily = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=11")
        await worker.deliver(daily['id'], NOW)
        message_id = self.row('SELECT message_id FROM outbox WHERE id=?', (daily['id'],))['message_id']
        text = self.session.calls[-1].text
        with self.store.connect(True) as db:
            self.store._changed(db, did)
        update = self.row("SELECT * FROM outbox WHERE kind='update' AND uid=11 ORDER BY id DESC LIMIT 1")
        await worker.deliver(update['id'], NOW)
        message = self.session.calls[-1]
        self.assertIsInstance(message, EditMessageText)
        self.assertEqual(message.message_id, message_id)
        self.assertEqual(message.text, text)
        self.assertEqual(message.reply_markup, keyboard([[('Сегодня не могу', f'decline:{did}')]]))

    async def test_morning_decline_button_works_and_pending_replacement_needs_an_answer(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid, NOW, 'available')
        worker = DutyWorker(self.app, self.bot)
        daily = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=11")
        await worker.deliver(daily['id'], NOW)
        callback = self.session.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
        with patch('duties.store.clock', return_value=NOW):
            await self.update(callback=callback, uid=11)
        self.assertEqual(self.row('SELECT state FROM assignments WHERE day=? AND member=(SELECT id FROM members WHERE uid=11)',
                                 (did,))['state'], 'declined')
        for uid in (11, 12):
            job = self.row("SELECT * FROM outbox WHERE kind='update' AND uid=? ORDER BY id DESC LIMIT 1", (uid,))
            text, rows = worker.render(job)
            self.assertNotIn('Ты сегодня дежуришь', text)
            self.assertNotIn('Отвечать на это сообщение не нужно', text)
            if uid == 12:
                self.assertIn('Ответить на предложение', str(rows))
            else:
                self.assertNotIn('Сегодня не могу', str(rows))
        offer = self.row("SELECT * FROM offers WHERE day=? AND state='pending'", (did,))
        self.store.reply_offer(offer['id'], 12, True, NOW)
        self.store.reply_offer(offer['id'], 15, True, NOW)
        for uid, partner in ((12, 'Максим'), (15, 'Анна')):
            job = self.row("SELECT * FROM outbox WHERE kind='update' AND uid=? ORDER BY id DESC LIMIT 1", (uid,))
            text, rows = worker.render(job)
            self.assertTrue(text.startswith(f'Ты сегодня дежуришь.\nС тобой в паре — {partner}.'))
            self.assertEqual(rows, [[('Сегодня не могу', f'decline:{did}')]])

    async def test_no_reserve_moves_to_next_whole_pair_without_mixing_partners(self):
        sid = self.fixture(reserves='')
        pairs_before = {m['uid']: m['pair'] for m in self.store.view(sid, 99)['members']}
        did = self.store.ensure_day(sid, NOW, 'available')
        worker = DutyWorker(self.app, self.bot)
        self.store.decline(did, 11, NOW)
        data = self.store.day_view(did, 99)
        self.assertEqual({a['uid']: a['state'] for a in data['assignments']},
                         {11: 'declined', 12: 'released', 13: 'waiting', 14: 'waiting'})
        self.assertEqual(len(data['offers']), 1)
        offer = data['offers'][0]
        self.assertEqual(offer['kind'], 'pair')
        self.assertEqual({offer['host_uid'], offer['guest_uid']}, {13, 14})
        with self.store.connect() as db:
            jobs = [dict(r) for r in db.execute("SELECT * FROM outbox WHERE kind='offer' AND ref=?", (offer['id'],))]
        self.assertEqual({job['uid'] for job in jobs}, {13, 14})
        for job in jobs:
            text, rows = worker.render(job)
            self.assertIn('Предыдущая пара сегодня не дежурит', text)
            self.assertIn('со своим напарником', text)
            self.assertIn('Да, могу', str(rows))
            self.assertNotIn('Отвечать на это сообщение не нужно', text)
        for uid in (11, 12, 13, 14):
            text, _ = self.app.day_panel(did, uid, notification=True)
            self.assertNotIn('Ты сегодня дежуришь', text)
        self.store.reply_offer(offer['id'], 13, True, NOW)
        self.store.reply_offer(offer['id'], 14, True, NOW)
        for uid, partner in ((13, 'Мария'), (14, 'Пётр')):
            text, rows = self.app.day_panel(did, uid, notification=True)
            self.assertTrue(text.startswith(f'Ты сегодня дежуришь.\nС тобой в паре — {partner}.'))
            self.assertEqual(rows, [[('Сегодня не могу', f'decline:{did}')]])
        self.assertEqual({m['uid']: m['pair'] for m in self.store.view(sid, 99)['members']}, pairs_before)

    async def test_morning_notice_is_not_used_for_no_duty_cancelled_or_old_day(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid, NOW, 'available')
        for now in (NOW - timedelta(days=1), NOW + timedelta(days=1)):
            with patch('commands.duty.clock', return_value=now):
                text, rows = self.app.day_panel(did, 11, notification=True)
                self.assertNotIn('Ты сегодня дежуришь', text)
                self.assertNotIn('Сегодня не могу', str(rows))
        self.store.cancel_day(did, 99, NOW)
        text, rows = self.app.day_panel(did, 11, notification=True)
        self.assertIn('Дежурство отменено старостой', text)
        self.assertNotIn('Отвечать на это сообщение не нужно', text)
        self.assertNotIn('Сегодня не могу', str(rows))
        self.store.manage(sid, 99, 'archive')
        other_sid = self.fixture()
        other_did = self.store.ensure_day(other_sid, NOW, 'no_lessons')
        text, rows = self.app.day_panel(other_did, 11, notification=True)
        self.assertIn('Сегодня дежурства нет', text)
        self.assertNotIn('Отвечать на это сообщение не нужно', text)
        self.assertNotIn('Сегодня не могу', str(rows))

    async def test_completed_duty_keeps_results_and_actions_instead_of_morning_copy(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid, NOW, 'available')
        later = NOW + timedelta(hours=1)
        with patch('commands.duty.clock', return_value=later):
            self.store.report_done(did, 11, later)
            text, rows = self.app.day_panel(did, 11, notification=True)
            self.assertIn('ждём подтверждения старосты', text)
            self.assertNotIn('Ты сегодня дежуришь', text)
            self.assertNotIn('Сегодня не могу', str(rows))
            self.store.finish(did, 99, now=later)
            text, rows = self.app.day_panel(did, 11, notification=True)
            self.assertIn('отдежурил', text)
            self.assertNotIn('Отвечать на это сообщение не нужно', text)

    async def test_unknown_network_result_is_not_retried(self):
        sid = self.fixture()
        self.store.ensure_day(sid,NOW,'available')
        job = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=11")
        self.session.error = lambda m:TelegramNetworkError(method=m,message='unknown outcome')
        worker = DutyWorker(self.app,self.bot)
        await worker.deliver(job['id'],NOW)
        self.assertEqual(self.row('SELECT status FROM outbox WHERE id=?',(job['id'],))['status'],'unknown')
        before = len(self.session.calls)
        self.store.recover_outbox()
        await worker.deliver(job['id'],NOW+timedelta(minutes=1))
        self.assertEqual(len(self.session.calls),before)

    async def test_explicit_rate_limit_is_retried_after_wait_and_respects_new_mute(self):
        sid = self.fixture()
        self.store.ensure_day(sid,NOW,'available')
        job = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=11")
        self.session.error = lambda m:TelegramRetryAfter(method=m,message='rate limit',retry_after=30)
        worker = DutyWorker(self.app,self.bot)
        await worker.deliver(job['id'],NOW)
        pending = self.row('SELECT * FROM outbox WHERE id=?',(job['id'],))
        self.assertEqual(pending['status'],'pending')
        self.assertEqual(pending['not_before'],NOW.timestamp()+31)
        self.store.manage(sid,11,'mute')
        before = len(self.session.calls)
        await worker.deliver(job['id'],NOW+timedelta(seconds=32))
        self.assertEqual(len(self.session.calls),before)
        self.assertEqual(self.row('SELECT status FROM outbox WHERE id=?',(job['id'],))['status'],'cancelled')

    async def test_blocked_bot_not_interpreted_as_decline_and_owner_alert_exists(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid,NOW,'available')
        job = self.row("SELECT * FROM outbox WHERE kind='daily' AND uid=11")
        self.session.error = lambda m:TelegramForbiddenError(method=m,message='blocked')
        worker = DutyWorker(self.app,self.bot)
        await worker.deliver(job['id'],NOW)
        self.assertEqual(self.row('SELECT status FROM outbox WHERE id=?',(job['id'],))['status'],'failed')
        self.assertEqual(self.row('SELECT state FROM assignments WHERE day=? ORDER BY id',(did,))['state'],'assigned')
        alert = self.row("SELECT * FROM outbox WHERE kind='delivery_issue' AND uid=99")
        await worker.deliver(alert['id'],NOW)
        self.assertIn('не считает это отказом',self.session.calls[-1].text)

    async def test_disabled_notifications_send_nothing_on_worker_pass(self):
        sid = self.fixture(owner_daily=False,audience='off',requests=False,owner_events=False)
        worker = DutyWorker(self.app,self.bot)
        await worker.once(NOW)
        self.assertFalse(self.session.calls)
        self.assertEqual(len(self.store.view(sid,99)['days']),1)

    async def test_day_buttons_are_personal_and_private_text_escaped(self):
        sid = self.fixture()
        with self.store.connect(True) as db:
            db.execute("UPDATE members SET name='<script>&имя' WHERE space=? AND uid=11",(sid,))
        did = self.store.ensure_day(sid,NOW,'available')
        text, rows = self.app.day_panel(did,11)
        self.assertIn('&lt;script&gt;&amp;имя',text)
        self.assertIn('decline',str(rows))
        _, other_rows = self.app.day_panel(did,13)
        self.assertNotIn('decline',str(other_rows))
        for row in keyboard(rows).inline_keyboard:
            for button in row:
                self.assertLessEqual(len(button.callback_data.encode()),64)

    async def test_large_setup_preview_fits_telegram_and_includes_all_pairs(self):
        pairs = [[{'name':f'{i} '+('А'*70),'username':None},{'name':f'{i} '+('Б'*70),'username':None}] for i in range(30)]
        text = roster_lines(pairs,[])
        self.assertLess(len(text),3000)
        self.assertIn('30.',text)

    async def test_unlinked_copy_distinguishes_list_membership_from_telegram_account(self):
        sid = self.store.create(99, '116-КД9-3ССА', parse_people('@ivan_ivanov + Анна'), [], now=NOW)
        text, rows = self.app.panel(sid,99)
        self.assertIn('Подключены к списку дежурств: 0 из 2',text)
        self.assertIn('Приглашение нужно только тем, кто ещё не подключён',text)
        self.assertIn('Пригласить участников', str(rows))
        self.assertNotIn('Подключились к боту',text)
        text, _ = self.app.roster(sid,99)
        self.assertIn('ещё не подключился к списку',text)
        self.assertNotIn('без аккаунта',text)

    async def test_offer_copy_keeps_deadline_two_consents_and_pair_rules(self):
        sid = self.fixture()
        did = self.store.ensure_day(sid,NOW,'available')
        self.store.decline(did,11,NOW)
        offer = self.row("SELECT * FROM offers WHERE day=? AND state='pending'",(did,))
        text, rows = self.app.offer_panel(offer['id'],12)
        self.assertIn('Ответь до 08:10 МСК',text)
        self.assertIn('когда вы оба согласитесь',text)
        self.assertIn('Постоянные пары не меняются',text)
        self.assertIn('Да, могу',str(rows))
        self.store.reply_offer(offer['id'],12,True,NOW)
        text, rows = self.app.offer_panel(offer['id'],12)
        self.assertIn('Твой ответ принят',text)
        self.assertNotIn('Да, могу',str(rows))

    async def test_settings_copy_has_time_note_only_for_clock_settings(self):
        sid = self.fixture()
        await self.update(callback=f'du:input:{sid}:place')
        self.assertNotIn('Время московское',self.session.calls[-1].text)
        await self.update(callback=f'du:input:{sid}:time')
        self.assertIn('Время московское',self.session.calls[-1].text)
        self.assertIn('для следующих дежурств',self.session.calls[-1].text)

    async def test_real_dispatcher_private_scope_rejects_foreign_callback(self):
        sid = self.fixture()
        await self.update(callback=f'du:options:{sid}',uid=777)
        self.assertIn('недоступен',self.session.calls[-1].text)


class ScheduleSourceTest(unittest.TestCase):
    def test_unknown_and_no_lessons_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'knowledge.db'
            self.assertEqual(schedule_state(path,'116-КД9-3ССА','2026-10-19'),('unknown',None))
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE revisions(id INTEGER,date TEXT,revision INTEGER,status TEXT,groups_json TEXT);'
                                 'CREATE TABLE lessons(revision_id INTEGER,group_name TEXT);')
                db.execute('INSERT INTO revisions VALUES(1,?,?,?,?)',('2026-10-19',1,'parsed',json.dumps(['116-КД9-3ССА'])))
            self.assertEqual(schedule_state(path,'116-КД9-3ССА','2026-10-19'),('no_lessons',1))
            self.assertEqual(schedule_state(path,'999-Д9-3ССА','2026-10-19'),('entity_not_found',1))
            with sqlite3.connect(path) as db:
                db.execute('INSERT INTO lessons VALUES(1,?)',('116-КД9-3ССА',))
            self.assertEqual(schedule_state(path,'116-КД9-3ССА','2026-10-19'),('available',1))


if __name__ == '__main__':
    unittest.main()
