(function () {
    'use strict';

    const tg = window.Telegram && window.Telegram.WebApp;
    const initData = tg ? tg.initData : '';
    const telegramUser = tg && tg.initDataUnsafe ? tg.initDataUnsafe.user : null;
    const userId = telegramUser && telegramUser.id ? String(telegramUser.id) : '';
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    const ownScriptUrl = document.currentScript ? new URL(document.currentScript.src) : null;
    const staticVersion = ownScriptUrl ? ownScriptUrl.searchParams.get('v') : '';
    const lowData = Boolean(
        (connection && connection.saveData)
        || (connection && /(^|-)2g$/.test(connection.effectiveType || ''))
    );
    const cacheMaxAge = 7 * 24 * 60 * 60 * 1000;

    function initialScheduleDate() {
        const date = new Date();
        if (date.getDay() === 0) date.setDate(date.getDate() + 1);
        return date;
    }

    function formatApiDate(date) {
        return [
            String(date.getDate()).padStart(2, '0'),
            String(date.getMonth() + 1).padStart(2, '0'),
            date.getFullYear(),
        ].join('.');
    }

    function safeRead(key) {
        if (!key) return null;
        try {
            const cached = JSON.parse(localStorage.getItem(key) || 'null');
            if (!cached || Date.now() - cached.cachedAt > cacheMaxAge) return null;
            return cached.value;
        } catch (error) {
            return null;
        }
    }

    function safeWrite(key, value) {
        if (!key || !value) return;
        try {
            localStorage.setItem(key, JSON.stringify({ cachedAt: Date.now(), value }));
        } catch (error) {
            // Storage can be unavailable in private WebViews; the network path still works.
        }
    }

    const requestedDate = formatApiDate(initialScheduleDate());
    const bootstrapKey = userId ? `kkepik:bootstrap:v2:${userId}:${requestedDate}` : '';
    const cachedBootstrap = safeRead(bootstrapKey);

    function scheduleKey(date) {
        return userId ? `kkepik:schedule:v2:${userId}:${date}` : '';
    }

    function getCachedSchedule(date) {
        return safeRead(scheduleKey(date));
    }

    function cacheSchedule(date, schedule) {
        if (date && schedule && Array.isArray(schedule.schedule)) {
            safeWrite(scheduleKey(date), schedule);
        }
    }

    function cacheAdjacentSchedules(data) {
        if (!data || !data.adjacent) return;
        ['previous', 'next'].forEach(function (direction) {
            const item = data.adjacent[direction];
            if (item && item.available && item.date && item.schedule) {
                cacheSchedule(item.date, item.schedule);
            }
        });
    }

    function setConnectionStatus(message, state) {
        const element = document.getElementById('connection-status');
        if (!element) return;
        element.textContent = message || '';
        element.dataset.state = state || '';
        element.hidden = !message;
    }

    async function fetchBootstrap() {
        if (!initData) {
            const error = new Error('Telegram initData отсутствует');
            error.auth = true;
            throw error;
        }

        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch(`/api/bootstrap?date=${encodeURIComponent(requestedDate)}`, {
                headers: { 'X-Telegram-Init-Data': initData },
                signal: controller.signal,
                cache: 'no-store',
            });
            const data = await response.json();
            if (response.status === 401 || response.status === 403) {
                const error = new Error(data.error || 'Ошибка авторизации Telegram');
                error.auth = true;
                throw error;
            }
            if (!response.ok || !data.success) {
                throw new Error(data.error || 'Не удалось загрузить приложение');
            }

            const cachedSchedule = getCachedSchedule(requestedDate);
            if (data.schedule_status >= 500 && cachedSchedule) {
                data.schedule = cachedSchedule;
                data.schedule_status = 200;
                data.from_cache = true;
            }
            cacheSchedule(requestedDate, data.schedule);
            cacheAdjacentSchedules(data);
            safeWrite(bootstrapKey, data);
            return data;
        } finally {
            clearTimeout(timeout);
        }
    }

    const appState = {
        initData,
        userId,
        requestedDate,
        lowData,
        latest: cachedBootstrap || null,
        getCachedSchedule,
        cacheSchedule,
        setConnectionStatus,
    };
    window.kkepikApp = appState;
    document.documentElement.classList.toggle('low-data', lowData);

    const networkReady = fetchBootstrap().then(data => {
        appState.latest = data;
        setConnectionStatus('', 'online');
        document.dispatchEvent(new CustomEvent('kkepik:bootstrap-updated', { detail: data }));
        return data;
    }).catch(error => {
        if (cachedBootstrap && !error.auth) {
            setConnectionStatus('Нет сети · показано сохранённое расписание', 'offline');
            return { ...cachedBootstrap, from_cache: true };
        }
        throw error;
    });

    if (cachedBootstrap) {
        const cached = { ...cachedBootstrap, from_cache: true };
        appState.ready = Promise.resolve(cached);
        setConnectionStatus('Показано сохранённое · обновляем', 'updating');
        networkReady.catch(function () {});
    } else {
        appState.ready = networkReady;
    }
    appState.networkReady = networkReady;

    function loadScripts(sources) {
        sources.forEach(function (source) {
            if (document.querySelector(`script[data-kkepik-source="${source}"]`)) return;
            const script = document.createElement('script');
            script.src = staticVersion ? `${source}?v=${encodeURIComponent(staticVersion)}` : source;
            script.async = true;
            script.dataset.kkepikSource = source;
            document.head.appendChild(script);
        });
    }

    function loadInteractiveScripts() {
        loadScripts([
            '/static/js/schedule_reactions.js',
            '/static/js/group_selector.js',
        ]);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadInteractiveScripts, { once: true });
    } else {
        loadInteractiveScripts();
    }

    appState.ready.then(function () {
        setTimeout(function () {
            loadScripts(['/static/js/image_cards.js']);
        }, lowData ? 1500 : 350);
    }).catch(function () {});

    if ('serviceWorker' in navigator) {
        window.addEventListener('load', function () {
            setTimeout(function () {
                navigator.serviceWorker.register('/service-worker.js', { updateViaCache: 'none' })
                    .then(function (registration) { return registration.update(); })
                    .catch(function () {});
            }, 750);
        }, { once: true });
    }
}());
