(function () {
    'use strict';

    const tg = window.Telegram && window.Telegram.WebApp;
    if (!tg || !tg.initData) return;

    const background = document.getElementById('pixelBackground');
    const canvas = document.getElementById('pixelCanvas');
    const banner = document.getElementById('canvasBanner');
    const bannerSubtitle = document.getElementById('canvasBannerSubtitle');
    const tools = document.getElementById('pixelTools');
    const main = document.getElementById('main-content');
    const picker = document.getElementById('pixelColorPicker');
    const colorToggle = document.getElementById('pixelColorToggle');
    const currentColor = document.getElementById('pixelCurrentColor');
    const colorName = document.getElementById('pixelColorName');
    const bottomControls = document.getElementById('pixelBottomControls');
    const done = document.getElementById('pixelDone');
    const onlineCounter = document.getElementById('miniappOnlineCount');
    const onlineBadge = document.getElementById('pixelOnline') || onlineCounter.parentElement;
    const topInfo = document.getElementById('pixelTopInfo');
    const eventDeadline = document.getElementById('pixelEventDeadline');
    const banScreen = document.getElementById('pixelBanScreen');
    const banButton = document.getElementById('pixelAdminBan');
    const rollbackButton = document.getElementById('pixelAdminRollback');
    const rollbackInfo = document.getElementById('pixelRollbackInfo');
    let rollbackPreview = null, rollbackPending = false;
    let banned = false, accessAt = 0, accessRevision = 0, onlineAt = 0, eventEnds = 0, eventTimer = null;
    let eventUntil = 0, eventFinished = false, inspectedAuthor = null, banPending = false;
    const status = document.getElementById('pixelStatus');
    const cooldownLine = document.getElementById('pixelCooldown');
    const ctx = canvas && canvas.getContext('2d', { alpha: true });
    if (!background || !ctx || !banner || !tools) return;

    const colors = ['#faf8f3', '#dddcd6', '#92958f', '#343b3e', '#d6a2b3', '#bb716a',
        '#ce9b68', '#98785f', '#d4bf79', '#afbd8c', '#789b82', '#80aaa6',
        '#82a9c1', '#6f85af', '#b09bbe', '#887795'];
    const colorNames = ['Белый', 'Светло-серый', 'Серый', 'Чёрный', 'Розовый', 'Красный',
        'Оранжевый', 'Коричневый', 'Жёлтый', 'Салатовый', 'Зелёный', 'Бирюзовый',
        'Голубой', 'Синий', 'Сиреневый', 'Фиолетовый'];
    const board = {x0:-64,y0:-128,x1:64,y1:128};
    const state = new window.KepikPixelState(board);
    const pixels = state.pixels;
    const pointers = new Map();
    const view = { x: 0, y: 0, cell: 9 };
    let width = 0;
    let height = 0;
    let editing = false;
    let selectedColor = 12;
    let selectedCell = null;
    let epoch = 0;
    let initialized = false;
    let online = navigator.onLine;
    let sending = false;
    let readyAt = 0;
    let cooldownMs = 1000, cooldownAt = 0, readyRevision = 0, progressPeriod = 1000;
    let message = '';
    let messageUntil = 0;
    let pollTimer = null;
    let cooldownTimer = null;
    let renderFrame = null;
    let pageActive = true;
    let replay = null, replayFit = true;
    let failureCount = 0;
    let gesture = null;
    let pinch = null;
    let savedScroll = 0;
    const magnifier = document.getElementById('pixelMagnifier');
    const magnifierCtx = magnifier.getContext('2d');
    const adminToggle = document.getElementById('pixelAdminToggle');
    const adminTools = document.getElementById('pixelAdminTools');
    const adminApply = document.getElementById('pixelAdminApply');
    let adminAllowed = false;
    let adminMode = false;
    let adminErase = false;
    let adminArea = false;
    let adminInspect = false;
    let inspectorEpoch = 0;
    const authorInfo = document.getElementById('pixelAuthorInfo');
    let areaSelection = null;
    let holdTimer = null;
    let brushTimer = null;
    let lastPainted = '';
    let keyboardInput = false;
    let requestedFullscreen = false;
    let restoreSwipes = false;
    let homeCell = 9;
    let lastKeyboardCell = { x: 0, y: 0 };

    try {
        const saved = JSON.parse(localStorage.getItem('kkepik:canvas:view:v1') || 'null');
        if (saved && ['x', 'y', 'cell'].every(key => Number.isFinite(saved[key]))) {
            view.x = Math.max(board.x0, Math.min(board.x1, saved.x));
            view.y = Math.max(board.y0, Math.min(board.y1, saved.y));
            view.cell = Math.max(6, Math.min(56, saved.cell));
        }
        const color = Number(localStorage.getItem('kkepik:canvas:color:v1'));
        if (localStorage.getItem('kkepik:canvas:color:v1') !== null && Number.isInteger(color)
            && color >= 0 && color < colors.length) selectedColor = color;
    } catch (_error) { /* Storage is optional in Telegram WebViews. */ }

    function saveView() {
        try {
            localStorage.setItem('kkepik:canvas:view:v1', JSON.stringify(view));
        } catch (_error) { /* Keep drawing without local storage. */ }
    }

    function haptic() {
        try { tg.HapticFeedback.impactOccurred('light'); } catch (_error) { /* Optional. */ }
    }

    function minimumCell() {
        if (replay && replay.active) return replayFitCell();
        return Math.max(3, width/(board.x1-board.x0), height/(board.y1-board.y0),
            Math.sqrt((width + 64) * (height + 64) / 58000));
    }

    function bounds() {
        const halfX = width / (2 * view.cell);
        const halfY = height / (2 * view.cell);
        return {
            x0: Math.max(board.x0, Math.floor(view.x - halfX) - 2),
            y0: Math.max(board.y0, Math.floor(view.y - halfY) - 2),
            x1: Math.min(board.x1, Math.ceil(view.x + halfX) + 2),
            y1: Math.min(board.y1, Math.ceil(view.y + halfY) + 2),
        };
    }

    function onBoard(cell) {
        return cell.x>=board.x0 && cell.x<board.x1 && cell.y>=board.y0 && cell.y<board.y1;
    }
    function clampView() {
        if (replay && replay.active) {
            const area = replayViewArea();
            const clampAxis = (value, lo, hi, begin, end, size) => (hi-lo)*view.cell <= end-begin
                ? (lo+hi)/2 - ((begin+end)/2-size/2)/view.cell
                : Math.max(lo-(begin-size/2)/view.cell, Math.min(hi-(end-size/2)/view.cell, value));
            view.x = clampAxis(view.x,board.x0,board.x1,area.left,area.right,width);
            view.y = clampAxis(view.y,board.y0,board.y1,area.top,area.bottom,height);
            return;
        }
        const pad=editing?18/view.cell:0;
        const halfX=width/(2*view.cell), halfY=height/(2*view.cell);
        const topPixels=editing?Math.max(18,
            (tg.safeAreaInset?.top || 0)+(tg.contentSafeAreaInset?.top || 0)+12,
            document.getElementById('pixelTopInfo').getBoundingClientRect().bottom+18,
            !adminTools.hidden?adminTools.getBoundingClientRect().bottom+18:0):0;
        const bottomTop=editing?Math.min(bottomControls.getBoundingClientRect().top,
            !picker.hidden?picker.getBoundingClientRect().top:height):height;
        const bottomPixels=editing?Math.max(18,height-bottomTop+18):0;
        view.x=Math.max(board.x0+halfX-pad,Math.min(board.x1-halfX+pad,view.x));
        // Keep the last row reachable above the controls and the first row
        // below Telegram's system controls, including with an open palette.
        view.y=Math.max(board.y0+halfY-topPixels/view.cell,
            Math.min(board.y1-halfY+bottomPixels/view.cell,view.y));
    }

    function worldPoint(x, y) {
        return { x: view.x + (x - width / 2) / view.cell,
            y: view.y + (y - height / 2) / view.cell };
    }

    function screenPoint(event) {
        const rect = canvas.getBoundingClientRect();
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    function applyPixel(pixel) { state.apply(pixel); }

    function cancelRead() {
        epoch += 1;
        state.cancel();
        clearTimeout(pollTimer);
        pollTimer = null;
    }

    function render() {
        renderFrame = null;
        if (document.hidden) return;
        ctx.clearRect(0, 0, width, height);
        const left = view.x - width / (2 * view.cell);
        const top = view.y - height / (2 * view.cell);
        const boardX=(board.x0-left)*view.cell, boardY=(board.y0-top)*view.cell;
        const boardW=(board.x1-board.x0)*view.cell, boardH=(board.y1-board.y0)*view.cell;
        if (editing) {
            ctx.fillStyle=tg.colorScheme==='dark'?'#ffffff10':'#343b3e12';
            ctx.fillRect(0,0,width,height);
            ctx.clearRect(boardX,boardY,boardW,boardH);
        }
        ctx.save();ctx.beginPath();ctx.rect(boardX,boardY,boardW,boardH);ctx.clip();
        if (replay && replay.active) { ctx.fillStyle=colors[0];ctx.fillRect(boardX,boardY,boardW,boardH); }
        if (replay && replay.active && replay.loaded) ctx.drawImage(replay.image,boardX,boardY,boardW,boardH);
        else for (const pixel of state.visible(bounds())) {
            if (pixel[2] < 0) continue;
            const x = Math.round((pixel[0] - left) * view.cell);
            const y = Math.round((pixel[1] - top) * view.cell);
            ctx.fillStyle = colors[pixel[2]];
            ctx.fillRect(x, y, Math.round((pixel[0] + 1 - left) * view.cell) - x,
                Math.round((pixel[1] + 1 - top) * view.cell) - y);
        }
        if (editing && view.cell >= 14) {
            ctx.beginPath();
            for (let x = (Math.ceil(left) - left) * view.cell; x < width; x += view.cell) {
                ctx.moveTo(x, 0); ctx.lineTo(x, height);
            }
            for (let y = (Math.ceil(top) - top) * view.cell; y < height; y += view.cell) {
                ctx.moveTo(0, y); ctx.lineTo(width, y);
            }
            ctx.strokeStyle = tg.colorScheme === 'dark' ? 'rgba(255,255,255,0.09)' : 'rgba(0,0,0,0.09)';
            ctx.lineWidth = 0.5;
            ctx.stroke();
        }
        if (editing && areaSelection) {
            const a = areaSelection;
            ctx.fillStyle = 'rgba(100,140,170,0.22)';
            ctx.fillRect((a.x0-left)*view.cell, (a.y0-top)*view.cell,
                (a.x1-a.x0)*view.cell, (a.y1-a.y0)*view.cell);
            ctx.strokeStyle = tg.themeParams.button_color || '#648caa';
            ctx.lineWidth = 2;
            ctx.strokeRect((a.x0-left)*view.cell, (a.y0-top)*view.cell,
                (a.x1-a.x0)*view.cell, (a.y1-a.y0)*view.cell);
        }
        if (editing && selectedCell && !(replay && replay.active)) {
            const x = (selectedCell.x - left) * view.cell;
            const y = (selectedCell.y - top) * view.cell;
            ctx.lineWidth = 2;
            ctx.strokeStyle = '#ffffff';
            ctx.strokeRect(x - 1, y - 1, view.cell + 2, view.cell + 2);
            ctx.lineWidth = 1;
            ctx.strokeStyle = '#222222';
            ctx.strokeRect(x, y, view.cell, view.cell);
        }
        ctx.restore();
        if (editing) {
            ctx.strokeStyle=tg.themeParams.button_color || '#648caa';ctx.lineWidth=2;
            ctx.strokeRect(boardX,boardY,boardW,boardH);
        }
    }

    function drawSoon() {
        if (renderFrame === null && !document.hidden) renderFrame = requestAnimationFrame(render);
    }

    function updateOnline(count, observedAt) {
        if (count === null) {
            onlineBadge.hidden = true;
            topInfo.hidden = eventDeadline.hidden;
            return;
        }
        if (!Number.isSafeInteger(count) || count < 0 || !Number.isSafeInteger(observedAt) || observedAt < onlineAt) return;
        onlineAt = observedAt;
        onlineBadge.hidden = count < 5;
        topInfo.hidden = onlineBadge.hidden && eventDeadline.hidden;
        onlineCounter.textContent = 'Онлайн: ' + count;
        onlineCounter.title = 'Сейчас во всей мини-аппке';
    }
    window.addEventListener('kkepik:presence', event => updateOnline(event.detail.count, event.detail.observedAt));
    if (window.KepikPresence) updateOnline(window.KepikPresence.count, window.KepikPresence.observedAt);

    function updateAccess(data) {
        if (typeof data.banned === 'boolean' && Number.isSafeInteger(data.access_at_ms)
            && Number.isSafeInteger(data.access_revision) && (data.access_revision > accessRevision
                || (data.access_revision === accessRevision && data.access_at_ms >= accessAt))) {
            accessAt = data.access_at_ms; accessRevision = data.access_revision; banned = data.banned;
        }
        const event = data.event;
        if (event && Number.isSafeInteger(event.ends_at_ms) && Number.isSafeInteger(event.server_time_ms)) {
            if (eventEnds !== event.ends_at_ms) { eventFinished = false; eventEnds = event.ends_at_ms; }
            eventFinished = eventFinished || event.finished;
            eventUntil = performance.now() + Math.max(0, eventEnds - event.server_time_ms);
        }
        updateEventDisplay();
    }
    function updateEventDisplay() {
        clearTimeout(eventTimer); eventTimer = null;
        eventDeadline.hidden = !eventEnds;
        topInfo.hidden = onlineBadge.hidden && eventDeadline.hidden;
        if (eventEnds) {
            const remaining = Math.max(0, eventUntil - performance.now());
            eventFinished = eventFinished || remaining === 0;
            const totalSeconds = Math.ceil(remaining / 1000);
            const days = Math.floor(totalSeconds / 86400);
            const clock = [Math.floor(totalSeconds / 3600) % 24, Math.floor(totalSeconds / 60) % 60, totalSeconds % 60]
                .map(value => String(value).padStart(2, '0')).join(':');
            const countdown = 'До конца · ' + (days ? days + ' д. ' : '') + clock;
            eventDeadline.textContent = eventFinished ? 'Игра завершена' : countdown;
            eventDeadline.title = 'Окончание игры: ' + new Date(eventEnds).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day:'numeric', month:'long', hour:'2-digit', minute:'2-digit' }) + ' МСК';
            if (bannerSubtitle) bannerSubtitle.textContent = eventFinished ? 'Посмотреть, как росло полотно' : countdown;
            const bannerTitle=banner.querySelector('strong');
            if (bannerTitle) bannerTitle.textContent = eventFinished ? 'Итоги пиксель-батла' : 'мама-анархия';
            banner.setAttribute('aria-label', eventFinished ? 'Итоги пиксель-батла — смотреть таймлапс' : 'мама-анархия — ' + countdown);
            if (!eventFinished && pageActive && !document.hidden) eventTimer = setTimeout(updateEventDisplay, Math.min(1000, remaining));
        }
        const blocked = editing && banned;
        if (blocked && banScreen.hidden) {
            banScreen.hidden = false;
            document.getElementById('pixelBanBack').focus({ preventScroll: true });
        }
        banScreen.hidden = !blocked;
        document.body.classList.toggle('pixel-banned', blocked);
        colorToggle.disabled = banned || (eventFinished && !adminMode);
        if (banned || (eventFinished && !adminMode)) stopBrush();
        window.KepikCanvasChat?.setContext({active:editing,admin:adminAllowed,banned,finished:eventFinished});
        if (editing && eventFinished && !adminMode && !banned && replay && !replay.active) {
            window.KepikCanvasChat?.close();
            selectedCell=null; replay.open();
        }
        updateCooldown();
    }

    function statusText(text) {
        if (status.textContent !== text) status.textContent = text;
    }

    function updatePacing(data) {
        if ([250,500,1000].includes(data.cooldown_ms) && Number.isSafeInteger(data.cooldown_at_ms)
            && data.cooldown_at_ms >= cooldownAt) {
            cooldownMs = data.cooldown_ms; cooldownAt = data.cooldown_at_ms;
        }
        const playerRevision = Number.isSafeInteger(data.player_revision) ? data.player_revision : null;
        if ((playerRevision !== null ? playerRevision >= readyRevision : readyRevision === 0)
            && Number.isFinite(data.remaining_ms) && data.remaining_ms >= 0) {
            if (playerRevision !== null) readyRevision = playerRevision;
            readyAt = Math.max(readyAt, performance.now() + data.remaining_ms);
            progressPeriod = Math.max(cooldownMs, data.remaining_ms, readyAt-performance.now());
        }
    }
    function paceLabel() {
        return cooldownMs === 250 ? 'Пиксель каждые 0,25 с' : cooldownMs === 500 ? 'Пиксель каждые 0,5 с' : 'Пиксель в секунду';
    }

    function updateCooldown() {
        clearTimeout(cooldownTimer);
        cooldownTimer = null;
        const remaining = Math.max(0, readyAt - performance.now());
        cooldownLine.style.transform = 'scaleX(' + (1 - Math.min(1, remaining / progressPeriod)) + ')';
        if (!editing || document.hidden) return;
        const hint = document.getElementById('pixelHint');
        const hintText = !adminMode ? (eventFinished ? 'Ивент завершён · полотно можно рассматривать' : paceLabel()+' · удержание — кисть')
            : adminInspect ? 'Коснись клетки, чтобы узнать автора'
            : adminArea ? (adminErase ? 'Проведи по области · затем сотри' : 'Проведи по области · затем закрась')
            : adminErase ? 'Ластик · два пальца — двигать'
            : 'Быстрая кисть · два пальца — двигать';
        if (hint.textContent !== hintText) hint.textContent = hintText;
        if (!online) statusText('Нет связи с доской');
        else if (message && performance.now() < messageUntil) statusText(message);
        else if (banned) statusText('Рисование заблокировано');
        else if (eventFinished && !adminMode) statusText('Только просмотр');
        else if (sending) statusText('Ставим пиксель…');
        else if (adminMode) statusText(adminInspect?'Коснись клетки':(adminArea ? 'Выдели участок' : (adminErase ? 'Ластик включён' : 'Быстрый режим')));
        else if (remaining > 0) statusText(paceLabel());
        else statusText('Можно рисовать');
        if (remaining > 0 || sending || performance.now() < messageUntil) {
            cooldownTimer = setTimeout(updateCooldown, 80);
        }
    }

    function showMessage(text) {
        message = text;
        messageUntil = performance.now() + 2800;
        updateCooldown();
    }

    function pickColor(index) {
        selectedColor = index;
        currentColor.style.backgroundColor = colors[index];
        colorName.textContent = colorNames[index];
        colorToggle.setAttribute('aria-label', 'Цвет: ' + colorNames[index]);
        picker.querySelectorAll('button').forEach((button, i) => {
            button.setAttribute('aria-pressed', String(i === index));
        });
        try { localStorage.setItem('kkepik:canvas:color:v1', String(index)); } catch (_error) { /* Optional. */ }
    }

    colors.forEach((color, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'pixel-swatch';
        button.setAttribute('aria-label', colorNames[index]);
        const swatch = document.createElement('span');
        swatch.style.backgroundColor = color;
        swatch.setAttribute('aria-hidden', 'true');
        button.appendChild(swatch);
        button.addEventListener('click', function () {
            pickColor(index);
            closePicker();
            colorToggle.focus({ preventScroll: true });
            haptic();
        });
        picker.appendChild(button);
    });
    pickColor(selectedColor);

    function closePicker() {
        picker.hidden = true;
        tools.classList.remove('pixel-palette-open');
        adminTools.hidden = !adminMode;
        adminToggle.setAttribute('aria-expanded', String(adminMode));
        colorToggle.setAttribute('aria-expanded', 'false');
        if(editing)invalidateView();
    }

    colorToggle.addEventListener('click', function () {
        picker.hidden = !picker.hidden;
        colorToggle.setAttribute('aria-expanded', String(!picker.hidden));
        tools.classList.toggle('pixel-palette-open', !picker.hidden);
        adminTools.hidden = !adminMode || !picker.hidden;
        adminToggle.setAttribute('aria-expanded', String(!adminTools.hidden));
        if (!picker.hidden) picker.querySelector('[aria-pressed="true"]').focus({ preventScroll: true });
        invalidateView();
    });

    function schedulePoll(delay) {
        clearTimeout(pollTimer);
        pollTimer = null;
        if (initialized && pageActive && !document.hidden && navigator.onLine) pollTimer = setTimeout(sync, delay);
    }

    function invalidateView() {
        clampView();
        drawSoon();
    }

    function replayViewArea() {
        const info = topInfo.getBoundingClientRect();
        const panel = document.getElementById('pixelReplay').getBoundingClientRect();
        const top = Math.max(18, info.bottom + 14);
        if (width > height && panel.width < width * .6) {
            return {left:18,right:Math.max(50,panel.left-18),top,bottom:height-18};
        }
        return {left:18,right:width-18,top,bottom:Math.max(top+48,panel.top-14)};
    }
    function replayFitCell() {
        const area=replayViewArea();
        return Math.max(.15,Math.min((area.right-area.left)/(board.x1-board.x0),(area.bottom-area.top)/(board.y1-board.y0)));
    }
    function fitReplayView() {
        if (!replay || !replay.active || !width || !height) return;
        replayFit=true; view.cell=replayFitCell(); view.x=0;view.y=0;
        invalidateView();
    }

    function resize() {
        width = background.clientWidth;
        height = background.clientHeight;
        if (!width || !height) return;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.imageSmoothingEnabled = false;
        view.cell = editing ? Math.max(minimumCell(), view.cell) : minimumCell();
        if (replay && replay.active && replayFit) fitReplayView();
        invalidateView();
    }

    async function sync() {
        clearTimeout(pollTimer);
        pollTimer = null;
        if (!initialized || !pageActive || document.hidden || !navigator.onLine || state.busy) return;
        const ownEpoch = epoch;
        const requestedRevision = state.revision;
        try {
            const data = await state.read(async (area, signal) => {
                const response = await fetch('/api/pixels/state?' + new URLSearchParams({...area,...window.KepikCanvasChat?.query()}), {
                    headers: { 'X-Telegram-Init-Data': tg.initData },
                    cache: 'no-store', signal,
                });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.error || 'Доска пока недоступна');
                return result;
            });
            if (!data || ownEpoch !== epoch || !pageActive || document.hidden || !navigator.onLine) return;
            if (data.full && data.revision < requestedRevision) {
                readyRevision=0;readyAt=0;cooldownAt=0;accessRevision=0;accessAt=0;
                window.KepikCanvasChat?.reset();
            }
            adminAllowed = data.admin === true;
            adminToggle.hidden = !adminAllowed;
            if (!adminAllowed && adminMode) setAdminMode(false);
            updatePacing(data);
            online = true;
            failureCount = 0;
            updateOnline(data.online_count, data.online_at_ms);
            updateAccess(data);
            window.KepikCanvasChat?.updateState(data.chat);
            clampView(); drawSoon(); updateCooldown();
        } catch (error) {
            if (ownEpoch !== epoch || !pageActive || document.hidden || !navigator.onLine) return;
            online = false;
            failureCount = Math.min(failureCount + 1, 5);
            updateCooldown();
        } finally {
            if (ownEpoch === epoch && !state.busy) {
                const usualDelay = editing ? 1000 : ((window.kkepikApp && window.kkepikApp.lowData) ? 15000 : 5000);
                schedulePoll(failureCount ? Math.min(30000, 1000 * (2 ** failureCount)) : usualDelay);
            }
        }
    }

    function requestId() {
        if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
        return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    }

    async function place(cell) {
        if (!editing || sending || (!adminMode && performance.now() < readyAt)) return;
        if (banned || (eventFinished && !adminMode)) { updateEventDisplay(); return; }
        const x = Math.floor(cell.x), y = Math.floor(cell.y);
        if (!onBoard({x,y})) { showMessage('Край полотна'); return; }
        if (adminMode) { if (adminInspect) await inspectPixel(cell); else await adminFill(brushArea(cell)); return; }
        if (!online || !navigator.onLine) { showMessage('Нет связи с доской'); return; }
        sending = true;
        closePicker();
        selectedCell = { x, y };
        lastKeyboardCell = { x, y };
        drawSoon();
        updateCooldown();
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 6000);
        try {
            const response = await fetch('/api/pixels/place', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': tg.initData },
                body: JSON.stringify({ x, y, color: selectedColor, request_id: requestId() }),
                signal: controller.signal,
            });
            const data = await response.json();
            updatePacing(data);
            if (!response.ok || !data.success) {
                updateAccess(data);
                showMessage(data.error || 'Не удалось поставить пиксель');
                return;
            }
            applyPixel(data.pixel);
            // Do not advance the read cursor here: concurrent changes from
            // other players still need to be received by the next state read.
            drawSoon();
            haptic();
            schedulePoll(100);
        } catch (_error) {
            showMessage('Проверяем связь с доской…');
            // Never replay a blind click: a timed-out request may be committed.
            schedulePoll(100);
        } finally {
            clearTimeout(timeout);
            sending = false;
            updateCooldown();
        }
    }

    function supports(version) {
        return typeof tg.isVersionAtLeast === 'function' && tg.isVersionAtLeast(version);
    }

    function enter() {
        if (editing) return;
        editing = true;
        homeCell = view.cell;
        view.cell = Math.max(24, view.cell);
        try {
            tg.expand();
            requestedFullscreen = supports('8.0') && !tg.isFullscreen;
            if (requestedFullscreen) tg.requestFullscreen();
            restoreSwipes = supports('7.7') && tg.isVerticalSwipesEnabled !== false;
            if (restoreSwipes) tg.disableVerticalSwipes();
        } catch (_error) { /* Expanded layout works in older clients too. */ }
        savedScroll = window.scrollY;
        main.inert = true;
        document.body.classList.add('pixel-editing');
        banner.setAttribute('aria-expanded', 'true');
        tools.hidden = false;
        updateEventDisplay();
        canvas.tabIndex = 0;
        (banned ? document.getElementById('pixelBanBack') : canvas).focus({ preventScroll: true });
        lastKeyboardCell = { x: Math.floor(view.x), y: Math.floor(view.y) };
        if (tg.BackButton) {
            tg.BackButton.onClick(exit);
            tg.BackButton.show();
        }
        haptic();
        resize();
        schedulePoll(0);
        updateCooldown();
    }

    function exit() {
        if (!editing) return;
        if (window.KepikCanvasChat?.isOpen()) { window.KepikCanvasChat.close(); return; }
        editing = false;
        if (replay) replay.close();
        window.KepikCanvasChat?.setContext({active:false,admin:adminAllowed,banned,finished:eventFinished});
        stopBrush();
        setAdminMode(false);
        areaSelection = null;
        try {
            if (requestedFullscreen) tg.exitFullscreen();
            if (restoreSwipes) tg.enableVerticalSwipes();
        } catch (_error) { /* Optional client features. */ }
        requestedFullscreen = false;
        restoreSwipes = false;
        view.cell = homeCell;
        pointers.clear();
        gesture = null;
        pinch = null;
        closePicker();
        tools.hidden = true;
        banScreen.hidden = true;
        document.body.classList.remove('pixel-banned');
        canvas.tabIndex = -1;
        document.body.classList.remove('pixel-editing');
        main.inert = false;
        banner.setAttribute('aria-expanded', 'false');
        if (tg.BackButton) {
            tg.BackButton.offClick(exit);
            tg.BackButton.hide();
        }
        saveView();
        updateCooldown();
        window.scrollTo(0, savedScroll);
        if (keyboardInput) banner.focus({ preventScroll: true });
        else { canvas.blur(); banner.blur(); }
        resize();
        drawSoon();
        schedulePoll(0);
    }

    banner.addEventListener('click', enter);
    if (window.KepikCanvasChat) {
        window.KepikCanvasChat.onOpen=()=>{stopBrush();pointers.clear();gesture=null;pinch=null;closePicker();canvas.inert=true;};
        window.KepikCanvasChat.onClose=()=>{canvas.inert=false;};
    }
    done.addEventListener('click', exit);
    document.getElementById('pixelBanBack').addEventListener('click', exit);
    document.getElementById('pixelZoomIn').addEventListener('click', () => zoom(1.4));
    document.getElementById('pixelZoomOut').addEventListener('click', () => zoom(1/1.4));
    function zoom(factor) {
        replayFit=false;
        stopBrush();
        view.cell = Math.max(minimumCell(), Math.min(56, view.cell*factor));
        invalidateView(); saveView();
    }

    function brushArea(cell) {
        const size = Number(document.getElementById('pixelAdminBrush').value) || 1;
        const half = Math.floor(size/2);
        const x = Math.floor(cell.x), y = Math.floor(cell.y);
        return { x0:Math.max(board.x0,x-half), y0:Math.max(board.y0,y-half),
            x1:Math.min(board.x1,x-half+size), y1:Math.min(board.y1,y-half+size) };
    }

    function setAdminMode(enabled) {
        stopBrush(); pointers.clear(); gesture=null; pinch=null;
        adminMode = enabled && adminAllowed;
        if (adminMode && replay && replay.active) { replay.close(); view.cell=Math.max(minimumCell(),view.cell); }
        closePicker();
        inspectorEpoch++; authorInfo.hidden=true; banButton.hidden=true; inspectedAuthor=null; resetRollback();
        adminTools.hidden = !adminMode;
        adminToggle.setAttribute('aria-expanded', String(adminMode));
        areaSelection = null;
        adminApply.hidden = true;
        updateEventDisplay(); invalidateView();
    }
    adminToggle.addEventListener('click', () => {
        if (!picker.hidden && adminMode) { closePicker(); return; }
        setAdminMode(!adminMode);
    });
    document.getElementById('pixelAdminFast').addEventListener('click', () => {
        adminErase = false; adminArea = false; adminInspect=false; updateAdminButtons();
    });
    document.getElementById('pixelAdminErase').addEventListener('click', () => {
        adminErase = !adminErase; adminInspect=false; updateAdminButtons();
    });
    document.getElementById('pixelAdminArea').addEventListener('click', () => {
        adminArea = !adminArea; adminInspect=false; updateAdminButtons();
    });
    document.getElementById('pixelAdminInspect').addEventListener('click',()=>{
        adminInspect=!adminInspect; adminArea=false; if(adminInspect)adminErase=false; updateAdminButtons();
    });
    function updateAdminButtons() {
        stopBrush(); pointers.clear(); gesture=null; pinch=null; areaSelection = null; adminApply.hidden = true;
        inspectorEpoch++; authorInfo.hidden = true; banButton.hidden=true; inspectedAuthor=null; resetRollback();
        document.getElementById('pixelAdminFast').setAttribute('aria-pressed', String(!adminErase && !adminArea && !adminInspect));
        document.getElementById('pixelAdminErase').setAttribute('aria-pressed', String(adminErase));
        document.getElementById('pixelAdminArea').setAttribute('aria-pressed', String(adminArea));
        document.getElementById('pixelAdminInspect').setAttribute('aria-pressed',String(adminInspect));
        updateCooldown(); invalidateView();
    }
    function updateAreaButton() {
        const count=areaSelection?(areaSelection.x1-areaSelection.x0)*(areaSelection.y1-areaSelection.y0):0;
        adminApply.hidden=!count;
        adminApply.disabled=count>65536 || sending;
        adminApply.textContent=(adminErase?'Стереть':'Закрасить')+(count?' · '+count.toLocaleString('ru-RU'):'');
    }
    adminApply.addEventListener('click', async () => {
        if (!areaSelection) return;
        const area = areaSelection;
        adminApply.disabled=true;
        if (await adminFill(area) && areaSelection === area) {
            areaSelection = null; adminApply.hidden = true; drawSoon();
        }
        updateAreaButton();
    });

    async function adminFill(area) {
        if (!adminMode || !adminAllowed || adminInspect || sending) return false;
        if (!online || !navigator.onLine) { showMessage('Нет связи с доской'); return false; }
        sending = true; updateCooldown();
        const color = adminErase ? -1 : selectedColor;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 6000);
        try {
            const response = await fetch('/api/pixels/admin/fill', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': tg.initData },
                body: JSON.stringify({ ...area, color }), signal: controller.signal,
            });
            const data = await response.json();
            if (!response.ok || !data.success) {
                if (response.status === 403) { adminAllowed = false; setAdminMode(false); }
                showMessage(data.error || 'Не удалось закрасить'); return false;
            }
            // Update the entire edited area, including cells off screen.
            for (let y=area.y0;y<area.y1;y++) {
                for (let x=area.x0;x<area.x1;x++) applyPixel([x,y,color,data.revision]);
            }
            drawSoon(); schedulePoll(100); return true;
        } catch (_error) { showMessage('Проверяем связь с доской…'); schedulePoll(100); return false; }
        finally { clearTimeout(timeout); sending = false; updateCooldown(); }
    }

    async function inspectPixel(cell) {
        const x=Math.floor(cell.x),y=Math.floor(cell.y);
        if(!adminAllowed || !adminMode || !adminInspect || !onBoard({x,y}))return;
        const own=++inspectorEpoch;authorInfo.hidden=false;authorInfo.textContent='Загружаем автора…';
        banButton.hidden=true;inspectedAuthor=null;resetRollback();
        const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),6000);
        try {
            const response=await fetch('/api/pixels/admin/pixel?'+new URLSearchParams({x,y}),{
                headers:{'X-Telegram-Init-Data':tg.initData},cache:'no-store',signal:controller.signal,
            });
            const data=await response.json();
            if(own!==inspectorEpoch || !adminMode || !adminInspect)return;
            if(!response.ok || !data.success)throw new Error(data.error || 'Не удалось получить автора');
            const pixel=data.pixel;
            let text='Пустая клетка';
            if(pixel) {
                text=pixel.actor_kind==='web_admin'?'Веб-администратор':
                    (pixel.actor_kind==='telegram' && pixel.actor_id?
                        (pixel.actor_name || 'Пользователь Telegram')+(pixel.actor_username?' · @'+pixel.actor_username:'')+' · ID '+pixel.actor_id:'Автор не записан');
                if(pixel.color<0)text='Стерта · '+text;
                if(pixel.placed_at_ms)text+=' · '+new Date(pixel.placed_at_ms).toLocaleString('ru-RU');
            }
            authorInfo.textContent='Клетка '+x+', '+y+' · '+text;
            if (pixel && pixel.actor_kind === 'telegram' && pixel.actor_id) {
                inspectedAuthor = {id:pixel.actor_id,banned:data.author_banned === true};
                banButton.hidden=pixel.actor_id===1084693264;banButton.disabled=banPending;
                banButton.textContent=inspectedAuthor.banned ? 'Разбанить автора' : 'Забанить автора';
                if(rollbackButton){rollbackButton.hidden=false;rollbackButton.disabled=rollbackPending;}
            }
        } catch(error) { if(own===inspectorEpoch)authorInfo.textContent=error.message; }
        finally{clearTimeout(timeout);if(editing)invalidateView();}
    }

    banButton.addEventListener('click', async () => {
        if (!inspectedAuthor || !adminAllowed || banPending) return;
        const author = inspectedAuthor, own = inspectorEpoch;
        banPending=true;banButton.disabled=true;
        const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),6000);
        try {
            const response=await fetch('/api/pixels/admin/ban', {
                method:'POST',headers:{'Content-Type':'application/json','X-Telegram-Init-Data':tg.initData},
                body:JSON.stringify({telegram_id:author.id,banned:!author.banned}),signal:controller.signal,
            });
            const data=await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Не удалось изменить бан');
            if (own !== inspectorEpoch || inspectedAuthor !== author) return;
            author.banned=data.banned;
            banButton.textContent=author.banned?'Разбанить автора':'Забанить автора';
            showMessage(author.banned?'Автор заблокирован на полотне':'Бан снят');
        } catch (error) { if (own===inspectorEpoch) showMessage(error.message); }
        finally {clearTimeout(timeout);banPending=false;banButton.disabled=false;}
    });

    function resetRollback() {
        rollbackPreview=null;
        if(!rollbackButton || !rollbackInfo)return;
        rollbackButton.hidden=true;rollbackInfo.hidden=true;
        rollbackButton.textContent='Откатить правки автора';
    }
    if(rollbackButton && rollbackInfo)rollbackButton.addEventListener('click',async()=>{
        if(!inspectedAuthor || !adminAllowed || !adminMode || rollbackPending)return;
        const author=inspectedAuthor,own=inspectorEpoch,preview=rollbackPreview;
        rollbackPending=true;rollbackButton.disabled=true;
        const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
        try{
            const response=await fetch('/api/pixels/admin/rollback'+(preview?'':'?'+new URLSearchParams({telegram_id:author.id})),{
                method:preview?'POST':'GET',cache:'no-store',signal:controller.signal,
                headers:{'X-Telegram-Init-Data':tg.initData,...(preview?{'Content-Type':'application/json'}:{})},
                ...(preview?{body:JSON.stringify({telegram_id:author.id,through_id:preview.through_id,request_id:preview.request_id})}:{}),
            });
            const data=await response.json();
            if(!response.ok || !data.success)throw new Error(data.error || 'Откат недоступен');
            if(preview){cancelRead();schedulePoll(0);}
            if(own!==inspectorEpoch || inspectedAuthor!==author || !editing)return;
            rollbackInfo.hidden=false;
            if(preview){
                rollbackPreview=null;rollbackButton.textContent='Проверить снова';
                authorInfo.textContent='Пользователь ID '+author.id;
                rollbackInfo.textContent='Правок отменено: '+data.undone_events+' · клеток восстановлено: '+data.changed_cells+'.';
            }else{
                rollbackPreview={...data,request_id:requestId()};
                rollbackInfo.textContent='По всему полотну · правок автора: '+data.undone_events+' · клеток к восстановлению: '+data.changed_cells+'. Более поздние правки других останутся.';
                rollbackButton.textContent=data.undone_events?'Подтвердить откат':'Нечего откатывать';
            }
        }catch(error){if(own===inspectorEpoch)showMessage(error.name==='AbortError'?'Проверьте полотно: откат мог выполниться. Повтор безопасен.':error.message);}
        finally{clearTimeout(timeout);rollbackPending=false;rollbackButton.disabled=Boolean(rollbackPreview && !rollbackPreview.undone_events);}
    });

    function stopBrush() {
        clearTimeout(holdTimer); clearTimeout(brushTimer);
        holdTimer = null; brushTimer = null;
        magnifier.hidden = true;
    }
    function preview(point) {
        const cell = worldPoint(point.x,point.y);
        if (!onBoard(cell)) { magnifier.hidden=true; selectedCell=null; drawSoon(); return; }
        selectedCell = {x:Math.floor(cell.x),y:Math.floor(cell.y)};
        magnifierCtx.clearRect(0,0,100,100);
        magnifierCtx.fillStyle = tg.themeParams.bg_color || '#faf8f3';
        magnifierCtx.fillRect(0,0,100,100);
        for (let dy=-2;dy<=2;dy++) for (let dx=-2;dx<=2;dx++) {
            const pixel = pixels.get((selectedCell.x+dx)+','+(selectedCell.y+dy));
            if (pixel && pixel[2]>=0) {
                magnifierCtx.fillStyle=colors[pixel[2]];
                magnifierCtx.fillRect((dx+2)*20,(dy+2)*20,20,20);
            }
        }
        magnifierCtx.strokeStyle=tg.themeParams.button_color || '#648caa';
        magnifierCtx.lineWidth=2; magnifierCtx.strokeRect(40,40,20,20);
        magnifier.style.left=Math.max(8,Math.min(width-108,point.x-50))+'px';
        magnifier.style.top=Math.max(8,point.y-135)+'px';
        magnifier.hidden=false; drawSoon();
    }
    async function brushTick() {
        if (!editing || !gesture || !gesture.paint || pointers.size!==1 || (adminMode && (adminArea || adminInspect))) return;
        const activeGesture = gesture;
        const point=Array.from(pointers.values())[0];
        const cell=worldPoint(point.x,point.y);
        const key=Math.floor(cell.x)+','+Math.floor(cell.y)+','+selectedColor+','+adminErase;
        if (key!==lastPainted && !sending && (adminMode || performance.now()>=readyAt)) {
            if (online && navigator.onLine) {
                lastPainted=key;
                await place(cell);
            }
        }
        if (gesture === activeGesture && gesture.paint && pointers.size===1) brushTimer=setTimeout(brushTick,adminMode?90:80);
    }

    canvas.addEventListener('pointerdown', function (event) {
        if (!editing || banned || window.KepikCanvasChat?.isOpen() || (event.pointerType === 'mouse' && event.button !== 0)) return;
        event.preventDefault(); keyboardInput=false;
        if (replay && replay.active) replayFit=false;
        canvas.focus({ preventScroll: true });
        const point=screenPoint(event);
        pointers.set(event.pointerId,point);
        try { canvas.setPointerCapture(event.pointerId); } catch (_error) { /* Optional. */ }
        if (pointers.size===1) {
            lastPainted='';
            gesture={start:point,cameraX:view.x,cameraY:view.y,moved:false,paint:false};
            preview(point);
            if (adminMode && adminArea) {
                const raw=worldPoint(point.x,point.y);
                const c={x:Math.max(board.x0,Math.min(board.x1-1,raw.x)),y:Math.max(board.y0,Math.min(board.y1-1,raw.y))};
                gesture.areaStart={x:Math.floor(c.x),y:Math.floor(c.y)};
                areaSelection={x0:gesture.areaStart.x,y0:gesture.areaStart.y,x1:gesture.areaStart.x+1,y1:gesture.areaStart.y+1};
                updateAreaButton(); drawSoon();
            } else if (!(adminMode && adminInspect) && !(eventFinished && !adminMode)) {
                holdTimer=setTimeout(() => {
                    if (gesture && !gesture.moved && pointers.size===1) {
                        gesture.paint=true; brushTick();
                    }
                },adminMode?150:320);
            }
        } else {
            stopBrush();
            if (gesture) { gesture.moved=true; gesture.paint=false; gesture.blockPaint=true; }
            areaSelection=null; adminApply.hidden=true;
            const pair=Array.from(pointers.values()).slice(0,2);
            const center={x:(pair[0].x+pair[1].x)/2,y:(pair[0].y+pair[1].y)/2};
            pinch={distance:Math.max(1,Math.hypot(pair[0].x-pair[1].x,pair[0].y-pair[1].y)),cell:view.cell,anchor:worldPoint(center.x,center.y)};
        }
    });
    canvas.addEventListener('pointermove', function (event) {
        if (!editing || !pointers.has(event.pointerId)) return;
        const point=screenPoint(event);
        pointers.set(event.pointerId,point);
        if (pointers.size>=2 && pinch) {
            const pair=Array.from(pointers.values()).slice(0,2);
            const center={x:(pair[0].x+pair[1].x)/2,y:(pair[0].y+pair[1].y)/2};
            view.cell=Math.max(minimumCell(),Math.min(56,pinch.cell*Math.hypot(pair[0].x-pair[1].x,pair[0].y-pair[1].y)/pinch.distance));
            view.x=pinch.anchor.x-(center.x-width/2)/view.cell;
            view.y=pinch.anchor.y-(center.y-height/2)/view.cell;
        } else if (gesture) {
            if (adminMode && adminArea && gesture.areaStart) {
                const raw=worldPoint(point.x,point.y), start=gesture.areaStart;
                const c={x:Math.max(board.x0,Math.min(board.x1-1,raw.x)),y:Math.max(board.y0,Math.min(board.y1-1,raw.y))};
                areaSelection={x0:Math.min(start.x,Math.floor(c.x)),y0:Math.min(start.y,Math.floor(c.y)),x1:Math.max(start.x,Math.floor(c.x))+1,y1:Math.max(start.y,Math.floor(c.y))+1};
                gesture.moved=true; updateAreaButton(); preview(point); return;
            }
            if (gesture.paint || (adminMode && !adminArea && !adminInspect && !gesture.blockPaint)) {
                clearTimeout(holdTimer);
                if (!gesture.paint) { gesture.paint=true; brushTick(); }
                preview(point); return;
            }
            const dx=point.x-gesture.start.x,dy=point.y-gesture.start.y;
            if (!gesture.moved && Math.hypot(dx,dy)<8) { preview(point); return; }
            stopBrush(); gesture.moved=true;
            view.x=gesture.cameraX-dx/view.cell; view.y=gesture.cameraY-dy/view.cell;
        }
        clampView();
        selectedCell=null; invalidateView();
    });
    function endPointer(event) {
        if (!pointers.has(event.pointerId)) return;
        const point=screenPoint(event);
        const tap=event.type==='pointerup' && pointers.size===1 && !pinch && gesture
            && !gesture.moved && !gesture.paint && !(adminMode && adminArea)
            && Math.hypot(point.x-gesture.start.x,point.y-gesture.start.y)<8;
        pointers.delete(event.pointerId);
        stopBrush();
        if (tap) place(worldPoint(point.x,point.y));
        if (pointers.size===0) { gesture=null; pinch=null; saveView(); }
        else {
            const remaining=Array.from(pointers.values())[0];
            gesture={start:remaining,cameraX:view.x,cameraY:view.y,moved:true,paint:false,blockPaint:true}; pinch=null;
        }
    }
    canvas.addEventListener('pointerup',endPointer);
    canvas.addEventListener('pointercancel',endPointer);
    canvas.addEventListener('lostpointercapture',function(event) {
        if (pointers.has(event.pointerId)) endPointer({type:'pointercancel',pointerId:event.pointerId,clientX:event.clientX,clientY:event.clientY});
    });

    canvas.addEventListener('wheel', function (event) {
        if (!editing) return;
        replayFit=false;
        event.preventDefault();
        const point = screenPoint(event);
        const anchor = worldPoint(point.x, point.y);
        view.cell = Math.max(minimumCell(), Math.min(56, view.cell * Math.exp(-event.deltaY * 0.002)));
        view.x = anchor.x - (point.x - width / 2) / view.cell;
        view.y = anchor.y - (point.y - height / 2) / view.cell;
        invalidateView();
        saveView();
    }, { passive: false });

    canvas.addEventListener('keydown', function (event) {
        if (!editing) return;
        const offsets = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        if (offsets[event.key]) {
            event.preventDefault();
            lastKeyboardCell.x += offsets[event.key][0];
            lastKeyboardCell.y += offsets[event.key][1];
            lastKeyboardCell.x=Math.max(board.x0,Math.min(board.x1-1,lastKeyboardCell.x));
            lastKeyboardCell.y=Math.max(board.y0,Math.min(board.y1-1,lastKeyboardCell.y));
            selectedCell = { ...lastKeyboardCell };
            drawSoon();
        } else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            place(lastKeyboardCell);
        }
    });

    document.addEventListener('pointerdown', () => { keyboardInput=false; }, true);
    document.addEventListener('keydown', function (event) {
        keyboardInput=true;
        if (!editing) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            if (window.KepikCanvasChat?.isOpen()) { window.KepikCanvasChat.close(); return; }
            if (!picker.hidden) { closePicker(); colorToggle.focus(); } else exit();
        }
        if (event.key === 'Tab') {
            const controls = [...(banned || window.KepikCanvasChat?.isOpen() ? [] : [canvas]), ...tools.querySelectorAll('button, select, input')].filter(button => !button.disabled && !button.closest('[hidden]') && button.getClientRects().length && getComputedStyle(button).visibility !== 'hidden');
            const first = controls[0];
            const last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });

    window.addEventListener('resize', resize);
    if (window.visualViewport) window.visualViewport.addEventListener('resize', resize);
    try {
        ['viewportChanged','fullscreenChanged','safeAreaChanged','contentSafeAreaChanged'].forEach(name => tg.onEvent(name,resize));
        tg.onEvent('themeChanged', function () {
            document.documentElement.setAttribute('data-theme', tg.colorScheme);
            drawSoon();
        });
    } catch (_error) { /* Optional. */ }
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            stopBrush(); pointers.clear(); gesture=null; pinch=null;
            clearTimeout(pollTimer); clearTimeout(cooldownTimer); clearTimeout(eventTimer);
            cancelRead();
            saveView();
        } else {
            if (editing && tg.BackButton) tg.BackButton.show();
            resize(); schedulePoll(0); updateEventDisplay();
        }
    });
    window.addEventListener('offline', function () {
        online = false;
        clearTimeout(pollTimer);
        cancelRead();
        updateCooldown();
    });
    window.addEventListener('online', function () { schedulePoll(0); });
    window.addEventListener('pagehide', function () {
        pageActive = false;
        stopBrush(); pointers.clear(); gesture=null; pinch=null;
        clearTimeout(pollTimer); clearTimeout(cooldownTimer); clearTimeout(eventTimer);
        cancelRead();
        saveView();
    });
    window.addEventListener('pageshow', function (event) {
        pageActive = true;
        if (event.persisted) { resize(); schedulePoll(0); updateEventDisplay(); if (editing && tg.BackButton) tg.BackButton.show(); }
    });

    const ready = window.kkepikApp ? window.kkepikApp.ready : Promise.reject(new Error('App unavailable'));
    if (window.KepikCanvasReplay) replay=window.KepikCanvasReplay.create({onChange:drawSoon,onFit:fitReplayView});
    ready.then(function () {
        initialized = true;
        background.hidden = false;
        banner.hidden = false;
        resize();
        // Optional artwork never delays the schedule's bootstrap or first paint.
        schedulePoll((window.kkepikApp && window.kkepikApp.lowData) ? 1500 : 400);
    }).catch(function () { /* The normal app handles authentication errors. */ });
}());
