(() => {
    'use strict';

    const telegram = window.Telegram?.WebApp || null;
    const RECENT_SEARCHES_KEY = 'kkepik.schedule-search.recent.v2';
    const SEARCH_STATE_KEY = 'kkepik.schedule-search.state.v1';
    const SEARCH_STATE_MAX_AGE = 30 * 24 * 60 * 60 * 1000;
    const MAX_RECENT_SEARCHES = 4;

    const TYPE_CONFIG = {
        group: {
            tabLabel: 'Группа',
            fieldLabel: 'Номер группы',
            placeholder: '103-Д9-2ИНС',
            hint: 'Начните вводить название группы',
            resultLabel: 'Группа',
            shortLabel: 'Г',
            downloadType: 'groups'
        },
        teacher: {
            tabLabel: 'Преподаватель',
            fieldLabel: 'Фамилия преподавателя',
            placeholder: 'Фамилия или ФИО',
            hint: 'Достаточно нескольких букв фамилии',
            resultLabel: 'Преподаватель',
            shortLabel: 'П',
            downloadType: 'teachers'
        },
        audience: {
            tabLabel: 'Аудитория',
            fieldLabel: 'Номер аудитории',
            placeholder: '84 или название',
            hint: 'Введите номер или название аудитории',
            resultLabel: 'Аудитория',
            shortLabel: 'А',
            downloadType: null
        }
    };

    const WEEKDAY_TIMES = {
        1: '08:45–10:05',
        2: '10:25–11:45',
        3: '12:05–13:25',
        4: '13:35–14:55',
        5: '15:05–16:25',
        6: '16:35–17:55'
    };

    const SATURDAY_TIMES = {
        1: '08:45–10:00',
        2: '10:10–11:25',
        3: '11:35–12:50',
        4: '13:00–14:15',
        5: '14:25–15:40',
        6: '15:50–17:05'
    };

    const MONTHS = [
        'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
        'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
    ];
    const CALENDAR_MONTHS = [
        'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
        'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
    ];
    const WEEKDAYS = [
        'воскресенье', 'понедельник', 'вторник', 'среда',
        'четверг', 'пятница', 'суббота'
    ];

    const state = {
        activeType: 'group',
        values: { group: '', teacher: '', audience: '' },
        selectedDate: atNoon(new Date()),
        dateSelection: { kind: 'offset', offset: 0 },
        calendarMonth: new Date(new Date().getFullYear(), new Date().getMonth(), 1, 12),
        groups: [],
        teachers: [],
        audiences: [],
        myEntity: null,
        autocompleteItems: [],
        autocompleteIndex: -1,
        requestId: 0,
        activeController: null
    };

    const elements = {};

    function init() {
        cacheElements();
        restoreSearchState();
        configureTelegram();
        bindEvents();
        renderDateChoices();
        renderRecentSearches();
        elements.typeButtons.forEach((button) => {
            const active = button.dataset.type === state.activeType;
            button.classList.toggle('active', active);
            button.setAttribute('aria-selected', String(active));
        });
        updateInputState();
        removeInitDataFromAddress();
        loadReferenceData();
    }

    function restoreSearchState() {
        try {
            const saved = JSON.parse(localStorage.getItem(SEARCH_STATE_KEY) || 'null');
            if (!saved || Date.now() - Number(saved.savedAt || 0) > SEARCH_STATE_MAX_AGE) return;

            if (TYPE_CONFIG[saved.activeType]) state.activeType = saved.activeType;
            Object.keys(state.values).forEach(type => {
                if (typeof saved.values?.[type] === 'string') {
                    state.values[type] = saved.values[type].slice(0, 200);
                }
            });

            const selection = saved.dateSelection;
            if (selection?.kind === 'offset' && [-1, 0, 1].includes(Number(selection.offset))) {
                const offset = Number(selection.offset);
                state.dateSelection = { kind: 'offset', offset };
                state.selectedDate = addDays(atNoon(new Date()), offset);
                return;
            }

            if (selection?.kind === 'custom') {
                const date = parseLocalIso(selection.date);
                const latestDate = addDays(atNoon(new Date()), 1);
                if (date && date.getDay() !== 0 && date <= latestDate) {
                    state.dateSelection = { kind: 'custom', date: toLocalIso(date) };
                    state.selectedDate = date;
                }
            }
        } catch (error) {}
    }

    function persistSearchState() {
        try {
            if (elements.searchInput) {
                state.values[state.activeType] = elements.searchInput.value;
            }
            localStorage.setItem(SEARCH_STATE_KEY, JSON.stringify({
                activeType: state.activeType,
                values: state.values,
                dateSelection: state.dateSelection,
                savedAt: Date.now()
            }));
        } catch (error) {}
    }

    function cacheElements() {
        elements.form = document.getElementById('schedule-search-form');
        elements.typeButtons = Array.from(document.querySelectorAll('.type-button'));
        elements.searchField = document.querySelector('.search-field');
        elements.searchInput = document.getElementById('search-value');
        elements.searchLabel = document.getElementById('search-label');
        elements.fieldHint = document.getElementById('field-hint');
        elements.clearSearch = document.getElementById('clear-search');
        elements.autocomplete = document.getElementById('autocomplete-list');
        elements.dateButtons = Array.from(document.querySelectorAll('[data-date-offset]'));
        elements.customDateChoice = document.getElementById('custom-date-choice');
        elements.customDateLabel = document.getElementById('custom-date-label');
        elements.calendarOverlay = document.getElementById('calendar-overlay');
        elements.calendarSheet = document.querySelector('.calendar-sheet');
        elements.calendarMonthLabel = document.getElementById('calendar-month-label');
        elements.calendarGrid = document.getElementById('calendar-grid');
        elements.calendarPrev = document.getElementById('calendar-prev');
        elements.calendarNext = document.getElementById('calendar-next');
        elements.calendarCancel = document.getElementById('calendar-cancel');
        elements.submit = document.getElementById('search-submit');
        elements.submitLabel = elements.submit.querySelector('span:not(.button-spinner)');
        elements.myEntity = document.getElementById('my-entity');
        elements.myEntityLabel = document.getElementById('my-entity-label');
        elements.myEntityValue = document.getElementById('my-entity-value');
        elements.recentSection = document.getElementById('recent-section');
        elements.recentList = document.getElementById('recent-list');
        elements.clearRecent = document.getElementById('clear-recent');
        elements.status = document.getElementById('status-message');
        elements.statusText = elements.status.querySelector('span');
        elements.results = document.getElementById('results-panel');
        elements.scheduleKind = document.getElementById('schedule-kind');
        elements.scheduleTitle = document.getElementById('schedule-title');
        elements.scheduleDate = document.getElementById('schedule-date');
        elements.scheduleContent = document.getElementById('schedule-content');
        elements.resultActions = document.getElementById('result-actions');
        elements.download = document.getElementById('download-schedule');
    }

    function configureTelegram() {
        if (!telegram) return;

        telegram.expand?.();
        telegram.ready?.();

        if (telegram.colorScheme) {
            document.documentElement.dataset.theme = telegram.colorScheme;
        }

        telegram.BackButton?.show();
        telegram.BackButton?.onClick(handleBack);
    }

    function bindEvents() {
        elements.form.addEventListener('submit', searchSchedule);

        elements.typeButtons.forEach((button) => {
            button.addEventListener('click', () => {
                setActiveType(button.dataset.type, true);
            });
        });

        elements.searchInput.addEventListener('input', handleSearchInput);
        elements.searchInput.addEventListener('focus', showAutocompleteForCurrentValue);
        elements.searchInput.addEventListener('keydown', handleAutocompleteKeyboard);
        elements.searchInput.addEventListener('blur', () => {
            window.setTimeout(hideAutocomplete, 120);
        });

        elements.searchField.addEventListener('click', (event) => {
            if (!event.target.closest('#clear-search')) {
                elements.searchInput.focus();
            }
        });

        elements.clearSearch.addEventListener('click', () => {
            elements.searchInput.value = '';
            state.values[state.activeType] = '';
            updateClearButton();
            hideAutocomplete();
            hideResults();
            persistSearchState();
            elements.searchInput.focus();
        });

        elements.dateButtons.forEach((button) => {
            button.addEventListener('click', () => {
                const offset = Number(button.dataset.dateOffset);
                setSelectedDate(
                    addDays(atNoon(new Date()), offset),
                    { kind: 'offset', offset }
                );
                haptic('soft');
            });
        });

        elements.customDateChoice.addEventListener('click', openDatePicker);
        elements.calendarPrev.addEventListener('click', () => changeCalendarMonth(-1));
        elements.calendarNext.addEventListener('click', () => changeCalendarMonth(1));
        elements.calendarCancel.addEventListener('click', closeDatePicker);
        elements.calendarOverlay.addEventListener('click', (event) => {
            if (event.target === elements.calendarOverlay) {
                closeDatePicker();
            }
        });

        elements.myEntity.addEventListener('click', () => {
            if (!state.myEntity) return;
            setActiveType(state.myEntity.type);
            setSearchValue(state.myEntity.value);
            elements.searchInput.focus();
            haptic('soft');
        });

        elements.clearRecent.addEventListener('click', () => {
            localStorage.removeItem(RECENT_SEARCHES_KEY);
            renderRecentSearches();
        });

        elements.download.addEventListener('click', (event) => {
            if (!telegram?.openLink) return;
            event.preventDefault();
            telegram.openLink(new URL(elements.download.href, window.location.origin).href);
        });

        document.addEventListener('click', (event) => {
            if (!event.target.closest('.search-field-group')) {
                hideAutocomplete();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !elements.calendarOverlay.hidden) {
                event.preventDefault();
                closeDatePicker();
            }
        });
    }

    function handleBack() {
        if (!elements.calendarOverlay.hidden) {
            closeDatePicker();
            return;
        }
        goBack();
    }

    function goBack() {
        try {
            if (document.referrer && new URL(document.referrer).origin === window.location.origin) {
                window.history.back();
                return;
            }
        } catch (error) {
            // A malformed referrer should not prevent navigation to the app root.
        }
        window.location.assign('/');
    }

    function removeInitDataFromAddress() {
        const url = new URL(window.location.href);
        if (!url.searchParams.has('tgWebAppData')) return;

        url.searchParams.delete('tgWebAppData');
        const query = url.searchParams.toString();
        window.history.replaceState(
            window.history.state,
            '',
            `${url.pathname}${query ? `?${query}` : ''}${url.hash}`
        );
    }

    function setActiveType(type, focusInput = false) {
        if (!TYPE_CONFIG[type]) return;

        state.values[state.activeType] = elements.searchInput.value;
        const changed = type !== state.activeType;
        state.activeType = type;

        elements.typeButtons.forEach((button) => {
            const active = button.dataset.type === type;
            button.classList.toggle('active', active);
            button.setAttribute('aria-selected', String(active));
        });

        updateInputState();
        updateMyEntityButton();
        hideAutocomplete();

        if (changed) {
            hideStatus();
            hideResults();
            haptic('soft');
        }

        if (focusInput) {
            elements.searchInput.focus();
        }
        persistSearchState();
    }

    function updateInputState() {
        const config = TYPE_CONFIG[state.activeType];
        elements.searchLabel.textContent = config.fieldLabel;
        elements.searchInput.placeholder = config.placeholder;
        elements.searchInput.value = state.values[state.activeType];
        elements.searchInput.autocapitalize = state.activeType === 'group' ? 'characters' : 'words';
        elements.fieldHint.textContent = config.hint;
        updateClearButton();
    }

    function handleSearchInput() {
        state.values[state.activeType] = elements.searchInput.value;
        updateClearButton();
        hideStatus();
        hideResults();
        showAutocompleteForCurrentValue();
        persistSearchState();
    }

    function updateClearButton() {
        elements.clearSearch.hidden = elements.searchInput.value.length === 0;
    }

    async function loadReferenceData() {
        const hasTelegramAuth = Boolean(telegram?.initData);
        const requests = [fetchJson('/api/schedule/catalog')];

        if (hasTelegramAuth) {
            requests.push(fetchJson('/api/me'));
        }

        const results = await Promise.allSettled(requests);
        const catalogData = fulfilledValue(results[0]);
        const userData = hasTelegramAuth ? fulfilledValue(results[1]) : null;

        state.groups = cleanStringList(catalogData?.groups);
        state.teachers = cleanStringList(catalogData?.teachers);
        state.audiences = cleanStringList(catalogData?.audiences);

        if (userData?.name_or_group) {
            const value = stripMarkup(userData.name_or_group);
            const currentGroup = findCanonicalValue(state.groups, value);
            const currentTeacher = findCanonicalValue(state.teachers, value);

            if (currentGroup) {
                state.myEntity = { type: 'group', value: currentGroup, label: 'Моя группа' };
            } else if (currentTeacher) {
                state.myEntity = { type: 'teacher', value: currentTeacher, label: 'Моё расписание' };
            }
        }

        updateMyEntityButton();
    }

    async function fetchJson(url) {
        const headers = { Accept: 'application/json' };
        if (telegram?.initData) {
            headers['X-Telegram-Init-Data'] = telegram.initData;
        }
        const response = await fetch(url, { headers });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
    }

    function fulfilledValue(result) {
        return result?.status === 'fulfilled' ? result.value : null;
    }

    function cleanStringList(list) {
        if (!Array.isArray(list)) return [];
        return Array.from(new Set(list.map(stripMarkup).filter(Boolean)))
            .sort((left, right) => left.localeCompare(right, 'ru', { numeric: true }));
    }

    function updateMyEntityButton() {
        const entity = state.myEntity;
        const visible = entity && entity.type === state.activeType;
        elements.myEntity.hidden = !visible;

        if (visible) {
            elements.myEntityLabel.textContent = entity.label;
            elements.myEntityValue.textContent = entity.value;
        }
    }

    function showAutocompleteForCurrentValue() {
        const query = elements.searchInput.value.trim();
        const source = state.activeType === 'group'
            ? state.groups
            : state.activeType === 'teacher'
                ? state.teachers
                : state.audiences;

        if (!query || source.length === 0) {
            hideAutocomplete();
            return;
        }

        const normalizedQuery = normalizeForSearch(query);
        const matches = source
            .map((value, index) => ({
                value,
                index,
                normalized: normalizeForSearch(value)
            }))
            .filter((item) => item.normalized.includes(normalizedQuery))
            .sort((left, right) => {
                const leftStarts = left.normalized.startsWith(normalizedQuery) ? 0 : 1;
                const rightStarts = right.normalized.startsWith(normalizedQuery) ? 0 : 1;
                return leftStarts - rightStarts || left.index - right.index;
            })
            .slice(0, 8)
            .map((item) => item.value);

        renderAutocomplete(matches);
    }

    function renderAutocomplete(items) {
        elements.autocomplete.replaceChildren();
        state.autocompleteItems = items;
        state.autocompleteIndex = -1;

        if (items.length === 0) {
            hideAutocomplete();
            return;
        }

        const fragment = document.createDocumentFragment();
        items.forEach((value, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'autocomplete-item';
            button.id = `autocomplete-option-${index}`;
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', 'false');

            const mark = document.createElement('span');
            mark.className = 'autocomplete-mark';
            mark.textContent = TYPE_CONFIG[state.activeType].shortLabel;

            const label = document.createElement('span');
            label.className = 'autocomplete-value';
            label.textContent = value;

            button.append(mark, label);
            button.addEventListener('pointerdown', (event) => event.preventDefault());
            button.addEventListener('click', () => selectAutocomplete(value));
            fragment.appendChild(button);
        });

        elements.autocomplete.appendChild(fragment);
        elements.autocomplete.hidden = false;
        elements.searchInput.setAttribute('aria-expanded', 'true');
    }

    function hideAutocomplete() {
        elements.autocomplete.hidden = true;
        elements.searchInput.setAttribute('aria-expanded', 'false');
        elements.searchInput.removeAttribute('aria-activedescendant');
        state.autocompleteItems = [];
        state.autocompleteIndex = -1;
    }

    function selectAutocomplete(value) {
        setSearchValue(value);
        hideAutocomplete();
        elements.searchInput.focus();
        haptic('soft');
    }

    function setSearchValue(value) {
        elements.searchInput.value = value;
        state.values[state.activeType] = value;
        updateClearButton();
        hideStatus();
        hideResults();
        persistSearchState();
    }

    function handleAutocompleteKeyboard(event) {
        if (elements.autocomplete.hidden) return;

        const options = Array.from(elements.autocomplete.querySelectorAll('.autocomplete-item'));
        if (options.length === 0) return;

        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            const direction = event.key === 'ArrowDown' ? 1 : -1;
            state.autocompleteIndex = (
                state.autocompleteIndex + direction + options.length
            ) % options.length;
            updateActiveAutocomplete(options);
        } else if (event.key === 'Enter' && state.autocompleteIndex >= 0) {
            event.preventDefault();
            selectAutocomplete(state.autocompleteItems[state.autocompleteIndex]);
        } else if (event.key === 'Escape') {
            event.preventDefault();
            hideAutocomplete();
        }
    }

    function updateActiveAutocomplete(options) {
        options.forEach((option, index) => {
            const active = index === state.autocompleteIndex;
            option.classList.toggle('active', active);
            option.setAttribute('aria-selected', String(active));
        });

        const activeOption = options[state.autocompleteIndex];
        elements.searchInput.setAttribute('aria-activedescendant', activeOption.id);
        activeOption.scrollIntoView({ block: 'nearest' });
    }

    function atNoon(date) {
        return new Date(date.getFullYear(), date.getMonth(), date.getDate(), 12, 0, 0, 0);
    }

    function addDays(date, amount) {
        const result = atNoon(date);
        result.setDate(result.getDate() + amount);
        return result;
    }

    function toLocalIso(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function parseLocalIso(value) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
        if (!match) return null;
        const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12);
        if (
            Number.isNaN(date.getTime())
            || date.getFullYear() !== Number(match[1])
            || date.getMonth() !== Number(match[2]) - 1
            || date.getDate() !== Number(match[3])
        ) return null;
        return date;
    }

    function setSelectedDate(date, selection = { kind: 'custom', date: toLocalIso(date) }) {
        state.selectedDate = atNoon(date);
        state.dateSelection = selection;
        renderDateChoices();
        hideStatus();
        hideResults();
        persistSearchState();
    }

    function openDatePicker() {
        state.calendarMonth = new Date(
            state.selectedDate.getFullYear(),
            state.selectedDate.getMonth(),
            1,
            12
        );
        renderCalendar();
        elements.calendarOverlay.hidden = false;
        document.body.classList.add('calendar-open');
        haptic('soft');

        window.requestAnimationFrame(() => {
            elements.calendarOverlay.classList.add('open');
            const preferredDay = elements.calendarGrid.querySelector('.calendar-day.selected:not(:disabled)');
            const firstAvailableDay = elements.calendarGrid.querySelector('.calendar-day:not(:disabled)');
            (preferredDay || firstAvailableDay)?.focus({ preventScroll: true });
        });
    }

    function closeDatePicker() {
        if (elements.calendarOverlay.hidden) return;
        elements.calendarOverlay.classList.remove('open');
        elements.calendarOverlay.hidden = true;
        document.body.classList.remove('calendar-open');
        elements.customDateChoice.focus({ preventScroll: true });
    }

    function changeCalendarMonth(offset) {
        const candidate = new Date(
            state.calendarMonth.getFullYear(),
            state.calendarMonth.getMonth() + offset,
            1,
            12
        );
        const latestDate = addDays(atNoon(new Date()), 1);
        const latestMonth = new Date(latestDate.getFullYear(), latestDate.getMonth(), 1, 12);

        if (candidate > latestMonth) return;
        state.calendarMonth = candidate;
        renderCalendar();
        haptic('soft');
    }

    function renderCalendar() {
        const year = state.calendarMonth.getFullYear();
        const month = state.calendarMonth.getMonth();
        const today = atNoon(new Date());
        const latestDate = addDays(today, 1);
        const latestMonth = new Date(latestDate.getFullYear(), latestDate.getMonth(), 1, 12);
        const nextMonth = new Date(year, month + 1, 1, 12);
        const firstWeekday = (new Date(year, month, 1, 12).getDay() + 6) % 7;
        const daysInMonth = new Date(year, month + 1, 0, 12).getDate();

        elements.calendarMonthLabel.textContent = `${CALENDAR_MONTHS[month]} ${year}`;
        elements.calendarNext.disabled = nextMonth > latestMonth;
        elements.calendarGrid.replaceChildren();

        const fragment = document.createDocumentFragment();
        for (let index = 0; index < firstWeekday; index += 1) {
            const spacer = document.createElement('span');
            spacer.className = 'calendar-spacer';
            spacer.setAttribute('aria-hidden', 'true');
            fragment.appendChild(spacer);
        }

        for (let day = 1; day <= daysInMonth; day += 1) {
            const date = new Date(year, month, day, 12);
            const isSunday = date.getDay() === 0;
            const isFuture = date > latestDate;
            const isToday = toLocalIso(date) === toLocalIso(today);
            const isSelected = toLocalIso(date) === toLocalIso(state.selectedDate);
            const button = document.createElement('button');

            button.type = 'button';
            button.className = 'calendar-day';
            button.textContent = String(day);
            button.disabled = isSunday || isFuture;
            button.classList.toggle('today', isToday);
            button.classList.toggle('selected', isSelected);
            button.setAttribute('aria-label', formatFullDate(date));
            if (isToday) button.setAttribute('aria-current', 'date');
            if (isSunday) button.title = 'Воскресенье — выходной';

            button.addEventListener('click', () => {
                setSelectedDate(date, { kind: 'custom', date: toLocalIso(date) });
                closeDatePicker();
                haptic('soft');
            });
            fragment.appendChild(button);
        }

        elements.calendarGrid.appendChild(fragment);
    }

    function renderDateChoices() {
        const today = atNoon(new Date());
        const selectedIso = toLocalIso(state.selectedDate);
        let selectedQuickDate = false;

        elements.dateButtons.forEach((button) => {
            const date = addDays(today, Number(button.dataset.dateOffset));
            const active = toLocalIso(date) === selectedIso;
            button.querySelector('strong').textContent = String(date.getDate());
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
            selectedQuickDate ||= active;
        });

        elements.customDateChoice.classList.toggle('active', !selectedQuickDate);
        elements.customDateChoice.setAttribute('aria-pressed', String(!selectedQuickDate));
        elements.customDateLabel.textContent = selectedQuickDate
            ? ''
            : `${String(state.selectedDate.getDate()).padStart(2, '0')}.${String(state.selectedDate.getMonth() + 1).padStart(2, '0')}`;
    }

    function formatDateForApi(date) {
        return `${String(date.getDate()).padStart(2, '0')}.${String(date.getMonth() + 1).padStart(2, '0')}.${date.getFullYear()}`;
    }

    function formatFullDate(date) {
        const year = date.getFullYear() === new Date().getFullYear() ? '' : ` ${date.getFullYear()}`;
        return `${date.getDate()} ${MONTHS[date.getMonth()]}${year}, ${WEEKDAYS[date.getDay()]}`;
    }

    async function searchSchedule(event) {
        event.preventDefault();
        hideAutocomplete();

        const rawValue = elements.searchInput.value.trim();
        if (!rawValue) {
            showStatus(`Введите ${TYPE_CONFIG[state.activeType].fieldLabel.toLowerCase()}.`, true);
            elements.searchInput.focus();
            hapticError();
            return;
        }

        const value = canonicalizeSearchValue(rawValue);
        const dateForApi = formatDateForApi(state.selectedDate);
        const requestId = ++state.requestId;
        let timedOut = false;

        state.activeController?.abort();
        const controller = new AbortController();
        state.activeController = controller;

        hideStatus();
        hideResults();
        setLoading(true);

        const timeout = window.setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, 15000);

        try {
            const response = await fetch(`/api/schedule/${state.activeType}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Accept: 'application/json'
                },
                body: JSON.stringify({
                    [state.activeType]: value,
                    date: dateForApi
                }),
                signal: controller.signal
            });

            const data = await readJsonResponse(response);
            if (!response.ok) {
                throw new Error(getApiError(data, response.status));
            }

            if (data.date && data.date !== dateForApi) {
                throw new Error('На выбранную дату расписания нет.');
            }

            const lessons = parseSchedule(data.schedule, state.activeType);
            if (lessons.length === 0) {
                throw new Error('На выбранную дату расписания нет.');
            }

            if (requestId !== state.requestId) return;

            setSearchValue(value);
            displaySchedule(data, lessons, state.activeType, value, state.selectedDate);
            saveRecentSearch(state.activeType, value);
            hapticSuccess();
        } catch (error) {
            if (requestId !== state.requestId) return;
            if (error.name === 'AbortError' && !timedOut) return;

            const message = timedOut
                ? 'Сервер отвечает слишком долго. Попробуйте ещё раз.'
                : error.message || 'Не удалось загрузить расписание.';
            showStatus(message, true);
            hapticError();
        } finally {
            window.clearTimeout(timeout);
            if (requestId === state.requestId) {
                state.activeController = null;
                setLoading(false);
            }
        }
    }

    function canonicalizeSearchValue(value) {
        if (state.activeType === 'group') {
            return findCanonicalValue(state.groups, value) || value.toUpperCase();
        }
        if (state.activeType === 'teacher') {
            return findCanonicalValue(state.teachers, value) || value;
        }
        return value.replace(/^ауд(?:итория)?\.?\s*/iu, '').trim();
    }

    async function readJsonResponse(response) {
        const text = await response.text();
        if (!text) return {};

        try {
            return JSON.parse(text);
        } catch (error) {
            throw new Error('Сервер вернул некорректный ответ.');
        }
    }

    function getApiError(data, status) {
        const message = stripMarkup(data?.error || data?.detail || '');
        if (message) return message;
        if (status === 404) return 'Расписание не найдено. Проверьте запрос и дату.';
        if (status === 429) return 'Слишком много запросов. Подождите несколько секунд.';
        return 'Не удалось загрузить расписание.';
    }

    function setLoading(loading) {
        elements.submit.disabled = loading;
        elements.submit.classList.toggle('loading', loading);
        elements.submitLabel.textContent = loading ? 'Ищем' : 'Найти';
        elements.form.setAttribute('aria-busy', String(loading));
    }

    function parseSchedule(schedule, type) {
        if (!Array.isArray(schedule)) return [];

        const parsed = [];
        let combined = false;

        schedule.forEach((rawItem) => {
            String(rawItem ?? '').split('\n').forEach((rawLine) => {
                const line = stripMarkup(rawLine);
                if (!line) return;

                if (/^Совмещенные пары:?$/iu.test(line)) {
                    combined = true;
                    return;
                }

                const lesson = parseScheduleLine(line, type);
                if (lesson) {
                    lesson.combined = combined;
                    parsed.push(lesson);
                }
            });
        });

        return mergeScheduleItems(parsed, type)
            .sort((left, right) => Number(left.combined) - Number(right.combined) || left.pairNumber - right.pairNumber);
    }

    function parseScheduleLine(line, type) {
        const match = /^[▪▫•]?\uFE0F?\s*(\d+)\s*пара(?:\s*\([^)]*\))?\s*[–—-]\s*(.+)$/iu.exec(line);
        if (!match) return null;

        const pairNumber = Number(match[1]);
        const rest = match[2].trim();
        if (!rest || /^нет(?:\s+пары)?$/iu.test(rest)) return null;

        const parts = rest
            .split(/\s+[–—-]\s+/u)
            .map((part) => part.trim())
            .filter(Boolean);

        if (parts.length === 0) return null;

        const lesson = {
            pairNumber,
            subject: '',
            teachers: [],
            rooms: [],
            groups: [],
            combined: false
        };

        if (type === 'group') {
            lesson.subject = parts[0];
            parts.slice(1).forEach((part) => {
                if (looksLikeRoom(part)) {
                    lesson.rooms.push(normalizeRoom(part));
                } else {
                    lesson.teachers.push(part);
                }
            });
        } else if (type === 'teacher') {
            lesson.groups.push(parts[0]);
            const details = parts.slice(1);
            const subjectParts = [];

            details.forEach((part) => {
                if (looksLikeRoom(part)) {
                    lesson.rooms.push(normalizeRoom(part));
                } else {
                    subjectParts.push(part);
                }
            });
            lesson.subject = subjectParts.join(' – ');
        } else {
            lesson.groups.push(parts[0]);
            const details = parts.slice(1);
            const subjectParts = [];

            details.forEach((part) => {
                if (looksLikeTeacher(part)) {
                    lesson.teachers.push(part);
                } else {
                    subjectParts.push(part);
                }
            });

            if (lesson.teachers.length === 0 && subjectParts.length > 1) {
                lesson.teachers.push(subjectParts.pop());
            }
            lesson.subject = subjectParts.join(' – ');
        }

        lesson.subject ||= 'Занятие';
        lesson.teachers = uniqueStrings(lesson.teachers);
        lesson.rooms = uniqueStrings(lesson.rooms);
        lesson.groups = uniqueStrings(lesson.groups);
        return lesson;
    }

    function mergeScheduleItems(items, type) {
        const merged = new Map();

        items.forEach((item) => {
            let key = `${Number(item.combined)}|${item.pairNumber}|${normalizeForSearch(item.subject)}`;
            if (type === 'teacher') {
                key += `|${item.rooms.map(normalizeForSearch).join(',')}`;
            }

            if (!merged.has(key)) {
                merged.set(key, {
                    ...item,
                    teachers: [...item.teachers],
                    rooms: [...item.rooms],
                    groups: [...item.groups]
                });
                return;
            }

            const current = merged.get(key);
            current.teachers = uniqueStrings([...current.teachers, ...item.teachers]);
            current.rooms = uniqueStrings([...current.rooms, ...item.rooms]);
            current.groups = uniqueStrings([...current.groups, ...item.groups]);
        });

        return Array.from(merged.values());
    }

    function displaySchedule(data, lessons, type, requestedValue, date) {
        const config = TYPE_CONFIG[type];
        const resolvedValue = stripMarkup(data?.[type] || requestedValue);

        elements.scheduleKind.textContent = config.resultLabel;
        elements.scheduleTitle.textContent = type === 'audience'
            ? `Аудитория ${normalizeRoom(resolvedValue)}`
            : resolvedValue;
        elements.scheduleDate.textContent = formatFullDate(date);
        elements.scheduleDate.dateTime = toLocalIso(date);
        elements.scheduleContent.replaceChildren();

        const fragment = document.createDocumentFragment();
        let combinedDividerAdded = false;

        lessons.forEach((lesson, index) => {
            if (lesson.combined && !combinedDividerAdded) {
                const divider = document.createElement('div');
                divider.className = 'combined-divider';
                divider.textContent = 'Совмещённые пары';
                divider.setAttribute('role', 'presentation');
                fragment.appendChild(divider);
                combinedDividerAdded = true;
            }

            const row = createLessonRow(lesson, type, date);
            row.style.animationDelay = `${Math.min(index, 6) * 45}ms`;
            fragment.appendChild(row);
        });

        elements.scheduleContent.appendChild(fragment);
        updateDownloadAction(config.downloadType, date);
        hideStatus();
        elements.results.hidden = false;
        document.body.classList.add('search-results-visible');

        window.setTimeout(() => {
            elements.results.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 80);
    }

    function createLessonRow(lesson, type, date) {
        const row = document.createElement('article');
        row.className = 'lesson-row';
        row.setAttribute('role', 'listitem');

        const marker = document.createElement('div');
        marker.className = 'pair-marker';

        const number = document.createElement('span');
        number.className = 'pair-number';
        number.textContent = String(lesson.pairNumber);

        const pairLabel = document.createElement('span');
        pairLabel.className = 'pair-label';
        pairLabel.textContent = 'пара';
        marker.append(number, pairLabel);

        const content = document.createElement('div');
        content.className = 'lesson-content';

        const subject = document.createElement('h3');
        subject.className = 'lesson-subject';
        subject.textContent = lesson.subject;
        content.appendChild(subject);

        const context = document.createElement('div');
        context.className = 'lesson-context';

        if (type === 'group') {
            appendPerson(context, lesson.teachers);
            appendAudience(context, lesson.rooms);
        } else if (type === 'teacher') {
            appendContextPill(context, lesson.groups);
            appendAudience(context, lesson.rooms);
        } else {
            appendContextPill(context, lesson.groups);
            appendPerson(context, lesson.teachers);
        }

        if (context.childElementCount > 0) {
            content.appendChild(context);
        }

        const time = document.createElement('time');
        time.className = 'lesson-time';
        time.textContent = getPairTime(lesson.pairNumber, date);
        content.appendChild(time);

        row.append(marker, content);
        return row;
    }

    function appendPerson(parent, values) {
        if (!values.length) return;
        const person = document.createElement('span');
        person.className = 'lesson-person';
        person.textContent = values.join(' / ');
        parent.appendChild(person);
    }

    function appendAudience(parent, values) {
        if (!values.length) return;
        const audience = document.createElement('span');
        audience.className = 'audience-pill';
        audience.textContent = `аудитория ${values.join(', ')}`;
        parent.appendChild(audience);
    }

    function appendContextPill(parent, values) {
        if (!values.length) return;
        const pill = document.createElement('span');
        pill.className = 'context-pill';
        pill.textContent = values.join(', ');
        parent.appendChild(pill);
    }

    function getPairTime(pairNumber, date) {
        const schedule = date.getDay() === 6 ? SATURDAY_TIMES : WEEKDAY_TIMES;
        return schedule[pairNumber] || 'Время уточняется';
    }

    function updateDownloadAction(downloadType, date) {
        if (!downloadType) {
            elements.resultActions.hidden = true;
            elements.download.removeAttribute('href');
            return;
        }

        const dateValue = encodeURIComponent(formatDateForApi(date));
        elements.download.href = `/api/schedule/download/${downloadType}/${dateValue}`;
        elements.resultActions.hidden = false;
    }

    function hideResults() {
        elements.results.hidden = true;
        document.body.classList.remove('search-results-visible');
    }

    function showStatus(message, isError = false) {
        elements.statusText.textContent = message;
        elements.status.classList.toggle('error', isError);
        elements.status.hidden = false;
        hideResults();
    }

    function hideStatus() {
        elements.status.hidden = true;
        elements.status.classList.remove('error');
    }

    function readRecentSearches() {
        try {
            const parsed = JSON.parse(localStorage.getItem(RECENT_SEARCHES_KEY) || '[]');
            if (!Array.isArray(parsed)) return [];

            return parsed
                .filter((item) => TYPE_CONFIG[item?.type] && typeof item.value === 'string' && item.value.trim())
                .slice(0, MAX_RECENT_SEARCHES);
        } catch (error) {
            return [];
        }
    }

    function saveRecentSearch(type, value) {
        const normalized = normalizeForSearch(value);
        const recent = readRecentSearches().filter((item) => (
            item.type !== type || normalizeForSearch(item.value) !== normalized
        ));

        recent.unshift({ type, value });
        localStorage.setItem(
            RECENT_SEARCHES_KEY,
            JSON.stringify(recent.slice(0, MAX_RECENT_SEARCHES))
        );
        renderRecentSearches();
    }

    function renderRecentSearches() {
        const recent = readRecentSearches();
        elements.recentList.replaceChildren();
        elements.recentSection.hidden = recent.length === 0;
        if (recent.length === 0) return;

        const fragment = document.createDocumentFragment();
        recent.forEach((item) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'recent-item';
            button.setAttribute('aria-label', `${TYPE_CONFIG[item.type].resultLabel}: ${item.value}`);

            const type = document.createElement('span');
            type.className = 'recent-item-type';
            type.textContent = TYPE_CONFIG[item.type].shortLabel;

            const value = document.createElement('span');
            value.className = 'recent-item-value';
            value.textContent = item.value;
            button.append(type, value);

            button.addEventListener('click', () => {
                setActiveType(item.type);
                setSearchValue(item.value);
                elements.searchInput.focus();
                haptic('soft');
            });
            fragment.appendChild(button);
        });

        elements.recentList.appendChild(fragment);
    }

    function stripMarkup(value) {
        const template = document.createElement('template');
        template.innerHTML = String(value ?? '');
        return (template.content.textContent || '')
            .replace(/\u00a0/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function normalizeForSearch(value) {
        return stripMarkup(value)
            .toLocaleLowerCase('ru-RU')
            .replace(/ё/g, 'е')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function findCanonicalValue(list, value) {
        const normalized = normalizeForSearch(value);
        return list.find((item) => normalizeForSearch(item) === normalized) || '';
    }

    function uniqueStrings(values) {
        const seen = new Set();
        return values
            .map(stripMarkup)
            .filter((value) => {
                if (!value || value === '-') return false;
                const normalized = normalizeForSearch(value);
                if (seen.has(normalized)) return false;
                seen.add(normalized);
                return true;
            });
    }

    function looksLikeRoom(value) {
        const normalized = stripMarkup(value).toLocaleLowerCase('ru-RU').trim();
        if (/^ауд(?:итория)?\.?\s*/u.test(normalized)) return true;
        if (/^\d{1,4}[а-я]?(?:\s*[,/]\s*\d{1,4}[а-я]?)*$/u.test(normalized)) return true;
        return /(акт\.?\s*зал|спорт(?:ивный)?\.?\s*зал|кабинет|лаборатория|мастерская)/u.test(normalized);
    }

    function normalizeRoom(value) {
        return stripMarkup(value)
            .replace(/^ауд(?:итория)?\.?\s*/iu, '')
            .trim();
    }

    function looksLikeTeacher(value) {
        const normalized = stripMarkup(value);
        return /^[А-ЯЁ][А-ЯЁа-яё'’-]+(?:\s+[А-ЯЁ][А-ЯЁа-яё'’-]+)*\s+[А-ЯЁ]\.?\s*[А-ЯЁ]?\.?$/u.test(normalized);
    }

    function haptic(style) {
        try {
            telegram?.HapticFeedback?.impactOccurred(style);
        } catch (error) {
            // Haptics are optional and must not affect the search flow.
        }
    }

    function hapticSuccess() {
        try {
            telegram?.HapticFeedback?.notificationOccurred('success');
        } catch (error) {
            // Haptics are optional and must not affect the search flow.
        }
    }

    function hapticError() {
        try {
            telegram?.HapticFeedback?.notificationOccurred('error');
        } catch (error) {
            // Haptics are optional and must not affect the search flow.
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
