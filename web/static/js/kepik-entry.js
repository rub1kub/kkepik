/* Screen selection only. The API still verifies Telegram's signed initData. */
(function () {
    'use strict';
    const root = document.documentElement;
    const telegram = window.Telegram && window.Telegram.WebApp;
    const inTelegram = Boolean(telegram && typeof telegram.initData === 'string' && telegram.initData.trim());
    const sdkFailed = !telegram && root.classList.contains('kepik-telegram-pending');
    root.classList.toggle('kepik-miniapp', inTelegram || sdkFailed);
    if (inTelegram || sdkFailed) {
        document.title = 'ККЭПик';
        document.getElementById('kepik-miniapp-style').media = 'all';
    }
    if (sdkFailed) {
        const error = document.getElementById('auth-error');
        error.textContent = 'Не удалось загрузить Telegram. Закройте и откройте мини-приложение.';
        error.style.display = '';
    }
    root.classList.remove('kepik-telegram-pending');
}());
