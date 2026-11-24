(function () {
    'use strict';

    const telegram = window.Telegram?.WebApp;
    const supportsHaptics = !!(telegram?.initData && telegram.isVersionAtLeast?.('6.1') && telegram.HapticFeedback);
    const preference = 'kkepik:haptics';
    let enabled = true;
    try { enabled = localStorage.getItem(preference) !== 'off'; } catch {}
    window.kkepikHaptics = {
        supported: supportsHaptics,
        get enabled() { return enabled; },
        setEnabled(value) {
            enabled = value;
            try { localStorage.setItem(preference, value ? 'on' : 'off'); } catch {}
        },
        play(kind = 'selection') {
            if (!supportsHaptics || !enabled || document.hidden || telegram.isActive === false) return;
            const feedback = telegram.HapticFeedback;
            try {
                if (kind === 'selection') feedback.selectionChanged?.();
                else if (kind === 'success' || kind === 'error' || kind === 'warning') feedback.notificationOccurred?.(kind);
                else feedback.impactOccurred?.(kind);
            } catch { /* A missing native bridge must not interrupt the action. */ }
        }
    };

    let loading = null;
    const reducedMotion = window.matchMedia
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function disabled(options) {
        const forceForInteraction = options && options.forceForInteraction;
        return reducedMotion
            || (!forceForInteraction && window.kkepikApp && window.kkepikApp.lowData);
    }

    function loadConfetti(options) {
        if (disabled(options)) return Promise.resolve(null);
        if (window.confetti && window.confetti !== queuedConfetti) {
            return Promise.resolve(window.confetti);
        }
        if (loading) return loading;

        loading = new Promise(function (resolve) {
            const script = document.createElement('script');
            script.src = '/static/js/vendor/canvas-confetti.1.6.0.min.js';
            script.async = true;
            script.onload = function () { resolve(window.confetti); };
            script.onerror = function () {
                loading = null;
                script.remove();
                resolve(null);
            };
            document.head.appendChild(script);
        });
        return loading;
    }

    function queuedConfetti(options) {
        return loadConfetti().then(function (confettiFunction) {
            if (confettiFunction) return confettiFunction(options);
            return undefined;
        });
    }

    function launchSideConfetti(options) {
        return loadConfetti(options).then(function (confettiFunction) {
            if (typeof confettiFunction !== 'function') return;
            for (const x of [0, 1]) {
                confettiFunction({
                    particleCount:40,
                    angle:x ? 120 : 60,
                    spread:55,
                    origin:{ x, y:1 },
                    colors:['#2ecc71', '#3498db', '#e74c3c'],
                    gravity:1.1,
                    scalar:1.1,
                    ticks:250,
                    disableForReducedMotion:true,
                });
            }
        });
    }

    function launchReactionBurst(button, emoji) {
        if (disabled({ forceForInteraction:true }) || !button.getClientRects().length) return;
        const rect = button.getBoundingClientRect();
        const image = button.querySelector('img.reaction-glyph');
        for (let i = 0; i < 8; i++) {
            const angle = i * Math.PI / 4 + Math.random() * 0.4;
            const distance = 24 + Math.random() * 20;
            const x = Math.cos(angle) * distance;
            const y = Math.sin(angle) * distance - 10;
            const spark = document.createElement('span');
            spark.className = 'reaction-spark';
            spark.setAttribute('aria-hidden', 'true');
            if (image?.complete && image.naturalWidth) spark.appendChild(image.cloneNode());
            else spark.textContent = emoji;
            spark.style.left = (rect.left + rect.width / 2) + 'px';
            spark.style.top = (rect.top + rect.height / 2) + 'px';
            spark.style.setProperty('--spark-x', x + 'px');
            spark.style.setProperty('--spark-y', y + 'px');
            spark.style.animationDelay = i * 13 + 'ms';
            document.body.appendChild(spark);
            setTimeout(() => spark.remove(), 800);
        }
    }

    function slideClose(dialog, onClosed) {
        if (!dialog?.open || dialog.hasAttribute('data-closing')) return;
        let timer;
        function finish() {
            clearTimeout(timer);
            dialog.removeEventListener('transitionend', settled);
            const open = dialog.open && dialog.isConnected;
            if (open) dialog.close();
            dialog.removeAttribute('data-closing');
            if (open) onClosed?.();
        }
        function settled(event) {
            if (event.target === dialog && event.propertyName === 'transform') finish();
        }
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            finish();
            return;
        }
        dialog.dataset.closing = '';
        dialog.addEventListener('transitionend', settled);
        timer = setTimeout(finish, 280);
    }

    window.confetti = queuedConfetti;
    window.kkepikLoadConfetti = loadConfetti;
    window.launchSideConfetti = launchSideConfetti;
    window.launchReactionBurst = launchReactionBurst;
    window.kkepikSlideClose = slideClose;
}());
