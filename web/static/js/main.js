let tg = window.Telegram.WebApp;
tg.expand();

tg.BackButton.hide();

if (tg && tg.colorScheme) {
    document.documentElement.setAttribute('data-theme', tg.colorScheme);
}

if (!window.scheduleState) {
    window.scheduleState = {};
}
if (!window.scheduleState.displayedDate) {
    window.scheduleState.displayedDate = new Date();
}

async function authorize() {
    const initData = tg.initData;
    if (!initData) {
        window.location.href = '/api/docs';
        return;
    }
    try {
        const resp = await fetch('/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tgWebAppData: initData })
        });
        const data = await resp.json();
        if (data.success) {
            document.getElementById('main-content').style.display = '';
        } else {
            showAuthError(data.error || 'Ошибка авторизации.');
        }
    } catch (e) {
        showAuthError('Ошибка соединения с сервером.');
    }
}
function showAuthError(msg) {
    document.getElementById('auth-error').textContent = msg;
    document.getElementById('auth-error').style.display = '';
    document.getElementById('main-content').style.display = 'none';
}
authorize();

window.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.game-card').forEach(card => {
        card.addEventListener('click', () => {
            tg.HapticFeedback.impactOccurred('soft');
        });
    });

    document.querySelectorAll('a').forEach(link => {
        if (link.id === 'downloadScheduleBtn') return; // не трогаем кнопку скачать!
        if (link.href.startsWith(window.location.origin)) {
            const url = new URL(link.href);
            url.searchParams.set('tgWebAppData', tg.initData);
            link.href = url.toString();
        }
    });

    const progressBar = document.getElementById('progress_bar');
    if (progressBar) {
        progressBar.addEventListener('click', function(e) {
            tg.HapticFeedback.impactOccurred('medium');
            launchSideConfetti();
        });
    }
});

const SUBJECT_SHORTCUTS = {
    'физическая культура': 'Физ-ра',
    'иностранный язык': 'Ин. яз',
    'русский язык': 'Рус. яз',
    'информационные технологии': 'Инф. тех',
    'основы безопасности и защиты родины': 'ОБЖ',
    'математика': 'Математика',
    'литература': 'Лит-ра',
    'история': 'История',
    'обществознание': 'Общество',
    'ндивидуальный проект': 'Инд. проект',
    'разговор о важном': 'Разговоры',
    'проектирование и разработка веб-приложений': 'Веб-разработка',
    'разработка кода информационных систем': 'Разраб. ИС'
};

function shortenSubjectName(subject) {
    if (!subject || subject === 'Нет пары') return subject;

    const lowerSubject = subject.toLowerCase();
    for (let [full, short] of Object.entries(SUBJECT_SHORTCUTS)) {
        if (lowerSubject.includes(full)) {
            return short;
        }
    }

    if (subject.length > 12) {
        return subject.slice(0, 12) + '...';
    }

    return subject;
}

