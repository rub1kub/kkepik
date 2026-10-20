(function () {
  'use strict';
  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = tg ? tg.initData : '';
  const $ = id => document.getElementById(id);
  const clone = value => JSON.parse(JSON.stringify(value));
  const fields = { pair: 'Номер пары', time: 'Время', subject: 'Предмет', teacher: 'Преподаватель / группа', room: 'Аудитория' };
  const blocks = { header: 'Заголовок', group: 'Группа / преподаватель', bounds: 'Начало и конец дня', lessons: 'Пары', footer: 'Ваш текст' };
  const fieldToggles = { time: 'show_times', teacher: 'show_teachers', room: 'show_rooms' };
  let preferences, saved, presets = [], entity = null, generation = 0, timer, controller, previewValid = false, saving = false;
  const images = new Map();

  if (tg) {
    document.documentElement.dataset.theme = tg.colorScheme || 'light';
    if (tg.themeParams) Object.entries(tg.themeParams).forEach(([name, value]) => document.documentElement.style.setProperty('--tg-theme-' + name.replaceAll('_', '-'), value));
    tg.ready(); tg.expand();
    if (tg.BackButton) { tg.BackButton.show(); tg.BackButton.onClick(() => { window.location.href = '/'; }); }
    tg.onEvent('themeChanged', () => { document.documentElement.dataset.theme = tg.colorScheme; });
  }

  function status(message, error = false) {
    $('constructorStatus').textContent = message;
    $('constructorStatus').dataset.error = String(error);
  }

  async function request(path, method = 'GET', body, signal) {
    const response = await fetch('/api/message-settings' + path, {
      method, headers: { 'X-Telegram-Init-Data': initData, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal, cache: 'no-store'
    });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('Сервис временно недоступен. Попробуйте ещё раз.'); }
    if (!response.ok) {
      if (response.status === 401) throw new Error('Откройте /settings в боте и нажмите «Конструктор и предпросмотр».');
      if (response.status === 404) throw new Error('Сначала зарегистрируйтесь в боте через /start.');
      if (response.status === 422 || response.status === 400) throw new Error('Проверьте шаблон: доступны только перечисленные поля в фигурных скобках; в паре нужен {subject}.');
      throw new Error(typeof data.error === 'string' ? data.error : 'Не удалось загрузить настройки. Попробуйте ещё раз.');
    }
    return data;
  }

  function dirty() { return preferences && JSON.stringify(preferences) !== JSON.stringify(saved); }
  function updateSave() {
    $('constructorSave').disabled = saving || !previewValid || !dirty();
    $('constructorSave').textContent = saving ? 'Сохраняем…' : 'Сохранить';
    $('constructorSaveState').textContent = saving ? 'Сохраняем настройки' : dirty() ? 'Есть несохранённые изменения' : 'Все изменения сохранены';
    $('constructorDraft').textContent = preferences && preferences.preset === 'custom' ? 'Мой шаблон' : 'Ваш шаблон';
    document.querySelectorAll('[data-preset]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.preset === preferences?.preset)));
  }

  function sortItem(kind, key, row, index, length) {
    const item = document.createElement('div');
    item.className = 'constructor-sort-item'; item.draggable = true;
    item.dataset.kind = kind; item.dataset.key = key;
    if (row !== null) item.dataset.row = row;
    const label = document.createElement('span'); label.textContent = kind === 'block' ? blocks[key] : fields[key]; item.append(label);
    for (const [action, text, description, disabled] of [
      ['up', '↑', 'Переместить выше', index === 0], ['down', '↓', 'Переместить ниже', index === length - 1],
      ...(kind === 'field' ? [['row', 'Строка ↔', 'Перенести в другую строку', false],
        ...(key !== 'subject' ? [['remove', '×', 'Убрать поле', false]] : [])] : [])
    ]) {
      const button = document.createElement('button'); button.type = 'button'; button.dataset.action = action;
      button.textContent = text; button.setAttribute('aria-label', description + ': ' + label.textContent); button.disabled = disabled; item.append(button);
    }
    return item;
  }

  function renderControls() {
    document.querySelectorAll('[data-pref]').forEach(input => {
      const value = preferences[input.dataset.pref];
      if (input.type === 'checkbox') input.checked = value; else input.value = value;
    });
    $('constructorLessonTemplate').value = preferences.lesson_template || '';
    $('constructorBlocks').replaceChildren(...preferences.blocks.map((key, index) => sortItem('block', key, null, index, preferences.blocks.length)));
    $('constructorRows').replaceChildren();
    preferences.lesson_rows.forEach((row, index) => {
      const section = document.createElement('div'); section.className = 'constructor-row';
      const heading = document.createElement('h3'); heading.textContent = 'Строка ' + (index + 1); section.append(heading);
      row.forEach((key, fieldIndex) => section.append(sortItem('field', key, index, fieldIndex, row.length)));
      $('constructorRows').append(section);
    });
    const used = preferences.lesson_rows.flat();
    const remaining = Object.keys(fields).filter(key => !used.includes(key));
    $('constructorAddField').replaceChildren(...remaining.map(key => { const option = document.createElement('option'); option.value = key; option.textContent = fields[key]; return option; }));
    $('constructorAddButton').disabled = !remaining.length; $('constructorAddField').disabled = !remaining.length;
    document.querySelectorAll('[data-preset]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.preset === preferences.preset)));
    updateSave();
  }

  function changed(custom = true, rerender = false) {
    if (custom) preferences.preset = 'custom';
    generation++; previewValid = false; controller?.abort(); clearTimeout(timer);
    if (rerender) renderControls(); else updateSave();
    $('constructorMessage').setAttribute('aria-busy', 'true');
    $('constructorPreviewStatus').textContent = 'Обновляем предпросмотр…';
    $('constructorPreviewStatus').dataset.error = 'false';
    timer = setTimeout(updatePreview, 250);
  }

  async function showPreview(data, version) {
    const usePhoto = data.has_image && preferences.delivery_mode !== 'text';
    $('constructorImage').hidden = true;
    const photoOnly = usePhoto && preferences.delivery_mode === 'photo';
    $('constructorText').hidden = photoOnly && !data.photo_notice;
    if (photoOnly) $('constructorText').textContent = data.photo_notice || '';
    else $('constructorText').innerHTML = usePhoto && !data.separate_text ? data.caption_html : data.html;
    $('constructorPreviewInfo').textContent = data.example
      ? 'Это пример оформления: актуального расписания пока нет. Настройки применятся к вашей рассылке.'
      : 'Ваше расписание на ' + data.date;
    let explanation = data.separate_text ? 'Текст не помещается в подпись к фото — придёт отдельным сообщением.' : '';
    if (!data.has_image && preferences.delivery_mode !== 'text') explanation = 'Для этого расписания скрина нет — придёт текст.';
    $('constructorLength').textContent = data.length + ' символов. ' + explanation;
    if (usePhoto && data.entity) {
      const imageKey = data.entity + ':' + data.date + ':' + (data.image_key || '');
      try {
        if (!images.has(imageKey)) {
          const response = await fetch('/api/message-settings/image?entity=' + encodeURIComponent(data.entity), { headers: { 'X-Telegram-Init-Data': initData }, cache: 'no-store' });
          if (!response.ok) throw new Error('Фото недоступно');
          images.set(imageKey, URL.createObjectURL(await response.blob()));
        }
        if (version !== generation) return;
        $('constructorImage').src = images.get(imageKey); $('constructorImage').hidden = false;
      } catch (_) {
        if (version !== generation) return;
        $('constructorText').hidden = false;
        $('constructorLength').textContent += ' Скрин не загрузился; показан текст.';
      }
    }
  }

  async function updatePreview() {
    const version = generation;
    controller = new AbortController();
    try {
      const data = await request('/preview', 'POST', { preferences, entity }, controller.signal);
      if (version !== generation) return;
      await showPreview(data, version);
      if (version !== generation) return;
      previewValid = true; $('constructorPreviewStatus').textContent = '';
      $('constructorMessage').removeAttribute('aria-busy'); updateSave();
    } catch (error) {
      if (error.name === 'AbortError' || version !== generation) return;
      previewValid = false; $('constructorMessage').removeAttribute('aria-busy');
      $('constructorPreviewStatus').textContent = error.message; $('constructorPreviewStatus').dataset.error = 'true'; updateSave();
    }
  }

  $('constructorForm').addEventListener('input', event => {
    if (!preferences) return;
    const key = event.target.dataset.pref;
    if (key) {
      preferences[key] = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
      const field = Object.keys(fieldToggles).find(field => fieldToggles[field] === key);
      let rerender = false;
      if (field && preferences[key] && !preferences.lesson_template && !preferences.lesson_rows.flat().includes(field)) {
        const row = field === 'time' ? preferences.lesson_rows.find(row => row.includes('subject')) : preferences.lesson_rows.at(-1);
        row.splice(field === 'time' ? row.indexOf('subject') : row.length, 0, field); rerender = true;
      }
      changed(!['notifications_enabled', 'notify_updates', 'silent'].includes(key), rerender);
    } else if (event.target.id === 'constructorLessonTemplate') {
      preferences.lesson_template = event.target.value || null; changed();
    }
  });

  $('constructorForm').addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || !preferences) return;
    if (button.dataset.preset) {
      const template = presets.find(preset => preset.id === button.dataset.preset);
      const notifications = Object.fromEntries(['notifications_enabled', 'notify_updates', 'silent'].map(key => [key, preferences[key]]));
      preferences = { ...clone(template.preferences), ...notifications }; changed(false, true); return;
    }
    const item = button.closest('.constructor-sort-item');
    if (!item) return;
    const kind = item.dataset.kind, key = item.dataset.key;
    const row = Number(item.dataset.row);
    const array = kind === 'block' ? preferences.blocks : preferences.lesson_rows[row];
    const index = array.indexOf(key), action = button.dataset.action;
    if (action === 'remove' && kind === 'field' && key !== 'subject') {
      array.splice(index, 1); preferences.lesson_rows = preferences.lesson_rows.filter(value => value.length);
      if (fieldToggles[key]) preferences[fieldToggles[key]] = false;
    } else if (action === 'row') {
      if (preferences.lesson_rows.length === 1) preferences.lesson_rows.push([]);
      const destination = (row + 1) % preferences.lesson_rows.length;
      preferences.lesson_rows[destination].push(key); array.splice(index, 1);
      preferences.lesson_rows = preferences.lesson_rows.filter(value => value.length);
    } else {
      const next = index + (action === 'up' ? -1 : 1);
      if (next < 0 || next >= array.length) return;
      [array[index], array[next]] = [array[next], array[index]];
    }
    if (kind === 'field') preferences.lesson_template = null;
    changed(true, true);
  });

  $('constructorForm').addEventListener('dragstart', event => {
    const item = event.target.closest('.constructor-sort-item');
    if (item) event.dataTransfer.setData('text/plain', JSON.stringify({ kind: item.dataset.kind, key: item.dataset.key, row: item.dataset.row }));
  });
  $('constructorForm').addEventListener('dragover', event => { if (event.target.closest('.constructor-sort-item')) event.preventDefault(); });
  $('constructorForm').addEventListener('drop', event => {
    const target = event.target.closest('.constructor-sort-item'); if (!target) return; event.preventDefault();
    let source; try { source = JSON.parse(event.dataTransfer.getData('text/plain')); } catch (_) { return; }
    if (source.kind !== target.dataset.kind || source.key === target.dataset.key) return;
    const from = source.kind === 'block' ? preferences.blocks : preferences.lesson_rows[Number(source.row)];
    const to = source.kind === 'block' ? preferences.blocks : preferences.lesson_rows[Number(target.dataset.row)];
    if (!from || !to || !from.includes(source.key)) return;
    from.splice(from.indexOf(source.key), 1); to.splice(to.indexOf(target.dataset.key), 0, source.key);
    preferences.lesson_rows = preferences.lesson_rows.filter(row => row.length);
    if (source.kind === 'field') preferences.lesson_template = null;
    changed(true, true);
  });

  $('constructorAddButton').addEventListener('click', () => {
    const key = $('constructorAddField').value;
    if (!key || preferences.lesson_rows.flat().includes(key)) return;
    if (fieldToggles[key]) preferences[fieldToggles[key]] = true;
    preferences.lesson_rows[preferences.lesson_rows.length - 1].push(key); preferences.lesson_template = null; changed(true, true);
  });
  $('constructorUseBlocks').addEventListener('click', () => { preferences.lesson_template = null; changed(true, true); });
  $('constructorEntity').addEventListener('change', event => { entity = event.target.value; changed(false); });
  $('constructorReset').addEventListener('click', () => {
    const notifications = Object.fromEntries(['notifications_enabled', 'notify_updates', 'silent'].map(key => [key, preferences[key]]));
    preferences = { ...clone(presets.find(preset => preset.id === 'full').preferences), ...notifications }; changed(false, true);
  });
  $('constructorForm').addEventListener('submit', async event => {
    event.preventDefault(); if (!dirty() || !previewValid || saving) return;
    const submitted = clone(preferences); saving = true; $('constructorControls').disabled = true; $('constructorReset').disabled = true; updateSave();
    try {
      const result = await request('', 'PUT', submitted);
      preferences = result.preferences; saved = clone(preferences); renderControls(); status('Сохранено. Следующие сообщения придут в выбранном виде.');
      tg?.HapticFeedback?.notificationOccurred('success');
    } catch (error) { status(error.message, true); }
    finally { saving = false; $('constructorControls').disabled = false; $('constructorReset').disabled = false; updateSave(); }
  });
  window.addEventListener('beforeunload', event => {
    if (dirty()) { event.preventDefault(); event.returnValue = ''; }
  });

  async function load() {
    if (!initData) { status('Откройте /settings в @kkepik_bot и нажмите «Конструктор и предпросмотр». Здесь сохраняются только ваши настройки.', true); return; }
    try {
      const data = await request('');
      preferences = data.preferences; saved = clone(preferences); presets = data.presets; entity = data.preview.entity;
      $('constructorPresets').replaceChildren(...presets.map(preset => {
        const button = document.createElement('button'); button.type = 'button'; button.dataset.preset = preset.id;
        const title = document.createElement('strong'); title.textContent = preset.name;
        const description = document.createElement('span'); description.textContent = preset.description;
        button.append(title, description); return button;
      }));
      $('constructorEntity').replaceChildren(...data.preview.choices.map(choice => { const option = document.createElement('option'); option.value = choice.key; option.textContent = choice.label; return option; }));
      $('constructorEntity').value = entity; $('constructorEntityLabel').hidden = data.preview.choices.length < 2;
      renderControls(); await showPreview(data.preview, generation); previewValid = true;
      $('constructorControls').disabled = false; $('constructorReset').disabled = false; $('constructorMessage').removeAttribute('aria-busy');
      status('Настройки сохранены лично для вас. Можно примерить любой шаблон.'); updateSave();
    } catch (error) { status(error.message, true); }
  }
  load();
})();
