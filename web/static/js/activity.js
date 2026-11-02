/* Semantic operational events. Authentication stays in memory, never in events. */
(function () {
    'use strict';
    if (window.kkepikActivity) return;
    const tg = window.Telegram?.WebApp;
    const initData = tg?.initData || new URLSearchParams(location.search).get('tgWebAppData') || '';
    if (!initData) return;
    let actor;
    try { actor = JSON.parse(new URLSearchParams(initData).get('user') || '{}').id; } catch (_) {}
    if (!Number.isSafeInteger(actor) || actor <= 0) return;
    const page = /^\/games\/[a-z0-9-]+$/.test(location.pathname) ? location.pathname :
        ['/', '/duties', '/settings', '/schedule_search', '/sudoku', '/attendance', '/congratulate', '/event-end'].includes(location.pathname) ? location.pathname : '/other';
    const storageKey = 'kkepik:activity:v1:' + actor;
    const transports = new WeakMap();
    let queue = [], busy = false, blockedAuth = false, timer, selection = null, lastView = '', retryMs = 2000;
    try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
        if (Array.isArray(saved)) queue = saved.filter(e => e && typeof e.id === 'string' && Date.now() - e.client_at_ms < 172800000).slice(-80);
    } catch (_) {}
    function persist() { try { localStorage.setItem(storageKey, JSON.stringify(queue)); } catch (_) {} }
    function schedule(delay = 300) { clearTimeout(timer); if (!blockedAuth && queue.length && navigator.onLine) timer = setTimeout(flush, delay); }
    function track(action, fields = {}) {
        queue.push({ id: crypto.randomUUID?.() || Date.now().toString(36) + '-' + Math.random().toString(36).slice(2),
            action, client_at_ms: Date.now(), ...fields, details: { page, ...(fields.details || {}) } });
        queue = queue.slice(-80); persist(); schedule();
    }
    async function flush() {
        clearTimeout(timer);
        if (busy || !queue.length || !navigator.onLine) return;
        busy = true;
        const batch = queue.slice(0, 20), ids = new Set(batch.map(e => e.id));
        const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 5000);
        try {
            const response = await fetch('/api/activity/events', { method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': initData },
                body: JSON.stringify({ events: batch }), keepalive: true, cache: 'no-store', signal: controller.signal });
            if (response.ok || response.status === 400) {
                queue = queue.filter(e => !ids.has(e.id)); persist(); retryMs = 2000;
            } else if (response.status === 401 || response.status === 403) {
                // Keep the queue for the next properly authenticated launch.
                blockedAuth = true;
                return;
            } else retryMs = Math.min(retryMs * 2, 60000);
        } catch (_) { retryMs = Math.min(retryMs * 2, 60000); }
        finally { clearTimeout(timeout); busy = false; schedule(retryMs); }
    }
    function iso(value) {
        if (value instanceof Date) return [value.getFullYear(), String(value.getMonth() + 1).padStart(2, '0'), String(value.getDate()).padStart(2, '0')].join('-');
        return value;
    }
    function shown(data, date, outcome = 'view') {
        const type = data?.group ? 'group' : data?.teacher ? 'teacher' : data?.audience ? 'audience' : null;
        if (!type) return;
        const value = data[type], dateString = iso(data.date || date), viewKey = [type, value, dateString, outcome].join('|');
        requestAnimationFrame(function () {
            if (document.hidden || tg?.isActive === false || viewKey === lastView) return;
            const content = document.getElementById('main-content');
            if (content && !content.getClientRects().length) return;
            lastView = viewKey;
            const key = type + ':' + value;
            track('schedule.' + outcome, { entity_type: type, entity_id: value, date: dateString,
                details: { is_favorite: Boolean(selection?.key === key && selection.favorite),
                    reason: selection?.key === key ? 'entity_switch' : 'render',
                    transport: transports.get(data) || 'unknown' } });
        });
    }
    window.kkepikActivity = { track, flush, shown,
        note(data, transport) { if (data && typeof data === 'object') transports.set(data, transport); return data; },
        selected(entity, favorite) {
            selection = { key: entity.entity_type + ':' + entity.entity_id, favorite: Boolean(favorite) };
            lastView = '';
            track('schedule.select', { entity_type: entity.entity_type, entity_id: entity.entity_id,
                details: { is_favorite: Boolean(favorite) } });
        } };
    document.addEventListener('scheduleDateChanged', function () {
        const state = window.scheduleState || {}, data = state.currentScheduleData;
        if (data && !data.error500) shown(data, state.displayedDate, state.hasRealLessons ? 'view' : 'empty');
    });
    const controls = new Set(['groupSelectorToggle', 'groupSelectorManage', 'reviewCard', 'progress_bar',
        'schedulePrevButton', 'scheduleNextButton', 'constructorReset', 'canvasArchive', 'dutyRefresh']);
    document.addEventListener('click', function (event) {
        const control = event.target.closest?.('button, a, summary');
        if (!control) return;
        if (control.dataset.tab && ['today', 'calendar', 'members', 'settings'].includes(control.dataset.tab)) {
            track('ui.action', { details: { control: 'duty_tab', tab: control.dataset.tab } });
        } else if (controls.has(control.id)) track('ui.action', { details: { control: control.id } });
    });
    let opened = false;
    function openPage() { if (!opened && !document.hidden) { opened = true; track('page.open'); } }
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden && opened) {
            track('page.resume');
            const state = window.scheduleState;
            if (state?.currentScheduleData) { lastView = ''; shown(state.currentScheduleData, state.displayedDate, state.hasRealLessons ? 'view' : 'empty'); }
        }
        openPage();
        if (document.hidden) flush();
    });
    window.addEventListener('online', () => schedule());
    window.addEventListener('pagehide', flush);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', openPage, { once: true });
    else openPage();
    schedule();
}());