document.addEventListener('DOMContentLoaded', async function() {
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

    let currentDate = new Date();
    let scheduleCache = new Map();
    let hasPrevDay = false;
    let hasNextDay = false;
    let lastSwipeDirection = null;
    let displayedDate = new Date();
    let isUpdating = false;
    let isInitialLoad = true;

    console.log('[init] currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);

    const months = [
        'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
        'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
    ];

    window.scheduleState = {
        displayedDate: new Date(),
        currentScheduleData: null,
        isUpdating: false,
        initialized: false,
        currentEntity: null,
        scheduleCache: new Map()
    };

    console.log('=== Initialization ===');
    console.log('Initial date:', displayedDate);

    function formatDateForApi(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function formatDisplayDate(date) {
        const months = [
            'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
            'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
        ];
        return `${date.getDate()} ${months[date.getMonth()]}`;
    }

    function isHoliday(date) {
        const holidays = [
            '2025-04-29', 
            '2025-05-01', 
            '2025-05-02', 
            '2025-05-03', 
            '2025-05-08', 
            '2025-05-09', 
            '2025-05-10',
            '2025-06-12', 
            '2025-06-13', 
            '2025-06-14'
        ];
        const dateString = [
            date.getFullYear(),
            String(date.getMonth() + 1).padStart(2, '0'),
            String(date.getDate()).padStart(2, '0')
        ].join('-');
        return holidays.includes(dateString);
    }

    async function checkAdjacentDays(callback) {
        console.log('[checkAdjacentDays] currentDate:', currentDate);
        // Поиск предыдущего рабочего дня
        let prevDate = new Date(currentDate);
        do {
            prevDate.setDate(prevDate.getDate() - 1);
        } while (prevDate.getDay() === 0 || isHoliday(prevDate));
        // Поиск следующего рабочего дня
        let nextDate = new Date(currentDate);
        do {
            nextDate.setDate(nextDate.getDate() + 1);
        } while (nextDate.getDay() === 0 || isHoliday(nextDate));

        let prevSchedule = null, nextSchedule = null;
        hasPrevDay = false;
        hasNextDay = false;

        if (prevDate.getDay() !== 0 && !isHoliday(prevDate)) {
            prevSchedule = await loadScheduleForDate(prevDate);
            hasPrevDay = prevSchedule && !prevSchedule.error500 && Array.isArray(prevSchedule.schedule);
            console.log('[checkAdjacentDays] prevDate:', prevDate, 'hasPrevDay:', hasPrevDay);
        }

        if (nextDate.getDay() !== 0 && !isHoliday(nextDate)) {
            nextSchedule = await loadScheduleForDate(nextDate);
            hasNextDay = nextSchedule && !nextSchedule.error500 && Array.isArray(nextSchedule.schedule);
            console.log('[checkAdjacentDays] nextDate:', nextDate, 'hasNextDay:', hasNextDay);
        }

        updateNavigationIndicators();
        if (typeof callback === 'function') callback();
        console.log('[checkAdjacentDays] END, currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
    }

    function updateNavigationIndicators() {
        const title = document.querySelector('.schedule-title');
        title.classList.toggle('has-prev', hasPrevDay);
        title.classList.toggle('has-next', hasNextDay);
    }

    async function loadScheduleForDate(date) {
        if (date.getDay() === 0 || isHoliday(date)) {
            console.log('Не отправляем запрос: выходной или праздник', date.toLocaleDateString('ru-RU'));
            return null;
        }
        const dateString = date.toLocaleDateString('ru-RU');
        console.log('Загрузка расписания для даты:', dateString);

        if (window.scheduleState.scheduleCache.has(dateString)) {
            console.log('Используем кэшированное расписание для:', dateString);
            return window.scheduleState.scheduleCache.get(dateString);
        }

        try {
            // Проверяем, есть ли выбранная группа/преподаватель
            if (window.scheduleState && window.scheduleState.currentEntity) {
                const entity = window.scheduleState.currentEntity;
                let url = '';
                let body = {};
                
                if (entity.entity_type === 'group') {
                    url = '/api/schedule/group';
                    body = { group: entity.entity_id, date: dateString };
                } else if (entity.entity_type === 'teacher') {
                    url = '/api/schedule/teacher';
                    body = { teacher: entity.entity_id, date: dateString };
                }
                
                if (url) {
                    console.log('Отправляем запрос на:', url, 'с данными:', body);
                    const response = await fetch(url, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(body)
                    });
                    
                    if (response.status === 500) {
                        console.log('Сервер вернул ошибку 500 для даты:', dateString);
                        return { error500: true };
                    }
                    
                    const data = await response.json();
                    if (response.ok) {
                        console.log('Успешно получено расписание для:', dateString);
                        window.scheduleState.scheduleCache.set(dateString, data);
                        return data;
                    }
                }
            }
            
            // Если нет выбранной группы/преподавателя или запрос не удался, используем ID пользователя
            const userId = tg.initDataUnsafe.user.id;
            console.log('Отправляем запрос для пользователя:', userId, 'на дату:', dateString);
            const response = await fetch(`/api/schedule/user/${userId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ date: dateString })
            });

            if (response.status === 500) {
                console.log('Сервер вернул ошибку 500 для даты:', dateString);
                return { error500: true };
            }

            const data = await response.json();
            if (response.ok) {
                console.log('Успешно получено расписание для:', dateString);
                window.scheduleState.scheduleCache.set(dateString, data);
                return data;
            }
            console.log('Не удалось получить расписание для:', dateString);
            return null;
        } catch (error) {
            console.error('Ошибка при получении расписания для', dateString + ':', error);
            return null;
        }
    }

    // === Поиск ближайшей даты с расписанием вперед ===
    async function findNextScheduleDate(fromDate) {
        let nextDate = new Date(fromDate);
        // Ищем только первый рабочий день после fromDate
        do {
            nextDate.setDate(nextDate.getDate() + 1);
        } while (nextDate.getDay() === 0 || isHoliday(nextDate));
        // Проверяем расписание только на найденную дату
        const nextSchedule = await loadScheduleForDate(nextDate);
        if (nextSchedule && nextSchedule.schedule && nextSchedule.schedule.some(lesson => lesson && !lesson.includes('Нет'))) {
            return { date: new Date(nextDate), schedule: nextSchedule };
        }
        return null;
    }

    async function initialLoad() {
        console.log('[initialLoad] START, currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
        lastSwipeDirection = null;
        if (currentDate.getDay() === 0 || isHoliday(currentDate)) {
            const nextWorkingDay = new Date(currentDate);
            while (nextWorkingDay.getDay() === 0 || isHoliday(nextWorkingDay)) {
                nextWorkingDay.setDate(nextWorkingDay.getDate() + 1);
            }
            // СРАЗУ обновляем даты!
            currentDate = new Date(nextWorkingDay);
            displayedDate = new Date(nextWorkingDay);
            window.scheduleState.displayedDate = new Date(nextWorkingDay);

            window.scheduleState.currentScheduleData = await loadScheduleForDate(nextWorkingDay);

            console.log('[initialLoad] Перешли на рабочий день:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
            await checkAdjacentDays(updateScheduleDisplay);
            isInitialLoad = false;
            console.log('[initialLoad] END (после смены даты), currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
            return;
        } else {
            window.scheduleState.currentScheduleData = await loadScheduleForDate(currentDate);
        }
        console.log('[schedule] initialLoad: scheduleState.currentScheduleData:', window.scheduleState.currentScheduleData);
        function isAllLessonsFinished(scheduleData, date) {
            if (!scheduleData || !scheduleData.schedule) return false;
            const lessons = scheduleData.schedule.filter(lesson => lesson && !lesson.includes('Нет'));
            if (lessons.length === 0) return false;
            let lastUserPairNumber = 0;
            for (const lesson of lessons) {
                const match = lesson.match(/^▪️(\d+) пара – (.+)$/);
                if (match) {
                    const pairNumber = parseInt(match[1]);
                    if (pairNumber > lastUserPairNumber) {
                        lastUserPairNumber = pairNumber;
                    }
                }
            }
            if (lastUserPairNumber === 0) return false;
            const isSaturday = date.getDay() === 6;
            const schedule = isSaturday ? SATURDAY_SCHEDULE : WEEKDAY_SCHEDULE;
            if (lastUserPairNumber > schedule.length) {
                lastUserPairNumber = schedule.length;
            }
            const lastPair = schedule[lastUserPairNumber - 1];
            const [lastEndHour, lastEndMinute] = lastPair.end.split(':').map(Number);
            const now = new Date();
            if (now.toDateString() !== date.toDateString()) return false;
            const currentTime = now.getHours() * 60 + now.getMinutes();
            const lastEndTime = lastEndHour * 60 + lastEndMinute;
            return currentTime >= lastEndTime;
        }
        // Если все пары на сегодня закончились или расписания на сегодня нет — ищем ближайшее будущее расписание
        if (isAllLessonsFinished(window.scheduleState.currentScheduleData, currentDate) ||
            !window.scheduleState.currentScheduleData || !window.scheduleState.currentScheduleData.schedule ||
            !window.scheduleState.currentScheduleData.schedule.some(lesson => lesson && !lesson.includes('Нет'))) {
            const found = await findNextScheduleDate(currentDate);
            if (found) {
                currentDate = found.date;
                window.scheduleState.currentScheduleData = found.schedule;
                if (!isInitialLoad && window.launchSideConfetti) {
                    window.launchSideConfetti();
                }
            }
        }
        document.querySelector('.schedule-column:last-child').style.display = 'none';
        document.querySelector('.schedule-column:first-child').style.flex = '1';
        await checkAdjacentDays(updateScheduleDisplay);
        isInitialLoad = false;
        console.log('[initialLoad] END, currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
    }

    // Перемещаю функцию updateScheduleDisplay в глобальную область видимости
    window.updateScheduleDisplay = function() {
        console.log('[updateScheduleDisplay] START, currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
        // Проверка на сокращённый день (11 июня 2025)
        const isSpecialShortDay = currentDate.getFullYear() === 2025 && currentDate.getMonth() === 5 && currentDate.getDate() === 11;
        const isSaturday = currentDate.getDay() === 6 || isSpecialShortDay;
        const schedule = isSaturday ? SATURDAY_SCHEDULE : WEEKDAY_SCHEDULE;

        document.querySelector('.schedule-title').textContent = `Расписание на ${formatDisplayDate(currentDate)}`;
        console.log('[updateScheduleDisplay] schedule-title set to:', formatDisplayDate(currentDate));

        if (window.scheduleState.currentScheduleData && window.scheduleState.currentScheduleData.error500) {
            const bellList = document.querySelector('#bell_list .row-container');
            bellList.innerHTML = '';

            // Скрываем schedule-title
            const title = document.querySelector('.schedule-title');
            if (title) title.style.display = 'none';

            // Скрываем schedule-reactions
            const reactions = document.getElementById('schedule-reactions');
            if (reactions) reactions.style.display = 'none';

            // Скрываем groupSelectorContainer
            const groupSelectorContainer = document.getElementById('groupSelectorContainer');
            if (groupSelectorContainer) groupSelectorContainer.style.display = 'none';

            // Алерт над menu-container
            let alert = document.getElementById('schedule-error-alert');
            if (!alert) {
                alert = document.createElement('div');
                alert.id = 'schedule-error-alert';
                alert.textContent = 'Расписание недоступно. Возможно вы неправильно зарегистрировались (или его попросту нет?)';
                alert.style.background = 'var(--tg-theme-secondary-bg-color)';
                alert.style.border = 'none';
                alert.style.borderRadius = 'var(--border-radius)';
                alert.style.padding = '6px 10px';
                alert.style.margin = '0 0 8px 0';
                alert.style.fontWeight = '400';
                alert.style.fontSize = '13px';
                alert.style.textAlign = 'center';
                alert.style.width = '100%';
                alert.style.color = 'var(--tg-theme-destructive-text-color)';
                const menuContainer = document.querySelector('.menu-container');
                if (menuContainer && menuContainer.parentNode) {
                    menuContainer.parentNode.insertBefore(alert, menuContainer);
                }
            }

            // Flex-контейнер для двух расписаний
            const tablesRow = document.createElement('div');
            tablesRow.style.display = 'flex';
            tablesRow.style.gap = '16px';
            tablesRow.style.justifyContent = 'center';

            // Будний день
            const weekdayTable = document.createElement('div');
            weekdayTable.className = 'row-container';
            weekdayTable.style.marginTop = '5px';
            const weekdayTitle = document.createElement('div');
            weekdayTitle.className = 'schedule-divider';
            weekdayTitle.textContent = 'Будний день';
            weekdayTable.appendChild(weekdayTitle);
            WEEKDAY_SCHEDULE.slice(0, 4).forEach((pair, idx) => {
                const row = document.createElement('div');
                row.className = 'row';
                row.innerHTML = `<div>${pair.start}</div><div class=\"lesson-name\">-</div><div>${pair.end}</div>`;
                weekdayTable.appendChild(row);
            });
            tablesRow.appendChild(weekdayTable);

            // Суббота
            const saturdayTable = document.createElement('div');
            saturdayTable.className = 'row-container';
            saturdayTable.style.marginTop = '5px';
            const saturdayTitle = document.createElement('div');
            saturdayTitle.className = 'schedule-divider';
            saturdayTitle.textContent = 'Суббота';
            saturdayTable.appendChild(saturdayTitle);
            SATURDAY_SCHEDULE.slice(0, 4).forEach((pair, idx) => {
                const row = document.createElement('div');
                row.className = 'row';
                row.innerHTML = `<div>${pair.start}</div><div class=\"lesson-name\">-</div><div>${pair.end}</div>`;
                saturdayTable.appendChild(row);
            });
            tablesRow.appendChild(saturdayTable);

            bellList.appendChild(tablesRow);
            return;
        } else {
            // Показываем schedule-title обратно, если оно было скрыто
            const title = document.querySelector('.schedule-title');
            if (title) title.style.display = '';
            // Показываем schedule-reactions обратно, если оно было скрыто
            const reactions = document.getElementById('schedule-reactions');
            if (reactions) reactions.style.display = '';
            // Показываем groupSelectorContainer обратно, если оно было скрыто
            const groupSelectorContainer = document.getElementById('groupSelectorContainer');
            if (groupSelectorContainer) groupSelectorContainer.style.display = '';
            // Удаляем алерт, если он был добавлен
            const alert = document.getElementById('schedule-error-alert');
            if (alert && alert.parentNode) alert.parentNode.removeChild(alert);
        }

        // Используем только window.scheduleState.currentScheduleData
        const currentLessons = window.scheduleState.currentScheduleData;

        // Если currentEntity не определён, но есть поле group — считаем, что это группа
        if (!window.scheduleState.currentEntity && currentLessons && currentLessons.group) {
            window.scheduleState.currentEntity = {
                entity_type: 'group',
                entity_id: currentLessons.group
            };
            console.log('[download] Автоматически установлен currentEntity как group:', window.scheduleState.currentEntity);
        }
        if (!window.scheduleState.currentEntity && currentLessons && currentLessons.teacher) {
            window.scheduleState.currentEntity = {
                entity_type: 'teacher',
                entity_id: currentLessons.teacher
            };
            console.log('[download] Автоматически установлен currentEntity как teacher:', window.scheduleState.currentEntity);
        }

        console.log('[schedule] updateScheduleDisplay: currentLessons:', currentLessons);

        if (currentLessons && Array.isArray(currentLessons.schedule)) {
            // Исправление: разбиваем строки с несколькими парами на отдельные элементы
            let normalizedSchedule = [];
            currentLessons.schedule.forEach(item => {
                if (typeof item === 'string' && item.includes('\n')) {
                    normalizedSchedule.push(...item.split('\n').map(s => s.trim()).filter(Boolean));
                } else {
                    normalizedSchedule.push(item);
                }
            });
            currentLessons.schedule = normalizedSchedule;
        }

        if (currentLessons) {
            const bellList = document.querySelector('#bell_list .row-container');
            bellList.style.opacity = '0';

            let newContent = document.createElement('div');
            newContent.className = 'row-container';

            const mainSchedule = [];
            const combinedSchedule = [];

            currentLessons.schedule.forEach((lesson, idx) => {
                console.log(`[schedule] forEach lesson[${idx}]:`, lesson);
                if (!lesson) return;

                const match = lesson.match(/^▪️(\d+) пара – (.+)$/);
                if (!match) {
                    console.log(`[schedule] lesson[${idx}] не совпал с шаблоном`);
                    return;
                }

                const [_, pairNumber, lessonDetails] = match;

                if (lessonDetails.trim() === 'Нет') {
                    // Для преподавателя явно добавляем строку "Нет" и прочерки
                    const emptyItem = {
                        pair_number: parseInt(pairNumber),
                        subject_name: '',
                        group_name: '',
                        teacher_name: '',
                        classroom: '',
                        is_combined: false,
                        is_empty: true
                    };
                    mainSchedule.push(emptyItem);
                    console.log(`[schedule] lesson[${idx}] пустая пара (Нет):`, emptyItem);
                    return;
                }

                const parts = lessonDetails.split(' – ');

                let subjectName, groupName, teacherName, classroom;

                if (parts.length === 2) {
                    [subjectName, teacherName] = parts;
                } else if (parts.length === 3) {
                    [subjectName, teacherName, classroom] = parts;
                    groupName = currentLessons.group || '';
                } else if (parts.length === 4) {
                    [subjectName, teacherName, classroom] = parts;
                    groupName = currentLessons.group || '';
                }

                const scheduleItem = {
                    pair_number: parseInt(pairNumber),
                    subject_name: subjectName ? subjectName.trim() : '',
                    group_name: groupName ? groupName.trim() : '',
                    teacher_name: teacherName ? teacherName.trim() : '',
                    classroom: classroom ? classroom.trim() : '',
                    is_combined: lesson.includes('(совмещ.)'),
                    is_empty: false
                };

                if (scheduleItem.is_combined) {
                    combinedSchedule.push(scheduleItem);
                    console.log(`[schedule] lesson[${idx}] добавлен в combinedSchedule:`, scheduleItem);
                } else {
                    mainSchedule.push(scheduleItem);
                    console.log(`[schedule] lesson[${idx}] добавлен в mainSchedule:`, scheduleItem);
                }
            });

            mainSchedule.sort((a, b) => a.pair_number - b.pair_number);
            combinedSchedule.sort((a, b) => a.pair_number - b.pair_number);

            console.log('[schedule] mainSchedule:', mainSchedule);
            console.log('[schedule] combinedSchedule:', combinedSchedule);
            console.log('[schedule] Перед displayScheduleItems mainSchedule:', mainSchedule);

            displayScheduleItems(mainSchedule, newContent, currentDate);

            if (combinedSchedule.length > 0) {
                const divider = document.createElement('div');
                divider.className = 'schedule-divider';
                divider.textContent = 'Совмещенные пары';
                newContent.appendChild(divider);

                displayScheduleItems(combinedSchedule, newContent, currentDate);
            }

            requestAnimationFrame(() => {
                bellList.innerHTML = '';
                bellList.appendChild(newContent);
                requestAnimationFrame(() => {
                    bellList.style.opacity = '1';
                });
            });

            // Вставка картинки только если newContent определён
            if (window.addCornerImageToRows && newContent) {
                window.addCornerImageToRows(newContent);
            }
        } else {
            const bellList = document.querySelector('#bell_list .row-container');
            bellList.innerHTML = schedule.slice(0, 4).map((pair, index) => `
                <div class="row ${index < 3 ? 'notlast' : ''}">
                    <div>${pair.start}</div>
                    <div class="lesson-name">-</div>
                    <div>${pair.end}</div>
                </div>
            `).join('');
        }
        
        // Обновляем глобальное состояние с текущей датой
        window.scheduleState.displayedDate = new Date(currentDate);
        
        // === Формируем ссылку для кнопки "Скачать" ===
        const downloadBtn = document.getElementById('downloadScheduleBtn');
        console.log('[download] currentEntity:', window.scheduleState.currentEntity);
        let downloadUrl = '';
        if (downloadBtn) {
            let scheduleType = '';
            if (window.scheduleState.currentEntity) {
                const entityType = window.scheduleState.currentEntity.entity_type;
                if (entityType === 'group') {
                    scheduleType = 'groups';
                } else if (entityType === 'teacher') {
                    scheduleType = 'teachers';
                }
            }
            // Форматируем дату как ДД.ММ.ГГГГ
            const pad = n => n.toString().padStart(2, '0');
            const dateObj = window.scheduleState.displayedDate || new Date();
            const dateStr = pad(dateObj.getDate()) + '.' + pad(dateObj.getMonth() + 1) + '.' + dateObj.getFullYear();
            if (scheduleType) {
                downloadUrl = `https://kkepik.ru/api/schedule/download/${scheduleType}/${dateStr}`;
                downloadBtn.setAttribute('data-url', downloadUrl);
                downloadBtn.style.display = '';
                console.log('[download] ссылка сформирована:', downloadUrl);
            } else {
                downloadBtn.removeAttribute('data-url');
                downloadBtn.style.display = 'none';
                downloadUrl = '';
                console.log('[download] ссылка скрыта: нет типа расписания');
            }
        }
        // === Конец формирования ссылки ===

        const event = new CustomEvent('scheduleDateChanged', {
            detail: {
                date: currentDate.toISOString().split('T')[0]
            }
        });
        document.dispatchEvent(event);
        console.log('[updateScheduleDisplay] END, currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
    };

    const scheduleArea = document.querySelector('#pairs_block');
    let touchStartX = 0;
    let touchStartY = 0;
    let isSwiping = false;

    scheduleArea.addEventListener('touchstart', e => {
        touchStartX = e.changedTouches[0].screenX;
        touchStartY = e.changedTouches[0].screenY;
        isSwiping = true;

        e.preventDefault();
    }, { passive: false });

    scheduleArea.addEventListener('touchmove', e => {
        if (!isSwiping) return;

        const currentX = e.changedTouches[0].screenX;
        const currentY = e.changedTouches[0].screenY;
        const swipeDistance = currentX - touchStartX;
        const verticalDistance = Math.abs(currentY - touchStartY);

        if (verticalDistance > Math.abs(swipeDistance)) {
            isSwiping = false;
            return;
        }

        if (swipeDistance > 0 && !hasPrevDay) {
            tg.HapticFeedback.notificationOccurred('warning');
            isSwiping = false;
        } else if (swipeDistance < 0 && !hasNextDay) {
            tg.HapticFeedback.notificationOccurred('warning');
            isSwiping = false;
        }

        e.preventDefault();
    }, { passive: false });

    scheduleArea.addEventListener('touchend', async e => {
        console.log('[touchend] START, currentDate:', currentDate);
        if (!isSwiping) return;
        touchEndX = e.changedTouches[0].screenX;
        const swipeDistance = touchEndX - touchStartX;
        if (Math.abs(swipeDistance) > 50) {
            if (swipeDistance > 0) {
                let prevDate = new Date(currentDate);
                do {
                    prevDate.setDate(prevDate.getDate() - 1);
                } while (prevDate.getDay() === 0 || isHoliday(prevDate));
                if (prevDate.getDay() !== 0 && !isHoliday(prevDate)) {
                    const prevSchedule = await loadScheduleForDate(prevDate);
                    if (prevSchedule && Array.isArray(prevSchedule.schedule)) {
                        currentDate = prevDate;
                        window.scheduleState.currentScheduleData = prevSchedule;
                        console.log('[touchend] swipe LEFT, currentDate:', currentDate);
                        tg.HapticFeedback.impactOccurred('light');
                        updateScheduleDisplay();
                        checkAdjacentDays();
                    }
                }
            } else if (swipeDistance < 0) {
                let nextDate = new Date(currentDate);
                do {
                    nextDate.setDate(nextDate.getDate() + 1);
                } while (nextDate.getDay() === 0 || isHoliday(nextDate));
                if (nextDate.getDay() !== 0 && !isHoliday(nextDate)) {
                    const nextSchedule = await loadScheduleForDate(nextDate);
                    if (nextSchedule && Array.isArray(nextSchedule.schedule)) {
                        currentDate = nextDate;
                        window.scheduleState.currentScheduleData = nextSchedule;
                        console.log('[touchend] swipe RIGHT, currentDate:', currentDate);
                        tg.HapticFeedback.impactOccurred('light');
                        updateScheduleDisplay();
                        checkAdjacentDays();
                    }
                }
            }
        }
        isSwiping = false;
        e.preventDefault();
        console.log('[touchend] END, currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
    }, { passive: false });

    scheduleArea.addEventListener('touchmove', e => {
        e.preventDefault();
    }, { passive: false });

    const title = document.querySelector('.schedule-title');

    title.addEventListener('click', async (e) => {
        console.log('[title.click] START, currentDate:', currentDate);
        const rect = title.getBoundingClientRect();
        const x = e.clientX - rect.left;
        if (x < 30) {
            let prevDate = new Date(currentDate);
            do {
                prevDate.setDate(prevDate.getDate() - 1);
            } while (prevDate.getDay() === 0 || isHoliday(prevDate));
            if (prevDate.getDay() !== 0 && !isHoliday(prevDate)) {
                const prevSchedule = await loadScheduleForDate(prevDate);
                if (prevSchedule && Array.isArray(prevSchedule.schedule)) {
                    currentDate = prevDate;
                    window.scheduleState.currentScheduleData = prevSchedule;
                    console.log('[title.click] prev, currentDate:', currentDate);
                    if (window.tg) tg.HapticFeedback.impactOccurred('light');
                    updateScheduleDisplay();
                    checkAdjacentDays();
                }
            }
        } else if (x > rect.width - 30) {
            let nextDate = new Date(currentDate);
            do {
                nextDate.setDate(nextDate.getDate() + 1);
            } while (nextDate.getDay() === 0 || isHoliday(nextDate));
            if (nextDate.getDay() !== 0 && !isHoliday(nextDate)) {
                const nextSchedule = await loadScheduleForDate(nextDate);
                if (nextSchedule && Array.isArray(nextSchedule.schedule)) {
                    currentDate = nextDate;
                    window.scheduleState.currentScheduleData = nextSchedule;
                    console.log('[title.click] next, currentDate:', currentDate);
                    if (window.tg) tg.HapticFeedback.impactOccurred('light');
                    updateScheduleDisplay();
                    checkAdjacentDays();
                }
            }
        }
        console.log('[title.click] END, currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
    });

    document.addEventListener('keydown', async (e) => {
        console.log('[keydown] START, currentDate:', currentDate);
        if (e.key === 'ArrowLeft') {
            let prevDate = new Date(currentDate);
            do {
                prevDate.setDate(prevDate.getDate() - 1);
            } while (prevDate.getDay() === 0 || isHoliday(prevDate));
            if (prevDate.getDay() !== 0 && !isHoliday(prevDate)) {
                const prevSchedule = await loadScheduleForDate(prevDate);
                if (prevSchedule && Array.isArray(prevSchedule.schedule)) {
                    currentDate = prevDate;
                    window.scheduleState.currentScheduleData = prevSchedule;
                    console.log('[keydown] ArrowLeft, currentDate:', currentDate);
                    if (window.tg) tg.HapticFeedback.impactOccurred('light');
                    updateScheduleDisplay();
                    checkAdjacentDays();
                }
            }
        } else if (e.key === 'ArrowRight') {
            let nextDate = new Date(currentDate);
            do {
                nextDate.setDate(nextDate.getDate() + 1);
            } while (nextDate.getDay() === 0 || isHoliday(nextDate));
            if (nextDate.getDay() !== 0 && !isHoliday(nextDate)) {
                const nextSchedule = await loadScheduleForDate(nextDate);
                if (nextSchedule && Array.isArray(nextSchedule.schedule)) {
                    currentDate = nextDate;
                    window.scheduleState.currentScheduleData = nextSchedule;
                    console.log('[keydown] ArrowRight, currentDate:', currentDate);
                    if (window.tg) tg.HapticFeedback.impactOccurred('light');
                    updateScheduleDisplay();
                    checkAdjacentDays();
                }
            }
        }
        console.log('[keydown] END, currentDate:', currentDate, 'displayedDate:', displayedDate, 'window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
    });

    initialLoad();
});

function displaySchedule(schedule) {
    const scheduleContainer = document.getElementById('schedule');
    scheduleContainer.innerHTML = '';

    const mainSchedule = [];
    const combinedSchedule = [];

    schedule.forEach(item => {
        if (item.is_combined) {
            combinedSchedule.push(item);
        } else {
            mainSchedule.push(item);
        }
    });

    mainSchedule.sort((a, b) => a.pair_number - b.pair_number);
    combinedSchedule.sort((a, b) => a.pair_number - b.pair_number);

    displayScheduleItems(mainSchedule, scheduleContainer, currentDate);

    if (combinedSchedule.length > 0) {
        const divider = document.createElement('div');
        divider.className = 'schedule-divider';
        divider.textContent = 'Совмещенные пары';
        scheduleContainer.appendChild(divider);

        displayScheduleItems(combinedSchedule, scheduleContainer, currentDate);
    }
}

function displayScheduleItems(items, container, date) {
    const isSaturday = date.getDay() === 6;
    const schedule = isSaturday ? SATURDAY_SCHEDULE : WEEKDAY_SCHEDULE;
    const isTeacher = window.scheduleState.currentEntity && window.scheduleState.currentEntity.entity_type === 'teacher';

    console.log('[schedule] displayScheduleItems: до обрезки items:', items);
    // Обрезаем последние подряд идущие пустые пары для преподавателя (если не все пары пустые)
    if (isTeacher && items && items.length > 0 && !items.every(item => item.is_empty)) {
        let lastRealIdx = -1;
        for (let i = items.length - 1; i >= 0; i--) {
            if (!items[i].is_empty) {
                lastRealIdx = i;
                break;
            }
        }
        if (lastRealIdx !== -1) {
            items = items.slice(0, lastRealIdx + 1);
        }
    }
    console.log('[schedule] displayScheduleItems: после обрезки items:', items);

    // Если преподаватель и все пары пустые (is_empty), рендерим "Нет" для всех пар
    if (isTeacher && (!items || items.length === 0 || items.every(item => item.is_empty))) {
        console.log('[schedule] displayScheduleItems: рендерим строки с Нет для всех пар');
        for (let i = 0; i < schedule.length; i++) {
            const pair = schedule[i];
            const row = document.createElement('div');
            row.className = 'row';
            row.innerHTML = `
                <div>${pair.start}</div>
                <div class=\"lesson-name\">\n                    <div class=\"group\">Нет</div>\n                    <div class=\"teacher\">—</div>\n                </div>\n                <div>${pair.end}</div>\n            `;
            container.appendChild(row);
        }
        return;
    }

    // Создаём мапу: номер пары -> массив items
    const itemsByPair = {};
    let minPair = null;
    let maxPair = null;
    let maxRealPair = null; // максимальный номер реально существующей пары (is_empty === false)
    items.forEach(item => {
        if (!itemsByPair[item.pair_number]) {
            itemsByPair[item.pair_number] = [];
        }
        itemsByPair[item.pair_number].push(item);
        if (minPair === null || item.pair_number < minPair) minPair = item.pair_number;
        if (maxPair === null || item.pair_number > maxPair) maxPair = item.pair_number;
        if (!item.is_empty && (maxRealPair === null || item.pair_number > maxRealPair)) maxRealPair = item.pair_number;
    });

    if (minPair === null || maxPair === null) return; // Нет пар — ничего не выводим

    // Если есть хотя бы одна не пустая пара, рендерим только до неё (последние пустые не показываем)
    let renderToPair = maxPair;
    if (isTeacher && maxRealPair !== null) {
        renderToPair = maxRealPair;
    }

    // Прочерки до первой существующей пары
    for (let i = 1; i < minPair; i++) {
        const scheduleTime = schedule[i - 1];
        if (!scheduleTime) continue;
        const row = document.createElement('div');
        row.className = 'row';
        row.innerHTML = `
            <div>${scheduleTime.start}</div>
            <div class="lesson-name">
                <div class="group">Нет</div>
                <div class="teacher">— · —</div>
            </div>
            <div>${scheduleTime.end}</div>
        `;
        container.appendChild(row);
    }

    // Пары и прочерки между ними (от minPair до renderToPair)
    for (let i = minPair; i <= renderToPair; i++) {
        const scheduleTime = schedule[i - 1];
        if (!scheduleTime) continue;
        const itemsForPair = itemsByPair[i];
        if (itemsForPair && itemsForPair.length > 0) {
            itemsForPair.forEach(item => {
                const row = document.createElement('div');
                row.className = 'row';
                if (item.is_empty) {
                    row.innerHTML = `
                        <div>${scheduleTime.start}</div>
                        <div class="lesson-name">
                            <div class="group">Нет</div>
                            <div class="teacher">—</div>
                        </div>
                        <div>${scheduleTime.end}</div>
                    `;
                    container.appendChild(row);
                } else {
                    if (item.is_combined) {
                        row.classList.add('combined');
                    }
                    const shortenedName = shortenSubjectName(item.subject_name);
                    let teacherSurname = '';
                    let classroom = '';
                    if (item.teacher_name) {
                        teacherSurname = item.teacher_name.split(' ')[0];
                    }
                    if (item.classroom) {
                        classroom = item.classroom;
                    }
                    let teacherAndClass = '';
                    if (teacherSurname && classroom) {
                        teacherAndClass = `${teacherSurname} · ${classroom}`;
                    } else if (teacherSurname) {
                        teacherAndClass = teacherSurname;
                    } else if (classroom) {
                        teacherAndClass = classroom;
                    }
                    let groupInfo = '';
                    if (isTeacher) {
                        groupInfo = `<div class="group">${item.group_name}</div>`;
                    }
                    row.innerHTML = `
                        <div class="row-time">${scheduleTime.start}</div>
                        <div class="lesson-name" title="${item.subject_name}">
                            ${groupInfo}
                            <div class="group">${shortenedName}</div>
                            <div class="teacher">${teacherAndClass}</div>
                        </div>
                        <div>${scheduleTime.end}</div>
                    `;

                    // Модальное окно с деталями (всплывающее)
                    const fullSubject = item.subject_name || '-';
                    const fullTeacher = item.teacher_name || '-';
                    const fullRoom = item.classroom || '-';
                    const timeRange = `${scheduleTime.start} – ${scheduleTime.end}`;

                    const entity = (window.scheduleState && window.scheduleState.currentEntity) ? window.scheduleState.currentEntity : null;
                    const entityKey = entity ? `${entity.entity_type}:${entity.entity_id}` : 'user';
                    // Используем одинаковый формат ключа для сохранения и загрузки
                    const hoursKey = `${fullSubject}::${fullTeacher}::${item.group_name || ''}`;

                                         // API функции для работы с часами
                     async function saveHoursToAPI(subject_name, teacher_name, group_name, planned_hours, completed_hours) {
                         try {
                             const response = await fetch('/api/subject-hours', {
                                 method: 'POST',
                                 headers: {
                                     'Content-Type': 'application/json',
                                 },
                                 body: JSON.stringify({
                                     tgWebAppData: window.Telegram?.WebApp?.initData || '',
                                     subject_name: subject_name,
                                     teacher_name: teacher_name,
                                     group_name: group_name,
                                     planned_hours: parseFloat(planned_hours) || 0,
                                     completed_hours: parseFloat(completed_hours) || 0
                                 })
                             });
 
                             if (!response.ok) {
                                 throw new Error(`HTTP error! status: ${response.status}`);
                             }
 
                             const result = await response.json();
                             if (!result.success) {
                                 throw new Error(result.error || 'Ошибка сохранения');
                             }
 
                             return result;
                         } catch (error) {
                             console.error('Ошибка сохранения часов:', error);
                             // Fallback на localStorage
                             try {
                                 const map = JSON.parse(localStorage.getItem('subject_hours') || '{}');
                                 const key = `${subject_name}::${teacher_name}::${group_name}`;
                                 map[key] = { plan: planned_hours.toString(), done: completed_hours.toString() };
                                 localStorage.setItem('subject_hours', JSON.stringify(map));
                             } catch (e) {
                                 console.error('Ошибка сохранения в localStorage:', e);
                             }
                             throw error;
                         }
                     }
 
                     async function loadHoursFromAPI() {
                         try {
                             const response = await fetch(`/api/subject-hours?tgWebAppData=${encodeURIComponent(window.Telegram?.WebApp?.initData || '')}`);
 
                             if (!response.ok) {
                                 throw new Error(`HTTP error! status: ${response.status}`);
                             }
 
                             const result = await response.json();
                             if (!result.success) {
                                 throw new Error(result.error || 'Ошибка загрузки');
                             }
 
                             // Преобразуем массив в объект для совместимости
                             const hoursMap = {};
                             result.hours.forEach(hour => {
                                 const key = `${hour.subject_name}::${hour.teacher_name}::${hour.group_name}`;
                                 hoursMap[key] = {
                                     plan: hour.planned_hours.toString(),
                                     done: hour.completed_hours.toString()
                                 };
                             });
 
                             return hoursMap;
                         } catch (error) {
                             console.error('Ошибка загрузки часов:', error);
                             // Fallback на localStorage
                             try {
                                 const raw = localStorage.getItem('subject_hours');
                                 return raw ? JSON.parse(raw) : {};
                             } catch (e) {
                                 return {};
                             }
                         }
                     }

                     function loadHours() {
                         try { const raw = localStorage.getItem('subject_hours'); return raw ? JSON.parse(raw) : {}; } catch (e) { return {}; }
                     }
                     function saveHours(map) {
                         try { localStorage.setItem('subject_hours', JSON.stringify(map)); } catch (e) {}
                     }
                     function getState() {
                         const map = loadHours();
                         // Используем тот же ключ, что и для API
                         const key = `${fullSubject}::${fullTeacher}::${item.group_name || ''}`;
                         return map[key] || { plan: '0', done: '0' };
                     }
                     async function setState(next) {
                         // Пробуем сохранить через API
                         try {
                             await saveHoursToAPI(
                                 fullSubject,
                                 fullTeacher,
                                 item.group_name || '',
                                 parseFloat(next.plan) || 0,
                                 parseFloat(next.done) || 0
                             );
                         } catch (error) {
                             console.error('Ошибка сохранения через API, используем localStorage:', error);
                             // Fallback на localStorage уже обрабатывается в saveHoursToAPI
                         }
                     }

                    function createTooltipButton(text) {
                        const btn = document.createElement('button');
                        btn.type = 'button';
                        btn.textContent = '？';
                        btn.style.cssText = 'margin-left:6px;width:22px;height:22px;border-radius:50%;border:1px solid var(--tg-theme-hint-color);background:transparent;color:var(--tg-theme-text-color);font-size:12px;line-height:1;display:flex;align-items:center;justify-content:center;';
                        const tip = document.createElement('div');
                        tip.textContent = text;
                        tip.style.cssText = 'position:absolute;bottom:125%;left:50%;transform:translateX(-50%);background:var(--tg-theme-secondary-bg-color);color:var(--tg-theme-text-color);border:1px solid var(--tg-theme-hint-color);padding:8px 10px;border-radius:10px;white-space:normal;min-width:160px;max-width:240px;font-size:12px;box-shadow:0 4px 12px rgba(0,0,0,.15);display:none;z-index:10001;';
                        const wrap = document.createElement('span');
                        wrap.style.cssText = 'position:relative;display:inline-flex;align-items:center;';
                        wrap.appendChild(btn);
                        wrap.appendChild(tip);
                        btn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            tip.style.display = (tip.style.display === 'none' || !tip.style.display) ? 'block' : 'none';
                            setTimeout(() => { tip.style.display = 'none'; }, 2500);
                        });
                        return wrap;
                    }

                    async function showLessonModal() {
                        // Загружаем актуальные данные из API
                        let state = { plan: '0', done: '0' };
                        try {
                            const hoursMap = await loadHoursFromAPI();
                            console.log('Загруженные данные из API:', hoursMap);
                            console.log('Ищем по ключу:', hoursKey);
                            state = hoursMap[hoursKey] || { plan: '0', done: '0' };
                            console.log('Найденное состояние:', state);
                        } catch (error) {
                            console.error('Ошибка загрузки данных, используем локальные:', error);
                            state = getState();
                        }
                        
                        const planVal = state.plan;
                        const doneVal = state.done;
                        const remaining = (Number(planVal) || 0) - (Number(doneVal) || 0);

                        // Создаём модальное окно с новыми классами
                        const modal = document.createElement('div');
                        modal.className = 'lesson-details-modal';

                        const content = document.createElement('div');
                        content.className = 'lesson-details-content';

                        // Кнопка закрытия
                        const closeBtn = document.createElement('button');
                        closeBtn.className = 'lesson-details-close';

                        // Заголовок
                        const header = document.createElement('div');
                        header.className = 'lesson-details-header';

                        const title = document.createElement('div');
                        title.className = 'lesson-details-title';
                        title.textContent = fullSubject;

                        const subtitle = document.createElement('div');
                        subtitle.className = 'lesson-details-subtitle';
                        subtitle.textContent = 'Подробная информация о паре';

                        header.appendChild(title);
                        header.appendChild(subtitle);

                        // Информационная секция
                        const infoSection = document.createElement('div');
                        infoSection.className = 'lesson-details-info';

                        const createInfoRow = (label, value) => {
                            const row = document.createElement('div');
                            row.className = 'lesson-info-row';
                            
                            const labelEl = document.createElement('span');
                            labelEl.className = 'lesson-info-label';
                            labelEl.textContent = label;
                            
                            const valueEl = document.createElement('span');
                            valueEl.className = 'lesson-info-value';
                            valueEl.textContent = value;
                            
                            row.appendChild(labelEl);
                            row.appendChild(valueEl);
                            return row;
                        };

                        infoSection.appendChild(createInfoRow('Преподаватель', fullTeacher));
                        infoSection.appendChild(createInfoRow('Кабинет', fullRoom));
                        infoSection.appendChild(createInfoRow('Время', timeRange));

                        // Блок учёта часов
                        const hoursSection = document.createElement('div');
                        hoursSection.className = 'lesson-hours-section';

                        const hoursHeader = document.createElement('div');
                        hoursHeader.className = 'lesson-hours-header';

                        const hoursTitle = document.createElement('div');
                        hoursTitle.className = 'lesson-hours-title';
                        hoursTitle.textContent = 'Учёт часов';

                        hoursHeader.appendChild(hoursTitle);

                        const hoursInputs = document.createElement('div');
                        hoursInputs.className = 'lesson-hours-inputs';

                        const createHoursRow = (label, inputId) => {
                            const row = document.createElement('div');
                            row.className = 'lesson-hours-row';

                            const labelEl = document.createElement('span');
                            labelEl.className = 'lesson-hours-label';
                            labelEl.textContent = label;

                            const input = document.createElement('input');
                            input.type = 'number';
                            input.min = '0';
                            input.max = '300';
                            input.step = '0.5';
                            input.id = inputId;
                            input.className = 'lesson-hours-input';
                            input.placeholder = '0';
                            input.value = '0';
                            
                            // Дополнительная валидация ввода
                            input.addEventListener('keydown', (e) => {
                                // Разрешаем: цифры, точка, backspace, delete, стрелки, tab
                                const allowedKeys = ['Backspace', 'Delete', 'Tab', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'];
                                const isNumber = /[0-9.]/.test(e.key);
                                const isAllowed = allowedKeys.includes(e.key);
                                
                                if (!isNumber && !isAllowed) {
                                    e.preventDefault();
                                    return;
                                }
                                
                                // Запрещаем ввод если уже есть точка
                                if (e.key === '.' && input.value.includes('.')) {
                                    e.preventDefault();
                                    return;
                                }
                            });
                            
                            // Очищаем значение "0" при фокусе
                            input.addEventListener('focus', () => {
                                input.classList.add('placeholder-hidden');
                                input.setAttribute('data-focused', 'true');
                                // Очищаем поле если там только "0"
                                if (input.value === '0') {
                                    input.value = '';
                                }
                            });
                            
                            // Скрываем плейсхолдер при вводе и валидируем значение
                            input.addEventListener('input', () => {
                                let value = input.value;
                                
                                // Убираем лишние нули в начале
                                if (value.startsWith('0') && value.length > 1 && !value.startsWith('0.')) {
                                    value = value.replace(/^0+/, '0');
                                    if (value === '0') value = '';
                                }
                                
                                // Запрещаем ввод больше 300
                                if (parseFloat(value) > 300) {
                                    value = '300';
                                }
                                
                                // Запрещаем отрицательные числа
                                if (parseFloat(value) < 0) {
                                    value = '0';
                                }
                                
                                // Обновляем значение поля
                                input.value = value;
                                
                                // Скрываем плейсхолдер если есть значение
                                if (value && value !== '0') {
                                    input.classList.add('placeholder-hidden');
                                } else {
                                    input.classList.remove('placeholder-hidden');
                                }
                            });
                            
                            // Восстанавливаем значение "0" при потере фокуса если поле пустое
                            input.addEventListener('blur', () => {
                                if (!input.value || input.value === '') {
                                    input.value = '0';
                                    input.classList.remove('placeholder-hidden');
                                }
                            });
                            
                            row.appendChild(labelEl);
                            row.appendChild(input);
                            return row;
                        };

                        const planRow = createHoursRow(
                            'План:',
                            'lessonPlan'
                        );
                        const completedRow = createHoursRow(
                            'Пройдено:',
                            'lessonCompleted'
                        );

                        // Строка "Осталось"
                        const remainingBlock = document.createElement('div');
                        remainingBlock.className = 'lesson-hours-remaining';
                        const remainingText = document.createElement('span');
                        remainingText.className = 'lesson-hours-remaining-text';
                        remainingBlock.appendChild(remainingText);

                        hoursInputs.appendChild(planRow);
                        hoursInputs.appendChild(completedRow);
                        hoursInputs.appendChild(remainingBlock);

                        // Общая кнопка помощи для блока часов
                        const generalHelpBtn = document.createElement('button');
                        generalHelpBtn.className = 'lesson-hours-help-btn';

                        // Тултип с объединенными подсказками
                        generalHelpBtn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            
                            // Удаляем существующий тултип если есть
                            const existingTooltip = document.querySelector('.lesson-tooltip');
                            if (existingTooltip) {
                                existingTooltip.remove();
                            }

                                                            const tooltip = document.createElement('div');
                                tooltip.className = 'lesson-tooltip show';
                                tooltip.innerHTML = `
                                <div style="margin-bottom: 8px;"><b>План:</b> Общее количество часов по программе для этого предмета</div>
                                <div><b>Пройдено:</b> Количество уже проведённых часов по этому предмету</div>
                            `;

                            const rect = generalHelpBtn.getBoundingClientRect();
                            tooltip.style.left = Math.max(10, rect.left + rect.width / 2 - 110) + 'px';
                            tooltip.style.top = (rect.top + rect.height + 12) + 'px';

                            document.body.appendChild(tooltip);
                            
                            // Автоматическое скрытие через 4 секунды
                            setTimeout(() => {
                                if (tooltip.parentNode) {
                                    tooltip.classList.remove('show');
                                    setTimeout(() => tooltip.remove(), 200);
                                }
                            }, 4000);
                        });


                        
                        // Кнопка автоматического списания
                        const autoDeductSection = document.createElement('div');
                        autoDeductSection.style.cssText = 'margin-top: 0;';
                        
                        const autoDeductBtn = document.createElement('button');
                        autoDeductBtn.textContent = '📚 Списать за проведенную пару';
                        autoDeductBtn.style.cssText = `
                            width: 100%;
                            padding: 12px 16px;
                            background: var(--primary-color);
                            color: white;
                            border: none;
                            border-radius: 12px;
                            font-size: 14px;
                            font-weight: 500;
                            cursor: pointer;
                            transition: all 0.2s ease;
                        `;
                        
                        autoDeductBtn.addEventListener('click', async (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            
                            try {
                                autoDeductBtn.disabled = true;
                                autoDeductBtn.textContent = '⏳ Списание...';
                                
                                const response = await fetch('/api/subject-hours/auto-deduct', {
                                    method: 'POST',
                                    headers: {
                                        'Content-Type': 'application/json',
                                    },
                                    body: JSON.stringify({
                                        tgWebAppData: window.Telegram?.WebApp?.initData || '',
                                        subject_name: fullSubject,
                                        teacher_name: fullTeacher,
                                        group_name: item.group_name || '',
                                        lesson_duration: 2 // 1 пара = 2 часа
                                    })
                                });
                                
                                const result = await response.json();
                                
                                if (result.success) {
                                    // Обновляем поля ввода с новыми данными
                                    const planInput = document.getElementById('lessonPlan');
                                    const completedInput = document.getElementById('lessonCompleted');
                                    
                                    if (planInput && completedInput) {
                                        planInput.value = result.planned_hours;
                                        completedInput.value = result.completed_hours;
                                        
                                        // Обновляем отображение оставшихся часов
                                        const updateRemaining = window.updateRemainingFunction;
                                        if (updateRemaining) {
                                            updateRemaining();
                                        }
                                    }
                                    
                                    autoDeductBtn.textContent = '✅ Списано 2 часа';
                                    setTimeout(() => {
                                        autoDeductBtn.textContent = '📚 Списать за проведенную пару';
                                        autoDeductBtn.disabled = false;
                                    }, 2000);
                                    
                                    // Тактильная обратная связь
                                    if (window.Telegram?.WebApp?.HapticFeedback) {
                                        window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
                                    }
                                } else {
                                    throw new Error(result.error || 'Ошибка списания');
                                }
                            } catch (error) {
                                console.error('Ошибка автоматического списания:', error);
                                autoDeductBtn.textContent = '❌ Ошибка';
                                setTimeout(() => {
                                    autoDeductBtn.textContent = '📚 Списать за проведенную пару';
                                    autoDeductBtn.disabled = false;
                                }, 2000);
                                
                                // Тактильная обратная связь об ошибке
                                if (window.Telegram?.WebApp?.HapticFeedback) {
                                    window.Telegram.WebApp.HapticFeedback.notificationOccurred('error');
                                }
                            }
                        });
                        
                        autoDeductSection.appendChild(autoDeductBtn);

                        hoursSection.appendChild(generalHelpBtn);
                        hoursSection.appendChild(hoursHeader);
                        hoursSection.appendChild(hoursInputs);
                        


                        // Собираем модальное окно
                        content.appendChild(closeBtn);
                        content.appendChild(header);
                        content.appendChild(infoSection);
                        content.appendChild(hoursSection);
                        content.appendChild(autoDeductSection);
                        modal.appendChild(content);

                        // Объявляем переменные для инпут полей в широкой области видимости
                        let planInput, completedInput;
                        
                        // Инициализация значений
                        setTimeout(() => {
                            planInput = document.getElementById('lessonPlan');
                            completedInput = document.getElementById('lessonCompleted');
                            
                            if (planInput && completedInput) {
                                planInput.value = planVal || '0';
                                completedInput.value = doneVal || '0';
                                
                                const updateRemaining = () => {
                                    const plan = parseFloat(planInput.value) || 0;
                                    const completed = parseFloat(completedInput.value) || 0;
                                    const remaining = Math.max(0, plan - completed);
                                    const pairs = Math.round(remaining / 2 * 10) / 10; // округление до 1 знака после запятой

                                    remainingText.textContent = `Осталось: ${remaining} ч ≈ ${pairs} пар`;
                                };
                                
                                // Сохраняем функцию обновления в глобальную переменную для использования кнопкой автоматического списания
                                window.updateRemainingFunction = updateRemaining;

                                planInput.addEventListener('input', (e) => {
                                    e.stopPropagation();
                                    updateRemaining();
                                    // Сохраняем изменения в API
                                    if (planInput && completedInput) {
                                        setState({
                                            plan: planInput.value || '0',
                                            done: completedInput.value || '0'
                                        });
                                    }
                                });
                                planInput.addEventListener('keydown', (e) => {
                                    if (e.key === 'Enter') {
                                        e.preventDefault();
                                        planInput.blur(); // Убираем фокус, что закроет клавиатуру
                                    }
                                });

                                completedInput.addEventListener('input', (e) => {
                                    e.stopPropagation();
                                    updateRemaining();
                                    // Сохраняем изменения в API
                                    if (planInput && completedInput) {
                                        setState({
                                            plan: planInput.value || '0',
                                            done: completedInput.value || '0'
                                        });
                                    }
                                });
                                completedInput.addEventListener('keydown', (e) => {
                                    if (e.key === 'Enter') {
                                        e.preventDefault();
                                        completedInput.blur(); // Убираем фокус, что закроет клавиатуру
                                    }
                                });
                                
                                updateRemaining();
                            }
                        }, 0);

                        // Закрытие модального окна с анимацией
                        function closeModal() {
                            // Сохраняем финальные значения перед закрытием, если элементы существуют
                            if (planInput && completedInput) {
                                setState({
                                    plan: planInput.value || '0',
                                    done: completedInput.value || '0'
                                });
                            }
                            
                            modal.style.animation = 'fadeOutModal 0.2s ease forwards';
                            content.style.animation = 'slideOutModalContent 0.2s ease forwards';

                            setTimeout(() => {
                                if (modal.parentNode) {
                                    document.body.removeChild(modal);
                                    // Возвращаем скролл страницы после закрытия модального окна
                                    document.body.style.overflow = '';
                                }
                            }, 200);
                        }

                        closeBtn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            closeModal();
                        });
                        
                        modal.addEventListener('click', (e) => {
                            if (e.target === modal) closeModal();
                        });

                        // Закрытие по Esc
                        const handleEscape = (e) => {
                            if (e.key === 'Escape') {
                                closeModal();
                                document.removeEventListener('keydown', handleEscape);
                            }
                        };
                        document.addEventListener('keydown', handleEscape);

                        // Запрещаем скролл страницы при открытии модального окна
                        document.body.style.overflow = 'hidden';
                        document.body.appendChild(modal);
                    }

                container.appendChild(row);

                // Обработчик для всех устройств (мышь + сенсор)
                const handleRowClick = async (e) => {
                    e.preventDefault();
                    await showLessonModal();
                };

                row.addEventListener('click', handleRowClick);

                // Для мобильных устройств - дополнительная поддержка touch
                if ('ontouchstart' in window) {
                    let rowTouchStartX = 0;
                    let rowTouchStartY = 0;
                    let rowTouchStartTime = 0;

                    row.addEventListener('touchstart', (e) => {
                        rowTouchStartX = e.changedTouches[0].clientX;
                        rowTouchStartY = e.changedTouches[0].clientY;
                        rowTouchStartTime = Date.now();
                    }, { passive: true });

                    row.addEventListener('touchend', (e) => {
                        // Убедимся, что это не было прокруткой
                        if (!e.changedTouches || e.changedTouches.length === 0) return;

                        const touch = e.changedTouches[0];
                        const rect = row.getBoundingClientRect();
                        const touchEndX = touch.clientX;
                        const touchEndY = touch.clientY;
                        const touchEndTime = Date.now();

                        // Проверяем, что это не свайп (расстояние больше 10px или время больше 300ms)
                        const distanceX = Math.abs(touchEndX - rowTouchStartX);
                        const distanceY = Math.abs(touchEndY - rowTouchStartY);
                        const touchDuration = touchEndTime - rowTouchStartTime;

                        // Если это свайп (большое расстояние или долгое время), не обрабатываем как клик
                        if (distanceX > 10 || distanceY > 10 || touchDuration > 300) {
                            return;
                        }

                        // Проверяем, что тач был внутри элемента
                        if (touchEndX >= rect.left && touchEndX <= rect.right &&
                            touchEndY >= rect.top && touchEndY <= rect.bottom) {
                            e.preventDefault();
                            handleRowClick(e);
                        }
                    }, { passive: false });
                }
                }
            });
        } else {
            // Прочерк между парами
            const row = document.createElement('div');
            row.className = 'row';
            row.innerHTML = `
                <div>${scheduleTime.start}</div>
                <div class="lesson-name">
                    <div class="group">-</div>
                    <div class="teacher">длинный прочерк · длинный прочерк</div>
                </div>
                <div>${scheduleTime.end}</div>
            `;

            // Обработчик для прочерков тоже нужен для консистентности
            const handleEmptyRowClick = (e) => {
                e.preventDefault();
                // Для прочерков можно показать информационное сообщение или ничего не делать
                // console.log('Клик по прочерку между парами');
            };

            row.addEventListener('click', handleEmptyRowClick);

            // Для мобильных устройств - дополнительная поддержка touch
            if ('ontouchstart' in window) {
                let emptyRowTouchStartX = 0;
                let emptyRowTouchStartY = 0;
                let emptyRowTouchStartTime = 0;

                row.addEventListener('touchstart', (e) => {
                    emptyRowTouchStartX = e.changedTouches[0].clientX;
                    emptyRowTouchStartY = e.changedTouches[0].clientY;
                    emptyRowTouchStartTime = Date.now();
                }, { passive: true });

                row.addEventListener('touchend', (e) => {
                    if (!e.changedTouches || e.changedTouches.length === 0) return;

                    const touch = e.changedTouches[0];
                    const rect = row.getBoundingClientRect();
                    const touchEndX = touch.clientX;
                    const touchEndY = touch.clientY;
                    const touchEndTime = Date.now();

                    // Проверяем, что это не свайп (расстояние больше 10px или время больше 300ms)
                    const distanceX = Math.abs(touchEndX - emptyRowTouchStartX);
                    const distanceY = Math.abs(touchEndY - emptyRowTouchStartY);
                    const touchDuration = touchEndTime - emptyRowTouchStartTime;

                    // Если это свайп (большое расстояние или долгое время), не обрабатываем как клик
                    if (distanceX > 10 || distanceY > 10 || touchDuration > 300) {
                        return;
                    }

                    if (touchEndX >= rect.left && touchEndX <= rect.right &&
                        touchEndY >= rect.top && touchEndY <= rect.bottom) {
                        e.preventDefault();
                        handleEmptyRowClick(e);
                    }
                }, { passive: false });
            }

            container.appendChild(row);
        }
    }

    // После добавления всех .row вызываем функцию для вставки картинки
    if (window.addCornerImageToRows) {
        window.addCornerImageToRows(container);
    }
}

