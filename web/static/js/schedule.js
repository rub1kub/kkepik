const WEEKDAY_SCHEDULE = [
    { start: '08:45', end: '10:05' },
    { start: '10:25', end: '11:45' },
    { start: '12:05', end: '13:25' },
    { start: '13:35', end: '14:55' },
    { start: '15:05', end: '16:25' },
    { start: '16:35', end: '17:55' }
];

const SATURDAY_SCHEDULE = [
    { start: '08:45', end: '10:00' },
    { start: '10:10', end: '11:25' },
    { start: '11:35', end: '12:50' },
    { start: '13:00', end: '14:15' },
    { start: '14:25', end: '15:40' },
    { start: '15:50', end: '17:05' }
];

let lastState = {
    wasInClass: false,
    lastPairEnd: null
};

let isTeacher = false;

const PROGRESS_TIME_MODES = [
    { id: 'natural', label: 'обычный формат' },
    { id: 'digital', label: 'минуты и секунды' },
    { id: 'seconds', label: 'только секунды' },
    { id: 'hours-minutes', label: 'часы и минуты' },
    { id: 'decimal-hours', label: 'часы десятичным числом' },
    { id: 'target-time', label: 'точное время события' }
];
let progressTimeModeIndex = 0;

async function checkUserRole() {
    try {
        if (window.kkepikApp) {
            const bootstrap = await window.kkepikApp.ready;
            if (bootstrap.user) {
                isTeacher = bootstrap.user.role === 'teacher';
                return;
            }
        }
        const response = await fetch('/api/me', {
            headers: { 'X-Telegram-Init-Data': tg.initData }
        });
        const data = await response.json();
        isTeacher = data.role === 'teacher';
    } catch (error) {
        console.error('Ошибка при получении роли пользователя:', error);
        isTeacher = false;
    }
}

function launchConfetti() {
    const duration = 1500;
    const end = Date.now() + duration;
    const colors = ['#fb41a2', '#007fe0', '#01cb56', '#01cb56'];

    (function frame() {
        confetti({
            particleCount: 3,
            angle: 60,
            spread: 45,
            origin: { x: 0, y: 0.8 },
            colors: colors,
            gravity: 1.2,
            scalar: 1.2,
            ticks: 300
        });
        confetti({
            particleCount: 3,
            angle: 120,
            spread: 45,
            origin: { x: 1, y: 0.8 },
            colors: colors,
            gravity: 1.2,
            scalar: 1.2,
            ticks: 300
        });

        if (Date.now() < end) {
            setTimeout(frame, 100);
        }
    }());

    const progressBox = document.getElementById('progressBox');
    progressBox.classList.add('celebrate');
    setTimeout(() => {
        progressBox.classList.remove('celebrate');
    }, 500);
}

function formatNaturalTime(totalSeconds) {
    if (totalSeconds < 60) {
        return `${totalSeconds} ${declOfNum(totalSeconds, ['секунда', 'секунды', 'секунд'])}`;
    }

    const totalMinutes = Math.floor(totalSeconds / 60);
    if (totalMinutes < 60) {
        return `${totalMinutes} ${declOfNum(totalMinutes, ['минута', 'минуты', 'минут'])}`;
    }

    const hours = Math.floor(totalMinutes / 60);
    const remainingMinutes = totalMinutes % 60;
    if (remainingMinutes === 0) {
        return `${hours} ${declOfNum(hours, ['час', 'часа', 'часов'])}`;
    }

    return `${hours} ${declOfNum(hours, ['час', 'часа', 'часов'])} и ${remainingMinutes} ${declOfNum(remainingMinutes, ['минута', 'минуты', 'минут'])}`;
}

