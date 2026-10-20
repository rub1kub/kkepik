// Read-only browser checks for the public schedule pages. No Telegram login,
// student records, or bot sends; Chrome uses a fresh, temporary profile.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { WebSocket } = require('undici');
const origin = process.env.KNOWLEDGE_QA_ORIGIN || 'https://kkepik.rub1kub.ru';
const canonicalOrigin = 'https://kkepik.rub1kub.ru';
const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), 'kkepik-public-browser-'));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  const response = await fetch(origin + '/api/v2/catalog');
  assert.equal(response.status, 200);
  const catalog = await response.json();
  assert.equal(catalog.timezone, 'Europe/Moscow');
  const group = catalog.groups.find(value => value === '103-Д9-3ИНС');
  assert.ok(group, 'Expected the requested group in the public catalog');
  const date = catalog.dates.filter(value => value.startsWith('2026-10')).sort().at(-1);
  assert.ok(date, 'Expected a real archived October date');
  const groupPath = '/colleges/kkep/groups/' + encodeURIComponent(group);
  const pagePath = groupPath + '/' + date;
  const html = await (await fetch(origin + pagePath)).text();
  assert.ok(html.includes(group), 'Schedule entity must be in server-rendered HTML');
  assert.ok(html.includes(date), 'Exact date must be in server-rendered HTML');
  assert.match(html, /canonical/);
  const chrome = spawn('/usr/bin/google-chrome', [
    '--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
    '--renderer-process-limit=1', '--disable-extensions', '--disable-background-networking',
    '--remote-debugging-port=19227', '--user-data-dir=' + path.join(artifacts, 'chrome'),
    'about:blank'
  ], { stdio: 'ignore' });
  let socket;
  try {
    let page;
    for (let i = 0; i < 80; i++) {
      try {
        page = (await (await fetch('http://127.0.0.1:19227/json/list')).json()).find(item => item.type === 'page');
        if (page) break;
      } catch (_) {}
      await sleep(100);
    }
    assert.ok(page, 'Chrome started');
    socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    let sequence = 0;
    const pending = new Map(), errors = [];
    const call = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 12000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
    socket.addEventListener('message', event => {
      const value = JSON.parse(event.data);
      const task = pending.get(value.id);
      if (task) {
        pending.delete(value.id); clearTimeout(task.timer);
        value.error ? task.reject(new Error(value.error.message)) : task.resolve(value.result);
      } else if (value.method === 'Runtime.exceptionThrown') {
        errors.push(value.params.exceptionDetails.text);
      }
    });
    const evaluate = async expression => {
      const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
      return result.result.value;
    };
    await call('Runtime.enable'); await call('Page.enable');
    // Ensure the page remains usable when its optional JavaScript is disabled.
    await call('Emulation.setScriptExecutionDisabled', { value: true });
    const checks = [];
    for (const width of [390, 1280]) {
      await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: width < 600 });
      await call('Page.navigate', { url: origin + pagePath });
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await evaluate('document.readyState === "complete" && document.body.innerText.includes(' + JSON.stringify(group) + ')')) break;
        await sleep(100);
      }
      const facts = await evaluate('({title:document.title,text:document.body.innerText,width:innerWidth,scrollWidth:document.documentElement.scrollWidth,canonical:document.querySelector("link[rel=canonical]")?.href,links:[...document.querySelectorAll("a")].map(a=>a.href)})');
      assert.ok(facts.text.includes(group));
      assert.ok(facts.canonical.startsWith(canonicalOrigin + '/colleges/kkep/groups/'));
      assert.ok(facts.scrollWidth <= facts.width + 1, 'No horizontal overflow at width ' + width);
      assert.ok(facts.links.some(url => url.includes('/api/v2/sources/')), 'Public source link');
      const screenshot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      fs.writeFileSync(path.join(artifacts, 'schedule-' + width + '.png'), Buffer.from(screenshot.data, 'base64'));
      checks.push({ width, title: facts.title, horizontalOverflow: false, javascriptRequired: false });
    }
    assert.deepEqual(errors, []);
    await call('Emulation.setScriptExecutionDisabled', { value: false });
    await call('Page.navigate', { url: origin + '/colleges/kkep/groups' });
    await sleep(500);
    assert.deepEqual(errors, [], 'Unsupported WebMCP must not cause page errors');
    // Exercise the draft integration without assuming Chrome implements it.
    await call('Page.addScriptToEvaluateOnNewDocument', {source:
      'window.__qaTools=[]; Object.defineProperty(document,"modelContext",{value:{registerTool:tool=>{window.__qaTools.push(tool);}}});'});
    await call('Page.navigate', {url: origin + '/colleges/kkep/groups'});
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await evaluate('window.__qaTools?.length===3')) break;
      await sleep(100);
    }
    const tools = await evaluate('window.__qaTools.map(t=>({name:t.name,readOnly:t.annotations.readOnlyHint}))');
    assert.equal(tools.length, 3);
    assert.ok(tools.every(tool => tool.readOnly));
    const catalogResult = await evaluate('window.__qaTools.find(t=>t.name==="get_kkep_catalog").execute({})');
    assert.ok(catalogResult.groups.includes(group));
    assert.ok(!Object.hasOwn(catalogResult, 'group_dates'));
    const scheduleResult = await evaluate('window.__qaTools.find(t=>t.name==="get_kkep_schedule").execute(' + JSON.stringify({entity_type:'group',entity:group,date}) + ')');
    assert.equal(scheduleResult.days[0].date, date);
    assert.ok(scheduleResult.days[0].source.url.includes('/api/v2/sources/'));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ checks, webMcpTools: tools, javascriptErrors: errors, artifacts }, null, 2));
  } finally {
    if (socket) socket.close();
    chrome.kill('SIGTERM');
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
