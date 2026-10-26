const CACHE_VERSION = 'kkepik-shell-20261026-pixel-archive-1';
const SHELL_URL = '/';
const APP_SHELL = [
    SHELL_URL,
    '/static/css/main.css?v=20261026-pixel-archive-1',
    '/static/css/message_settings.css?v=20261026-pixel-archive-1',
    '/static/css/duties.css?v=20261026-pixel-archive-1',
    '/static/js/duties.js?v=20261026-pixel-archive-1',
    '/static/js/vendor/telegram-web-app.63.js',
    '/static/js/app_bootstrap.js?v=20261026-pixel-archive-1',
    '/static/js/effects.js?v=20261026-pixel-archive-1',
    '/static/js/swipe_detector.js?v=20261026-pixel-archive-1',
    '/static/js/schedule.js?v=20261026-pixel-archive-1',
    '/static/js/main.js?v=20261026-pixel-archive-1',
    '/static/js/kepik-entry.js?v=20261026-pixel-archive-1',
    '/static/js/message_settings.js?v=20261026-pixel-archive-1',
    '/static/js/image_cards.js?v=20261026-pixel-archive-1',
    '/static/js/schedule_reactions.js?v=20261026-pixel-archive-1',
    '/static/js/group_selector.js?v=20261026-pixel-archive-1',
    '/static/js/miniapp_presence.js?v=20261026-pixel-archive-1',
    '/static/js/pixel_state.js?v=20261026-pixel-archive-1',
    '/static/js/pixel_canvas.js?v=20261026-pixel-archive-1',
    '/static/js/pixel_replay.js?v=20261026-pixel-archive-1',
    '/static/css/pixel_replay.css?v=20261026-pixel-archive-1',
    '/static/js/pixel_chat.js?v=20261026-pixel-archive-1',
    '/static/css/pixel_chat.css?v=20261026-pixel-archive-1',
];

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE_VERSION)
            .then(function (cache) { return cache.addAll(APP_SHELL); })
            .then(function () { return self.skipWaiting(); })
    );
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys()
            .then(function (keys) {
                return Promise.all(keys
                    .filter(function (key) { return key.startsWith('kkepik-shell-') && key !== CACHE_VERSION; })
                    .map(function (key) { return caches.delete(key); }));
            })
            .then(function () { return self.clients.claim(); })
    );
});

function isPrivateRequest(url) {
    return url.pathname.startsWith('/api/')
        // Public schedules carry changing, date-specific information. Never
        // fall back to an old app-shell copy or strip their query parameters.
        || url.pathname.startsWith('/colleges/')
        || url.pathname === '/agents'
        || url.pathname === '/mcp'
        || url.pathname === '/duties'
        || url.pathname === '/robots.txt'
        || url.pathname === '/sitemap.xml'
        || url.pathname === '/llms.txt'
        || url.pathname === '/validate'
        || url.pathname.startsWith('/admin/')
        || url.pathname.startsWith('/vpn/');
}

async function staticResponse(request) {
    const cache = await caches.open(CACHE_VERSION);
    const cached = await cache.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    if (response.ok) await cache.put(request, response.clone());
    return response;
}

function navigationCacheKey(url) {
    return new Request(url.origin + url.pathname);
}

function refreshNavigation(request, cacheKey) {
    return caches.open(CACHE_VERSION).then(function (cache) {
        return fetch(request).then(function (response) {
            if (response.ok) cache.put(cacheKey, response.clone());
            return response;
        });
    });
}

self.addEventListener('fetch', function (event) {
    if (event.request.method !== 'GET') return;
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin || isPrivateRequest(url)) return;

    if (event.request.mode === 'navigate') {
        const cacheKey = navigationCacheKey(url);
        const update = refreshNavigation(event.request, cacheKey);
        event.waitUntil(update.then(function () {}).catch(function () {}));
        event.respondWith(
            caches.open(CACHE_VERSION).then(function (cache) {
                return cache.match(cacheKey).then(function (cached) {
                    if (!cached) return update;
                    const timeout = new Promise(function (resolve) {
                        setTimeout(function () { resolve(cached); }, 1200);
                    });
                    return Promise.race([
                        update.catch(function () { return cached; }),
                        timeout,
                    ]);
                });
            })
        );
        return;
    }
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(staticResponse(event.request));
    }
});