function formatProgressTime(totalSeconds, targetTime, contextLabel) {
    const safeSeconds = Math.max(0, Math.round(totalSeconds));
    const totalMinutes = Math.floor(safeSeconds / 60);
    const mode = PROGRESS_TIME_MODES[progressTimeModeIndex];

    if (mode.id === 'digital') {
        return {
            primary: `${String(totalMinutes).padStart(2, '0')}:${String(safeSeconds % 60).padStart(2, '0')}`,
            secondary: contextLabel
        };
    }
    if (mode.id === 'seconds') {
        return {
            primary: `${safeSeconds} секунд`,
            secondary: contextLabel
        };
    }
    if (mode.id === 'hours-minutes') {
        return {
            primary: `${Math.floor(safeSeconds / 3600)} ч ${Math.floor((safeSeconds % 3600) / 60)} мин`,
            secondary: contextLabel
        };
    }
    if (mode.id === 'decimal-hours') {
        return {
            primary: `${(safeSeconds / 3600).toFixed(2).replace('.', ',')} часа`,
            secondary: contextLabel
        };
    }
    if (mode.id === 'target-time' && targetTime) {
        let secondary = 'время окончания';
        if (contextLabel === 'до пары') secondary = 'начало пары';
        if (contextLabel === 'до перемены') secondary = 'конец пары';
        if (contextLabel === 'до конца пар') secondary = 'конец занятий';
        return { primary: targetTime, secondary };
    }
    return {
        primary: formatNaturalTime(safeSeconds),
        secondary: contextLabel
    };
}

function renderProgressTime(totalSeconds, targetTime, contextLabel) {
    const display = formatProgressTime(totalSeconds, targetTime, contextLabel);
    document.getElementById('timerPassed').textContent = display.primary;
    document.getElementById('timerLeft').textContent = display.secondary;
    const progressBar = document.getElementById('progress_bar');
    if (progressBar) {
        const mode = PROGRESS_TIME_MODES[progressTimeModeIndex];
        progressBar.dataset.timeMode = mode.id;
        progressBar.setAttribute(
            'aria-label',
            `${display.primary}, ${display.secondary}. Сейчас выбран ${mode.label}`
        );
    }
}

function renderRestProgress(now) {
    const mode = PROGRESS_TIME_MODES[progressTimeModeIndex];
    const hours = now.getHours();
    const minutes = now.getMinutes();
    const seconds = now.getSeconds();
    const secondsSinceMidnight = hours * 3600 + minutes * 60 + seconds;
    const clock = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
    let primary = 'Приятного';
    let secondary = 'отдыха!';

    if (mode.id === 'digital') {
        primary = `${clock}:${String(seconds).padStart(2, '0')}`;
        secondary = 'сейчас';
    } else if (mode.id === 'seconds') {
        primary = `${secondsSinceMidnight} секунд`;
        secondary = 'с начала суток';
    } else if (mode.id === 'hours-minutes') {
        primary = `${hours} ч ${minutes} мин`;
        secondary = 'сейчас';
    } else if (mode.id === 'decimal-hours') {
        primary = `${(secondsSinceMidnight / 3600).toFixed(2).replace('.', ',')} часа`;
        secondary = 'с начала суток';
    } else if (mode.id === 'target-time') {
        primary = clock;
        secondary = `${now.getDate()} ${[
            'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
            'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
        ][now.getMonth()]}`;
    }

    document.getElementById('timerPassed').textContent = primary;
    document.getElementById('timerLeft').textContent = secondary;
    const progressBar = document.getElementById('progress_bar');
    if (progressBar) {
        progressBar.dataset.timeMode = mode.id;
        progressBar.setAttribute(
            'aria-label',
            `${primary}, ${secondary}. Сейчас выбран ${mode.label}`
        );
    }
}

function declOfNum(n, titles) {
    return titles[n % 10 === 1 && n % 100 !== 11 ? 0 : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? 1 : 2];
}

