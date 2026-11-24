(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || new URLSearchParams(location.search).get('tgWebAppData') || '';
  const labels = { assigned: 'дежурит', waiting: 'ждём ответа', reported: 'ждёт подтверждения', done: 'отдежурил(а)',
    missed: 'не отдежурил(а)', excused: 'освобождён(а)', declined: 'сегодня не может', released: 'перенесено', cancelled: 'отменено' };
  const params = new URLSearchParams(location.search);
  const initialTab = params.get('view');
  let state = null, sid = Number(params.get('space')) || null;
  let tab = ['members', 'settings'].includes(initialTab) ? initialTab : 'assignments';
  let canCreate = true, busy = false, dirty = false, generation = 0;
  let draft = { pairs: [['', '']], reserves: [], options: null }, preview = null, setupStep = 1;

  const feedback = kind => window.kkepikHaptics?.play(kind);
  function setDirty(value) {
    if (dirty === value) return;
    dirty = value;
    if (!initData || !tg?.isVersionAtLeast?.('6.2')) return;
    if (value) tg.enableClosingConfirmation?.(); else tg.disableClosingConfirmation?.();
  }

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
    feedback('soft');
    $('dutyDialogTitle').textContent = title; $('dutyDialogBody').replaceChildren(...children.filter(child => child != null));
    if (!$('dutyDialog').open) $('dutyDialog').showModal();
  }
  function closeDialog() {
    const element = $('dutyDialog');
    if (window.kkepikSlideClose) window.kkepikSlideClose(element);
    else if (element.open) element.close();
  }
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
    try { await run(); status(success); if (success) feedback('success'); }
    catch (error) {
      feedback('error');
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
      closeDialog(); setDirty(false); await load();
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
      canCreate = result.can_create !== false;
      if (!spaces.some(s => s.id === sid)) sid = spaces[0]?.id || null;
      $('dutyLists').hidden = spaces.length < 2;
      if (spaces.length > 1) {
        const chooser = select(spaces.map(s => [s.id, s.group + (s.status === 'paused' ? ' · пауза' : '')]), sid);
        chooser.setAttribute('aria-label', 'Выбрать дежурства');
        chooser.addEventListener('change', async () => {
          if (dirty && !window.confirm('Перейти к другому списку без сохранения?')) {
            chooser.value = String(sid);
            return;
          }
          setDirty(false);
          sid = Number(chooser.value);
          state = null;
          $('dutyTabs').hidden = true;
          $('dutyContent').replaceChildren();
          await refresh();
        });
        $('dutyLists').replaceChildren(chooser);
      }
      if (sid) {
        const data = await request('/' + sid);
        if (version !== generation) return;
        state = data; $('dutyGroup').textContent = data.group;
      } else state = null;
      $('dutyTabs').hidden = !state;
      $('dutyBack').href = sid ? '/?tab=duty&space=' + sid : '/?tab=duty';
      render();
    } finally {
      if (version === generation) {
        $('dutySkeleton').hidden = true;
        $('dutyContent').setAttribute('aria-busy', 'false');
      }
    }
  }
  async function refresh() {
    if (busy) return;
    if (dirty && !window.confirm('Есть несохранённые изменения. Обновить страницу?')) return;
    setDirty(false); status(state ? 'Обновляем…' : '');
    try { await load(); status(''); } catch (error) {
      status(error.message, true);
      if (!state) $('dutyContent').replaceChildren(card('Не удалось загрузить дежурства', button('Попробовать ещё раз', refresh, 'duty-primary duty-wide')));
    }
  }
  // Keep roster rows under the user's finger when a dialog saves a change.
  const rowVersions = new WeakMap();
  function rosterRow(key, version, row) {
    row.dataset.rosterKey = key;
    rowVersions.set(row, JSON.stringify(version));
    return row;
  }
  function updateRoster(current, next) {
    const oldRows = new Map([...current.children].map(node => [node.dataset.rosterKey, node]));
    const desired = [...next.children].map(node => {
      const key = node.dataset.rosterKey;
      const old = oldRows.get(key);
      return key && old && rowVersions.get(old) === rowVersions.get(node) ? old : node;
    });
    placeChildren(current, desired);
  }
  function placeChildren(parent, nodes) {
    const keep = new Set(nodes);
    for (const child of [...parent.children]) if (!keep.has(child)) child.remove();
    nodes.forEach((node, i) => { if (parent.children[i] !== node) parent.insertBefore(node, parent.children[i] || null); });
  }
  function render() {
    if (state && !admin() && tab === 'assignments') tab = 'members';
    const nav = $('dutyTabs');
    nav.querySelector('[data-tab=assignments]').hidden = !admin();
    nav.querySelector('[data-tab=settings] span').textContent = admin() ? 'Настройки' : 'Уведомления';
    nav.querySelectorAll('[data-tab]').forEach(b => {
      if (b.dataset.tab === tab) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    const content = $('dutyContent');
    if (!state) {
      content.replaceChildren();
      content.append(canCreate ? card('Дежурства вашей группы', p('Добавьте пары и запасных.'),
        button('Настроить дежурства', () => perform(async () => { await fetchPeople(null); setupStep = 1; showSetup(); }, '', 'Загружаем список группы…'), 'duty-primary duty-wide')) :
        card('Дежурства уже настроены', p('Попросите старосту добавить вас в список.')));
      return;
    }
    const url = new URL(location.href);
    url.searchParams.set('space', String(sid));
    url.searchParams.set('view', tab);
    history.replaceState(null, '', url);
    const sameView = content.dataset.view === tab && content.dataset.space === String(sid);
    const expanded = new Set(sameView ? [...content.querySelectorAll('details[open] > summary')].map(el => el.textContent) : []);
    const scroll = window.scrollY;
    const next = document.createDocumentFragment();
    if (state.status === 'paused') next.append(card('Дежурства на паузе'));
    ({ assignments: renderAssignments, members: renderMembers, settings: renderSettings })[tab](next);
    const sections = [...next.children].map(section => {
      const roster = section.dataset.roster;
      const old = sameView && roster && content.querySelector('[data-roster="' + roster + '"]');
      if (!old) return section;
      updateRoster(old.querySelector(':scope > .duty-details-body') || old, section.querySelector(':scope > .duty-details-body') || section);
      return old;
    });
    for (const details of next.querySelectorAll('details')) {
      if (expanded.has(details.querySelector('summary')?.textContent)) details.open = true;
    }
    placeChildren(content, sections);
    content.dataset.view = tab;
    content.dataset.space = String(sid);
    if (sameView && window.scrollY !== scroll) window.scrollTo(0, scroll);
  }
  function renderAssignments(content) {
    const currentDate = today();
    const day = state.days.find(d => d.date === currentDate);
    const planned = !day && state.forecast?.find(d => d.date === currentDate);
    const now = Date.now() / 1000;
    const requests = day && now < day.cutoff ? (day.requests || []).filter(r => !r.mine) : [];
    if (requests.length) content.append(card('Запросы на перенос', ...requests.map(r => el('div', { className: 'duty-flow-row' }, [
      p(r.name), r.reason ? p(r.reason, 'duty-help') : null,
      actions(button('Одобрить перенос', () => mutate({ action: 'approve_decline', day: day.id, member: r.member }), 'duty-primary'),
        button('Оставить дежурство', () => mutate({ action: 'reject_decline', day: day.id, member: r.member }))) ]))));
    const active = day?.people.filter(m => ['assigned', 'reported', 'waiting', 'done'].includes(m.state)) || [];
    let statusText = { no_duty: 'Сегодня без дежурства', searching: 'Подбираем замену', unfilled: 'Сегодня нужна замена',
      cancelled: 'Дежурство отменено', closed: 'Итоги сохранены' }[day?.status];
    if (!day && state.status !== 'paused') {
      if (state.first_date > currentDate) statusText = 'Дежурства начнутся ' + dateLabel(state.first_date);
      else if (planned?.status === 'no_duty') statusText = 'Сегодня без дежурства';
      else if (planned?.people.length) statusText = 'По графику сегодня дежурят';
      else statusText = 'Сегодня нужна замена';
    }
    const canAssign = day && !['no_duty', 'closed'].includes(day.status) && !day.people.some(m => ['done', 'reported', 'missed', 'excused'].includes(m.state));
    const canMark = day && day.status !== 'no_duty' && now >= day.cutoff;
    content.append(card('Назначение на сегодня',
      statusText ? p(statusText, 'duty-help') : null,
      ...active.map(m => p(m.name + ' — ' + labels[m.state])),
      ...(planned?.people || []).map(m => p(m.name)),
      canMark ? button(day.status === 'closed' ? 'Открыть итоги' : 'Записать итоги', () => markDialog(day), 'duty-primary duty-wide') : null,
      canAssign ? button('Изменить назначение', () => dayActions(day), 'duty-wide') : null));
    if (day?.offer_declines?.length) content.append(details('Отказы от подмены', ...day.offer_declines.map(r =>
      el('div', { className: 'duty-flow-row' }, [p(r.name), p(r.reason, 'duty-help')]))));
    const past = state.days.filter(d => d.date < currentDate && d.status !== 'no_duty');
    const needsResult = d => d.people.some(m => ['assigned', 'reported'].includes(m.state));
    const open = past.filter(needsResult);
    if (open.length) {
      const pending = card('Нужно отметить', ...open.map(d => rosterRow('d' + d.id, d,
        button(dateLabel(d.date), () => markDialog(d), 'duty-wide'))));
      pending.dataset.roster = 'pending';
      content.append(pending);
    }
    const old = past.filter(d => !needsResult(d));
    if (old.length) {
      const history = details('Итоги прошлых дежурств', ...old.map(d => rosterRow('d' + d.id, d,
        el('div', { className: 'duty-row' }, [
          el('div', { className: 'duty-row-copy' }, [el('strong', { text: dateLabel(d.date) }),
            ...d.people.filter(m => ['done', 'missed', 'excused'].includes(m.state)).map(m => el('small', { text: m.name + ' — ' + labels[m.state] }))]),
          button('Итоги', () => markDialog(d)) ]))));
      history.dataset.roster = 'history';
      content.append(history);
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
    dialog('Итоги · ' + dateLabel(day.date), ...candidates.map(m => p(m.name + ' — ' + labels[m.state])),
      pending.length ? button('Все назначенные отдежурили', () => mutate({ action: 'finish', day: day.id }), 'duty-primary duty-wide') : null,
      button('Указать, кто отдежурил фактически', () => recordResultsDialog(day), pending.length ? 'duty-wide' : 'duty-primary duty-wide'),
      candidates.length ? individual : null);
  }
  function recordResultsDialog(day) {
    const current = day.people.filter(m => ['assigned', 'reported', 'done'].includes(m.state));
    const roster = state.members.filter(m => m.active || day.people.some(p => p.member === m.id));
    const choices = [['', 'Выберите участника'], ...roster.map(m => [m.id, m.name + (m.pair ? '' : ' · запасной')])];
    const first = select(choices, current[0]?.member || '');
    const second = select(choices, current[1]?.member || '');
    const save = button('Записать итоги', () => mutate({ action: 'record_results', day: day.id,
      member: Number(first.value), other: Number(second.value), revision: day.revision }), 'duty-primary duty-wide');
    const update = () => {
      for (const [node, other] of [[first, second], [second, first]]) {
        for (const option of node.options) option.disabled = Boolean(option.value && option.value === other.value);
      }
      save.disabled = !(first.value && second.value && first.value !== second.value);
    };
    first.addEventListener('change', update); second.addEventListener('change', update); update();
    dialog('Кто отдежурил?', p('Дежурство зачтётся выбранным участникам. График обновится.'),
      field('Первый участник', first), field('Второй участник', second),
      actions(button('Назад', () => markDialog(day)), save));
  }
  function manualDialog(day) {
    const pairs = [...new Set(state.members.filter(m => m.active && m.pair).map(m => m.pair))];
    const available = state.members.filter(m => m.active && !m.credit && !day.people.some(p => p.member === m.id && p.state === 'declined')
      && (!m.exempt_until || m.exempt_until < day.date));
    const pairOptions = pairs.map(pid => available.filter(m => m.pair === pid)).filter(members => members.length === 2)
      .map(members => [members[0].id, members.map(m => m.name).join(' + ')]);
    const choices = select([['', 'Выберите пару'], ...pairOptions], '');
    const host = select([['', 'Выберите дежурного'], ...available.filter(m => m.pair).map(m => [m.id, m.name])], '');
    const reserve = select([['', 'Выберите запасного'], ...available.filter(m => !m.pair).map(m => [m.id, m.name])], '');
    const assign = button('Назначить пару', () => mutate({ action: 'manual_pair', day: day.id, member: Number(choices.value) }), 'duty-primary duty-wide');
    const substitute = button('Назначить с запасным', () => mutate({ action: 'manual_reserve', day: day.id, member: Number(host.value), other: Number(reserve.value) }), 'duty-wide');
    const update = () => { assign.disabled = !choices.value; substitute.disabled = !host.value || !reserve.value; };
    for (const node of [choices, host, reserve]) node.addEventListener('change', update);
    update();
    dialog('Назначить вручную', p('Сначала договоритесь с участниками. Это назначение действует только на сегодня.', 'duty-help'),
      pairOptions.length ? field('Целая пара', choices) : null, pairOptions.length ? assign : null,
      reserve.options.length > 1 && host.options.length > 1
        ? details('Назначить с запасным', field('Дежурный', host), field('Запасной', reserve), substitute)
        : null,
      !pairOptions.length && (reserve.options.length <= 1 || host.options.length <= 1)
        ? p('Нет доступной пары или запасного. Проверьте предыдущие итоги и состав.', 'duty-help') : null);
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
    pairs.dataset.roster = 'pairs';
    pairIds.forEach((id, index) => {
      const members = active.filter(m => m.pair === id);
      const row = el('section', { className: 'duty-pair-view' }, [el('h3', { text: 'Пара ' + (index + 1) }),
        el('div', { className: 'duty-pair-names' }, [
          ...members.map(m => p(m.name + (m.mine ? ' · ты' : ''))),
          members.length === 1 ? p('Без напарника', 'duty-help') : null])]);
      if (admin()) row.append(button('Изменить', () => dialog('Пара ' + (index + 1), ...members.map(memberRow)), 'duty-quiet'));
      pairs.append(rosterRow('p' + id, [index,state.role,members], row));
    });
    content.append(pairs);
    const reserves = active.filter(m => !m.pair);
    const reserveCard = card('Запасные', ...reserves.map(m => rosterRow('m' + m.id, [state.role,m], memberRow(m))),
      reserves.length ? null : p('Запасных пока нет.', 'duty-help'));
    reserveCard.dataset.roster = 'reserves';
    content.append(reserveCard);
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
    const earliest = today();
    const exempt = input(m.exempt_until, { type: 'date', min: earliest, required: true, 'aria-label': 'Освободить до даты' });
    const saveExemption = button('Сохранить освобождение', () => mutate({ action: 'exempt', member: m.id, value: exempt.value }), 'duty-wide');
    const updateExemption = () => { saveExemption.disabled = !exempt.validity.valid; };
    exempt.addEventListener('input', updateExemption);
    exempt.addEventListener('change', updateExemption);
    updateExemption();
    const exemption = m.exempt_until && m.exempt_until >= earliest
      ? 'Освобождение действует до ' + m.exempt_until.split('-').reverse().join('.') + ' включительно.'
      : null;
    dialog(m.name, p(mate ? 'Напарник — ' + mate.name : m.pair ? 'Без напарника.' : 'В запасных.', 'duty-help'),
      m.active ? button('Поменять местами', () => swapDialog(m), 'duty-primary duty-wide') : null,
      details('Освобождение', field('Последний день освобождения', exempt, exemption), saveExemption,
        m.exempt_until ? button('Снять освобождение', () => mutate({ action: 'exempt', member: m.id, value: null }), 'duty-wide') : null),
      !m.linked ? button('Подключить уведомления', () => inviteDialog(m.id), 'duty-wide') : null,
      details('Другие действия',
        m.active ? (m.pair ? button('Перевести в запасные', () => moveToReserveDialog(m), 'duty-wide')
          : button('Добавить в пару', () => pairReserveDialog(m), 'duty-wide')) : null,
        field('Имя в списке', name), button('Сохранить имя', () => mutate({ action: 'rename', member: m.id, value: name.value }), 'duty-wide'),
        owner() && m.role && !m.mine ? button('Передать управление', () => confirmAction('Передать управление?', m.name + ' станет админом дежурств.', () => mutate({ action: 'transfer', member: m.id }), 'Передать'), 'duty-wide') : null,
        button(m.active ? 'Временно убрать из очереди' : 'Вернуть в очередь', () => confirmAction(m.active ? 'Убрать из очереди?' : 'Вернуть в очередь?', 'История дежурств сохранится.', () => mutate({ action: 'active', member: m.id, value: !m.active }), m.active ? 'Убрать' : 'Вернуть'), 'duty-wide')));
  }
  function moveToReserveDialog(m) {
    const mate = partner(m);
    dialog('Перевести в запасные?', p(m.name + ' будет в списке запасных.' + (mate ? ' ' + mate.name + ' останется без напарника.' : '')),
      actions(button('Назад', () => memberDialog(m)),
        button('Перевести в запасные', () => mutate({ action: 'move_to_reserve', member: m.id }), 'duty-primary')));
  }
  function pairReserveDialog(m) {
    const active = state.members.filter(other => other.id !== m.id && other.active);
    const available = active.filter(other => other.pair && !partner(other));
    const replace = active.filter(other => partner(other));
    const reserves = active.filter(other => !other.pair);
    const groups = [
      ['Свободное место', 'join_pair', available, 'В пару к '],
      ['Замена в паре', 'swap', replace, 'Вместо '],
      ['Новая пара', 'pair_reserves', reserves, 'В пару с '],
    ].filter(([, , people]) => people.length);
    if (!groups.length) {
      dialog('Добавить в пару', p('Пока нет свободного места или участника для новой пары.'), button('Назад', () => memberDialog(m), 'duty-wide'));
      return;
    }
    const chooser = el('select', {}, [el('option', { value: '', text: 'Выберите участника' }),
      ...groups.map(([label, action, people, prefix]) => el('optgroup', { label }, people.map(other =>
        el('option', { value: action + ':' + other.id, text: prefix + other.name }))))]);
    const preview = p('', 'duty-help');
    const save = button('Сохранить пару', () => {
      const [action, id] = chooser.value.split(':');
      mutate({ action, member: m.id, other: Number(id) });
    }, 'duty-primary');
    save.disabled = true;
    chooser.addEventListener('change', () => {
      const [action, id] = chooser.value.split(':');
      const other = person(Number(id));
      save.disabled = !other;
      if (!other) preview.textContent = '';
      else if (action === 'swap') preview.textContent = m.name + ' будет дежурить с ' + partner(other).name + '. ' + other.name + ' будет в запасных.';
      else {
        preview.textContent = m.name + ' и ' + other.name + ' будут дежурить вместе.';
        if (action === 'join_pair' && state.members.some(peer => peer.pair === other.pair && !peer.active)) {
          preview.textContent += ' Прежний напарник останется вне очереди.';
        }
      }
    });
    dialog('Добавить в пару', field('Напарник или место в паре', chooser), preview,
      actions(button('Назад', () => memberDialog(m)), save));
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
  function rosterEditor(model, mode = 'all') {
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
      node.addEventListener('change', () => { set(node.value); if (model === draft) setDirty(true); updateOptions(); });
      return node;
    }
    function renderEditor() {
      root.replaceChildren();
      if (!peopleCatalog.length) root.append(p('Пока нет доступных участников. Обновите список или проверьте, что у них выбрана ваша группа в боте.', 'duty-help'));
      if (mode !== 'reserves') {
        const pairs = card('Пары');
    pairs.dataset.roster = 'pairs';
        model.pairs.forEach((pair, index) => {
          const row = el('div', { className: 'duty-pair-editor' }, [
            el('div', { className: 'duty-pair-heading' }, [el('h3', { text: 'Пара ' + (index + 1) }), button('Удалить пару', () => { model.pairs.splice(index, 1); renderEditor(); }, 'duty-remove')]),
            el('div', { className: 'duty-fields' }, [field('Дежурный', chooser(pair[0], value => { pair[0] = value; }, 'Пара ' + (index + 1) + ': дежурный')),
              field('Напарник', chooser(pair[1], value => { pair[1] = value; }, 'Пара ' + (index + 1) + ': напарник'))])]);
          pairs.append(rosterRow('p' + id, [index,state.role,members], row));
        });
        pairs.append(button('Добавить пару', () => { model.pairs.push(['', '']); renderEditor(); }, 'duty-wide'));
        root.append(pairs);
      }
      if (mode !== 'pairs') {
        const reserves = card('Запасные');
        model.reserves.forEach((value, index) => reserves.append(el('div', { className: 'duty-reserve-editor' }, [
          chooser(value, next => { model.reserves[index] = next; }, 'Запасной ' + (index + 1)),
          button('Убрать', () => { model.reserves.splice(index, 1); renderEditor(); }, 'duty-remove')])));
        reserves.append(button('Добавить запасного', () => { model.reserves.push(''); renderEditor(); }, 'duty-wide'));
        root.append(reserves);
      }
      updateOptions();
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
      const editor = rosterEditor(model, reserves ? 'reserves' : 'pairs');
      dialog(reserves ? 'Добавить запасных' : 'Добавить участников', editor,
        button('Добавить в дежурства', () => perform(async () => {
          await request('/' + sid + '/roster', 'POST', editor.getSelection());
          peopleExpires = 0; setDirty(false); closeDialog(); await load();
        }), 'duty-primary duty-wide'));
    }, '', 'Загружаем список группы…');
  }
  function settingsForm(values, setup = false) {
    const opts = structuredClone(values), form = el('form', { className: 'duty-form' });
    form.addEventListener('submit', event => event.preventDefault());
    const control = (key, type, props = {}) => input(opts[key], { type, ...props, oninput: event => { opts[key] = type === 'number' ? Number(event.target.value) : event.target.value; setDirty(true); } });
    const audience = select([['all', 'Всем участникам'], ['assigned', 'Только дежурным'], ['off', 'Не отправлять']], opts.audience);
    audience.addEventListener('change', () => { opts.audience = audience.value; setDirty(true); });
    const notify = card('Уведомления',
      field('Кому сообщать о дежурных', audience),
      check('Писать мне, кто дежурит', opts.owner_daily, value => { opts.owner_daily = value; setDirty(true); }),
      check('Спрашивать о подменах в личке', opts.requests, value => { opts.requests = value; setDirty(true); }),
      check('Сообщать мне об отказах и итогах', opts.owner_events, value => { opts.owner_events = value; setDirty(true); }));
    form.append(notify);
    const days = el('div', { className: 'duty-days' });
    ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].forEach((name, index) => days.append(el('label', {}, [
      input('', { type: 'checkbox', checked: opts.days.includes(index), 'aria-label': name, onchange: event => { opts.days = event.target.checked ? [...opts.days, index].sort() : opts.days.filter(d => d !== index); setDirty(true); } }),
      el('span', { text: name })])));
    const times = card('Дни и время', field('Напоминание, МСК', control('time', 'time', { required: true })), days);
    const mode = select([['reserve', 'Сначала запасной'], ['pair', 'Следующая пара']], opts.mode);
    mode.addEventListener('change', () => { opts.mode = mode.value; setDirty(true); });
    const replacements = details('Подбор замен и итоги',
      field('При отказе дежурного', mode), field('Искать замену до, МСК', control('cutoff', 'time', { required: true })),
      field('Ждать ответа, минут', control('wait_minutes', 'number', { min: 2, max: 30 })),
      field('Напоминать об итогах в, МСК', control('report', 'time', { required: true })));
    const place = details('Место и обязанности', field('Где дежурят', control('place', 'text', { maxLength: 120, placeholder: 'Например, кабинет 48' })),
      field('Что нужно сделать', el('textarea', { value: opts.tasks, maxLength: 500, rows: 3, oninput: event => { opts.tasks = event.target.value; setDirty(true); } })));
    if (setup) form.append(details('Изменить время и другие настройки', times, replacements, place));
    else form.append(times, replacements, place);
    form.getOptions = () => opts; return form;
  }
  function renderSettings(content) {
    if (!admin()) {
      content.append(card('Сообщения в Telegram', check('Получать уведомления о дежурствах', !state.muted, () => mutate({ action: 'mute' })),
        p('Рассылку о том, кто дежурит, можно выключить на главной странице дежурств.', 'duty-help')));
      return;
    }
    if (owner()) {
      const deputies = state.deputies || [];
      const candidates = state.members.filter(m => m.linked && m.role === 'member');
      content.append(card('Заместители',
        ...(deputies.length ? deputies.map(m => el('div', { className: 'duty-flow-row' }, [
          p(m.name), button('Снять права', () => confirmAction('Снять права заместителя?', m.name + ' останется в списке участников.',
            () => mutate({ action: 'manager', deputy: m.id, value: false }), 'Снять права'), 'duty-wide')])) : [p('Пока нет заместителей.', 'duty-help')]),
        candidates.length ? button('Назначить заместителя', () => deputyDialog(candidates), 'duty-wide') : null));
    }
    const form = settingsForm(state.options), original = JSON.stringify(state.options);
    const save = button('Сохранить изменения', () => perform(async () => {
      await request('/' + sid + '/settings', 'PUT', form.getOptions()); setDirty(false); await load();
    }), 'duty-primary duty-wide');
    const bar = el('div', { className: 'duty-savebar', hidden: true }, [save]);
    const updateSave = () => { setDirty(JSON.stringify(form.getOptions()) !== original); bar.hidden = !dirty; save.disabled = !dirty; };
    form.addEventListener('input', updateSave); form.addEventListener('change', updateSave);
    content.append(form, details('Управление списком',
      button(state.status === 'paused' ? 'Возобновить' : 'Поставить на паузу', () => confirmAction(state.status === 'paused' ? 'Возобновить дежурства?' : 'Поставить на паузу?', state.status === 'paused' ? 'Возобновим назначения и напоминания.' : 'Приостановим назначения и напоминания.', () => mutate({ action: state.status === 'paused' ? 'resume' : 'pause' })), 'duty-wide'),
      button('История изменений', () => dialog('История изменений', ...state.events.slice(0, 12).map(e => p(
        new Date(e.created * 1000).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'short' }) + ' · ' +
        ({ settings: 'Настройки обновлены', partners_changed: 'Участников поменяли местами', notification_migration: 'Обновлены уведомления', done: 'Дежурство подтверждено', missed: 'Отмечен пропуск', day_created: 'Назначены дежурные', created: 'Дежурства настроены', result_corrected: 'Результат исправлен', pause: 'Пауза', resume: 'Дежурства возобновлены' }[e.action] || 'Список обновлён'), 'duty-help'))), 'duty-wide'),
      owner() ? button('Архивировать дежурства', () => confirmAction('Архивировать дежурства?', 'Назначения прекратятся. История сохранится.', () => mutate({ action: 'archive' }), 'Архивировать'), 'duty-danger duty-wide') : null), bar);
  }
  function deputyDialog(candidates) {
    const chooser = select([['', 'Выберите участника'], ...candidates.map(m => [m.id, m.name])], '');
    const save = button('Назначить', () => mutate({ action: 'manager', member: Number(chooser.value), value: true }), 'duty-primary duty-wide');
    save.disabled = true;
    chooser.addEventListener('change', () => { save.disabled = !chooser.value; });
    dialog('Назначить заместителя', p('Заместитель сможет менять назначения, отвечать на запросы и отмечать итоги.'),
      field('Участник', chooser), save);
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
          setupStep = 3; setDirty(true); showSetup();
        }, '', 'Проверяем участников…'), 'duty-primary')));
    } else {
      content.append(card('Проверьте состав', ...preview.pairs.map((pair, index) => el('div', { className: 'duty-preview-pair' }, [
        el('span', { className: 'duty-pair-number', text: String(index + 1) }), el('div', {}, pair.map(m => p(m.name)))])),
        p('Запасные: ' + (preview.reserves.map(m => m.name).join(', ') || 'нет')),
        p('После запуска участникам придёт уведомление.', 'duty-help')),
        actions(button('Изменить состав', () => { setupStep = 1; showSetup(); }), button('Запустить дежурства', () => perform(async () => {
          const result = await request('', 'POST', { pairs: draft.pairs.filter(pair => pair.every(Boolean)), reserves: draft.reserves.filter(Boolean), options: draft.options, preview_token: preview.preview_token });
          sid = result.id; setDirty(false); peopleExpires = 0; await load();
        }), 'duty-primary')));
    }
  }
  function dayActions(day) {
    dialog('На сегодня', button('Назначить другую пару', () => manualDialog(day), 'duty-wide'),
      button('Отменить дежурство', () => confirmAction('Отменить дежурство?', 'Сегодня дежурных больше не назначаем.', () => mutate({ action: 'cancel_day', day: day.id }), 'Отменить дежурство'), 'duty-danger duty-wide'));
  }
  $('dutyRefresh').addEventListener('click', refresh);
  $('dutyDialogClose').addEventListener('click', closeDialog);
  $('dutyDialog').addEventListener('cancel', event => { event.preventDefault(); closeDialog(); });
  $('dutyDialog').addEventListener('click', event => { if (event.target === $('dutyDialog')) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog(); } });
  document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => {
    if (dirty && !window.confirm('Есть несохранённые изменения. Перейти без сохранения?')) return;
    if (b.dataset.tab !== tab) feedback('selection');
    setDirty(false); tab = b.dataset.tab; status(''); render();
  }));
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  if (tg) {
    const theme = () => { document.documentElement.dataset.theme = tg.colorScheme || 'light'; Object.entries(tg.themeParams || {}).forEach(([key, value]) => document.documentElement.style.setProperty('--tg-theme-' + key.replaceAll('_', '-'), value)); };
    theme(); tg.ready(); tg.expand(); tg.onEvent('themeChanged', theme);
    if (initData && tg.isVersionAtLeast?.('7.7')) tg.disableVerticalSwipes?.();
    if (initData && tg.isVersionAtLeast?.('6.2')) {
      window.addEventListener('pagehide', () => { if (dirty) tg.disableClosingConfirmation?.(); });
      window.addEventListener('pageshow', () => { if (dirty) tg.enableClosingConfirmation?.(); });
    }
    if (tg.BackButton) {
      tg.BackButton.show();
      tg.BackButton.onClick(() => { if ($('dutyDialog').open) closeDialog(); else location.href = $('dutyBack').href; });
    }
  }
  if (!initData) {
    $('dutySkeleton').hidden = true;
    status('');
    $('dutyContent').setAttribute('aria-busy', 'false');
    $('dutyContent').append(card('Дежурства в Telegram', p('Откройте раздел через бота.'),
      el('a', { href: 'https://t.me/kkepik_bot?startapp=duties', text: 'Открыть мини-апп', className: 'duty-button duty-primary duty-wide' })));
  } else refresh();
})();
