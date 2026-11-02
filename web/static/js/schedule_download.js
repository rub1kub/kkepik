/* Plain user_id labels for download statistics. */
(function () {
    'use strict';
    const tg = window.Telegram?.WebApp;
    const route = /^\/api\/schedule\/download\/(groups|teachers)\/(\d{2}\.\d{2}\.\d{4})$/;
    let userId = tg?.initData ? tg.initDataUnsafe?.user?.id : null;
    if (tg?.initData && !userId) {
        try { userId = JSON.parse(new URLSearchParams(tg.initData).get('user') || '{}').id; } catch (_) {}
    }
    if (!Number.isSafeInteger(userId) || userId <= 0 || userId >= 2 ** 52) userId = null;

    function prepare(link) {
        const url = new URL(link.href, location.origin);
        const match = url.origin === location.origin && route.exec(decodeURIComponent(url.pathname));
        if (!match) return null;
        const data = window.scheduleState?.currentScheduleData || {};
        const kind = link.dataset.downloadEntityType || (data.group ? 'group' : data.teacher ? 'teacher' : null);
        const value = link.dataset.downloadEntityId || data[kind];
        const fields = { date: match[2], ...(kind && value ? { entity_type: kind, entity_id: value } : {}) };
        url.searchParams.delete('download');
        if (userId) url.searchParams.set('user_id', String(userId));
        url.searchParams.set('source', 'miniapp');
        if (kind && value) {
            url.searchParams.set('entity_type', kind);
            url.searchParams.set('entity_id', value);
        }
        link.href = url.href;
        return fields;
    }
    window.kkepikDownloads = { prepare };

    document.addEventListener('click', function (event) {
        const link = event.target.closest?.('a[href]');
        if (!link) return;
        const fields = prepare(link);
        if (!fields) return;
        for (const phase of ['click', 'launch']) {
            window.kkepikActivity?.track('schedule.download.' + phase, {
                ...fields, details: { control: 'download_schedule' }
            });
        }
        window.kkepikActivity?.flush();
        if (tg?.openLink) {
            event.preventDefault();
            event.stopImmediatePropagation();
            tg.openLink(link.href);
        }
    }, true);
})();
