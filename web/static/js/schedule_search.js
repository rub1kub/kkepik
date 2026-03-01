let tg = window.Telegram.WebApp;
tg.expand();

tg.BackButton.show();
tg.BackButton.onClick(() => {
    window.location.href = `/?tgWebAppData=${encodeURIComponent(tg.initData)}`;
});

// Словарь сокращений предметов (как на главной странице)
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
    'индивидуальный проект': 'Инд. проект',
    'разговор о важном': 'Разговоры',
    'проектирование и разработка веб-приложений': 'Веб-разработка',
    'разработка кода информационных систем': 'Разраб. ИС',
    'английский язык': 'Англ. яз',
    'практика': 'Практика'
};

function shortenSubjectName(subject) {
    if (!subject || subject === 'Нет пары' || subject === '-') return subject;

    const lowerSubject = subject.toLowerCase();
    for (let [full, short] of Object.entries(SUBJECT_SHORTCUTS)) {
        if (lowerSubject.includes(full)) {
            return short;
        }
    }

    if (subject.length > 15) {
        return subject.slice(0, 15) + '...';
    }

    return subject;
}

document.addEventListener('DOMContentLoaded', function() {
    const typeButtons = document.querySelectorAll('.type-btn');
    const groupInput = document.querySelector('.group-input');
    const teacherInput = document.querySelector('.teacher-input');
    const audienceInput = document.querySelector('.audience-input');
    const searchButton = document.getElementById('search-btn');
    const resultsContainer = document.querySelector('.results-container');
    const errorMessage = document.querySelector('.error-message');
    const scheduleContent = document.getElementById('schedule-content');
    const scheduleTitle = document.getElementById('schedule-title');
    const scheduleDate = document.getElementById('schedule-date');

    const groupDropdown = document.createElement('div');
    groupDropdown.className = 'autocomplete-dropdown';
    groupInput.appendChild(groupDropdown);

    const teacherDropdown = document.createElement('div');
    teacherDropdown.className = 'autocomplete-dropdown';
    teacherInput.appendChild(teacherDropdown);

    let groupsList = [];
    let teachersList = [];

    async function loadLists() {
        try {
            const [groupsResponse, teachersResponse] = await Promise.all([
                fetch('/api/groups'),
                fetch('/api/teachers')
            ]);

            if (!groupsResponse.ok || !teachersResponse.ok) {
                throw new Error('Ошибка при загрузке списков');
            }

            const groupsData = await groupsResponse.json();
            const teachersData = await teachersResponse.json();

            groupsList = groupsData.groups;
            teachersList = teachersData.teachers;
        } catch (error) {
            console.error('Ошибка при загрузке списков:', error);
            showError('Ошибка при загрузке списков. Пожалуйста, обновите страницу.');
        }
    }

    loadLists();

    const today = new Date();
    document.getElementById('date').valueAsDate = today;

    typeButtons.forEach(button => {
        button.addEventListener('click', () => {
            typeButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            if (button.dataset.type === 'group') {
                groupInput.style.display = '';
                teacherInput.style.display = 'none';
                audienceInput.style.display = 'none';
                groupDropdown.style.display = 'none';
            } else if (button.dataset.type === 'teacher') {
                groupInput.style.display = 'none';
                teacherInput.style.display = '';
                audienceInput.style.display = 'none';
                teacherDropdown.style.display = 'none';
            } else if (button.dataset.type === 'audience') {
                groupInput.style.display = 'none';
                teacherInput.style.display = 'none';
                audienceInput.style.display = '';
            }
            
            // Обновляем заголовки таблицы при переключении типа поиска
            updateTableHeaders(button.dataset.type);
        });
    });

    function filterList(list, query) {
        return list.filter(item => 
            item.toLowerCase().includes(query.toLowerCase())
        );
    }

    function showDropdown(dropdown, items, input) {
        dropdown.innerHTML = '';
        if (items.length === 0) {
            dropdown.style.display = 'none';
            return;
        }

        items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'dropdown-item';
            div.textContent = item;
            div.addEventListener('click', () => {
                input.value = item;
                dropdown.style.display = 'none';
            });
            dropdown.appendChild(div);
        });

        dropdown.style.display = 'block';
    }

    document.getElementById('group').addEventListener('input', (e) => {
        const query = e.target.value;
        if (query.length < 1) {
            groupDropdown.style.display = 'none';
            return;
        }
        const suggestions = filterList(groupsList, query);
        showDropdown(groupDropdown, suggestions, e.target);
    });

    document.getElementById('teacher').addEventListener('input', (e) => {
        const query = e.target.value;
        if (query.length < 1) {
            teacherDropdown.style.display = 'none';
            return;
        }
        const suggestions = filterList(teachersList, query);
        showDropdown(teacherDropdown, suggestions, e.target);
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('.autocomplete-dropdown') && !e.target.closest('.search-input')) {
            groupDropdown.style.display = 'none';
            teacherDropdown.style.display = 'none';
        }
    });

    searchButton.addEventListener('click', async () => {
        const activeType = document.querySelector('.type-btn.active').dataset.type;
        const date = document.getElementById('date').value;
        let searchValue;
        if (activeType === 'group') {
            searchValue = document.getElementById('group').value;
        } else if (activeType === 'teacher') {
            searchValue = document.getElementById('teacher').value;
        } else if (activeType === 'audience') {
            searchValue = document.getElementById('audience').value;
        }

        if (!searchValue) {
            showError('Пожалуйста, введите значение для поиска');
            return;
        }

        if (!date) {
            showError('Пожалуйста, выберите дату');
            return;
        }

        try {
            const response = await fetch(`/api/schedule/${activeType}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    [activeType]: searchValue,
                    date: formatDateForDisplay(date)
                })
            });

            const data = await response.json();

            if (!response.ok) {
                if (response.status === 404 || data.error) {
                    showError(data.error || 'Ошибка при получении расписания');
                } else {
                    showError(data.error || 'Ошибка при получении расписания');
                }
                return;
            }

            if (!data.schedule || data.schedule.length === 0) {
                showError('Расписания на эту дату еще нет!');
                return;
            }

            // Проверка: дата в ответе должна совпадать с датой запроса
            const requestedDate = formatDateForDisplay(date);
            if (data.date && data.date !== requestedDate) {
                showError('Расписания на эту дату еще нет!');
                return;
            }

            displaySchedule(data, activeType, searchValue, date);
        } catch (error) {
            showError(error.message);
        }
    });

    function formatDateForDisplay(dateString) {
        const date = new Date(dateString);
        const day = String(date.getDate()).padStart(2, '0');
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const year = date.getFullYear();
        return `${day}.${month}.${year}`;
    }

    function formatDateForTitle(dateString) {
        const months = [
            'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
            'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
        ];
        const date = new Date(dateString);
        return `${date.getDate()} ${months[date.getMonth()]}`;
    }

    function updateTableHeaders(type) {
        const headerRow = document.querySelector('.schedule-row.header');
        if (!headerRow) return;

        const timeHeader = headerRow.querySelector('.time');
        const subjectHeader = headerRow.querySelector('.subject');
        const roomHeader = headerRow.querySelector('.room');

        if (type === 'audience') {
            subjectHeader.textContent = 'Группа';
            roomHeader.textContent = 'Предмет';
        } else {
            subjectHeader.textContent = 'Предмет';
            roomHeader.textContent = 'Аудитория';
        }
    }

    function parseScheduleItem(item, type) {
        if (!item || !item.includes('пара')) return null;

        const match = item.match(/^▪️(\d+) пара – (.+)$/);
        if (!match) return null;

        const [_, pairNumber, rest] = match;

        if (rest.trim() === 'Нет') {
            return {
                pairNumber,
                time: getPairTime(parseInt(pairNumber)),
                subject: '-',
                teacher: '-',
                room: '-',
                group: '-'
            };
        }

        if (type === 'group') {
            const parts = rest.split(' – ');

            // Специальная обработка для иностранного языка с подгруппами
            if ((rest.includes('Английский язык') || rest.includes('Иностранный язык')) && parts.length >= 3) {
                const [subject, teacher, room] = parts.map(s => s.trim());
                return {
                    pairNumber,
                    time: getPairTime(parseInt(pairNumber)),
                    subject: subject || '-',
                    teacher: teacher || '-',
                    room: room || '-'
                };
            }
            // Обработка практики
            else if (rest.includes('Практика') && parts.length === 3) {
                const [group, room, subject] = parts.map(s => s.trim());
                return {
                    pairNumber,
                    time: getPairTime(parseInt(pairNumber)),
                    subject: subject || '-',
                    teacher: '-',
                    room: room || '-',
                    group: group || '-'
                };
            } 
            // Стандартная обработка
            else {
                const [subject, teacher, room] = parts.map(s => s.trim());
                return {
                    pairNumber,
                    time: getPairTime(parseInt(pairNumber)),
                    subject: subject || '-',
                    teacher: teacher || '-',
                    room: room || '-'
                };
            }
        } else if (type === 'teacher') {
            const parts = rest.split(' – ');

            // Специальная обработка для иностранного языка с подгруппами
            if ((rest.includes('Английский язык') || rest.includes('Иностранный язык')) && parts.length >= 3) {
                const [group, room, subject] = parts.map(s => s.trim());
                return {
                    pairNumber,
                    time: getPairTime(parseInt(pairNumber)),
                    subject: subject || '-',
                    group: group || '-',
                    room: room || '-'
                };
            }
            // Обработка практики
            else if (rest.includes('Практика') && parts.length === 3) {
                const [group, room, subject] = parts.map(s => s.trim());
                return {
                    pairNumber,
                    time: getPairTime(parseInt(pairNumber)),
                    subject: subject || '-',
                    group: group || '-',
                    room: room || '-'
                };
            } 
            // Стандартная обработка
            else {
                const [group, room, subject] = parts.map(s => s.trim());
                return {
                    pairNumber,
                    time: getPairTime(parseInt(pairNumber)),
                    subject: subject || '-',
                    group: group || '-',
                    room: room || '-'
                };
            }
        } else if (type === 'audience') {
            const parts = rest.split(' – ');

            // Специальная обработка для иностранного языка с подгруппами
            if ((rest.includes('Английский язык') || rest.includes('Иностранный язык')) && parts.length >= 3) {
                const [group, teacher, subject] = parts.map(s => s.trim());
                return {
                    pairNumber,
                    time: getPairTime(parseInt(pairNumber)),
                    subject: subject || '-',
                    group: group || '-',
                    teacher: teacher || '-'
                };
            }
            // Обработка практики
            else if (rest.includes('Практика') && parts.length === 3) {
                const [group, teacher, subject] = parts.map(s => s.trim());
                return {
                    pairNumber,
                    time: getPairTime(parseInt(pairNumber)),
                    subject: subject || '-',
                    group: group || '-',
                    teacher: teacher || '-'
                };
            } 
            // Стандартная обработка
            else {
                const [group, teacher, subject] = parts.map(s => s.trim());
                return {
                    pairNumber,
                    time: getPairTime(parseInt(pairNumber)),
                    subject: subject || '-',
                    group: group || '-',
                    teacher: teacher || '-'
                };
            }
        }
    }

    function combineScheduleItems(items, type) {
        const combined = new Map();

        items.forEach(item => {
            const parsedItem = parseScheduleItem(item, type);
            if (!parsedItem) return;

            // Для иностранного языка используем комбинированный ключ: номер пары + предмет
            const isForeignLanguage = parsedItem.subject && (
                parsedItem.subject.toLowerCase().includes('английский') || 
                parsedItem.subject.toLowerCase().includes('иностранный')
            );
            const key = isForeignLanguage ? `${parsedItem.pairNumber}-${parsedItem.subject}` : parsedItem.pairNumber;

            if (parsedItem.subject === '-' && parsedItem.room === '-') {
                if (!combined.has(key)) {
                    combined.set(key, {
                        ...parsedItem,
                        teachers: [],
                        rooms: ['-'],
                        groups: []
                    });
                }
                return;
            }

            if (!combined.has(key)) {
                combined.set(key, {
                    ...parsedItem,
                    teachers: type === 'group' ? [parsedItem.teacher].filter(t => t !== '-') : 
                             type === 'audience' ? [parsedItem.teacher].filter(t => t !== '-') : [],
                    rooms: type === 'audience' ? [] : [parsedItem.room].filter(r => r !== '-'),
                    groups: type === 'teacher' ? [parsedItem.group].filter(g => g !== '-') : 
                            type === 'audience' ? [parsedItem.group].filter(g => g !== '-') : []
                });
            } else {
                const existing = combined.get(key);
                if ((type === 'group' || type === 'audience') && parsedItem.teacher !== '-') {
                    if (!existing.teachers.includes(parsedItem.teacher)) {
                        existing.teachers.push(parsedItem.teacher);
                    }
                } else if ((type === 'teacher' || type === 'audience') && parsedItem.group !== '-') {
                    if (!existing.groups.includes(parsedItem.group)) {
                        existing.groups.push(parsedItem.group);
                    }
                }
                if (type !== 'audience' && parsedItem.room !== '-' && !existing.rooms.includes(parsedItem.room)) {
                    existing.rooms.push(parsedItem.room);
                }
            }
        });

        return Array.from(combined.values());
    }

    function displayPairTime(pairNumber, timeRange) {
        const [start, end] = timeRange.split('-');
        return `<div class="pair-number">${pairNumber} пара</div>
                <div class="time-range">
                    <div>${start}</div>
                    <div>${end}</div>
                </div>`;
    }

    function displaySchedule(data, type, value, date) {
        errorMessage.style.display = 'none';
        resultsContainer.style.display = '';

        let title;
        if (type === 'group') {
            title = `Расписание группы ${value}`;
        } else if (type === 'teacher') {
            title = `Расписание преподавателя ${value}`;
        } else if (type === 'audience') {
            title = `Расписание в аудитории ${value}`;
        }
        scheduleTitle.textContent = title;
        scheduleDate.textContent = formatDateForTitle(date);

        // Обновляем заголовки таблицы в зависимости от типа поиска
        updateTableHeaders(type);

        scheduleContent.innerHTML = '';
        if (!data.schedule || data.schedule.length === 0) {
            const row = document.createElement('div');
            row.className = 'schedule-row';
            row.innerHTML = '<div class="subject" style="grid-column: 1 / -1; text-align: center;">Расписание отсутствует</div>';
            scheduleContent.appendChild(row);
            return;
        }

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
                // Разделяем элементы с переносами строк на отдельные элементы
                if (item.includes('\n')) {
                    const splitItems = item.split('\n').filter(subItem => subItem.trim());
                    splitItems.forEach(subItem => mainSchedule.push(subItem.trim()));
                } else {
                    mainSchedule.push(item);
                }
            }
        });

        const combinedMainSchedule = combineScheduleItems(mainSchedule, type);
        combinedMainSchedule.sort((a, b) => parseInt(a.pairNumber) - parseInt(b.pairNumber));
        displayScheduleItems(combinedMainSchedule, type);

        if (combinedSchedule.length > 0) {
            const divider = document.createElement('div');
            divider.className = 'schedule-divider';
            divider.innerHTML = '<div class="divider-text">Совмещенные пары</div>';
            scheduleContent.appendChild(divider);

            // Обрабатываем совмещенные пары аналогично основному расписанию
            const processedCombinedSchedule = [];
            combinedSchedule.forEach(item => {
                if (item.includes('\n')) {
                    const splitItems = item.split('\n').filter(subItem => subItem.trim());
                    splitItems.forEach(subItem => processedCombinedSchedule.push(subItem.trim()));
                } else {
                    processedCombinedSchedule.push(item);
                }
            });

            const combinedCombinedSchedule = combineScheduleItems(processedCombinedSchedule, type);
            combinedCombinedSchedule.sort((a, b) => parseInt(a.pairNumber) - parseInt(b.pairNumber));
            displayScheduleItems(combinedCombinedSchedule, type, true);
        }
    }

    function displayScheduleItems(items, type, isCombined = false) {
        items.forEach(item => {
            const row = document.createElement('div');
            row.className = `schedule-row ${isCombined ? 'combined' : ''}`;

            const timeCell = document.createElement('div');
            timeCell.className = 'time';
            timeCell.innerHTML = displayPairTime(item.pairNumber, item.time);

            const subjectCell = document.createElement('div');
            subjectCell.className = 'subject';

            // Сокращаем название предмета
            const shortenedSubject = shortenSubjectName(item.subject || '-');
            
            if (type === 'group') {
                const teachersText = item.teachers?.length ? item.teachers.join(', ') : '-';
                subjectCell.innerHTML = `<div class="subject-name" title="${item.subject || '-'}">${shortenedSubject}</div><span class="teacher">${teachersText}</span>`;
            } else if (type === 'teacher') {
                const groupsText = item.groups?.length ? item.groups.join(', ') : '-';
                const shortenedGroup = shortenSubjectName(groupsText);
                subjectCell.innerHTML = `<div class="subject-name" title="${groupsText}">${shortenedGroup}</div><span class="group">${shortenedSubject}</span>`;
            } else if (type === 'audience') {
                const groupsText = item.groups?.length ? item.groups.join(', ') : '-';
                const shortenedGroup = shortenSubjectName(groupsText);
                subjectCell.innerHTML = `<div class="subject-name" title="${groupsText}">${shortenedGroup}</div><span class="group">${shortenedSubject}</span>`;
            }

            const roomCell = document.createElement('div');
            roomCell.className = 'room';
            if (type === 'audience') {
                const teachersText = item.teachers?.length ? item.teachers.join(', ') : '-';
                roomCell.textContent = teachersText;
            } else {
                roomCell.textContent = item.rooms?.length ? item.rooms.join(', ') : '-';
            }

            row.appendChild(timeCell);
            row.appendChild(subjectCell);
            row.appendChild(roomCell);

            scheduleContent.appendChild(row);
        });
    }

    function getPairTime(pairNumber) {
        const times = [
            '08:45-10:05',
            '10:25-11:45',
            '12:05-13:25',
            '13:35-14:55',
            '15:05-16:25',
            '16:35-17:55'
        ];
        return times[pairNumber - 1] || '-';
    }

    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.style.display = '';
        resultsContainer.style.display = 'none';
    }
});