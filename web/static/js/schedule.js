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
    { start: '14:25', end: '15:50' },
    { start: '16:00', end: '17:15' }
];

let lastState = {
    wasInClass: false,
    lastPairEnd: null
};

let isTeacher = false;

async function checkUserRole() {
    try {
        const userId = tg.initDataUnsafe.user.id;
        const response = await fetch(`/api/user/${userId}`);
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

function formatTimeLeft(minutes, seconds = 0) {
    if (minutes < 1) {
        const totalSeconds = minutes * 60 + seconds;
        return `${totalSeconds} ${declOfNum(totalSeconds, ['секунда', 'секунды', 'секунд'])}`;
    }
    
    if (minutes < 60) {
        return `${minutes} ${declOfNum(minutes, ['минута', 'минуты', 'минут'])}`;
    }
    
    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    
    if (remainingMinutes === 0) {
        return `${hours} ${declOfNum(hours, ['час', 'часа', 'часов'])}`;
    }
    
    return `${hours} ${declOfNum(hours, ['час', 'часа', 'часов'])} и ${remainingMinutes} ${declOfNum(remainingMinutes, ['минута', 'минуты', 'минут'])}`;
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

    if (isSunday || isHolidayToday || forceRest) {
        progress = 100;
        timerPassed = 'Приятного';
        timerLeft = 'отдыха!';
        document.getElementById('progress_line').style.width = (100 - progress) + '%';
        document.getElementById('timerPassed').textContent = timerPassed;
        document.getElementById('timerLeft').textContent = timerLeft;
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
            
            const remainingMinutes = endTime - currentTime - 1;
            const remainingSeconds = 60 - currentSeconds;
            timerPassed = formatTimeLeft(remainingMinutes, remainingSeconds);
            timerLeft = i === schedule.length - 1 ? 'до конца пар' : 'до перемены';
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

        const remainingMinutes = startTime - currentTime - 1;
        const remainingSeconds = 60 - currentSeconds;
        
        if (remainingMinutes >= 0 || (remainingMinutes === -1 && remainingSeconds > 0)) {
            timerPassed = formatTimeLeft(remainingMinutes, remainingSeconds);
            timerLeft = 'до пары';
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

        if (lastState.wasInClass && currentTime === lastEndTime && currentSeconds === 0) {
            if (window.launchSideConfetti) {
                window.launchSideConfetti();
            }
        }
    }

    lastState.wasInClass = isInClass;
    lastState.lastPairEnd = currentPair ? currentPair.end : null;

    document.getElementById('timerPassed').textContent = timerPassed;
    document.getElementById('timerLeft').textContent = timerLeft;
    document.getElementById('progress_line').style.width = `${100 - Math.min(100, Math.max(0, progress))}%`;
}

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