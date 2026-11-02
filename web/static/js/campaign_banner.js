(function () {
    'use strict';
    const banner = document.getElementById('championshipBanner');
    if (!banner) return;
    const link = banner.querySelector('a');
    const webApp = window.Telegram && window.Telegram.WebApp;
    if (!webApp || !webApp.initData) return;

    let expiresAt = 0;
    let timer;
    let request;

    function hide() {
        banner.hidden = true;
        clearTimeout(timer);
    }

    function updateVisibility() {
        clearTimeout(timer);
        const remaining = expiresAt - performance.now();
        banner.hidden = document.hidden || remaining <= 0;
        if (!banner.hidden) timer = setTimeout(updateVisibility, Math.min(remaining, 60000));
    }

    async function verify() {
        hide();
        if (document.hidden) return;
        if (request) request.abort();
        const controller = new AbortController();
        request = controller;
        const timeout = setTimeout(function () { controller.abort(); }, 4000);
        const started = performance.now();
        try {
            const response = await fetch('/api/campaign/banner', {
                cache: 'no-store', credentials: 'omit', signal: controller.signal
            });
            if (!response.ok) return;
            const state = await response.json();
            if (request !== controller) return;
            if (!state.active || state.id !== banner.dataset.campaignId ||
                !Number.isFinite(state.ends_at_ms) || !Number.isFinite(state.server_now_ms)) return;
            // Subtract request time rather than extending the paid display window.
            expiresAt = started + Math.max(0, state.ends_at_ms - state.server_now_ms);
            updateVisibility();
        } catch (error) {
            // An offline cached page must never keep an expired promotion visible.
            if (request === controller) hide();
        } finally {
            clearTimeout(timeout);
        }
    }

    link.addEventListener('click', function (event) {
        if (banner.hidden || expiresAt <= performance.now()) {
            event.preventDefault();
            hide();
            return;
        }
        if (typeof webApp.openLink === 'function') {
            try {
                webApp.openLink(link.href, { try_instant_view: false });
                event.preventDefault();
            } catch (error) {
                // Keep the ordinary HTTPS link available when the bridge is unavailable.
            }
        }
    });
    document.addEventListener('visibilitychange', verify);
    window.addEventListener('pageshow', function (event) { if (event.persisted) verify(); });
    verify();
}());
