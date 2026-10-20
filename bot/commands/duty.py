"""Telegram-only guided setup and everyday duty controls."""
from __future__ import annotations

from datetime import datetime, timedelta
from copy import deepcopy
from html import escape as h
from pathlib import Path
import json
import logging
import re
import secrets
import sqlite3

from aiogram import BaseMiddleware, types, F
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramBadRequest

from duties.store import (Store, DutyError, AmbiguousPerson, DEFAULTS, MSK, clock,
                          parse_people, person, settings, validate_roster, validate_people, matches_member)
from duties.profiles import ProfileDirectory

log = logging.getLogger(__name__)


def keyboard(rows):
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=label, callback_data='du:' + data) for label, data in row]
        for row in rows if row])


def toggle(value):
    return '✓' if value else '○'


def date_label(value):
    return datetime.fromisoformat(value).strftime('%d.%m.%Y')


def roster_lines(pairs, reserves):
    short = lambda p: h(p['name'][:35] + ('…' if len(p['name']) > 35 else ''))
    lines = [f"{i}. {short(pair[0])} + {short(pair[1])}" for i, pair in enumerate(pairs, 1)]
    if reserves:
        lines.append('\nЗапасные: ' + ', '.join(short(p) for p in reserves))
    return '\n'.join(lines)


class IdentityMiddleware(BaseMiddleware):
    def __init__(self, app):
        self.app = app

    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        message = event.message if isinstance(event, types.CallbackQuery) else event
        if user and getattr(getattr(message, 'chat', None), 'type', None) == 'private':
            try:
                self.app.store.observe(user.id, user.full_name, user.username, self.app.group_hint(user.id))
                text = getattr(event, 'text', None) or ''
                if text.startswith('/') and not re.match(r'^/(duty|cancel)(?:@\w+)?(?:\s|$)', text) and not re.match(r'^/start(?:@\w+)? duty_', text):
                    self.app.store.set_session(user.id)
            except sqlite3.Error:
                log.warning('Duty identity update unavailable')
        return await handler(event, data)