// === Конфетти из границ страницы ===
function launchSideConfetti() {
    const colors = ['#2ecc71', '#3498db', '#e74c3c']; // зелёный, синий, красный (новые оттенки)
    // Левая сторона
    confetti({
        particleCount: 40,
        angle: 60,
        spread: 55,
        origin: { x: 0, y: 1 },
        colors: colors,
        gravity: 1.1,
        scalar: 1.1,
        ticks: 250
    });
    // Правая сторона
    confetti({
        particleCount: 40,
        angle: 120,
        spread: 55,
        origin: { x: 1, y: 1 },
        colors: colors,
        gravity: 1.1,
        scalar: 1.1,
        ticks: 250
    });
}

// Делаем функцию доступной глобально
window.launchSideConfetti = launchSideConfetti;

// === Оценка приложения ===
document.addEventListener('DOMContentLoaded', function() {
    const reviewCard = document.getElementById('reviewCard');
    const modal = document.getElementById('reviewModal');
    const closeBtn = document.getElementById('closeReviewModal');
    const sendBtn = document.getElementById('sendReviewBtn');
    const marks = document.getElementById('reviewMarks');
    const reviewText = document.getElementById('reviewText');
    const status = document.getElementById('reviewStatus');
    let selectedMark = null;

    // Скрыть блок, если отзыв уже был отправлен
    if (reviewCard && localStorage.getItem('review_submitted')) {
        reviewCard.style.display = 'none';
    }

    if (reviewCard && modal && closeBtn && sendBtn && marks && reviewText && status) {
        reviewCard.onclick = function(e) {
            e.preventDefault();
            if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
            modal.style.display = 'block';
            document.body.style.overflow = 'hidden';
            status.textContent = '';
            reviewText.value = '';
            selectedMark = null;
            marks.querySelectorAll('.mark-btn').forEach(btn => btn.classList.remove('selected'));
        };
        function closeReviewModal() {
            modal.style.display = 'none';
            document.body.style.overflow = 'auto';
        }
        closeBtn.onclick = function() {
            if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
            closeReviewModal();
        };
        // Закрытие модалки по клику на оверлей (вне .modal-content)
        modal.addEventListener('mousedown', function(event) {
            if (event.target === modal) {
                if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
                closeReviewModal();
            }
        });
        // Скрытие клавиатуры при клике вне textarea и вне модального окна
        document.addEventListener('mousedown', function(event) {
            if (document.activeElement === reviewText) {
                if (!reviewText.contains(event.target) && !modal.contains(event.target)) {
                    reviewText.blur();
                }
            }
        });
        marks.querySelectorAll('.mark-btn').forEach(btn => {
            btn.onclick = function() {
                if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('soft');
                marks.querySelectorAll('.mark-btn').forEach(b => b.classList.remove('selected'));
                btn.classList.add('selected');
                selectedMark = parseInt(btn.dataset.mark);
            };
        });
        sendBtn.onclick = async function() {
            if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
            if (!selectedMark) {
                status.textContent = 'Пожалуйста, выберите оценку.';
                if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('error');
                return;
            }
            if (!reviewText.value.trim()) {
                status.textContent = 'Пожалуйста, напишите отзыв.';
                if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('error');
                return;
            }
            sendBtn.disabled = true;
            status.textContent = 'Отправка...';
            try {
                const resp = await fetch('/api/review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mark: selectedMark, review: reviewText.value.trim() })
                });
                const data = await resp.json();
                if (data.success) {
                    status.textContent = 'Спасибо за ваш отзыв!';
                    status.style.color = 'green';
                    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
                    localStorage.setItem('review_submitted', '1');
                    setTimeout(() => {
                        closeReviewModal();
                        if (inviteBlock) inviteBlock.style.display = 'none';
                    }, 1200);
                } else {
                    status.textContent = data.error || 'Ошибка отправки.';
                    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('error');
                }
            } catch (e) {
                status.textContent = 'Ошибка соединения.';
                if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('error');
            }
            sendBtn.disabled = false;
        };
    }
});