// Добавляем функцию проверки праздничных дней
function isHoliday(date) {
    const holidays = [
        '2025-04-29', // 29 апреля
        '2025-05-01', // 1 мая
        '2025-05-02', // 2 мая
        '2025-05-03', // 3 мая
        '2025-05-08', // 8 мая
        '2025-05-09', // 9 мая
        '2025-05-10', // 10 мая
        '2025-06-12', // 12 июня 2025
        '2025-06-13', // 13 июня 2025
        '2025-06-14'  // 14 июня 2025
    ];
    const dateString = [
        date.getFullYear(),
        String(date.getMonth() + 1).padStart(2, '0'),
        String(date.getDate()).padStart(2, '0')
    ].join('-');
    return holidays.includes(dateString);
}

function updateProgressBar(forceRest = false) {
    const now = new Date();
    const currentTime = now.getHours() * 60 + now.getMinutes();
    const currentSeconds = now.getSeconds();
    // Проверка на сокращённый день (11 июня 2025)
    const isSpecialShortDay = now.getFullYear() === 2025 && now.getMonth() === 5 && now.getDate() === 11;
    const isSaturday = now.getDay() === 6 || isSpecialShortDay;
    const isSunday = now.getDay() === 0;
    const isHolidayToday = isHoliday(now);
    const schedule = isSaturday ? SATURDAY_SCHEDULE : WEEKDAY_SCHEDULE;

    let currentPair = null;
    let progress = 100;
    let timerPassed = 'Приятного';
    let timerLeft = 'отдыха!';
    let isInClass = false;
    let isActive = false;
    let countdownSeconds = null;
    let targetTime = null;

    if (isSunday || isHolidayToday || forceRest) {
        progress = 100;
        document.getElementById('progress_line').style.width = (100 - progress) + '%';
        renderRestProgress(now);
        lastState.wasInClass = false;
        lastState.lastPairEnd = null;
        return;
    }

    let currentOrNextPairIndex = -1;
    for (let i = 0; i < schedule.length; i++) {
        const pair = schedule[i];
        const [startHour, startMinute] = pair.start.split(':').map(Number);
        const [endHour, endMinute] = pair.end.split(':').map(Number);
        const startTime = startHour * 60 + startMinute;
        const endTime = endHour * 60 + endMinute;

        if (currentTime >= startTime && currentTime < endTime) {
            currentPair = pair;
            const totalDuration = endTime - startTime;
            const elapsedTime = (currentTime - startTime) + (currentSeconds / 60);
            progress = (elapsedTime / totalDuration) * 100;
            
            timerLeft = i === schedule.length - 1 ? 'до конца пар' : 'до перемены';
            countdownSeconds = endTime * 60 - (currentTime * 60 + currentSeconds);
            targetTime = pair.end;
            isActive = true;
            isInClass = true;
            currentOrNextPairIndex = i;
            break;
        }
        else if (currentTime < startTime) {
            currentOrNextPairIndex = i;
            break;
        }
    }

    if (!isActive && currentOrNextPairIndex !== -1) {
        const nextPair = schedule[currentOrNextPairIndex];
        const [startHour, startMinute] = nextPair.start.split(':').map(Number);
        const startTime = startHour * 60 + startMinute;

        const secondsUntilStart = startTime * 60 - (currentTime * 60 + currentSeconds);

        if (secondsUntilStart > 0) {
            timerLeft = 'до пары';
            countdownSeconds = secondsUntilStart;
            targetTime = nextPair.start;
            progress = 0;
            isActive = true;
        }
    }

    const lastPair = schedule[schedule.length - 1];
    const [lastEndHour, lastEndMinute] = lastPair.end.split(':').map(Number);
    const lastEndTime = lastEndHour * 60 + lastEndMinute;

    if ((!isTeacher && currentTime >= lastEndTime) || !isActive) {
        progress = 100;
        timerPassed = 'Приятного';
        timerLeft = 'отдыха!';
        countdownSeconds = null;
        targetTime = null;

        if (lastState.wasInClass && currentTime === lastEndTime && currentSeconds === 0) {
            if (window.launchSideConfetti) {
                window.launchSideConfetti();
            }
        }
    }

    lastState.wasInClass = isInClass;
    lastState.lastPairEnd = currentPair ? currentPair.end : null;

    if (countdownSeconds !== null) {
        renderProgressTime(countdownSeconds, targetTime, timerLeft);
    } else {
        renderRestProgress(now);
    }
    document.getElementById('progress_line').style.width = `${100 - Math.min(100, Math.max(0, progress))}%`;
}

