/* Configure the schedule screen. The API verifies Telegram's signed initData. */
(function () {
    'use strict';
    const root = document.documentElement;
    const telegram = window.Telegram && window.Telegram.WebApp;
    const inTelegram = Boolean(telegram && typeof telegram.initData === 'string' && telegram.initData.trim());
    if (inTelegram && telegram.isVersionAtLeast?.('7.7')) telegram.disableVerticalSwipes?.();
    const sdkFailed = !telegram && root.classList.contains('kepik-telegram-pending');
    root.classList.toggle('kepik-miniapp', inTelegram || sdkFailed);
    if (inTelegram || sdkFailed) {
        document.title = 'Расписание';
        document.getElementById('kepik-miniapp-style').media = 'all';
    }
    if (sdkFailed) {
        if (window.kkepikMiniappStartup) window.kkepikMiniappStartup.fail();
        else {
            const error = document.getElementById('auth-error');
            error.textContent = 'Не удалось загрузить Telegram. Закройте и откройте мини-приложение.';
            error.style.display = '';
        }
    }
    root.classList.remove('kepik-telegram-pending');
}());
