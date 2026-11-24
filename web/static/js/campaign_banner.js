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
        window.kkepikActivity?.track('ui.action', { details: { control: 'campaign_best_company' } });
        window.kkepikActivity?.flush();
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

(function () {
    'use strict';
    const slot = document.getElementById('ownerCampaignBanner');
    const webApp = window.Telegram?.WebApp;
    const loader = window.kkepikBannerLoader;
    delete window.kkepikBannerLoader;
    if (!slot || !loader || !webApp?.initData) return;

    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let loading = false;
    let rotation;
    let expiryTimer;
    let expiresAt = 0;
    let expiresAtWall = 0;
    let slide = 'gym';
    let touchStart;

    function showSlide(next) {
        slide = next;
        slot.querySelector('.champion-preview-carousel').dataset.slide = next;
        slot.querySelectorAll('.champion-preview-title [data-slide]').forEach(title => {
            title.hidden = title.dataset.slide !== next;
        });
        slot.querySelectorAll('[data-slide-choice]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.slideChoice === next));
        });
    }

    function restartRotation() {
        clearInterval(rotation);
        if (motion.matches || slot.hidden) return;
        rotation = setInterval(() => {
            if (!touchStart && !slot.querySelector(':focus-visible')) showSlide(slide === 'gym' ? 'pool' : 'gym');
        }, 5000);
    }

    function clearPreview() {
        clearInterval(rotation);
        clearTimeout(expiryTimer);
        touchStart = undefined;
        expiresAt = expiresAtWall = 0;
        slot.hidden = true;
        slot.replaceChildren();
    }

    function remainingTime() {
        return Math.min(expiresAt - performance.now(), expiresAtWall - Date.now());
    }

    async function loadPreview() {
        if (document.hidden || !navigator.onLine || loading) return;
        loading = true;
        try {
            const request = loader.prefetch?.initData === webApp.initData
                ? loader.prefetch : loader.load(webApp.initData);
            delete loader.prefetch;
            const preview = await request.pending;
            if (!preview || !Number.isFinite(preview.duration)) return;
            const { duration, html } = preview;
            expiresAt = request.started + duration;
            expiresAtWall = request.startedAt + duration;
            const remaining = remainingTime();
            if (remaining <= 0) return;
            slot.innerHTML = html;
            showSlide(slide);
            slot.hidden = document.hidden || !navigator.onLine;
            restartRotation();
            expiryTimer = setTimeout(clearPreview, remaining);
        } finally {
            loading = false;
        }
    }

    function resumePreview() {
        clearInterval(rotation);
        touchStart = undefined;
        if (document.hidden || !navigator.onLine) {
            slot.hidden = true;
            return;
        }
        if (remainingTime() > 0) {
            slot.hidden = false;
            restartRotation();
            return;
        }
        clearPreview();
        loadPreview();
    }

    slot.addEventListener('click', function (event) {
        if (remainingTime() <= 0) {
            event.preventDefault();
            clearPreview();
            return;
        }
        const choice = event.target.closest('[data-slide-choice]');
        if (choice) {
            showSlide(choice.dataset.slideChoice);
            restartRotation();
            return;
        }
        const link = event.target.closest('a');
        if (!link || typeof webApp.openLink !== 'function') return;
        try {
            webApp.openLink(link.href, { try_instant_view: false });
            event.preventDefault();
        } catch {
            // The original link also works without the Telegram bridge.
        }
    });
    slot.addEventListener('touchstart', event => {
        touchStart = event.touches.length === 1 && event.target.closest('a') ? event.touches[0] : undefined;
    }, { passive: true });
    slot.addEventListener('touchcancel', () => { touchStart = undefined; });
    slot.addEventListener('touchend', event => {
        if (!touchStart) return;
        const touch = event.changedTouches[0];
        const dx = touch.clientX - touchStart.clientX;
        const dy = touch.clientY - touchStart.clientY;
        touchStart = undefined;
        if (Math.abs(dx) < 35 || Math.abs(dx) <= Math.abs(dy)) return;
        event.preventDefault();
        showSlide(slide === 'gym' ? 'pool' : 'gym');
        restartRotation();
    }, { passive: false });
    motion.addEventListener('change', restartRotation);
    document.addEventListener('visibilitychange', resumePreview);
    window.addEventListener('pageshow', event => { if (event.persisted) resumePreview(); });
    window.addEventListener('offline', resumePreview);
    window.addEventListener('online', resumePreview);
    resumePreview();
}());