class DutyBot:
    def __init__(self, store: Store, profile_db, data_dir):
        self.store = store
        self.profile_db = str(profile_db)
        self.data_dir = Path(data_dir)
        self.bot_username = 'kkepik_bot'
        self.profiles = ProfileDirectory(store, profile_db)

    def group_hint(self, uid):
        try:
            with sqlite3.connect(f'file:{self.profile_db}?mode=ro', uri=True) as db:
                row = db.execute('SELECT role,name_or_group,class_group FROM users WHERE user_id=?', (uid,)).fetchone()
            if row:
                return row[1] if row[0] == 'Я студент' else row[2]
        except sqlite3.Error:
            pass
        return None

    def normalize_group(self, text):
        value = text.strip().upper().replace('–', '-').replace('—', '-').replace('‑', '-')
        if not re.fullmatch(r'\d{2,3}-[А-ЯЁ]{1,3}\d{1,2}-[1-4][А-ЯЁ]{2,}(?:-\d)?', value):
            raise DutyError("Нужен полный номер группы, например 116-КД9-3ССА.")
        try:
            catalog = json.loads((self.data_dir / 'schedule_catalog.json').read_text())
            if catalog.get('groups') and value not in catalog['groups']:
                raise DutyError("Такой группы нет в каталоге ККЭП. Проверьте полный номер группы.")
        except (OSError, json.JSONDecodeError):
            pass
        return value

    async def resolve_roster(self, bot, group, pairs, reserves):
        people = [p for pair in pairs for p in pair] + reserves
        targets = [p['telegram_id'] for p in people if p.get('telegram_id')]
        await self.profiles.refresh(bot, group, targets)
        return self.store.fill_profile_names(group, pairs, reserves)

    async def enrollment_plan(self, bot, uid, data):
        people = [p for pair in data['pairs'] for p in pair] + data.get('reserves', [])
        await self.profiles.refresh(bot, data['group'], [p['telegram_id'] for p in people if p.get('telegram_id')])
        return self.profiles.enrollment(uid, data['group'], data['pairs'], data.get('reserves', []))

    def validate_draft(self, uid, draft):
        pairs, reserves = draft['pairs'], draft['reserves']
        if draft['origin'].startswith('setup_'):
            validate_roster(pairs, reserves)
            return draft['base']['group']
        sid = draft['base']['sid']
        with self.store.connect() as db:
            self.store.permission(db, sid, uid, manage=True)
            group = self.store.space(db, sid)['group_name']
            old = [dict(r) for r in db.execute(
                'SELECT name,username,telegram_id,uid FROM members WHERE space=?', (sid,))]
        people = [p for pair in pairs for p in pair] + reserves
        if not people or len(old) + len(people) > 60:
            raise DutyError('В списке может быть до 60 человек.')
        validate_people(old + people)
        return group

    def clarification_panel(self, uid, data):
        self.validate_draft(uid, data)
        path = data['path']
        if path[0] == 'pairs':
            pair = data['pairs'][path[1]]
            context = f'Пара {path[1]+1}: ' + ' + '.join(h(p['name']) for p in pair)
        else:
            context = f'Запасной № {path[1]+1}: ' + h(data['name'])
        question = h(data['question']) if data.get('question') else f'В группе несколько профилей с именем «{h(data["name"])}».'
        return (f'<b>Уточните участника</b>\n\n{context}\n\n{question} '
                'Пришлите только @username или Telegram user ID этого человека.\n\n'
                'Остальной список сохранён, присылать его заново не нужно.',
                [[('Ввести список заново', f'rreset:{data["token"]}')], [('Отмена', 'home')]])

    async def show_roster_input(self, target, uid):
        step, data = self.store.session(uid)
        if step == 'roster_clarify':
            await self.send(target, *self.clarification_panel(uid, data))
        elif step.startswith('setup_'):
            await self.show_setup(target, uid)
        else:
            self.options_panel(data['sid'], uid)
            prompt = ('Пришлите новые пары, по одной в строке: @ivan_ivanov + @anna_petrova.' if step == 'addpairs'
                      else 'Пришлите запасных, по одному человеку в строке.')
            await self.send(target, prompt + '\n\nУкажите @username или Telegram user ID числом. '
                'Если их нет — имя из профиля Telegram. Имена по известным username бот подставит сам.\n\n'
                'Если все участники списка найдены и у всех выбрана ваша группа, новые участники получат уведомление '
                'и подключатся без приглашений. Иначе отправьте приглашение и подтвердите их заявки.',
                [[('Отмена', f'open:{data["sid"]}')]])

    async def accept_roster(self, message, session, text):
        """Keep the entire draft while asking for one unambiguous identity."""
        uid = message.from_user.id
        step, data = session
        if step == 'roster_clarify':
            replacement = person(text)
            if not (replacement.get('telegram_id') or re.fullmatch(r'@[A-Za-z0-9_]{5,32}', text)):
                raise DutyError('Нужен один @username или Telegram user ID числом — только для указанного человека.')
            draft = deepcopy(data)
            container = draft
            for key in draft['path'][:-1]:
                container = container[key]
            container[draft['path'][-1]] = replacement
        else:
            is_reserves = step in ('setup_reserves', 'addreserves')
            parsed = parse_people(text, reserves=is_reserves)
            draft = dict(origin=step, base=deepcopy(data),
                         pairs=deepcopy(data['pairs']) if step == 'setup_reserves' else [] if is_reserves else parsed,
                         reserves=parsed if is_reserves else [])
        group = self.validate_draft(uid, draft)
        ambiguity = None
        try:
            pairs, reserves = await self.resolve_roster(message.bot, group, draft['pairs'], draft['reserves'])
        except AmbiguousPerson as exc:
            ambiguity = exc
            pairs, reserves = exc.pairs, exc.reserves
        # A cancel, navigation click or newer answer may arrive during getChat.
        if self.store.session(uid) != session:
            return
        draft.update(pairs=pairs, reserves=reserves)
        self.validate_draft(uid, draft)
        if ambiguity:
            draft.update(name=ambiguity.name, path=ambiguity.path, token=secrets.token_hex(6))
            draft.pop('question', None)
            self.store.set_session(uid, 'roster_clarify', draft)
            await self.show_roster_input(message, uid)
            return
        origin, base = draft['origin'], draft['base']
        if origin.startswith('setup_'):
            base.update(pairs=pairs, reserves=reserves)
            next_step = {'setup_pairs': 'setup_reserves', 'setup_reserves': 'setup_options', 'setup_preview': 'setup_preview'}[origin]
            self.store.set_session(uid, next_step, base)
            await self.show_setup(message, uid)
        else:
            sid = base['sid']
            view = self.store.view(sid, uid)
            plan = self.profiles.enrollment(uid, group, pairs, reserves, existing=view['members'])
            auto = plan.ids is not None
            self.store.append_roster(sid, uid, pairs=plan.pairs if auto else pairs,
                                     reserves=plan.reserves if auto else reserves, auto_link=auto)
            self.store.set_session(uid)
            await self.send(message, *self.roster(sid, uid))

    async def send(self, target, text, rows):
        markup = keyboard(rows)
        if isinstance(target, types.CallbackQuery):
            try:
                await target.message.edit_text(text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)
                return
            except TelegramBadRequest as exc:
                if 'message is not modified' in exc.message:
                    return
                if not any(s in exc.message.lower() for s in ('message to edit not found', "message can't be edited")):
                    raise
            target = target.message
        await target.answer(text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)

    def home(self, uid):
        spaces = self.store.lists(uid)
        if len(spaces) == 1:
            return self.panel(spaces[0]['id'], uid)
        text = '<b>Дежурства</b>\n\n'
        if not spaces:
            text += 'Ведёте дежурства в группе? Нажмите «Настроить дежурства».\n\n' \
                    'Если староста уже добавил вас, список появится здесь. Иначе попросите у него приглашение.'
        else:
            # Preserve access to previously created or delegated rosters.
            text += 'Какие дежурства открыть?'
        roles = {'owner': 'вы ведёте', 'manager': 'вы заместитель', 'member': 'вы участвуете'}
        rows = [[(f"{s['group_name']} · {roles[s['role']]}" + (' · пауза' if s['status'] == 'paused' else ''), f"open:{s['id']}")] for s in spaces]
        if not spaces:
            rows.append([('Настроить дежурства', 'create')])
        rows.append([('Как это работает', 'help')])
        return text, rows

    async def command(self, message, state=None, command=None):
        if message.chat.type != 'private':
            await message.answer('Настройте дежурства в личном чате с ботом: /duty')
            return
        if state:
            await state.clear()
        session = self.store.session(message.from_user.id)
        if session and (session[0].startswith('setup_') or session[0] == 'roster_clarify'):
            try:
                await self.show_roster_input(message, message.from_user.id)
            except DutyError as exc:
                await message.answer(h(str(exc)) + '\n\nОтмена: /cancel')
        else:
            self.store.set_session(message.from_user.id)
            await self.send(message, *self.home(message.from_user.id))

    async def cancel(self, message, state=None):
        self.store.set_session(message.from_user.id)
        if state:
            await state.clear()
        await self.send(message, 'Ввод отменён. Вернуться к дежурствам: /duty.', [[('Дежурства', 'home')]])

    async def start_invite(self, message, command, state=None):
        if message.chat.type != 'private':
            return
        if state:
            await state.clear()
        self.store.set_session(message.from_user.id)
        token = (command.args or '')[5:]
        try:
            invite = self.store.invitation(token)
            if invite['member']:
                text = f"<b>Дежурства {h(invite['group_name'])}</b>\n\nВас приглашают как «{h(invite['name'])}». Это вы?\n\n" \
                       'Нажмите «Да, это я», чтобы отправить заявку старосте. Он увидит ваше имя, @username и группу, указанную в боте.'
                await self.send(message, text, [[('Да, это я', f'join:{token}')], [('Не моя запись', 'home')]])
            else:
                self.store.set_session(message.from_user.id, 'join_name', {'token': token})
                await self.send(message, f"<b>Дежурства {h(invite['group_name'])}</b>\n\nПришлите свой @username, Telegram user ID или имя, которое староста внёс в список.\n\n"
                    'Староста проверит заявку. Он увидит ваше имя, @username и группу, указанную в боте.', [[('Отмена', 'home')]])
        except DutyError as exc:
            await message.answer(h(str(exc)))

    async def show_setup(self, target, uid):
        session = self.store.session(uid)
        if not session:
            raise DutyError("Откройте /duty и начните настройку.")
        step, data = session
        group = h(data.get('group', ''))
        cancel = [('Отмена', 'home')]
        if step == 'setup_group':
            await self.send(target, '<b>Для какой группы?</b>\n\nНапишите полный номер, например 116-КД9-3ССА.', [cancel])
        elif step == 'setup_pairs':
            await self.send(target, f'<b>1 из 3 · Пары {group}</b>\n\n'
                'Пришлите пары: одна пара на строку, между людьми знак +.\n'
                '<code>@ivan_ivanov + @anna_petrova\n@petr_sidorov + Мария Орлова</code>\n\n'
                'Укажите @username или Telegram user ID числом. Если их нет — имя из профиля Telegram. '
                'Имена по известным username бот подставит сам.\n\n'
                'Можно прислать людей по одному в строке: соседние строки станут парой. '
                'Перед сохранением проверим список.', [[('Другая группа', 'wgroup')], cancel])
        elif step == 'setup_reserves':
            await self.send(target, f'<b>1 из 3 · Запасные</b>\n\nПары: {len(data["pairs"])}.\n\n'
                'Кто сможет подменять дежурных? Пришлите запасных по одному в строке.\n\n'
                'Укажите @username, Telegram user ID числом или имя из профиля Telegram.\n\n'
                'Если запасных нет, нажмите «Без запасных».',
                [[('Без запасных', 'wskip')], [('Назад к парам', 'wpairs')], cancel])
        elif step == 'setup_options':
            opts = settings(data.get('options'))
            await self.send(target, '<b>2 из 3 · Уведомления</b>\n\n'
                'Бот сообщает, кто дежурит, в 08:00 МСК с понедельника по субботу. '
                'Если в расписании нет пар, дежурства не будет.\n\n'
                'Выберите, кому писать. Нажатие на кнопку меняет настройку, ✓ означает «включено». '
                'Время и дни можно поменять после создания списка.',
                [[(toggle(opts['owner_daily']) + ' Сообщать мне о дежурных', 'wopt:owner_daily')],
                 [(f"Участникам: { {'all':'всем','assigned':'дежурным','off':'не отправлять'}[opts['audience']]}", 'wopt:audience')],
                 [(toggle(opts['requests']) + ' Предлагать замены в личке', 'wopt:requests')],
                 [(toggle(opts['owner_events']) + ' Сообщать мне о проблемах и итогах', 'wopt:owner_events')],
                 [('Дальше →', 'wpreview')], [('Назад', 'wreserves')], cancel])
        elif step == 'setup_preview':
            bot = target.bot
            plan = await self.enrollment_plan(bot, uid, data)
            if self.store.session(uid) != session:
                return
            data['auto_preview'] = plan.ids
            data['auto_issue'] = plan.issues[0] if plan.issues else None
            data['auto_token'] = secrets.token_hex(6)
            self.store.set_session(uid, step, data)
            opts = settings(data.get('options'))
            now = clock()
            first = now.date() if now.strftime('%H:%M') < opts['time'] else now.date()+timedelta(days=1)
            text = f'<b>3 из 3 · Проверьте список {group}</b>\n\n' + roster_lines(data['pairs'], data['reserves'])
            text += (f"\n\nНачало: <b>{first.strftime('%d.%m')}</b>. Время рассылки: {opts['time']} МСК.\n"
                f"На замену должны согласиться оба. Ждём ответа {opts['wait_minutes']} мин., ищем замену до {opts['cutoff']}. "
                'Постоянные пары при этом не меняются.\n\n')
            if plan.ids is not None:
                text += ('Все участники найдены, у всех выбрана ваша группа. После создания бот добавит их сам '
                         'и сообщит каждому об этом. Приглашения не нужны. Участники смогут отключить уведомления или выйти.')
            else:
                text += h(plan.reason) + '\n\n' + (
                    'Автоподключения не будет. После создания отправьте участникам приглашения: '
                    'они откроют ссылку, вы подтвердите заявки. Дежурства неподключённых участников можно отмечать вручную.')
            clarify = [[('Уточнить аккаунт', f'rclarify:{data["auto_token"]}')]] if plan.issues else []
            await self.send(target, text, clarify + [[('Создать список', 'wpublish')], [('Исправить пары', 'wpairs')],
                                         [('Уведомления', 'woptions')], cancel])
        else:
            raise DutyError("Откройте /duty заново.")

    def panel(self, sid, uid):
        data = self.store.view(sid, uid)
        space, access, members = data['space'], data['access'], data['members']
        opts = json.loads(space['settings'])
        admin = access['role'] != 'member'
        text = f"<b>Дежурства {h(space['group_name'])}</b>\n\n"
        text += ('⏸ На паузе\n' if space['status'] == 'paused' else '')
        pairs = len({m['pair'] for m in members if m['pair']})
        linked = sum(bool(m['uid']) for m in members)
        text += f"Пар: {pairs} · запасных: {sum(m['pair'] is None for m in members)}\nПодключены к списку дежурств: {linked} из {len(members)}\n"
        text += f"Время рассылки: {opts['time']} МСК\n"
        today = next((d for d in data['days'] if d['date'] == clock().date().isoformat()), None)
        rows = []
        if today:
            rows.append([('Сегодняшнее дежурство', f'day:{today["id"]}')])
        else:
            text += f"\nДежурных на сегодня пока нет. Начало работы списка: {date_label(space['first_date'])}."
        if admin and linked < len(members):
            unlinked = [m for m in members if not m['uid']]
            names = ', '.join(h(m['name'][:35]) for m in unlinked[:3]) + ('…' if len(unlinked) > 3 else '')
            text += '\n\nБез привязанного аккаунта: ' + names + '.\nПриглашение нужно только тем, кто ещё не подключён к этому списку. Заявки появятся в разделе «Заявки».'
        rows.append([('Пары и запасные', f'roster:{sid}:0')])
        if admin:
            if linked < len(members):
                rows.append([('Пригласить участников', f'invite:{sid}:0')])
            if linked < len(members) or data['claims']:
                rows.append([('Заявки' + (f" · {len(data['claims'])}" if data['claims'] else ''), f'claims:{sid}')])
            rows += [[('Кто отдежурил', f'results:{sid}')], [('Настройки', f'options:{sid}')]]
        else:
            rows += [[('Мои уведомления: ' + ('выключены' if access['muted'] else 'включены'), f'mute:{sid}')],
                     [('Выйти из списка', f'leaveask:{sid}')]]
        if len(self.store.lists(uid)) > 1:
            rows.append([('Другие дежурства', 'home')])
        rows.append([('Как это работает', 'help')])
        return text, rows

    def roster(self, sid, uid, page=0):
        data = self.store.view(sid, uid)
        admin = data['access']['role'] != 'member'
        members = data['members']
        page = max(0, min(page, max(0, (len(members)-1)//8)))
        text = f"<b>Состав {h(data['space']['group_name'])}</b>\n\n"
        rows = []
        pair_numbers = {pid: i for i, pid in enumerate(sorted({m['pair'] for m in members if m['pair']}), 1)}
        for m in members[page*8:page*8+8]:
            role = f"Пара {pair_numbers[m['pair']]}" if m['pair'] else 'Запасной'
            status = 'подключён к списку' if m['uid'] else 'ещё не подключился к списку'
            text += f"{h(m['name'])} · {role}\n{status} · дежурств: {m['completed']}"
            if m['credit']:
                text += ' · в этот раз уже отдежурил'
            if m['exempt_until']:
                text += f" · освобождение до {date_label(m['exempt_until'])}"
            text += '\n\n'
            if admin:
                rows.append([(m['name'][:45], f'member:{m["id"]}')])
        nav = []
        if page:
            nav.append(('←', f'roster:{sid}:{page-1}'))
        if (page+1)*8 < len(members):
            nav.append(('→', f'roster:{sid}:{page+1}'))
        rows.append(nav)
        if admin:
            rows += [[('＋ Добавить пары', f'addpairs:{sid}'), ('＋ Запасные', f'addreserves:{sid}')]]
        rows.append([('Назад', f'open:{sid}')])
        return text, rows

    def member_panel(self, mid, uid):
        with self.store.connect() as db:
            m = self.store._member(db, mid)
            access = self.store.permission(db, m['space'], uid, manage=True)
            identity = db.execute('SELECT * FROM identities WHERE uid=?', (m['uid'],)).fetchone() if m['uid'] else None
            # Only a hint that a verified username is known; never reveal a
            # stranger's group or private profile to the roster creator.
            known = bool(m['username'] and db.execute('SELECT 1 FROM identities WHERE username=?', (m['username'],)).fetchone())
        text = f"<b>{h(m['name'])}</b>\n\n"
        if m.get('telegram_id'):
            text += f"Telegram user ID: <code>{m['telegram_id']}</code>\n"
        if identity:
            text += 'Telegram: ' + h(identity['name']) + '\n'
            if identity['group_hint']:
                text += 'Указанная в боте группа: ' + h(identity['group_hint']) + '\n'
        elif m['uid']:
            text += 'Аккаунт подключён к списку. Имя профиля пока не удалось получить.\n'
        else:
            text += 'Ещё не подключился к списку. Отправьте ему личное приглашение.\n'
            if known:
                text += 'Этот username бот уже знает.\n'
        rows = []
        if not m['uid']:
            rows.append([('Получить личное приглашение', f'invite:{m["space"]}:{mid}')])
        rows += [[('Изменить имя', f'rename:{mid}')], [('Освободить до даты', f'exempt:{mid}'), ('Снять освобождение', f'unexempt:{mid}')]]
        if m['pair']:
            rows.append([('Изменить состав постоянных пар', f'swap:{mid}')])
        if m['uid']:
            rows += [[('Отвязать аккаунт', f'unlinkask:{mid}')]]
            if access['role'] == 'owner' and m['uid'] != uid:
                rows += [[('Заместитель: включить / выключить', f'role:{m["space"]}:{m["uid"]}')],
                         [('Передать управление', f'transferask:{m["space"]}:{m["uid"]}')]]
        rows.append([('Назад к составу', f'roster:{m["space"]}:0')])
        return text, rows

    def options_panel(self, sid, uid):
        data = self.store.view(sid, uid)
        if data['access']['role'] == 'member':
            raise DutyError("Настройки списка доступны старосте и заместителям.")
        opts = json.loads(data['space']['settings'])
        names = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
        text = '<b>Настройки дежурств</b>\n\n' \
            f"Рассылка: {opts['time']} · подбор до {opts['cutoff']} · итоги: {opts['report']} МСК\n" \
            f"Дни: {', '.join(names[d] for d in opts['days'])}\n" \
            f"Место: {h(opts['place']) or 'не указано'}\nОбязанности: {h(opts['tasks']) or 'не указаны'}\n\n" \
            'Нажмите на кнопку, чтобы изменить настройку. ✓ означает «включено».\n\n' \
            'Если дежурный не ответил, он по-прежнему дежурит. Для замены нужно согласие обоих. ' \
            'Пока расписания занятий нет, бот назначает дежурных по выбранным дням недели.'
        rows = [[(toggle(opts['owner_daily'])+' Утром писать создателю списка', f'opt:{sid}:owner_daily')],
                [("Участникам: " + {'all':'всем','assigned':'только дежурным','off':'выключено'}[opts['audience']], f'opt:{sid}:audience')],
                [(toggle(opts['requests'])+' Предлагать замены в личке', f'opt:{sid}:requests')],
                [(toggle(opts['owner_events'])+' Отказы, проблемы и итоги старосте', f'opt:{sid}:owner_events')],
                [('При отказе: ' + ('предлагать запасного' if opts['mode']=='reserve' else 'переносить пару'), f'opt:{sid}:mode')],
                [('Время рассылки', f'input:{sid}:time'), ('До скольки искать замену', f'input:{sid}:cutoff')],
                [('Время итогов', f'input:{sid}:report'), ('Сколько ждать ответа', f'input:{sid}:wait_minutes')],
                [('Дни недели', f'week:{sid}')], [('Место', f'input:{sid}:place'), ('Обязанности', f'input:{sid}:tasks')],
                [('Мои уведомления: '+('выкл.' if data['access']['muted'] else 'вкл.'), f'mute:{sid}')],
                [('Возобновить' if data['space']['status']=='paused' else 'Пауза', f"status:{sid}:{'resume' if data['space']['status']=='paused' else 'pause'}")],
                [('История изменений', f'history:{sid}')], [('Архивировать список', f'archiveask:{sid}')], [('Назад', f'open:{sid}')]]
        return text, rows

    def day_panel(self, did, uid, *, notification=False):
        data = self.store.day_view(did, uid)
        day, space = data['day'], data['space']
        opts = json.loads(space['settings'])
        assigned = [a for a in data['assignments'] if a['state'] == 'assigned']
        if (notification and day['date'] == clock().date().isoformat()
                and day['status'] == 'assigned' and not data['offers']
                and len(assigned) == 2 and any(a['uid'] == uid for a in assigned)):
            partner = next(a for a in assigned if a['uid'] != uid)
            text = f"Ты сегодня дежуришь.\nС тобой в паре — {h(partner['name'])}."
            if opts['place']:
                text += '\n\nМесто: ' + h(opts['place'])
            if opts['tasks']:
                text += '\n\nЧто нужно сделать: ' + h(opts['tasks'])
            text += ('\n\nОтвечать на это сообщение не нужно. '
                     'Если не можешь отдежурить — нажми «Сегодня не могу».')
            return text, [[('Сегодня не могу', f'decline:{did}')]]
        if notification and day['date'] == clock().date().isoformat() and data['access']['role'] == 'owner':
            rows = [[('Открыть дежурство', f'day:{did}')]]
            if day['status'] == 'no_duty':
                return 'Сегодня дежурства нет.', rows
            if day['status'] == 'assigned' and len(assigned) == 2 and not data['offers']:
                text = 'Сегодня дежурят:\n' + '\n'.join('• ' + h(a['name']) for a in assigned)
                if day['source_state'] not in ('available', 'no_lessons'):
                    text += '\n\nНе удалось проверить расписание занятий. Дежурство назначено по выбранным дням недели.'
                return text, rows
        labels = {'assigned':'дежурит', 'waiting':'ждём ответа на замену', 'reported':'отдежурил, ждём подтверждения старосты',
                  'done':'отдежурил', 'missed':'не отдежурил', 'excused':'освобождён', 'declined':'сегодня не может', 'released':'дежурство перенесено', 'cancelled':'отменено'}
        text = f"<b>Дежурства {h(space['group_name'])} · {date_label(day['date'])}</b>\n\n"
        if day['status'] == 'no_duty':
            text += 'Сегодня дежурства нет.\n'
        elif day['status'] == 'cancelled':
            text += 'Дежурство отменено старостой.\n'
        elif day['status'] == 'unfilled':
            text += '⚠️ Дежурных не хватает. Старосте нужно договориться о замене.\n'
        elif day['status'] == 'searching':
            text += 'Ищем замену. Ждём ответов участников.\n'
        visible = [a for a in data['assignments'] if a['state'] not in ('declined','released')]
        visible += [a for a in data['assignments'] if a['state'] in ('declined','released')][-4:]
        for item in visible[:12]:
            text += f"• {h(item['name'])} — {labels[item['state']]}\n"
        if len(data['assignments']) > len(visible[:12]):
            text += f"Ещё переносов: {len(data['assignments']) - len(visible[:12])}.\n"
        if opts['place']:
            text += '\nМесто: ' + h(opts['place'])
        if opts['tasks']:
            text += '\nОбязанности: ' + h(opts['tasks'])
        if day['source_state'] not in ('available', 'no_lessons') and day['status'] != 'no_duty':
            text += '\n\nНе удалось проверить расписание занятий. Дежурство назначено по дням, которые выбрал староста.'
        rows = []
        mine = next((a for a in data['assignments'] if a['uid'] == uid and a['state'] in ('assigned','waiting')), None)
        if mine and day['date'] == clock().date().isoformat():
            rows.append([('Сегодня не могу', f'decline:{did}')])
        with self.store.connect() as db:
            my_member = db.execute('SELECT id FROM members WHERE space=? AND uid=?', (space['id'], uid)).fetchone()
        if my_member and my_member['id'] in json.loads(day['excluded']) and day['date'] == clock().date().isoformat():
            rows.append([('Всё-таки могу', f'reconsider:{did}')])
        if mine and mine['state'] == 'assigned' and clock().timestamp() >= day['cutoff']:
            rows.append([('Я отдежурил(а)', f'done:{did}')])
        for offer in data['offers']:
            if uid in (offer['host_uid'], offer['guest_uid']):
                rows.append([('Ответить на предложение', f'offer:{offer["id"]}')])
        if data['access']['role'] != 'member':
            if any(a['state'] in ('assigned','reported') for a in data['assignments']) and clock().timestamp() >= day['cutoff']:
                rows.append([('Все дежурные отдежурили', f'finishask:{did}')])
            rows.append([('Отметить каждого отдельно', f'marks:{did}')])
            if day['date'] == clock().date().isoformat():
                rows.append([('Назначить пару вручную', f'manual:{did}')])
            if day['status'] not in ('cancelled','no_duty','closed'):
                rows.append([('Отменить дежурство', f'canceldayask:{did}')])
        rows.append([('Обновить', f'day:{did}')])
        rows.append([('Открыть список', f'open:{space["id"]}')])
        return text, rows

    def offer_panel(self, oid, uid):
        data = self.store.offer_view(oid, uid)
        offer = data['offer']
        if offer['state'] != 'pending' or clock().timestamp() >= offer['expires']:
            return 'На это предложение уже нельзя ответить. Посмотрите, кто сейчас дежурит.', [[('Открыть дежурство', f'day:{data["day"]["id"]}')]]
        host = uid == data['host']['uid']
        other = data['guest'] if host else data['host']
        if offer['kind'] == 'reserve':
            text = f"Напарник сегодня не дежурит. Продежуришь с запасным — {h(other['name'])}?" if host else \
                   f"Нужна подмена. Продежуришь сегодня с {h(other['name'])}?"
        else:
            text = f"Предыдущая пара сегодня не дежурит. Сможешь подменить её со своим напарником — {h(other['name'])}?"
        deadline = datetime.fromtimestamp(offer['expires'], MSK).strftime('%H:%M')
        text = f"<b>Дежурства {h(data['space']['group_name'])}</b>\n\n" + text
        text += f'\n\nОтветь до {deadline} МСК. Замену подтвердим, когда вы оба согласитесь. Постоянные пары не меняются.'
        already = offer['host_yes'] if host else offer['guest_yes']
        if already:
            text += '\n\nТвой ответ принят. Осталось дождаться напарника.'
        rows = [] if already else [[('Да, могу', f'yes:{oid}')]]
        rows += [[('Нет, перенести пару' if host or offer['kind']=='pair' else 'Сегодня не могу', f'no:{oid}')],
                 [('Открыть список', f'open:{data["space"]["id"]}')]]
        return text, rows

    async def input_text(self, message):
        uid = message.from_user.id
        session = self.store.session(uid)
        if not session:
            return
        step, data = session
        text = (message.text or '').strip()
        if not text:
            await message.answer('Нужен текст сообщения. Чтобы отменить ввод: /cancel')
            return
        try:
            if step == 'setup_group':
                data['group'] = self.normalize_group(text)
                self.store.set_session(uid, 'setup_pairs', data)
            elif step in ('setup_pairs', 'setup_reserves', 'addpairs', 'addreserves', 'roster_clarify'):
                await self.accept_roster(message, session, text)
                return
            elif step == 'join_name':
                self.store.claim(data['token'], uid, text)
                self.store.set_session(uid)
                await self.send(message, 'Заявка отправлена старосте. После подтверждения список появится в /duty.', [[('Мои дежурства', 'home')]])
                return
            elif step == 'setting_input':
                key, sid = data['key'], data['sid']
                value = int(text) if key == 'wait_minutes' else '' if text == '-' else text
                changes = {key: value}
                if key == 'time':
                    parsed = datetime.strptime(text, '%H:%M')
                    if parsed.hour >= 22:
                        raise DutyError('Для подбора и итогов нужно время в тот же день. Выберите время до 22:00.')
                    opts = json.loads(self.store.view(sid, uid)['space']['settings'])
                    changes['time'] = parsed.strftime('%H:%M')
                    if changes['time'] >= opts['cutoff']:
                        changes['cutoff'] = (parsed + timedelta(minutes=30)).strftime('%H:%M')
                    if changes.get('cutoff', opts['cutoff']) >= opts['report']:
                        changes['report'] = (parsed + timedelta(hours=1)).strftime('%H:%M')
                self.store.update_settings(sid, uid, changes)
                self.store.set_session(uid)
                await self.send(message, *self.options_panel(sid, uid))
                return
            elif step in ('rename', 'exempt'):
                mid, sid = data['mid'], data['sid']
                value = text if step == 'rename' else datetime.strptime(text, '%d.%m.%Y').date().isoformat()
                if step == 'exempt' and value < clock().date().isoformat():
                    raise DutyError('Укажите сегодняшнюю или будущую дату.')
                self.store.edit_member(mid, uid, 'name' if step == 'rename' else 'exempt_until', value)
                self.store.set_session(uid)
                await self.send(message, *self.member_panel(mid, uid))
                return
            else:
                await message.answer('На этом шаге выберите кнопку под сообщением. Вернуться: /duty')
                return
            await self.show_setup(message, uid)
        except DutyError as exc:
            if self.store.session(uid) != session:
                return
            if step == 'roster_clarify':
                try:
                    prompt, rows = self.clarification_panel(uid, data)
                except DutyError:
                    await message.answer(h(str(exc)) + '\n\nОтмена: /cancel')
                else:
                    await self.send(message, h(str(exc)) + '\n\n' + prompt, rows)
            else:
                await message.answer(h(str(exc)) + '\n\nПопробуйте ещё раз или /cancel.')
        except (ValueError, TypeError):
            await message.answer('Не получилось прочитать ответ. Пришлите его в формате из подсказки выше. Отмена: /cancel')

    async def callback(self, callback, state=None):
        if not callback.message or callback.message.chat.type != 'private' or callback.message.chat.id != callback.from_user.id:
            await callback.answer('Откройте /duty в личном чате с ботом.', show_alert=True)
            return
        uid = callback.from_user.id
        await callback.answer()
        if state:
            await state.clear()
        try:
            parts = (callback.data or '').split(':')[1:]
            action, args = parts[0], parts[1:]
            if action == 'home':
                self.store.set_session(uid)
                panel = self.home(uid)
            elif action == 'help':
                panel = ('<b>Как работают дежурства</b>\n\n'
                    'Староста добавляет пары и запасных. Если бот нашёл всех участников и у всех выбрана его группа, '
                    'он подключит их сам и сообщит об этом. Иначе нужны приглашения и подтверждение заявок. '
                    'Кто не подключился, остаётся в списке — его дежурства староста отмечает вручную.\n\n'
                    'В 08:00 бот сообщает, кто дежурит. Время и получателей можно поменять в настройках списка. '
                    'Если дежурный не ответил, он по-прежнему дежурит.\n\n'
                    'По кнопке «Сегодня не могу» бот ищет замену. Напарник и запасной должны согласиться оба. '
                    'Не договорились — дежурство пары переносится, бот спрашивает следующую пару. '
                    'Если в настройках выбран перенос без запасных, бот сразу перейдёт к следующей паре.\n\n'
                    'Староста отмечает, кто отдежурил. Подмена считается полноценным дежурством для обоих. '
                    'Постоянные пары не меняются. Бот не отмечает пропуск по одному только молчанию.\n\n'
                    'Можно договориться о подмене самим и назначить её в разделе «Сегодняшнее дежурство».', [[('Назад', 'home')]])
            elif action == 'create':
                existing = next((s for s in self.store.lists(uid) if s['role'] == 'owner'), None)
                if existing:
                    self.store.set_session(uid)
                    await self.send(callback, *self.panel(existing['id'], uid))
                    return
                group = self.group_hint(uid)
                try:
                    group = self.normalize_group(group or '')
                except DutyError:
                    group = None
                self.store.set_session(uid, 'setup_pairs' if group else 'setup_group', {'group': group, 'options': DEFAULTS.copy()})
                await self.show_setup(callback, uid)
                return
            elif action.startswith('w') and action not in ('week', 'weekday'):
                await self.setup_callback(callback, uid, action, args)
                return
            elif action == 'rclarify':
                session = self.store.session(uid)
                if not session or session[0] != 'setup_preview' or args != [session[1].get('auto_token')] or not session[1].get('auto_issue'):
                    raise DutyError('Этот вопрос уже закрыт. Продолжить настройку: /duty')
                data = session[1]
                draft = dict(origin='setup_preview', base=deepcopy(data), pairs=deepcopy(data['pairs']),
                             reserves=deepcopy(data.get('reserves', [])), **data['auto_issue'], token=secrets.token_hex(6),
                             question='Не удалось проверить аккаунт и его группу. Если аккаунт указан верно, '
                                      'попросите человека выбрать вашу группу в боте.')
                self.store.set_session(uid, 'roster_clarify', draft)
                await self.show_roster_input(callback, uid)
                return
            elif action == 'rreset':
                session = self.store.session(uid)
                if not session or session[0] != 'roster_clarify' or args != [session[1]['token']]:
                    raise DutyError('Этот вопрос уже закрыт. Продолжить настройку: /duty')
                data = session[1]
                self.validate_draft(uid, data)
                self.store.set_session(uid, 'setup_pairs' if data['origin'] == 'setup_preview' else data['origin'], data['base'])
                await self.show_roster_input(callback, uid)
                return
            elif action == 'join':
                self.store.claim(args[0], uid, '')
                panel = ('Заявка отправлена старосте. После подтверждения список появится в /duty.', [[('Мои дежурства', 'home')]])
            elif action == 'open':
                self.store.set_session(uid)
                panel = self.panel(int(args[0]), uid)
            elif action == 'roster':
                panel = self.roster(int(args[0]), uid, int(args[1]))
            elif action == 'member':
                panel = self.member_panel(int(args[0]), uid)
            elif action == 'invite':
                sid, mid = map(int, args)
                token = self.store.invite(sid, uid, mid or None)
                url = f'https://t.me/{self.bot_username}?start=duty_{token}'
                panel = ('<b>Приглашение готово</b>\n\n' + ('Отправьте эту ссылку нужному человеку.' if mid else 'Отправьте ссылку в чат группы. Каждый укажет имя, @username или свой ID из списка дежурств, вы подтвердите запись.') +
                    '\n\n' + h(url) + '\n\nСсылка действует 7 дней. Если создадите новое приглашение сюда же, это перестанет работать.', [[('Назад', f'open:{sid}')]])
            elif action == 'claims':
                panel = self.claims_panel(int(args[0]), uid)
            elif action == 'claim':
                panel = self.claim_panel(int(args[0]), uid)
            elif action == 'approve':
                cid, mid = map(int, args)
                with self.store.connect() as db:
                    row = db.execute('SELECT space FROM claims WHERE id=?', (cid,)).fetchone()
                    if not row:
                        raise DutyError('Заявка не найдена.')
                    sid = row['space']
                self.store.resolve_claim(cid, uid, mid or None)
                panel = self.claims_panel(sid, uid)
            elif action == 'options':
                panel = self.options_panel(int(args[0]), uid)
            elif action == 'opt':
                sid, key = int(args[0]), args[1]
                opts = json.loads(self.store.view(sid, uid)['space']['settings'])
                if key == 'audience':
                    choices = ['all','assigned','off']
                    value = choices[(choices.index(opts[key])+1)%3]
                elif key == 'mode':
                    value = 'pair' if opts[key] == 'reserve' else 'reserve'
                elif key in ('owner_daily','requests','owner_events'):
                    value = not opts[key]
                else:
                    raise DutyError('Настройка не найдена.')
                self.store.update_settings(sid, uid, {key: value})
                panel = self.options_panel(sid, uid)
            elif action in ('week','weekday'):
                sid = int(args[0])
                data = self.store.view(sid, uid)
                if data['access']['role'] == 'member':
                    raise DutyError('Настройка доступна старосте.')
                opts = json.loads(data['space']['settings'])
                if action == 'weekday':
                    d = int(args[1])
                    days = set(opts['days'])
                    days.symmetric_difference_update({d})
                    self.store.update_settings(sid, uid, {'days': sorted(days)})
                    opts['days'] = sorted(days)
                panel = ('<b>Дни дежурства</b>\n\nНажмите, чтобы включить или выключить день. '
                    'Если в расписании на этот день нет пар, дежурства не будет.',
                    [[(toggle(i in opts['days'])+' '+label, f'weekday:{sid}:{i}')] for i, label in enumerate(['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'])] + [[('Назад', f'options:{sid}')]])
            elif action == 'input':
                sid, key = int(args[0]), args[1]
                self.options_panel(sid, uid)
                prompts = {'time':'Во сколько сообщать о дежурных? Например 08:00. Если к этому времени поиск замен уже заканчивается, бот продлит его до получаса после рассылки.',
                           'cutoff':'До скольки искать замену? Например 08:30. После этого договориться о подмене сможет только староста.',
                           'report':'Во сколько напомнить старосте отметить, кто отдежурил? Например 16:00.',
                           'wait_minutes':'Сколько минут ждать ответа на предложение? От 2 до 30.',
                           'place':'Где дежурят? До 120 символов. Отправьте - чтобы убрать место.',
                           'tasks':'Что нужно сделать? До 500 символов. Отправьте - чтобы убрать обязанности.'}
                if key not in prompts:
                    raise DutyError('Настройка не найдена.')
                self.store.set_session(uid, 'setting_input', {'sid': sid, 'key': key})
                note = '\n\nВремя московское. Новое время действует для следующих дежурств.' if key in ('time','cutoff','report') else ''
                panel = (prompts[key]+note, [[('Отмена', f'open:{sid}')]])
            elif action in ('rename', 'exempt', 'unexempt','unlinkask','unlink'):
                mid = int(args[0])
                self.member_panel(mid, uid)
                with self.store.connect() as db:
                    m = self.store._member(db, mid)
                if action in ('rename','exempt'):
                    self.store.set_session(uid, action, {'mid': mid, 'sid': m['space']})
                    panel = ('Пришлите новое имя.' if action == 'rename' else 'До какой даты включительно освободить? Например 25.10.2026.', [[('Отмена', f'open:{m["space"]}')]])
                elif action == 'unlinkask':
                    panel = ('Отключить личные уведомления и доступ этого человека к списку? Он останется в своей паре, прошлые дежурства сохранятся. Чтобы вернуться, понадобится новое приглашение.',
                             [[('Да, отвязать', f'unlink:{mid}')], [('Назад', f'member:{mid}')]])
                else:
                    self.store.edit_member(mid, uid, 'uid' if action == 'unlink' else 'exempt_until', None)
                    panel = self.member_panel(mid, uid)
            elif action in ('addpairs','addreserves'):
                sid = int(args[0])
                self.options_panel(sid, uid)
                self.store.set_session(uid, action, {'sid': sid})
                await self.show_roster_input(callback, uid)
                return
            elif action in ('mute', 'silence'):
                sid = int(args[0])
                self.store.manage(sid, uid, action)
                panel = self.panel(sid, uid)
            elif action in ('leaveask','leave','archiveask','status'):
                sid = int(args[0])
                self.store.view(sid, uid)
                if action.endswith('ask'):
                    is_leave = action == 'leaveask'
                    panel = ('Отключить доступ к этому списку и личные уведомления? Запись в парах останется у старосты. '
                             'Если вас добавили по ошибке, сообщите ему об этом.' if is_leave else 'Архивировать список? Рассылки остановятся, история сохранится.',
                             [[('Выйти' if is_leave else 'Архивировать', f'leave:{sid}' if is_leave else f'status:{sid}:archive')], [('Назад', f'open:{sid}')]])
                else:
                    operation = 'leave' if action == 'leave' else args[1]
                    self.store.manage(sid, uid, operation)
                    panel = self.home(uid) if operation in ('leave','archive') else self.panel(sid, uid)
            elif action in ('role','transferask','transfer'):
                sid, target = map(int, args)
                with self.store.connect() as db:
                    self.store.permission(db, sid, uid, owner=True)
                if action == 'transferask':
                    panel = ('Передать этому участнику управление списком? Вы останетесь заместителем.',
                             [[('Передать управление', f'transfer:{sid}:{target}')], [('Отмена', f'open:{sid}')]])
                else:
                    self.store.manage(sid, uid, 'manager' if action=='role' else 'transfer', target)
                    panel = self.panel(sid, uid)
            elif action == 'day':
                panel = self.day_panel(int(args[0]), uid)
            elif action == 'decline':
                self.store.decline(int(args[0]), uid)
                panel = self.day_panel(int(args[0]), uid)
            elif action == 'reconsider':
                self.store.reconsider(int(args[0]), uid)
                panel = self.day_panel(int(args[0]), uid)
            elif action in ('canceldayask','cancelday'):
                did = int(args[0])
                data = self.store.day_view(did, uid)
                if data['access']['role'] == 'member':
                    raise DutyError('Отменить дежурство может староста.')
                if action == 'canceldayask':
                    panel = ('Отменить дежурство на этот день? Уже отмеченные дежурства останутся в учёте.',
                             [[('Отменить дежурство', f'cancelday:{did}')], [('Назад', f'day:{did}')]])
                else:
                    self.store.cancel_day(did, uid)
                    panel = self.day_panel(did, uid)
            elif action == 'offer':
                panel = self.offer_panel(int(args[0]), uid)
            elif action in ('yes','no'):
                self.store.reply_offer(int(args[0]), uid, action=='yes')
                panel = self.offer_panel(int(args[0]), uid)
            elif action == 'done':
                self.store.report_done(int(args[0]), uid)
                panel = self.day_panel(int(args[0]), uid)
            elif action == 'results':
                data = self.store.view(int(args[0]), uid)
                if data['access']['role'] == 'member':
                    raise DutyError('Итоги доступны старосте.')
                panel = ('<b>Кто отдежурил</b>\n\nВыберите день и отметьте результат. Если человек не ответил боту, это ещё не значит, что он не дежурил.',
                         [[(date_label(d['date']), f'day:{d["id"]}')] for d in data['days']] + [[('Назад', f'open:{args[0]}')]])
            elif action in ('finishask','finish'):
                did = int(args[0])
                data = self.store.day_view(did, uid)
                if data['access']['role'] == 'member':
                    raise DutyError('Подтверждать выполнение может староста.')
                if action == 'finishask':
                    names = ', '.join(h(a['name']) for a in data['assignments'] if a['state'] in ('assigned','reported'))
                    panel = (f'Все эти участники отдежурили?\n\n{names}', [[('Да, отдежурили', f'finish:{did}')], [('Назад', f'day:{did}')]])
                else:
                    self.store.finish(did, uid)
                    panel = self.day_panel(did, uid)
            elif action in ('marks','mark','markask','setmark'):
                panel = self.mark_action(uid, action, args)
            elif action in ('manual','manualask','manualset','backup','backuppick','backupask','backupset'):
                panel = self.manual_action(uid, action, args)
            elif action in ('swap','swapask','swapset'):
                panel = self.swap_action(uid, action, args)
            elif action == 'history':
                sid = int(args[0])
                events = self.store.history(sid, uid)
                translations = {'created':'Создан список','settings':'Изменены настройки','claim':'Заявка на вступление',
                    'claim_approved':'Подтверждён аккаунт','claim_rejected':'Заявка отклонена','day_created':'Создано дежурство',
                    'declined':'Отказ от сегодняшнего дежурства','offer_created':'Предложена замена','offer_yes':'Согласие на замену',
                    'offer_no':'Отказ от замены','done':'Выполнение принято','reported':'Сообщение о выполнении',
                    'missed':'Отмечено невыполнение','excused':'Отмечено освобождение','result_corrected':'Исправлен результат',
                    'unfilled':'Не найден полный состав','manual_pair_confirmed':'Пара назначена вручную','members_added':'Добавлены участники',
                    'pause':'Пауза','resume':'Возобновление','transfer':'Передано управление','manager':'Изменён заместитель',
                    'mute':'Изменены личные уведомления','silence':'Личные уведомления отключены',
                    'members_linked':'Участники подключены по группе','leave':'Выход из списка','member_name':'Изменено имя',
                    'member_exempt_until':'Изменено освобождение','member_uid':'Отвязан аккаунт',
                    'reconsidered':'Участник снова может дежурить','day_cancelled':'Дежурство отменено',
                    'manual_reserve_confirmed':'Подмена согласована старостой','partners_changed':'Изменены постоянные пары'}
                text = '<b>Последние изменения</b>\n\n' + '\n'.join(
                    datetime.fromtimestamp(e['created'], MSK).strftime('%d.%m %H:%M')+' · '+translations.get(e['action'],'Изменение списка')+
                    (' · '+h(e['actor_name'][:28]) if e['actor_name'] else '') for e in events)
                panel = text, [[('Назад', f'open:{sid}')]]
            else:
                raise DutyError('Кнопка устарела. Откройте /duty.')
            await self.send(callback, *panel)
        except DutyError as exc:
            await callback.message.answer(h(str(exc)), reply_markup=keyboard([[('Мои дежурства', 'home')]]))
        except (ValueError, IndexError, KeyError, TypeError):
            await callback.message.answer('Кнопка устарела. Откройте /duty.')

    async def setup_callback(self, callback, uid, action, args):
        session = self.store.session(uid)
        if not session or not session[0].startswith('setup_'):
            raise DutyError('Эта настройка уже закрыта. Откройте /duty.')
        step, data = session
        if action == 'wgroup':
            step = 'setup_group'
        elif action == 'wpairs':
            step = 'setup_pairs'
        elif action == 'wreserves' and data.get('pairs'):
            step = 'setup_reserves'
        elif action in ('woptions','wskip') and data.get('pairs'):
            if action == 'wskip':
                data['reserves'] = []
            step = 'setup_options'
        elif action == 'wopt' and step == 'setup_options':
            key = args[0]
            opts = settings(data.get('options'))
            if key == 'audience':
                values = ['all','assigned','off']
                opts[key] = values[(values.index(opts[key])+1)%3]
            elif key in ('owner_daily','requests','owner_events'):
                opts[key] = not opts[key]
            else:
                raise DutyError('Настройка не найдена.')
            data['options'] = opts
        elif action == 'wpreview' and step == 'setup_options':
            validate_roster(data['pairs'], data.get('reserves', []))
            step = 'setup_preview'
        elif action == 'wpublish' and step == 'setup_preview':
            plan = await self.enrollment_plan(callback.bot, uid, data)
            if self.store.session(uid) != session:
                return
            if 'auto_preview' not in data or data['auto_preview'] != plan.ids:
                await callback.message.answer('Данные участников изменились. Посмотрите, как они будут подключены, и подтвердите создание ещё раз.')
                await self.show_setup(callback, uid)
                return
            auto = plan.ids is not None
            sid = self.store.create(uid, data['group'], plan.pairs if auto else data['pairs'],
                                    plan.reserves if auto else data.get('reserves', []), data.get('options'), auto_link=auto)
            await self.send(callback, *self.panel(sid, uid))
            return
        else:
            raise DutyError('Этот шаг уже пройден. Откройте /duty, чтобы продолжить.')
        self.store.set_session(uid, step, data)
        await self.show_setup(callback, uid)

    def claims_panel(self, sid, uid):
        data = self.store.view(sid, uid)
        if data['access']['role'] == 'member':
            raise DutyError('Заявки доступны старосте.')
        rows = [[(c['name'][:45], f'claim:{c["id"]}')] for c in data['claims'][:30]]
        return ('<b>Заявки участников</b>\n\n' + ('Выберите заявку и проверьте человека.' if rows else 'Новых заявок нет.'), rows + [[('Назад', f'open:{sid}')]])

    def claim_panel(self, cid, uid):
        with self.store.connect() as db:
            claim = db.execute('SELECT c.*,i.name profile_name,i.username profile_username,i.group_hint FROM claims c '
                'LEFT JOIN identities i ON i.uid=c.uid WHERE c.id=?', (cid,)).fetchone()
            if not claim:
                raise DutyError('Заявка не найдена.')
            self.store.permission(db, claim['space'], uid, manage=True)
            if claim['status'] != 'pending':
                raise DutyError('Заявка уже обработана.')
            candidates = [dict(r) for r in db.execute('SELECT * FROM members WHERE space=? AND uid IS NULL AND active=1', (claim['space'],))]
        candidates = [m for m in candidates if m['id'] == claim['member'] and
                      (m.get('telegram_id') is None or m['telegram_id'] == claim['uid'])] if claim['member'] else \
                     [m for m in candidates if matches_member(m, claim['name'], claim['uid'])]
        text = f"<b>Заявка: {h(claim['name'])}</b>\n\nTelegram: {h(claim['profile_name'] or 'неизвестно')}"
        if claim['profile_username']:
            text += ' · @' + h(claim['profile_username'])
        if claim['group_hint']:
            text += '\nГруппа, которую человек выбрал в боте: ' + h(claim['group_hint'])
        if not candidates:
            text += '\n\nНе нашёл подходящую свободную запись. Проверьте имя, username и ID в списке, затем отправьте человеку личное приглашение.'
        rows = [[('Подтвердить: '+m['name'][:35], f'approve:{cid}:{m["id"]}')] for m in candidates]
        rows += [[('Отклонить', f'approve:{cid}:0')], [('Назад', f'claims:{claim["space"]}')]]
        return text, rows

    def mark_action(self, uid, action, args):
        did = int(args[0])
        data = self.store.day_view(did, uid)
        if data['access']['role'] == 'member':
            raise DutyError('Результаты подтверждает староста.')
        available = [a for a in data['assignments'] if a['state'] in ('assigned','reported','done','missed','excused')]
        if action == 'marks':
            return '<b>Кто отдежурил</b>\n\nВыберите человека, чтобы отметить или исправить результат. Изменения останутся в истории.', \
                [[(a['name'][:45], f'mark:{did}:{a["member"]}')] for a in available] + [[('Назад', f'day:{did}')]]
        mid = int(args[1])
        assignment = next((a for a in available if a['member'] == mid), None)
        if not assignment:
            raise DutyError('Назначение не найдено.')
        if action == 'mark':
            return f"<b>{h(assignment['name'])}</b>\n\nКак завершилось дежурство?", [
                [('Отдежурил(а)', f'markask:{did}:{mid}:done')], [('Не отдежурил(а)', f'markask:{did}:{mid}:missed')],
                [('Освобождён(а)', f'markask:{did}:{mid}:excused')], [('Назад', f'marks:{did}')]]
        outcome = args[2]
        labels = {'done':'отдежурил(а)','missed':'не отдежурил(а)','excused':'освобождён(а)'}
        if outcome not in labels:
            raise DutyError('Результат не найден.')
        if action == 'markask':
            return f"Сохранить для {h(assignment['name'])}: {labels[outcome]}?", [
                [('Сохранить', f'setmark:{did}:{mid}:{outcome}')], [('Назад', f'mark:{did}:{mid}')]]
        if assignment['state'] in ('done','missed','excused'):
            self.store.correct_result(did, mid, uid, outcome)
        else:
            self.store.finish(did, uid, mid, outcome)
        return self.day_panel(did, uid)

    def manual_action(self, uid, action, args):
        did = int(args[0])
        data = self.store.day_view(did, uid)
        if data['access']['role'] == 'member':
            raise DutyError('Назначать вручную может староста.')
        sid = data['space']['id']
        members = self.store.view(sid, uid)['members']
        pairs = {}
        for m in members:
            if m['pair']:
                pairs.setdefault(m['pair'], []).append(m)
        if action == 'manual':
            rows = [[((' + '.join(m['name'] for m in people))[:55], f'manualask:{did}:{pid}')]
                    for pid, people in pairs.items() if len(people) == 2]
            return 'Договоритесь с обоими участниками и выберите их пару.', rows + [
                [('Назначить с запасным', f'backup:{did}')], [('Назад', f'day:{did}')]]
        if action == 'backup':
            return 'Кто дежурит с запасным? Сначала получите согласие обоих.', [
                [(m['name'][:45], f'backuppick:{did}:{m["id"]}')] for m in members if m['pair'] and not m['credit']] + [[('Назад', f'manual:{did}')]]
        if action == 'backuppick':
            return 'Выберите согласившегося запасного.', [
                [(m['name'][:45], f'backupask:{did}:{args[1]}:{m["id"]}')] for m in members if not m['pair']] + [[('Назад', f'backup:{did}')]]
        if action in ('backupask','backupset'):
            host, guest = int(args[1]), int(args[2])
            if action == 'backupask':
                names = [h(m['name']) for m in members if m['id'] in (host,guest)]
                if len(names) != 2:
                    raise DutyError('Участники не найдены.')
                return 'Оба согласны дежурить сегодня: '+' + '.join(names)+'?', [
                    [('Оба согласны · Назначить', f'backupset:{did}:{host}:{guest}')], [('Назад', f'manual:{did}')]]
            self.store.manual_reserve(did, uid, host, guest)
            return self.day_panel(did, uid)
        pid = int(args[1])
        if pid not in pairs:
            raise DutyError('Пара не найдена.')
        if action == 'manualask':
            names = ' + '.join(h(m['name']) for m in pairs[pid])
            return f'Назначить на сегодня {names}?\n\nОба должны согласиться с вами заранее.', [
                [('Оба согласны · Назначить', f'manualset:{did}:{pid}')], [('Назад', f'manual:{did}')]]
        self.store.manual_pair(did, uid, pid)
        return self.day_panel(did, uid)

    def swap_action(self, uid, action, args):
        first = int(args[0])
        self.member_panel(first, uid)
        with self.store.connect() as db:
            member = self.store._member(db, first)
        members = self.store.view(member['space'], uid)['members']
        if action == 'swap':
            return f"С кем поменять местами {h(member['name'])}? Меняются две постоянные пары. История выполнения сохраняется.", [
                [(m['name'][:45], f'swapask:{first}:{m["id"]}')] for m in members if m['pair'] and m['pair'] != member['pair']] + [
                [('Назад', f'member:{first}')]]
        second = int(args[1])
        other = next((m for m in members if m['id']==second and m['pair']), None)
        if not other:
            raise DutyError('Участник не найден.')
        if action == 'swapask':
            a_partner = next((m for m in members if m['pair']==member['pair'] and m['id']!=first), None)
            b_partner = next((m for m in members if m['pair']==other['pair'] and m['id']!=second), None)
            if not a_partner or not b_partner:
                raise DutyError('В паре не хватает участника.')
            text = '<b>Новые постоянные пары</b>\n\n' + h(other['name'])+' + '+h(a_partner['name'])+'\n'+h(member['name'])+' + '+h(b_partner['name'])
            return text, [[('Сохранить пары', f'swapset:{first}:{second}')], [('Отмена', f'member:{first}')]]
        self.store.swap_partners(first, second, uid)
        return self.roster(member['space'], uid)

    def register(self, dp):
        middleware = IdentityMiddleware(self)
        dp.message.outer_middleware(middleware)
        dp.callback_query.outer_middleware(middleware)
        dp.message.register(self.start_invite, CommandStart(deep_link=True, magic=F.args.startswith('duty_')))
        dp.message.register(self.command, Command('duty'))
        dp.message.register(self.cancel, Command('cancel'), lambda m: m.chat.type == 'private' and bool(self.store.session(m.from_user.id)))
        dp.callback_query.register(self.callback, F.data.startswith('du:'))
        dp.message.register(self.input_text, lambda m: m.chat.type == 'private' and bool(m.from_user)
            and not (m.text or '').startswith('/') and bool(self.store.session(m.from_user.id)))