window.updateScheduleForEntity = async function(entity) {
    if (!entity || !entity.entity_type || !entity.entity_id) return;
    
    // Сохраняем выбранную группу/преподавателя в глобальном состоянии
    window.scheduleState.currentEntity = entity;
    
    // Очищаем кэш расписания при смене группы/преподавателя
    window.scheduleState.scheduleCache.clear();
    
    let url = '';
    let body = {};
    // Получаем актуальную дату (аналогично остальному коду)
    let dateObj = (window.scheduleState && window.scheduleState.displayedDate) ? new Date(window.scheduleState.displayedDate) : new Date();
    const pad = n => n.toString().padStart(2, '0');
    const dateStr = pad(dateObj.getDate()) + '.' + pad(dateObj.getMonth() + 1) + '.' + dateObj.getFullYear();
    if (entity.entity_type === 'group') {
        url = '/api/schedule/group';
        body = { group: entity.entity_id, date: dateStr };
    } else if (entity.entity_type === 'teacher') {
        url = '/api/schedule/teacher';
        body = { teacher: entity.entity_id, date: dateStr };
    } else {
        return;
    }
    try {
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await resp.json();
        if (data && data.schedule) {
            window.scheduleState.currentScheduleData = data;
            updateScheduleDisplay();
        }
    } catch (e) {
        console.error('Ошибка при получении расписания:', e);
    }
}

