(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || new URLSearchParams(location.search).get('tgWebAppData') || '';
  const labels = { assigned: 'дежурит', waiting: 'ждём ответа', reported: 'ждёт подтверждения', done: 'отдежурил(а)',
    missed: 'не отдежурил(а)', excused: 'освобождён(а)', declined: 'сегодня не может', released: 'перенесено', cancelled: 'отменено' };
  let state = null, sid = null, tab = 'today', busy = false, dirty = false, generation = 0;
  let draft = { pairs: [['', '']], reserves: [], options: null }, preview = null, setupStep = 1;

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key === 'text') node.textContent = value;
      else if (key in node && !key.startsWith('aria')) node[key] = value;
      else node.setAttribute(key, value);
    }
    for (const child of children) if (child !== null && child !== undefined) node.append(child);
    return node;
  }
  const iconPaths = {
    clipboard: ['M9 5H6a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3', 'M9 3h6v4H9z', 'm8 14 2 2 5-5'],
    calendar: ['M8 2v4m8-4v4M3 10h18', 'M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z', 'M8 14h2m4 0h2m-8 4h2'],
    users: ['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87', 'M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Zm3-3.87a4 4 0 0 1 0 7.75'],
    user: ['M20 21v-2a7 7 0 0 0-14 0v2', 'M17 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z'],
    settings: ['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z', 'm10 2-.7 3-2.6 1.5-3-.8-2 3.5 2.2 2.1v3l-2.2 2.1 2 3.5 3-.8 2.6 1.5.7 3h4l.7-3 2.6-1.5 3 .8 2-3.5-2.2-2.1v-3l2.2-2.1-2-3.5-3 .8L14.7 5 14 2Z'],
    bell: ['M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9', 'M10 21h4'],
    swap: ['M3 7h16m-4-4 4 4-4 4M21 17H5m4-4-4 4 4 4'],
    check: ['m5 12 4 4L19 6'],
    close: ['m6 6 12 12M6 18 18 6'],
    plus: ['M12 5v14M5 12h14'],
    edit: ['m16 3 5 5-12 12-6 1 1-6Z', 'm14 5 5 5'],
    refresh: ['M20 7v5h-5', 'M20 12a8 8 0 1 0-2.34 5.66'],
    trash: ['M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7'],
    clock: ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z', 'M12 6v6l4 2'],
    pin: ['M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z', 'M15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z'],
    coffee: ['M3 8h14v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4ZM17 8h2a3 3 0 0 1 0 6h-2M6 2v2m4-2v2m4-2v2'],
    pause: ['M7 4h3v16H7zM14 4h3v16h-3z'],
    play: ['m7 4 14 8-14 8Z'],
    alert: ['m12 3 10 18H2Z', 'M12 9v5m0 3v.01'],
    back: ['m12 5-7 7 7 7M5 12h14'],
    forward: ['m12 5 7 7-7 7M5 12h14'],
    chevron: ['m9 5 7 7-7 7'],
    link: ['M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71', 'M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'],
    logout: ['M9 3H4v18h5m7-14 5 5-5 5M8 12h13'],
  };
  function icon(name) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false', class: 'duty-icon' })) svg.setAttribute(key, value);
    for (const path of iconPaths[name] || iconPaths.clipboard) { const node = document.createElementNS(ns, 'path'); node.setAttribute('d', path); svg.append(node); }
    return svg;
  }
  function labelIcon(text) {
    const matches = [['Добавить', 'plus'], ['Удалить', 'trash'], ['Убрать', 'trash'], ['Уведомлен', 'bell'], ['Мои уведомлен', 'bell'], ['Сохранить', 'check'], ['Провер', 'check'], ['Изменить', 'edit'], ['Поменять', 'swap'], ['После обмена', 'swap'], ['Запасн', 'swap'], ['Если дежурный', 'swap'], ['Пары', 'users'], ['Пара ', 'users'], ['Твоя пара', 'users'], ['Кто с кем', 'users'], ['График', 'calendar'], ['Следующее', 'calendar'], ['Твоя следующая', 'calendar'], ['Ближайшие', 'calendar'], ['Прошлые', 'check'], ['Итоги', 'check'], ['Все отдежурили', 'check'], ['Я отдежурил', 'check'], ['Отметить', 'check'], ['Назад', 'back'], ['Дальше', 'forward'], ['Настроить', 'settings'], ['Настройки', 'settings'], ['Время', 'clock'], ['Место', 'pin'], ['Обновить', 'refresh'], ['Приглашен', 'link'], ['Подключить', 'link'], ['Получить приглашен', 'link'], ['Скопировать', 'link'], ['Заявки', 'user'], ['Возобновить', 'play'], ['Вернуть', 'play'], ['Поставить на паузу', 'pause'], ['Управление', 'settings'], ['Последние', 'clock'], ['Выйти', 'logout'], ['Сегодня не могу', 'close'], ['Отменить', 'close'], ['Дежурства', 'clipboard'], ['Назначить', 'users'], ['Запустить', 'play']];
    return matches.find(([prefix]) => text.startsWith(prefix))?.[1];
  }
  const p = (text, className = '') => el('p', { text, className });
  const button = (text, handler, className = '') => el('button', { type: 'button', className: 'duty-button ' + className, onclick: handler }, [labelIcon(text) ? icon(labelIcon(text)) : null, el('span', { text })]);
  const actions = (...children) => el('div', { className: 'duty-actions' }, children);
  const card = (title, ...children) => el('section', { className: 'duty-card' }, [el('h2', {}, [labelIcon(title) ? icon(labelIcon(title)) : null, el('span', { text: title })]), ...children]);
  const dateLabel = iso => new Date(iso + 'T12:00:00+03:00').toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
  const today = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow' }).format(new Date());
  const admin = () => state && state.role !== 'member';
  const owner = () => state && state.role === 'owner';
  const person = id => state.members.find(m => m.id === id);
  const partner = m => m.pair && state.members.find(x => x.pair === m.pair && x.id !== m.id && x.active);
  let statusTimer;
  const status = (text, error = false) => {
    clearTimeout(statusTimer); $('dutyStatus').textContent = text; $('dutyStatus').dataset.error = String(error);
    if (text === 'Сохранено' || text === 'Ссылка скопирована') statusTimer = setTimeout(() => { $('dutyStatus').textContent = ''; }, 2500);
  };
  function defaults() { return { time: '08:40', cutoff: '09:10', report: '16:00', days: [0, 1, 2, 3, 4, 5], owner_daily: true,
    audience: 'all', requests: true, owner_events: true, mode: 'reserve', place: '', tasks: '', wait_minutes: 10 }; }
  function field(text, input, hint) { if (!input.hasAttribute('aria-label')) input.setAttribute('aria-label', text); return el('label', { className: 'duty-label' }, [el('span', { className: 'duty-field-title', text }), input, hint ? el('span', { className: 'duty-help', text: hint }) : null]); }
  function details(title, ...children) {
    const arrow = icon('chevron'); arrow.classList.add('duty-chevron');
    return el('details', { className: 'duty-details' }, [el('summary', {}, [el('span', { text: title }), arrow]), el('div', { className: 'duty-details-body' }, children)]);
  }
  function input(value, props = {}) { return el('input', { value: value == null ? '' : value, ...props }); }
  function select(options, value) {
    const node = el('select', {}, options.map(([id, name]) => el('option', { value: String(id), text: name })));
    node.value = String(value); return node;
  }
  function check(label, checked, onChange, hint) {
    return el('label', { className: 'duty-row' }, [el('span', { className: 'duty-row-copy' }, [
      el('strong', { text: label }), hint ? el('small', { text: hint }) : null]), input('', { type: 'checkbox', checked, onchange: event => onChange(event.target.checked) })]);
  }
  function dialog(title, ...children) {
    $('dutyDialogTitle').textContent = title; $('dutyDialogBody').replaceChildren(...children.filter(child => child != null));
    if (!$('dutyDialog').open) $('dutyDialog').showModal();
  }
  function closeDialog() { $('dutyDialog').close(); }
  function confirmAction(title, text, run, label = 'Подтвердить') {
    dialog(title, p(text), actions(button('Отмена', closeDialog), button(label, run, 'duty-danger')));
  }
  async function request(path = '', method = 'GET', body) {
    const response = await fetch('/api/duties' + path, { method, cache: 'no-store',
      headers: { 'X-Telegram-Init-Data': initData, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body) });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('Не удалось загрузить дежурства. Попробуйте ещё раз.'); }
    if (!response.ok) {
      if (response.status === 401) throw new Error('Откройте мини-апп заново из бота — нужно обновить вход.');
      throw new Error(typeof data.detail === 'string' ? data.detail : typeof data.error === 'string' ? data.error : 'Проверьте заполненные поля.');
    }
    return data;
  }
  async function perform(run, success = 'Сохранено', working = 'Сохраняем…') {
    if (busy) return;
    const controls = [...document.querySelectorAll('.duty-page button,.duty-dialog button,.duty-page input,.duty-page select,.duty-page textarea,.duty-dialog input,.duty-dialog select,.duty-dialog textarea')].map(node => [node, node.disabled]);
    busy = true; controls.forEach(([node]) => { node.disabled = true; });
    status(working);
    try { await run(); status(success); }
    catch (error) {
      if ($('dutyDialog').open) {
        status('');
        $('dutyDialogBody').querySelector('.duty-error')?.remove();
        $('dutyDialogBody').append(p(error.message, 'duty-error'));
      } else status(error.message, true);
    } finally {
      busy = false; controls.forEach(([node, disabled]) => { if (node.isConnected) node.disabled = disabled; });
    }
  }
  async function mutate(payload) {
    if (dirty && !window.confirm('Есть несохранённые настройки. Продолжить без сохранения?')) return;
    await perform(async () => {
      await request('/' + sid + '/actions', 'POST', payload);
      closeDialog(); dirty = false; await load();
    });
  }
  async function load() {
    const version = ++generation;
    $('dutyContent').setAttribute('aria-busy', 'true');
    $('dutySkeleton').hidden = Boolean(state);
    try {
      const result = await request();
      if (version !== generation) return;
      $('dutyGroup').textContent = result.group;
      const spaces = result.spaces;
      if (!spaces.some(s => s.id === sid)) sid = spaces[0]?.id || null;
      $('dutyLists').hidden = spaces.length < 2;
      if (spaces.length > 1) {
        const chooser = select(spaces.map(s => [s.id, s.group + (s.status === 'paused' ? ' · пауза' : '')]), sid);
        chooser.setAttribute('aria-label', 'Выбрать дежурства');
        chooser.addEventListener('change', async () => { sid = Number(chooser.value); await refresh(); });
        $('dutyLists').replaceChildren(chooser);
      }
      if (sid) {
        const data = await request('/' + sid);
        if (version !== generation) return;
        state = data; $('dutyGroup').textContent = data.group;
      } else state = null;
      $('dutyTabs').hidden = !state;
      render();
    } finally {
      if (version === generation) {
        $('dutySkeleton').hidden = true;
        $('dutyContent').setAttribute('aria-busy', 'false');
      }
    }
  }
  async function refresh() {
    if (dirty && !window.confirm('Есть несохранённые изменения. Обновить страницу?')) return;
    dirty = false; status(state ? 'Обновляем…' : '');
    try { await load(); status(''); } catch (error) {
      status(error.message, true);
      if (!state) $('dutyContent').replaceChildren(card('Не удалось загрузить дежурства', button('Попробовать ещё раз', refresh, 'duty-primary duty-wide')));
    }
  }
  function render() {
    document.querySelectorAll('[data-tab]').forEach(b => {
      if (b.dataset.tab === tab) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    const content = $('dutyContent'); content.replaceChildren();
    if (!state) {
      content.append(card('Дежурства вашей группы', p('Добавьте пары и запасных.'),
        button('Настроить дежурства', () => perform(async () => { await fetchPeople(null); setupStep = 1; showSetup(); }, '', 'Загружаем список группы…'), 'duty-primary duty-wide')));
      return;
    }
    if (state.status === 'paused' && tab !== 'today') content.append(card('Дежурства на паузе', admin() ? button('Возобновить', () => mutate({ action: 'resume' }), 'duty-primary') : null));
    ({ today: renderToday, calendar: renderCalendar, members: renderMembers, settings: renderSettings })[tab](content);
  }
  function renderToday(content) {
    const day = state.days.find(d => d.date === today());
    const projected = state.forecast.find(d => d.date === today());
    const active = day?.people.filter(m => ['assigned', 'reported', 'done'].includes(m.state)) || [];
    const mine = day?.people.find(m => m.mine);
    const off = day?.status === 'no_duty' || projected?.status === 'no_duty' || (!day && !state.options.days.includes((new Date(today() + 'T12:00:00+03:00').getUTCDay() + 6) % 7));
    let title, message, symbol = 'clipboard';
    if (state.status === 'paused') { title = 'Дежурства на паузе'; symbol = 'pause'; }
    else if (off) { title = 'Сегодня без дежурства'; symbol = 'coffee'; }
    else if (mine?.state === 'assigned') { title = 'Ты сегодня дежуришь'; message = 'С тобой — ' + (active.find(m => !m.mine)?.name || 'напарник уточняется'); }
    else if (mine?.state === 'reported') { title = 'Дежурство отмечено'; message = 'Ждём подтверждения старосты.'; symbol = 'check'; }
    else if (mine?.state === 'declined' || mine?.state === 'released') { title = 'Твоё дежурство перенесено'; symbol = 'calendar'; }
    else if (day?.status === 'searching') { title = 'Подбираем замену'; symbol = 'swap'; }
    else if (day?.status === 'unfilled') { title = 'Дежурные пока не назначены'; symbol = 'alert'; }
    else if (day?.status === 'cancelled') { title = 'Дежурство отменено'; symbol = 'coffee'; }
    else if (day?.status === 'closed') { title = 'Сегодня отдежурили'; symbol = 'check'; }
    else if (!day) { title = 'Дежурные ещё не назначены'; }
    else { title = 'Сегодня дежурят'; }
    const hero = el('section', { className: 'duty-card duty-hero' }, [
      el('div', { className: 'duty-hero-icon' }, [icon(symbol)]), el('h2', { text: title }), message ? p(message) : null]);
    if (active.length && !mine?.mine) hero.append(el('div', { className: 'duty-names' }, active.map(m => p(m.name))));
    if (mine?.state === 'assigned') {
      if (state.options.place) hero.append(p(state.options.place, 'duty-help'));
      if (state.options.tasks) hero.append(p(state.options.tasks));
      if (Date.now() / 1000 >= day.cutoff) hero.append(button('Я отдежурил(а)', () => mutate({ action: 'done', day: day.id }), 'duty-primary duty-wide'));
      hero.append(button('Сегодня не могу', () => confirmAction('Перенести дежурство?', 'Подберём замену на сегодня.', () => mutate({ action: 'decline', day: day.id }), 'Сегодня не могу'), 'duty-danger duty-wide'));
    }
    if (day && mine?.state === 'declined' && Date.now() / 1000 < day.cutoff) hero.append(button('Всё-таки могу', () => mutate({ action: 'reconsider', day: day.id })));
    if (admin() && day && !['no_duty', 'cancelled', 'closed'].includes(day.status)) {
      if (Date.now() / 1000 >= day.cutoff && active.some(m => ['assigned', 'reported'].includes(m.state))) hero.append(button('Подтвердить дежурство', () => markDialog(day), 'duty-primary duty-wide'));
      hero.append(button('Другие действия', () => dayActions(day), 'duty-quiet duty-wide'));
    }
    content.append(hero);
    for (const offer of day?.offers || []) content.append(card('Нужна подмена', p(offer.host + ' + ' + offer.guest),
      p(offer.answered ? 'Ждём ответа напарника.' : 'Сможешь продежурить в этой паре?'),
      p('Ответ до ' + new Date(offer.expires * 1000).toLocaleTimeString('ru-RU', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit' }) + ' МСК', 'duty-help'),
      offer.answered ? null : actions(button('Смогу', () => mutate({ action: 'offer_yes', offer: offer.id }), 'duty-primary'), button('Не могу', () => mutate({ action: 'offer_no', offer: offer.id }), 'duty-danger'))));
    const next = state.forecast.find(d => d.date > today() && d.status !== 'no_duty' && d.people.length && (admin() || d.people.some(m => m.mine)));
    if (next) content.append(card(admin() ? 'Следующее дежурство' : 'Твоя следующая очередь', p(dateLabel(next.date), 'duty-next-date'),
      ...next.people.map(m => p(m.name)), p('Предварительно', 'duty-help')));
    if (admin()) {
      const unresolved = state.days.filter(d => d.date < today() && d.people.some(m => ['assigned', 'reported'].includes(m.state)));
      if (unresolved.length) content.append(card('Незакрытые дежурства', ...unresolved.map(d => button(dateLabel(d.date), () => markDialog(d), 'duty-wide'))));
    }
  }
  function markDialog(day) {
    const candidates = day.people.filter(m => ['assigned', 'reported', 'done', 'missed', 'excused'].includes(m.state));
    const pending = candidates.filter(m => ['assigned', 'reported'].includes(m.state));
    const individual = details('Отметить по одному', ...candidates.map(m => {
        const outcome = select([['done', 'Отдежурил(а)'], ['missed', 'Не отдежурил(а)'], ['excused', 'Освобождён(а)']], ['done', 'missed', 'excused'].includes(m.state) ? m.state : 'done');
        outcome.setAttribute('aria-label', 'Итог: ' + m.name);
        return el('div', { className: 'duty-mark-row' }, [field(m.name, outcome),
          button('Применить', () => mutate({ action: ['done', 'missed', 'excused'].includes(m.state) ? 'correct' : 'finish', day: day.id, member: m.member, outcome: outcome.value }), 'duty-wide')]);
      }));
    if (!pending.length) individual.open = true;
    dialog('Итоги · ' + dateLabel(day.date), ...pending.map(m => p(m.name)),
      pending.length ? button('Все отдежурили', () => mutate({ action: 'finish', day: day.id }), 'duty-primary duty-wide') : null, individual);
  }
  function manualDialog(day) {
    const pairs = [...new Set(state.members.filter(m => m.active && m.pair).map(m => m.pair))];
    const choices = select(pairs.map(pid => { const members = state.members.filter(m => m.active && m.pair === pid); return [members[0].id, members.map(m => m.name).join(' + ')]; }), '');
    const host = select(state.members.filter(m => m.active && m.pair).map(m => [m.id, m.name]), '');
    const reserve = select(state.members.filter(m => m.active && !m.pair).map(m => [m.id, m.name]), '');
    dialog('Назначить вручную', p('Сначала договоритесь с участниками. Это назначение действует только на сегодня.', 'duty-help'),
      field('Целая пара', choices), button('Назначить пару', () => mutate({ action: 'manual_pair', day: day.id, member: Number(choices.value) }), 'duty-primary duty-wide'),
      field('Дежурный', host), field('Запасной', reserve), button('Назначить с запасным', () => mutate({ action: 'manual_reserve', day: day.id, member: Number(host.value), other: Number(reserve.value) }), 'duty-wide'));
  }
  function renderCalendar(content) {
    const list = card('Ближайшие четыре недели', p('График предварительный.', 'duty-help'));
    for (const d of state.forecast) {
      const date = new Date(d.date + 'T12:00:00+03:00');
      list.append(el('div', { className: 'duty-row' + (d.people.some(m => m.mine) ? ' duty-mine' : '') }, [
        el('div', { className: 'duty-date', text: date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) }, [el('small', { text: date.toLocaleDateString('ru-RU', { weekday: 'short' }) })]),
        el('div', { className: 'duty-row-copy' }, [el('strong', { text: d.status === 'no_duty' ? 'Без дежурства' : d.people.map(m => m.name).join(' + ') || 'Пока не назначено' }),
          el('small', { text: d.people.some(m => m.mine) ? 'Твоя очередь' : '' })]) ]));
    }
    content.append(list);
    const past = state.days.filter(d => d.date <= today() && d.status !== 'no_duty');
    if (past.length) content.append(card('Прошлые дежурства', ...past.map(d => el('div', { className: 'duty-row' }, [
      el('div', { className: 'duty-row-copy' }, [el('strong', { text: dateLabel(d.date) }),
        ...d.people.filter(m => !['released', 'declined', 'waiting', 'cancelled'].includes(m.state)).map(m => el('small', { text: m.name + ' — ' + (labels[m.state] || m.state) }))]),
      admin() ? button('Итоги', () => markDialog(d)) : null ]))));
  }
  function memberRow(m) {
    const label = m.name + (m.mine ? ' · ты' : '');
    if (!admin()) return el('div', { className: 'duty-person' }, [p(label)]);
    const action = button(label, () => memberDialog(m), 'duty-person-action');
    action.append(icon('chevron')); return action;
  }
  function renderMembers(content) {
    const active = state.members.filter(m => m.active);
    const pairIds = [...new Set(active.filter(m => m.pair).map(m => m.pair))];
    const pairs = card('Пары');
    pairIds.forEach((id, index) => {
      const members = active.filter(m => m.pair === id);
      const row = el('section', { className: 'duty-pair-view' }, [el('h3', { text: 'Пара ' + (index + 1) }),
        el('div', { className: 'duty-pair-names' }, members.map(m => p(m.name + (m.mine ? ' · ты' : ''))))]);
      if (admin()) row.append(button('Изменить', () => dialog('Пара ' + (index + 1), ...members.map(memberRow)), 'duty-quiet'));
      pairs.append(row);
    });
    content.append(pairs);
    const reserves = active.filter(m => !m.pair);
    content.append(card('Запасные', ...reserves.map(memberRow), reserves.length ? null : p('Запасных пока нет.', 'duty-help')));
    if (!admin()) return;
    content.append(actions(button('Добавить пару', () => appendDialog(false), 'duty-primary'), button('Добавить запасного', () => appendDialog(true))));
    const missing = active.filter(m => !m.linked);
    if (missing.length || state.claims.length) content.append(details('Подключение участников',
      ...missing.map(m => button(m.name, () => inviteDialog(m.id), 'duty-person-action')),
      ...state.claims.map(claim => {
        const chooser = select(state.members.filter(m => !m.linked).map(m => [m.id, m.name]), claim.member || '');
        return el('div', { className: 'duty-claim' }, [p(claim.name), field('Участник', chooser),
          actions(button('Подтвердить', () => mutate({ action: 'approve', claim: claim.id, member: Number(chooser.value) }), 'duty-primary'),
            button('Отклонить', () => mutate({ action: 'reject', claim: claim.id }), 'duty-danger'))]);
      }), missing.length ? button('Общее приглашение', () => inviteDialog(), 'duty-wide') : null));
    const inactive = state.members.filter(m => !m.active);
    if (inactive.length) content.append(details('Не участвуют', ...inactive.map(memberRow)));
  }
  function memberDialog(m) {
    const mate = partner(m);
    const name = input(m.name, { maxLength: 80, 'aria-label': 'Имя участника' });
    const exempt = input(m.exempt_until, { type: 'date', 'aria-label': 'Освободить до даты' });
    dialog(m.name, p(mate ? 'Напарник — ' + mate.name : m.pair ? 'Напарник пока не участвует.' : 'В запасных.', 'duty-help'),
      button('Поменять местами', () => swapDialog(m), 'duty-primary duty-wide'),
      details('Освобождение', field('До какой даты включительно', exempt), button('Сохранить', () => mutate({ action: 'exempt', member: m.id, value: exempt.value || null }), 'duty-wide'),
        m.exempt_until ? button('Снять освобождение', () => mutate({ action: 'exempt', member: m.id, value: null }), 'duty-wide') : null),
      !m.linked ? button('Подключить уведомления', () => inviteDialog(m.id), 'duty-wide') : null,
      details('Другие действия', field('Имя в списке', name), button('Сохранить имя', () => mutate({ action: 'rename', member: m.id, value: name.value }), 'duty-wide'),
        owner() && m.role && !m.mine ? button(m.role === 'manager' ? 'Убрать заместителя' : 'Назначить заместителем', () => mutate({ action: 'manager', member: m.id }), 'duty-wide') : null,
        owner() && m.role && !m.mine ? button('Передать управление', () => confirmAction('Передать управление?', m.name + ' станет админом дежурств.', () => mutate({ action: 'transfer', member: m.id }), 'Передать'), 'duty-wide') : null,
        button(m.active ? 'Временно убрать из очереди' : 'Вернуть в очередь', () => confirmAction(m.active ? 'Убрать из очереди?' : 'Вернуть в очередь?', 'История дежурств сохранится.', () => mutate({ action: 'active', member: m.id, value: !m.active }), m.active ? 'Убрать' : 'Вернуть'), 'duty-wide')));
  }
  function swapDialog(m) {
    const candidates = state.members.filter(x => x.active && x.pair !== m.pair);
    const chooser = select(candidates.map(x => [x.id, (x.pair ? '' : 'Запасной · ') + x.name]), candidates[0]?.id);
    const previewBox = el('div');
    function update() {
      const other = person(Number(chooser.value)); previewBox.replaceChildren();
      if (!other) { previewBox.append(p('Нет участников для обмена.')); return; }
      previewBox.append(el('h3', { text: 'После обмена' }));
      for (const [source, replacement] of [[m, other], [other, m]]) {
        const mate = partner(source);
        previewBox.append(p(source.pair ? '' + replacement.name + ' + ' + (mate?.name || 'нет напарника') : 'В запасных: ' + replacement.name));
      }
    }
    chooser.addEventListener('change', update); update();
    dialog('Поменять местами',
      field('С кем', chooser), previewBox, actions(button('Назад', () => memberDialog(m)), button('Сохранить обмен', () => mutate({ action: 'swap', member: m.id, other: Number(chooser.value) }), 'duty-primary')));
  }
  async function inviteDialog(member) {
    await perform(async () => {
      const data = await request('/' + sid + '/actions', 'POST', { action: 'invite', member: member || null });
      const link = input(data.url, { readOnly: true, 'aria-label': 'Ссылка приглашения' });
      dialog('Приглашение', p('Отправьте ссылку участнику.'), link,
        button('Скопировать ссылку', async () => { try { await navigator.clipboard.writeText(data.url); status('Ссылка скопирована'); } catch (_) { link.select(); status('Выделите и скопируйте ссылку.'); } }, 'duty-primary duty-wide'));
    });
  }
  let peopleCatalog = [], peopleContext, peopleExpires = 0;
  async function fetchPeople(space, force = false) {
    if (!force && peopleContext === space && peopleExpires > Date.now() + 60000) return peopleCatalog;
    const response = await request('/people', 'POST', { space });
    peopleCatalog = response.people; peopleContext = space; peopleExpires = response.expires_at * 1000;
    return peopleCatalog;
  }
  function rosterEditor(model, reservesOnly = false) {
    const root = el('div', { className: 'duty-roster-editor' });
    const selected = () => [...model.pairs.flat(), ...model.reserves].filter(Boolean);
    function updateOptions() {
      const used = selected();
      root.querySelectorAll('select').forEach(node => {
        [...node.options].forEach(option => { option.disabled = Boolean(option.value && option.value !== node.value && used.includes(option.value)); });
      });
    }
    function chooser(value, set, label) {
      const node = select([['', 'Выберите участника'], ...peopleCatalog.map(person => [person.ref, person.name])], value);
      node.setAttribute('aria-label', label);
      node.addEventListener('change', () => { set(node.value); if (model === draft) dirty = true; updateOptions(); });
      return node;
    }
    function renderEditor() {
      root.replaceChildren();
      if (!peopleCatalog.length) root.append(p('Пока нет доступных участников. Обновите список или проверьте, что у них выбрана ваша группа в боте.', 'duty-help'));
      if (!reservesOnly) {
        const pairs = card('Пары');
        model.pairs.forEach((pair, index) => {
          const row = el('div', { className: 'duty-pair-editor' }, [
            el('div', { className: 'duty-pair-heading' }, [el('h3', { text: 'Пара ' + (index + 1) }), button('Удалить пару', () => { model.pairs.splice(index, 1); renderEditor(); }, 'duty-remove')]),
            el('div', { className: 'duty-fields' }, [field('Дежурный', chooser(pair[0], value => { pair[0] = value; }, 'Пара ' + (index + 1) + ': дежурный')),
              field('Напарник', chooser(pair[1], value => { pair[1] = value; }, 'Пара ' + (index + 1) + ': напарник'))])]);
          pairs.append(row);
        });
        pairs.append(button('Добавить пару', () => { model.pairs.push(['', '']); renderEditor(); }, 'duty-wide'));
        root.append(pairs);
      }
      const reserves = card('Запасные');
      model.reserves.forEach((value, index) => reserves.append(el('div', { className: 'duty-reserve-editor' }, [
        chooser(value, next => { model.reserves[index] = next; }, 'Запасной ' + (index + 1)),
        button('Убрать', () => { model.reserves.splice(index, 1); renderEditor(); }, 'duty-remove')])));
      reserves.append(button('Добавить запасного', () => { model.reserves.push(''); renderEditor(); }, 'duty-wide'));
      root.append(reserves); updateOptions();
    }
    root.getSelection = () => {
      const pairs = model.pairs.filter(pair => pair.some(Boolean));
      if (pairs.some(pair => pair.some(ref => !ref))) throw new Error('Выберите напарника для каждого дежурного.');
      return { pairs, reserves: model.reserves.filter(Boolean) };
    };
    renderEditor(); return root;
  }
  function appendDialog(reserves) {
    perform(async () => {
      await fetchPeople(sid);
      const model = { pairs: reserves ? [] : [['', '']], reserves: reserves ? [''] : [] };
      const editor = rosterEditor(model, reserves);
      dialog(reserves ? 'Добавить запасных' : 'Добавить участников', editor,
        button('Добавить в дежурства', () => perform(async () => {
          await request('/' + sid + '/roster', 'POST', editor.getSelection());
          peopleExpires = 0; dirty = false; closeDialog(); await load();
        }), 'duty-primary duty-wide'));
    }, '', 'Загружаем список группы…');
  }
  function settingsForm(values, setup = false) {
    const opts = structuredClone(values), form = el('form', { className: 'duty-form' });
    form.addEventListener('submit', event => event.preventDefault());
    const control = (key, type, props = {}) => input(opts[key], { type, ...props, oninput: event => { opts[key] = type === 'number' ? Number(event.target.value) : event.target.value; dirty = true; } });
    const audience = select([['all', 'Всем участникам'], ['assigned', 'Только дежурным'], ['off', 'Не отправлять']], opts.audience);
    audience.addEventListener('change', () => { opts.audience = audience.value; dirty = true; });
    const notify = card('Уведомления',
      field('Кому сообщать о дежурных', audience),
      check('Писать мне, кто дежурит', opts.owner_daily, value => { opts.owner_daily = value; dirty = true; }),
      check('Спрашивать о подменах в личке', opts.requests, value => { opts.requests = value; dirty = true; }),
      check('Сообщать мне об отказах и итогах', opts.owner_events, value => { opts.owner_events = value; dirty = true; }));
    form.append(notify);
    const days = el('div', { className: 'duty-days' });
    ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].forEach((name, index) => days.append(el('label', {}, [
      input('', { type: 'checkbox', checked: opts.days.includes(index), 'aria-label': name, onchange: event => { opts.days = event.target.checked ? [...opts.days, index].sort() : opts.days.filter(d => d !== index); dirty = true; } }),
      el('span', { text: name })])));
    const times = card('Дни и время', field('Напоминание, МСК', control('time', 'time', { required: true })), days);
    const mode = select([['reserve', 'Сначала запасной'], ['pair', 'Следующая пара']], opts.mode);
    mode.addEventListener('change', () => { opts.mode = mode.value; dirty = true; });
    const replacements = details('Подбор замен и итоги',
      field('При отказе дежурного', mode), field('Искать замену до, МСК', control('cutoff', 'time', { required: true })),
      field('Ждать ответа, минут', control('wait_minutes', 'number', { min: 2, max: 30 })),
      field('Напоминать об итогах в, МСК', control('report', 'time', { required: true })));
    const place = details('Место и обязанности', field('Где дежурят', control('place', 'text', { maxLength: 120, placeholder: 'Например, кабинет 48' })),
      field('Что нужно сделать', el('textarea', { value: opts.tasks, maxLength: 500, rows: 3, oninput: event => { opts.tasks = event.target.value; dirty = true; } })));
    if (setup) form.append(details('Изменить время и другие настройки', times, replacements, place));
    else form.append(times, replacements, place);
    form.getOptions = () => opts; return form;
  }
  function renderSettings(content) {
    const daily = check('Кто сегодня дежурит', state.daily_notifications, value => mutate({ action: 'daily_notifications', value }));
    daily.querySelector('input').disabled = state.muted;
    content.append(card('Личные уведомления', check('Получать сообщения', !state.muted, () => mutate({ action: 'mute' })), daily));
    if (!admin()) {
      content.append(details('Другие действия', button('Выйти из дежурств', () => confirmAction('Выйти из дежурств?', 'Личные уведомления прекратятся.', () => mutate({ action: 'leave' }), 'Выйти'), 'duty-danger duty-wide')));
      return;
    }
    const form = settingsForm(state.options), original = JSON.stringify(state.options);
    const save = button('Сохранить изменения', () => perform(async () => {
      await request('/' + sid + '/settings', 'PUT', form.getOptions()); dirty = false; await load();
    }), 'duty-primary duty-wide');
    const bar = el('div', { className: 'duty-savebar', hidden: true }, [save]);
    const updateSave = () => { dirty = JSON.stringify(form.getOptions()) !== original; bar.hidden = !dirty; save.disabled = !dirty; };
    form.addEventListener('input', updateSave); form.addEventListener('change', updateSave);
    content.append(form, details('Управление списком',
      button(state.status === 'paused' ? 'Возобновить' : 'Поставить на паузу', () => confirmAction(state.status === 'paused' ? 'Возобновить дежурства?' : 'Поставить на паузу?', state.status === 'paused' ? 'Возобновим назначения и напоминания.' : 'Приостановим назначения и напоминания.', () => mutate({ action: state.status === 'paused' ? 'resume' : 'pause' })), 'duty-wide'),
      button('История изменений', () => dialog('История изменений', ...state.events.slice(0, 12).map(e => p(
        new Date(e.created * 1000).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'short' }) + ' · ' +
        ({ settings: 'Настройки обновлены', partners_changed: 'Участников поменяли местами', notification_migration: 'Обновлены уведомления', done: 'Дежурство подтверждено', missed: 'Отмечен пропуск', day_created: 'Назначены дежурные', created: 'Дежурства настроены', result_corrected: 'Результат исправлен', pause: 'Пауза', resume: 'Дежурства возобновлены' }[e.action] || 'Список обновлён'), 'duty-help'))), 'duty-wide'),
      button('Архивировать дежурства', () => confirmAction('Архивировать дежурства?', 'Назначения прекратятся. История сохранится.', () => mutate({ action: 'archive' }), 'Архивировать'), 'duty-danger duty-wide')), bar);
  }
  function showSetup() {
    const content = $('dutyContent'); content.replaceChildren();
    content.append(el('div', { className: 'duty-stepper' }, ['Участники', 'Уведомления', 'Проверка'].map((name, index) =>
      el('span', { className: index + 1 === setupStep ? 'is-current' : index + 1 < setupStep ? 'is-done' : '' }, [el('b', { text: String(index + 1) }), document.createTextNode(name)]))));
    if (setupStep === 1) {
      const editor = rosterEditor(draft);
      content.append(editor,
        actions(button('Обновить список группы', () => {
          if ((draft.pairs.flat().some(Boolean) || draft.reserves.some(Boolean)) && !window.confirm('После обновления нужно выбрать участников заново. Продолжить?')) return;
          perform(async () => { await fetchPeople(null, true); draft.pairs = [['', '']]; draft.reserves = []; showSetup(); }, '', 'Обновляем список группы…');
        }), button('Дальше', () => {
          try { const selection = editor.getSelection(); if (!selection.pairs.length) throw new Error('Добавьте хотя бы одну пару.'); setupStep = 2; status(''); showSetup(); }
          catch (error) { status(error.message, true); }
        }, 'duty-primary')));
    } else if (setupStep === 2) {
      const form = settingsForm(draft.options || defaults(), true);
      content.append(form, actions(button('Назад', () => { draft.options = form.getOptions(); setupStep = 1; showSetup(); }),
        button('Проверить список', () => perform(async () => {
          draft.options = form.getOptions();
          preview = await request('/preview', 'POST', { pairs: draft.pairs.filter(pair => pair.every(Boolean)), reserves: draft.reserves.filter(Boolean), options: draft.options });
          setupStep = 3; dirty = true; showSetup();
        }, '', 'Проверяем участников…'), 'duty-primary')));
    } else {
      content.append(card('Проверьте состав', ...preview.pairs.map((pair, index) => el('div', { className: 'duty-preview-pair' }, [
        el('span', { className: 'duty-pair-number', text: String(index + 1) }), el('div', {}, pair.map(m => p(m.name)))])),
        p('Запасные: ' + (preview.reserves.map(m => m.name).join(', ') || 'нет')),
        p('После запуска участникам придёт уведомление.', 'duty-help')),
        actions(button('Изменить состав', () => { setupStep = 1; showSetup(); }), button('Запустить дежурства', () => perform(async () => {
          const result = await request('', 'POST', { pairs: draft.pairs.filter(pair => pair.every(Boolean)), reserves: draft.reserves.filter(Boolean), options: draft.options, preview_token: preview.preview_token });
          sid = result.id; dirty = false; peopleExpires = 0; await load();
        }), 'duty-primary')));
    }
  }
  function dayActions(day) {
    dialog('На сегодня', button('Назначить другую пару', () => manualDialog(day), 'duty-wide'),
      button('Отменить дежурство', () => confirmAction('Отменить дежурство?', 'Сегодня дежурных больше не назначаем.', () => mutate({ action: 'cancel_day', day: day.id }), 'Отменить дежурство'), 'duty-danger duty-wide'));
  }
  $('dutyRefresh').addEventListener('click', refresh);
  $('dutyDialogClose').addEventListener('click', closeDialog);
  $('dutyDialog').addEventListener('click', event => { if (event.target === $('dutyDialog')) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog(); } });
  document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => {
    if (dirty && !window.confirm('Есть несохранённые изменения. Перейти без сохранения?')) return;
    dirty = false; tab = b.dataset.tab; status(''); render();
  }));
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  if (tg) {
    const theme = () => { document.documentElement.dataset.theme = tg.colorScheme || 'light'; Object.entries(tg.themeParams || {}).forEach(([key, value]) => document.documentElement.style.setProperty('--tg-theme-' + key.replaceAll('_', '-'), value)); };
    theme(); tg.ready(); tg.expand(); tg.onEvent('themeChanged', theme);
    if (tg.BackButton) { tg.BackButton.show(); tg.BackButton.onClick(() => { if (!dirty || window.confirm('Выйти без сохранения изменений?')) location.href = '/'; }); }
  }
  if (!initData) {
    $('dutySkeleton').hidden = true;
    status('');
    $('dutyContent').setAttribute('aria-busy', 'false');
    $('dutyContent').append(card('Дежурства в Telegram', p('Откройте раздел через бота.'),
      el('a', { href: 'https://t.me/kkepik_bot?startapp=duties', text: 'Открыть мини-апп', className: 'duty-button duty-primary duty-wide' })));
  } else refresh();
})();
