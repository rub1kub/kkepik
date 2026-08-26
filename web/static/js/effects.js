(function () {
    'use strict';

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
            script.onerror = function () { resolve(null); };
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

    window.confetti = queuedConfetti;
    window.kkepikLoadConfetti = loadConfetti;
}());