// === Перенос кода из index.html ===

// Анимация появления кнопки 'Скачать'
function animateDownloadBtn() {
    console.log('animateDownloadBtn вызвана, window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
    const btn = document.getElementById('downloadScheduleBtn');
    if (btn) {
        btn.classList.remove('fade-slide-up');
        void btn.offsetWidth;
        btn.classList.add('fade-slide-up');
        updateDownloadLink();
    }
}

// Динамическое обновление ссылки на скачивание расписания
function updateDownloadLink() {
    console.log('[updateDownloadLink] START, window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
    let scheduleType = '';
    if (window.scheduleState && window.scheduleState.currentEntity) {
        const entityType = window.scheduleState.currentEntity.entity_type;
        if (entityType === 'group') scheduleType = 'groups';
        else if (entityType === 'teacher') scheduleType = 'teachers';
    }
    let dateObj = window.scheduleState && window.scheduleState.displayedDate ? new Date(window.scheduleState.displayedDate) : new Date();
    const pad = n => n.toString().padStart(2, '0');
    const dateStr = pad(dateObj.getDate()) + '.' + pad(dateObj.getMonth() + 1) + '.' + dateObj.getFullYear();
    let url = '';
    if (scheduleType) {
        url = `https://kkepik.ru/api/schedule/download/${scheduleType}/${dateStr}`;
    }
    const btn = document.getElementById('downloadScheduleBtn');
    if (btn) {
        if (url) {
            btn.setAttribute('data-url', url);
            btn.setAttribute('href', url);
            btn.style.display = '';
            console.log('[updateDownloadLink] ссылка для скачивания:', url);
        } else {
            btn.removeAttribute('data-url');
            btn.removeAttribute('href');
            btn.style.display = 'none';
        }
    }
    console.log('[updateDownloadLink] END, window.scheduleState.displayedDate:', window.scheduleState.displayedDate);
}

// Слушаем кастомные события для анимации и обновления кнопки

document.addEventListener('reactionsAnimated', animateDownloadBtn);
document.addEventListener('scheduleDateChanged', function() {
    updateDownloadLink();
    animateDownloadBtn();
});

const btn = document.getElementById('downloadScheduleBtn');
btn.addEventListener('click', function(e) {
            const url = btn.getAttribute('data-url');
            tg.openLink(url);
});