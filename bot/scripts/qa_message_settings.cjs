// Browser QA against qa_message_settings.py only. Never uses real bot credentials.
'use strict';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { WebSocket } = require('undici');
const token = 'constructor-qa-only-token';
const artifactDir = fs.mkdtempSync(path.join(os.tmpdir(), 'kkepik-constructor-browser-'));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const servers = [];
const serverDiagnostics = [];

function qaServer(command, mode, cwd) {
  const info = { mode, log: '', exit: null };
  serverDiagnostics.push(info);
  const server = spawn(command, [path.join(__dirname, 'qa_message_settings.py'), mode], { cwd, stdio: ['ignore', 'pipe', 'pipe'] });
  for (const stream of [server.stdout, server.stderr]) stream.on('data', data => { info.log = (info.log + data.toString()).slice(-2000); });
  server.on('exit', (code, signal) => { info.exit = { code, signal }; });
  servers.push(server);
}

function initData(uid = 42, timestamp = Math.floor(Date.now() / 1000)) {
  const entries = { auth_date: String(timestamp), user: JSON.stringify({ id: uid, first_name: 'QA' }) };
  const secret = crypto.createHmac('sha256', 'WebAppData').update(token).digest();
  const hash = crypto.createHmac('sha256', secret).update(Object.keys(entries).sort().map(key => key + '=' + entries[key]).join('\n')).digest('hex');
  return new URLSearchParams({ ...entries, hash }).toString();
}

async function http(endpoint, { method = 'GET', data, signed = initData() } = {}) {
  const response = await fetch('http://127.0.0.1:18080/message-settings' + endpoint, {
    method, headers: { 'X-Telegram-Init-Data': signed, 'Content-Type': 'application/json' },
    body: data === undefined ? undefined : JSON.stringify(data)
  });
  const payload = response.headers.get('content-type')?.includes('json') ? await response.json() : null;
  return { status: response.status, payload };
}