window.cycleProgressTimeMode = function() {
    progressTimeModeIndex = (progressTimeModeIndex + 1) % PROGRESS_TIME_MODES.length;
    updateProgressBar();
    return PROGRESS_TIME_MODES[progressTimeModeIndex].id;
};

window.getProgressTimeMode = function() {
    return PROGRESS_TIME_MODES[progressTimeModeIndex].id;
};

function getUpdateInterval() {
    const now = new Date();
    const currentTime = now.getHours() * 60 + now.getMinutes();
    const currentSeconds = now.getSeconds();
    // Проверка на сокращённый день (11 июня 2025)
    const isSpecialShortDay = now.getFullYear() === 2025 && now.getMonth() === 5 && now.getDate() === 11;
    const isSaturday = now.getDay() === 6 || isSpecialShortDay;
    const schedule = isSaturday ? SATURDAY_SCHEDULE : WEEKDAY_SCHEDULE;

    for (const pair of schedule) {
        const [startHour, startMinute] = pair.start.split(':').map(Number);
        const [endHour, endMinute] = pair.end.split(':').map(Number);
        const startTime = startHour * 60 + startMinute;
        const endTime = endHour * 60 + endMinute;

        const timeToStart = startTime - currentTime;
        const timeToEnd = endTime - currentTime;

        if ((timeToStart >= -1 && timeToStart <= 5) || (timeToEnd >= -1 && timeToEnd <= 5)) {
            return 100;
        }
    }

    return 1000;
}

let updateTimer = null;
async function startDynamicUpdate() {
    if (updateTimer) {
        clearTimeout(updateTimer);
    }
    
    updateProgressBar();
    const interval = getUpdateInterval();
    updateTimer = setTimeout(startDynamicUpdate, interval);
}

// Проверяем роль пользователя один раз при загрузке страницы
document.addEventListener('DOMContentLoaded', async function() {
    await checkUserRole();
    startDynamicUpdate();
});

function displaySchedule(data) {
    const scheduleContent = document.getElementById('schedule-content');
    scheduleContent.innerHTML = '';

    if (!data.schedule || data.schedule.length === 0) {
        const row = document.createElement('div');
        row.className = 'schedule-row';
        row.innerHTML = '<div class="subject" style="grid-column: 1 / -1; text-align: center;">Расписание отсутствует</div>';
        scheduleContent.appendChild(row);
        return;
    }

    // Разделяем основные и совмещенные пары
    const mainSchedule = [];
    const combinedSchedule = [];
    let isCombined = false;

    data.schedule.forEach(item => {
        if (item === '\nСовмещенные пары:') {
            isCombined = true;
            return;
        }
        if (isCombined) {
            combinedSchedule.push(item);
        } else {
            mainSchedule.push(item);
        }
    });

    // Отображаем основные пары
    mainSchedule.forEach(item => {
        const row = document.createElement('div');
        row.className = 'schedule-row';
        row.innerHTML = item;
        scheduleContent.appendChild(row);
    });

    // Если есть совмещенные пары, добавляем разделитель и отображаем их
    if (combinedSchedule.length > 0) {
        const divider = document.createElement('div');
        divider.className = 'schedule-divider';
        divider.innerHTML = '<div class="divider-text">Совмещенные пары</div>';
        scheduleContent.appendChild(divider);

        combinedSchedule.forEach(item => {
            const row = document.createElement('div');
            row.className = 'schedule-row combined';
            row.innerHTML = item;
            scheduleContent.appendChild(row);
        });
    }
}
