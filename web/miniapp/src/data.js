import { useEffect, useState } from 'preact/hooks';

export const tg = window.Telegram?.WebApp;
export const initData = tg?.initData || '';
const launch = window.kkepikLaunch || {};
delete window.kkepikLaunch;
const actor = tg?.initDataUnsafe?.user?.id || new URLSearchParams(initData).get('user')?.match(/"id":\s*(\d+)/)?.[1];
const storageKey = actor ? 'kkepik:miniapp:v1:' + actor : '';
const pending = new Map();
const cache = new Map();
const listeners = new Map();
const MAX_CACHE = 36;
const timezone = 'Europe/Moscow';
const isoFormat = new Intl.DateTimeFormat('sv-SE', { timeZone: timezone });
const dayFormat = new Intl.DateTimeFormat('ru-RU', { weekday: 'long', day: 'numeric', month: 'long', timeZone: timezone });

export const today = () => isoFormat.format(new Date());
export const dateAtNoon = iso => new Date(iso + 'T12:00:00+03:00');
export function dateLabel(iso) {
    const label = dayFormat.format(dateAtNoon(iso));
    return label[0].toLocaleUpperCase('ru-RU') + label.slice(1);
}
export const apiDate = iso => iso.split('-').reverse().join('.');
export const isoDate = d => d?.includes('.') ? d.split('.').reverse().join('-') : d;
export const shift = (iso, delta) => isoFormat.format(new Date(+dateAtNoon(iso) + delta * 86400000));
export const holiday = iso => dateAtNoon(iso).getUTCDay() === 0 || window.kkepikCalendar?.isHoliday(iso);
export function studyDay(iso, delta) {
    let next = shift(iso, delta);
    while (holiday(next)) next = shift(next, delta);
    return next;
}

export const haptic = kind => window.kkepikHaptics?.play(kind);

export class ApiError extends Error {
    constructor(message, status) {
        super(message);
        this.status = status;
    }
}

export async function api(path, { method = 'GET', body } = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
        const response = await fetch(path, {
            method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
            headers: { 'X-Telegram-Init-Data': initData, ...(body ? { 'Content-Type': 'application/json' } : {}) },
            body: body ? JSON.stringify(body) : undefined,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) {
            throw new ApiError(payload.error || payload.detail || 'Не удалось загрузить данные.', response.status);
        }
        return payload;
    } finally {
        clearTimeout(timeout);
    }
}

if (storageKey) {
    try {
        const rows = JSON.parse(localStorage.getItem(storageKey) || '[]');
        for (const [key, entry] of rows) {
            if (entry && Date.now() - entry.at < 3 * 86400000 && key.startsWith('schedule:')) cache.set(key, entry);
        }
    } catch {
        // Telegram may disable storage.
    }
}

function persist() {
    if (!storageKey) return;
    const schedules = [...cache.entries()].filter(([key, entry]) => key.startsWith('schedule:') && entry.value?.schedule);
    try { localStorage.setItem(storageKey, JSON.stringify(schedules.slice(-10))); } catch {}
}

export function store(key, value) {
    cache.delete(key);
    cache.set(key, { value, at: Date.now() });
    if (cache.size > MAX_CACHE) {
        const stale = [...cache.keys()].find(k => !listeners.has(k) && k.startsWith('schedule:'));
        if (stale) cache.delete(stale);
    }
    if (key.startsWith('schedule:')) persist();
    listeners.get(key)?.forEach(run => run());
}

export const peek = key => cache.get(key)?.value;
export function query(key, fetcher, { ttl = 30000, force = false } = {}) {
    if (pending.has(key)) return pending.get(key);
    const entry = cache.get(key);
    if (!force && entry && Date.now() - entry.at < ttl) return Promise.resolve(entry.value);
    const request = Promise.resolve().then(fetcher).then(value => {
        if (cache.get(key) === entry) store(key, value);
        return peek(key) ?? value;
    }).finally(() => pending.delete(key));
    pending.set(key, request);
    return request;
}