async function main() {
  qaServer(path.resolve(__dirname, '../venv/bin/python'), 'backend', path.resolve(__dirname, '..'));
  qaServer('/var/www/kkepik.ru/venv/bin/python', 'site', '/var/www/kkepik.ru');
  let online = false;
  for (let attempt = 0; attempt < 200; attempt++) {
    try { online = (await http('', { signed: '' })).status === 401 && (await fetch('http://127.0.0.1:18081/settings')).ok; if (online) break; } catch (_) {}
    await sleep(100);
  }
  assert.ok(online, 'Isolated QA servers did not start');
  assert.equal((await http('', { signed: '' })).status, 401);
  assert.equal((await http('', { signed: initData(42, 1) })).status, 401);
  assert.equal((await http('', { signed: initData() + '&user=x' })).status, 401);
  const initial = (await http('')).payload;
  assert.equal(initial.presets.length, 4);
  assert.equal(initial.preview.example, false, 'Expected actual October 6 schedule, not a sample');
  assert.equal(initial.preview.has_image, true);
  assert.equal((await http('/preview', { method: 'POST', data: { preferences: initial.preferences, entity: 'group:151-Д9-1ИНС' } })).status, 403);
  assert.equal((await http('/image?entity=group:151-%D0%949-1%D0%98%D0%9D%D0%A1')).status, 403);
  assert.equal((await http('', { method: 'PUT', data: { ...initial.preferences, user_id: 43 } })).status, 422);
  assert.equal((await http('/preview', { method: 'POST', data: { preferences: { ...initial.preferences, lesson_template: '{user.id}' } } })).status, 422);
  await http('/preview', { method: 'POST', data: { preferences: { ...initial.preferences, footer_text: 'DRAFT ONLY' } } });
  assert.equal((await http('')).payload.preferences.footer_text, '');
  console.log('HTTP authentication, ownership, validation, preview-without-saving: PASS');

  const chrome = spawn('/usr/bin/google-chrome', [
    '--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
    '--remote-debugging-port=19226', '--user-data-dir=' + path.join(artifactDir, 'chrome'), 'about:blank'
  ], { stdio: 'ignore' });
  let socket;
  try {
    let page;
    for (let attempt = 0; attempt < 60; attempt++) {
      try { page = (await (await fetch('http://127.0.0.1:19226/json/list')).json()).find(item => item.type === 'page'); if (page) break; } catch (_) {}
      await sleep(100);
    }
    assert.ok(page, 'Chrome did not start');
    socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
    let sequence = 0;
    const pending = new Map(), errors = [];
    function call(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++sequence;
        const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 12000);
        pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
      });
    }
    socket.addEventListener('message', event => {
      const value = JSON.parse(event.data);
      if (value.id && pending.has(value.id)) {
        const task = pending.get(value.id); pending.delete(value.id); clearTimeout(task.timer);
        value.error ? task.reject(new Error(value.error.message)) : task.resolve(value.result);
      } else if (value.method === 'Runtime.exceptionThrown') errors.push(value.params.exceptionDetails.text);
      else if (value.method === 'Page.javascriptDialogOpening') call('Page.handleJavaScriptDialog', { accept: true }).catch(() => {});
    });
    await call('Runtime.enable'); await call('Page.enable');
    async function evaluate(expression) {
      const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
      return result.result.value;
    }
    async function until(expression) {
      for (let count = 0; count < 80; count++) { if (await evaluate(expression)) return; await sleep(100); }
      throw new Error('UI timed out: ' + expression + ' ' + JSON.stringify(await evaluate("({status:document.getElementById('constructorStatus')?.textContent,preview:document.getElementById('constructorPreviewStatus')?.textContent})")));
    }
    async function ready() { await until("document.querySelectorAll('[data-preset]').length === 4 && !document.getElementById('constructorControls').disabled && !document.getElementById('constructorMessage').hasAttribute('aria-busy')"); }
    async function previewReady() { await until("!document.getElementById('constructorMessage').hasAttribute('aria-busy') && !document.getElementById('constructorPreviewStatus').textContent"); }
    async function setField(selector, value) {
      await evaluate(`(() => { const input=document.querySelector(${JSON.stringify(selector)}); ${typeof value === 'boolean' ? 'input.checked=' : 'input.value='}${JSON.stringify(value)}; input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
      await previewReady();
    }
    async function screenshot(filename) {
      await evaluate('document.fonts.ready');
      const result = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      fs.writeFileSync(path.join(artifactDir, filename), Buffer.from(result.data, 'base64'));
    }
    function launchUrl(dark = false) {
      const theme = dark ? { bg_color: '#17212b', secondary_bg_color: '#0e1621', text_color: '#f5f5f5', hint_color: '#788998', link_color: '#6ab2f2', button_color: '#5288c1', button_text_color: '#ffffff' } : {};
      return 'http://127.0.0.1:18081/settings#' + new URLSearchParams({ tgWebAppData: initData(), tgWebAppVersion: '8.0', tgWebAppPlatform: 'tdesktop', tgWebAppThemeParams: JSON.stringify(theme) });
    }
    await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await call('Page.navigate', { url: launchUrl() }); await ready();
    await sleep(150);
    await screenshot('desktop-full.png');
    const presetResults = [];
    for (const name of ['full', 'compact', 'time_focus', 'minimal']) {
      await evaluate(`document.querySelector('[data-preset="${name}"]').click()`); await previewReady();
      presetResults.push(await evaluate("document.getElementById('constructorText').textContent"));
      if (name === 'time_focus') assert.match(presetResults.at(-1), /Начало занятий/);
      if (name === 'minimal') {
        assert.equal(await evaluate("document.querySelector('[data-pref=show_times]').checked"), false);
        assert.equal(await evaluate("document.getElementById('constructorImage').hidden"), true);
      }
    }
    assert.equal(new Set(presetResults).size, 4);
    await setField('[data-pref=show_times]', true);
    assert.match(await evaluate("document.getElementById('constructorText').textContent"), /8:45–10:05/);
    await setField('[data-pref=footer_text]', '<img src=x onerror=alert(1)> Хорошего дня!');
    await setField('[data-pref=header_template]', 'Мой день — {date}');
    await evaluate("(() => {for(let i=0;i<5;i++){let button=document.querySelector('#constructorRows [data-key=subject] [data-action=up]');if(button.disabled)break;button.click();}})()"); await previewReady();
    assert.equal(await evaluate("document.querySelector('#constructorRows .constructor-sort-item').dataset.key"), 'subject');
    await evaluate("document.querySelector('#constructorRows [data-key=room] [data-action=row]').click()"); await previewReady();
    assert.equal(await evaluate("document.querySelectorAll('#constructorRows .constructor-row').length"), 2);
    await setField('#constructorLessonTemplate', '{time} → {subject}\n{room}');
    await evaluate("document.querySelector('#constructorBlocks [data-key=footer] [data-action=up]').click()"); await previewReady();
    assert.equal(await evaluate("document.getElementById('constructorLessonTemplate').value"), '{time} → {subject}\n{room}');
    assert.equal(await evaluate("document.getElementById('constructorText').querySelector('img') === null"), true);
    await evaluate("(() => {let input=document.getElementById('constructorLessonTemplate');input.value='{user.id}';input.dispatchEvent(new Event('input',{bubbles:true}));})()");
    await until("document.getElementById('constructorPreviewStatus').dataset.error === 'true'");
    assert.equal(await evaluate("document.getElementById('constructorSave').disabled"), true);
    await setField('#constructorLessonTemplate', '{time} → {subject}\n{room}');
    await setField('[data-pref=notifications_enabled]', false);
    await setField('[data-pref=notify_updates]', false);
    await setField('[data-pref=silent]', true);
    await evaluate("document.getElementById('constructorSave').click()");
    await until("document.getElementById('constructorStatus').textContent.startsWith('Сохранено') && !document.getElementById('constructorControls').disabled");
    const persisted = (await http('')).payload.preferences;
    assert.equal(persisted.footer_text, '<img src=x onerror=alert(1)> Хорошего дня!');
    assert.equal(persisted.preset, 'custom'); assert.equal(persisted.silent, true); assert.equal(persisted.notifications_enabled, false);
    assert.equal((await http('', { signed: initData(43) })).payload.preferences.preset, 'full');
    await call('Page.reload'); await ready();
    assert.equal(await evaluate("document.querySelector('[data-pref=footer_text]').value"), persisted.footer_text);
    assert.equal(await evaluate("document.getElementById('constructorSave').disabled"), true);
    console.log('All presets, field/block ordering, escaped custom template, invalid draft, save/reload, personal isolation: PASS');

    await evaluate("document.querySelector('[data-preset=time_focus]').click()"); await previewReady();
    assert.equal(await evaluate("document.querySelector('[data-pref=notifications_enabled]').checked"), false);
    await setField('[data-pref=delivery_mode]', 'text');
    await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await evaluate('window.scrollTo(0,0)'); await screenshot('mobile-time-focus.png');
    assert.equal(await evaluate('document.documentElement.scrollWidth <= 390'), true);
    assert.ok(await evaluate("document.getElementById('constructorPresets').getBoundingClientRect().top < document.getElementById('constructorPreview').getBoundingClientRect().top"));
    await evaluate("document.querySelector('[data-pref=footer_text]').scrollIntoView({block:'center'})");
    await screenshot('mobile-editor.png');
    await evaluate("document.querySelector('.constructor-preview-link').click()"); await sleep(150);
    assert.ok(await evaluate("document.getElementById('constructorPreview').getBoundingClientRect().top < 40"));
    await evaluate("window.Telegram.WebView.receiveEvent('theme_changed', {theme_params:{bg_color:'#17212b',secondary_bg_color:'#0e1621',text_color:'#f5f5f5',hint_color:'#788998',link_color:'#6ab2f2',button_color:'#5288c1',button_text_color:'#ffffff'}})");
    await screenshot('mobile-dark.png');
    assert.equal(await evaluate('document.documentElement.dataset.theme'), 'dark');
    assert.deepEqual(errors, []);
    console.log('390px mobile layout, preview navigation, dark theme, no JavaScript exceptions: PASS');
    console.log('Screenshots: ' + artifactDir);
  } finally { socket?.close(); chrome.kill('SIGTERM'); }
}
main().catch(error => { console.error(error.stack); console.log('QA server diagnostics: ' + JSON.stringify(serverDiagnostics)); console.log('Screenshots: ' + artifactDir); process.exitCode = 1; }).finally(() => servers.forEach(server => server.kill('SIGTERM')));