export function useQuery(key, fetcher, options) {
    const [, redraw] = useState(0);
    const [problem, setProblem] = useState(null);
    const [retry, setRetry] = useState(0);
    const ttl = options?.ttl;
    useEffect(() => {
        if (!key) return;
        let active = true;
        const refresh = () => redraw(count => count + 1);
        const updates = listeners.get(key) || new Set();
        listeners.set(key, updates);
        updates.add(refresh);
        function refreshFromServer(force = false) {
            query(key, fetcher, { ttl, force }).then(() => {
                if (active) setProblem(null);
            }).catch(error => {
                if (active) setProblem({ key, error });
            });
        }
        function onResume() { if (!document.hidden && navigator.onLine) refreshFromServer(); }
        refreshFromServer(retry > 0);
        document.addEventListener('visibilitychange', onResume);
        window.addEventListener('online', onResume);
        tg?.onEvent?.('activated', onResume);
        return () => {
            active = false;
            document.removeEventListener('visibilitychange', onResume);
            window.removeEventListener('online', onResume);
            tg?.offEvent?.('activated', onResume);
            updates.delete(refresh);
            if (!updates.size) listeners.delete(key);
        };
    }, [key, retry, ttl]);
    return [key ? peek(key) : undefined, problem?.key === key ? problem.error : null, () => setRetry(n => n + 1)];
}

const catalogNames = { group: 'groups', teacher: 'teachers', audience: 'audiences' };
export const catalogKind = type => catalogNames[type];
export const canWarmSchedules = () => navigator.onLine && !navigator.connection?.saveData && !/2g$/.test(navigator.connection?.effectiveType || '');
export const scheduleKey = (iso, type = 'me', entity = '') => 'schedule:' + JSON.stringify([iso, type, entity]);
export async function loadSchedule(iso, type = 'me', entity = '') {
    if (holiday(iso)) return { schedule: [], date: apiDate(iso), holiday: true };
    const params = new URLSearchParams({ date: apiDate(iso) });
    if (type !== 'me') params.set(type, entity);
    try {
        return await api('/api/schedule/' + type + '?' + params);
    } catch (error) {
        if (error.status === 404) return { unavailable: true, date: apiDate(iso) };
        throw error;
    }
}

export const warmSchedule = (iso, type = 'me', entity = '') => query(
    scheduleKey(iso, type, entity), () => loadSchedule(iso, type, entity), { ttl: 45000 },
);
export const predictionKey = (iso, group) => 'prediction:' + JSON.stringify([iso, group]);
export const loadPrediction = (iso, group) => api('/api/schedule/prediction?' + new URLSearchParams({ date: apiDate(iso), group }));
export const reactionsFor = (iso) => api('/api/schedule/reactions?date=' + iso);
export const toggleReaction = (iso, reaction) => api('/api/schedule/reactions/toggle', { method: 'POST', body: { date: iso, reaction, tgWebAppData: initData } });
export async function saveFavorite(action, value) {
    try {
        await api('/api/favorites/' + action, { method: 'POST', body: { ...value, tgWebAppData: initData } });
    } catch (error) {
        if (action !== 'add' || error.message !== 'Уже в избранном') throw error;
    }
    const rest = (peek('favorites') || []).filter(item =>
        item.entity_type !== value.entity_type || item.entity_id !== value.entity_id);
    store('favorites', action === 'add' ? [value, ...rest] : rest);
}
export const dutyList = () => api('/api/duties');
export const dutyDetail = sid => api('/api/duties/' + sid);
export const dutyAction = (sid, body) => api('/api/duties/' + sid + '/actions', { method: 'POST', body });

export function bootstrap(iso) {
    return query('bootstrap', async () => {
        const prefetch = launch.initData === initData && launch.iso === iso ? await launch.bootstrap : null;
        const payload = prefetch || await api('/api/bootstrap?date=' + apiDate(iso) + '&adjacent=0');
        if (payload.schedule) store(scheduleKey(iso), payload.schedule);
        if (payload.reaction_date === iso) store('reaction:' + iso, { reactions: payload.reactions, user_reactions: payload.user_reactions });
        for (const adjacent of Object.values(payload.adjacent || {})) {
            if (adjacent.available && adjacent.schedule) store(scheduleKey(isoDate(adjacent.date)), adjacent.schedule);
        }
        store('favorites', payload.favorites || []);
        return payload;
    }, { ttl: 45000 });
}
