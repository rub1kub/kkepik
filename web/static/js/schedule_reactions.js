// Инициализация Telegram WebApp
let tg_test = window.Telegram.WebApp;
tg_test.expand();

// Получаем tgUserId из Telegram WebApp
let tgUserId = null;
if (tg_test && tg_test.initDataUnsafe && tg_test.initDataUnsafe.user) {
    tgUserId = tg_test.initDataUnsafe.user.id;
}

// Флаг для отслеживания первичной загрузки
let isFirstLoad = true;

let lastRequestId = 0;

// Глобальный флаг загрузки реакций
let reactionsLoaded = false;
let reactionPollTimer = null;
let fallbackReactions = [];
let reactionRequestInFlight = false;
let latestReactions = [];
let latestUserReactions = [];
let reactionFeedbackTimer = null;
const REACTION_RECENTS_KEY = `kkepik:reaction-recent:v1:${tgUserId || 'anonymous'}`;
const MAX_RECENT_REACTIONS = 6;

function uniqueReactionList(reactions) {
    return Array.from(new Set((Array.isArray(reactions) ? reactions : []).filter(Boolean)));
}

function readRecentReactions() {
    try {
        const parsed = JSON.parse(localStorage.getItem(REACTION_RECENTS_KEY) || '[]');
        return uniqueReactionList(parsed).slice(0, MAX_RECENT_REACTIONS);
    } catch (error) {
        return [];
    }
}

function rememberReaction(reaction) {
    try {
        const recent = readRecentReactions().filter(item => item !== reaction);
        recent.unshift(reaction);
        localStorage.setItem(REACTION_RECENTS_KEY, JSON.stringify(recent.slice(0, MAX_RECENT_REACTIONS)));
    } catch (error) {}
}

function formatLocalIsoDate(date) {
    return [
        date.getFullYear(),
        String(date.getMonth() + 1).padStart(2, '0'),
        String(date.getDate()).padStart(2, '0')
    ].join('-');
}

function parseLocalIsoDate(dateString) {
    const match = String(dateString || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return new Date(dateString);
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

// Функция для получения текущей даты из расписания
function getCurrentScheduleDate() {
    // Проверяем, есть ли элемент с заголовком расписания
    const scheduleTitle = document.querySelector('.schedule-title');
    if (!scheduleTitle) {
        // Если нет, возвращаем текущую дату
        return formatLocalIsoDate(new Date());
    }
    
    // Пытаемся извлечь дату из заголовка расписания
    const titleText = scheduleTitle.textContent;
    const dateMatch = titleText.match(/Расписание на (\d+) ([а-яА-Я]+)/);
    
    if (dateMatch) {
        const day = parseInt(dateMatch[1]);
        const monthName = dateMatch[2];
        
        // Преобразуем название месяца в номер
        const months = {
            'января': 0, 'февраля': 1, 'марта': 2, 'апреля': 3, 'мая': 4, 'июня': 5,
            'июля': 6, 'августа': 7, 'сентября': 8, 'октября': 9, 'ноября': 10, 'декабря': 11
        };
        
        const month = months[monthName.toLowerCase()];
        if (month !== undefined) {
            // Создаем дату с текущим годом
            const year = new Date().getFullYear();
            const date = new Date(year, month, day);
            return formatLocalIsoDate(date);
        }
    }
    
    // Если не удалось извлечь дату, возвращаем текущую дату
    return formatLocalIsoDate(new Date());
}

function getInitialScheduleDate() {
    // Если есть глобальный window.scheduleState.nextDate, используем его
    if (window.scheduleState && window.scheduleState.nextDate) {
        return window.scheduleState.nextDate;
    }
    if (window.scheduleState && window.scheduleState.displayedDate) {
        return formatLocalIsoDate(new Date(window.scheduleState.displayedDate));
    }
    // Фолбэк — сегодня
    return formatLocalIsoDate(new Date());
}

function setScheduleTitleToToday() {
    const months = [
        'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
        'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
    ];
    // Используем getInitialScheduleDate вместо new Date()
    const dateStr = getInitialScheduleDate();
    const now = parseLocalIsoDate(dateStr);
    const day = now.getDate();
    const month = months[now.getMonth()];
    const title = `Расписание на ${day} ${month}`;
    const titles = document.querySelectorAll('.schedule-title');
    titles.forEach((el, idx) => {
        if (idx === 0) {
            el.textContent = title;
            el.style.display = '';
        } else {
            el.style.display = 'none';
        }
    });
}

// Функция для инициализации реакций
function initScheduleReactions() {
    setScheduleTitleToToday(); // Показываем актуальную дату до загрузки
    const initialDate = getInitialScheduleDate();
    if (window.kkepikApp) {
        window.kkepikApp.ready.then(data => {
            if (data.reaction_date === initialDate) {
                updateReactionsUI(data.reactions || [], data.user_reactions || []);
            } else {
                loadReactions(initialDate);
            }
        }).catch(function () {});
    } else {
        loadReactions(initialDate);
    }

    armReactionPolling();

    // Добавляем обработчик события изменения даты в расписании
    document.addEventListener('scheduleDateChanged', (event) => {
        isFirstLoad = true;
        fallbackReactions = [];
        const date = event.detail && event.detail.date;
        const bootstrap = window.kkepikApp && window.kkepikApp.latest;
        if (bootstrap && bootstrap.reaction_date === date) {
            updateReactionsUI(bootstrap.reactions || [], bootstrap.user_reactions || []);
            return;
        }
        loadReactions(date);
    });

    document.addEventListener('kkepik:bootstrap-updated', event => {
        const data = event.detail;
        if (data && data.reaction_date === getCurrentScheduleDate()) {
            updateReactionsUI(data.reactions || [], data.user_reactions || []);
        }
    });

    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            clearTimeout(reactionPollTimer);
            reactionPollTimer = null;
        } else {
            loadReactions();
            armReactionPolling();
        }
    });
}

function validateTelegramData(initData) {
    return initData ? Promise.resolve(initData) : Promise.reject(new Error('initData отсутствует'));
}

function armReactionPolling() {
    clearTimeout(reactionPollTimer);
    if (document.hidden || (window.kkepikApp && window.kkepikApp.lowData)) return;
    reactionPollTimer = setTimeout(async function () {
        await loadReactions();
        armReactionPolling();
    }, 60000);
}

// Функция для загрузки реакций
async function loadReactions(dateOverride) {
    const currentDate = dateOverride || getCurrentScheduleDate();
    const initData = tg_test.initData;
    if (!initData) return;

    lastRequestId += 1;
    const requestId = lastRequestId;

    try {
        const response = await fetch(`/api/schedule/reactions?date=${currentDate}&tgWebAppData=${encodeURIComponent(initData)}`);
        const data = await response.json();
        if (requestId !== lastRequestId) return;
        if (data.success) {
            updateReactionsUI(data.reactions || [], data.user_reactions || []);
        } else {
            console.error('Ошибка при загрузке реакций:', data.error);
        }
    } catch (error) {
        if (requestId === lastRequestId) {
            console.error('Ошибка при загрузке реакций:', error);
        }
    }
}

// Функция для обновления UI с реакциями
function updateReactionsUI(reactions, userReactions) {
    const reactionsContainer = document.getElementById('schedule-reactions');
    if (!reactionsContainer) return;
    
    // Список популярных эмодзи для случайного выбора
    const defaultEmojis = [
        '👍', '👎', '❤️', '🔥', '😂', '😮', '😢', '😡', '🎉', '👏',
        '🙏', '👀', '🤔', '🤮', '💩', '👻', '👽', '🤖', '🤡', '🍑'
    ];
    const allowAnimatedImages = !(window.kkepikApp && window.kkepikApp.lowData);
    const customFallbacks = { 'AAA.webm': '✨', 'mirbi.gif': '🙂', 'smeshno.gif': '😂' };
    
    // Убедимся, что все реакции имеют числовое значение count
    const normalizedReactions = (Array.isArray(reactions) ? reactions : []).map(r => ({
        ...r,
        count: parseInt(r.count) || 0
    }));
    latestReactions = normalizedReactions.map(item => ({ ...item }));
    latestUserReactions = Array.isArray(userReactions) ? [...userReactions] : [];
    
    // Сортируем реакции по количеству (от большего к меньшему)
    const sortedReactions = [...normalizedReactions].sort((a, b) => b.count - a.count);
    
    // Выбранная пользователем реакция всегда остается на виду.
    const selectedReaction = latestUserReactions[0];
    const selectedReactionData = selectedReaction
        ? sortedReactions.find(item => item.reaction === selectedReaction)
        : null;
    const topReactions = selectedReactionData
        ? [selectedReactionData, ...sortedReactions.filter(item => item.reaction !== selectedReaction)].slice(0, 3)
        : sortedReactions.slice(0, 3);

    const fallbackCount = Math.max(0, 3 - topReactions.length);
    const usedReactions = new Set(topReactions.map(item => item.reaction));
    const fallbackCandidates = uniqueReactionList([
        ...readRecentReactions(),
        ...fallbackReactions,
        ...defaultEmojis
    ]);
    fallbackReactions = [];
    for (const emoji of fallbackCandidates) {
        if (fallbackReactions.length >= fallbackCount) break;
        if (!usedReactions.has(emoji)) {
            fallbackReactions.push(emoji);
        }
    }

    const desiredReactions = [
        ...topReactions.map(item => item.reaction),
        ...fallbackReactions
    ];
    
    // Проверяем, существует ли уже список реакций
    let reactionsList = reactionsContainer.querySelector('.reactions-list');

    const renderedReactions = reactionsList
        ? [...reactionsList.querySelectorAll('.reaction-item')].map(item => item.dataset.reaction)
        : [];
    const reactionSetChanged = renderedReactions.length !== desiredReactions.length
        || renderedReactions.some((reaction, index) => reaction !== desiredReactions[index]);

    if (isFirstLoad || reactionSetChanged) {
        reactionsContainer.innerHTML = '';
        reactionsList = null;
    }
    // Если списка реакций нет — создаём новый
    if (!reactionsList) {
        reactionsList = document.createElement('div');
        reactionsList.className = 'reactions-list';
        
        // Добавляем класс animate только при первичной загрузке
        if (isFirstLoad) {
            reactionsList.classList.add('animate');
            // Диспатчим событие для синхронизации анимации кнопки 'Скачать'
            setTimeout(() => {
                document.dispatchEvent(new Event('reactionsAnimated'));
            }, 0);
        }
        
        // Создаем массив для всех элементов реакций
        const reactionElements = [];
        
        // Добавляем существующие реакции (топ-3 по количеству)
        topReactions.forEach((reactionData, index) => {
            const reaction = reactionData.reaction;
            const count = reactionData.count;
            
            const reactionItem = document.createElement('button');
            reactionItem.type = 'button';
            reactionItem.className = 'reaction-item';
            reactionItem.dataset.reaction = reaction;
            reactionItem.setAttribute('aria-label', `Реакция ${reaction}, ${count}`);
            
            // Добавляем класс animate и задержку анимации только при первичной загрузке
            if (isFirstLoad) {
                reactionItem.classList.add('animate');
            }
            
            // Проверяем, выбрал ли пользователь эту реакцию
            const isSelected = userReactions && userReactions.includes(reaction);
            if (isSelected) {
                reactionItem.classList.add('selected');
            }
            reactionItem.setAttribute('aria-pressed', String(Boolean(isSelected)));
            
            // Создаем элемент с эмодзи или gif и счетчиком
            let reactionContent = '';
            if (reaction === 'AAA.webm' && allowAnimatedImages) {
                reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/AAA.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
            } else if (reaction === 'mirbi.gif' && allowAnimatedImages) {
                reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/mirbi.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
            } else if (reaction === 'smeshno.gif' && allowAnimatedImages) {
                reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/smeshno.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
            } else {
                const displayReaction = customFallbacks[reaction] || reaction;
                reactionContent = `<span class="reaction-emoji" style="font-size:16px;line-height:1;">${displayReaction}</span>`;
            }
            reactionItem.innerHTML = `
                ${reactionContent}
                <span class="reaction-count">${count > 0 ? count : ''}</span>
            `;
            
            // Добавляем обработчик клика
            reactionItem.addEventListener('click', () => {
                toggleReaction(reaction);
            });
            
            // Добавляем элемент в массив
            reactionElements.push(reactionItem);
        });
        
        // Дополняем список стабильными быстрыми реакциями.
        if (fallbackReactions.length > 0) {
            fallbackReactions.forEach(emoji => {
                const reactionItem = document.createElement('button');
                reactionItem.type = 'button';
                reactionItem.className = 'reaction-item';
                reactionItem.dataset.reaction = emoji;
                reactionItem.setAttribute('aria-label', `Поставить реакцию ${emoji}`);
                reactionItem.setAttribute('aria-pressed', 'false');
                if (isFirstLoad) {
                    reactionItem.classList.add('animate');
                }
                let reactionContent = '';
                if (emoji === 'AAA.webm' && allowAnimatedImages) {
                    reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/AAA.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
                } else if (emoji === 'mirbi.gif' && allowAnimatedImages) {
                    reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/mirbi.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
                } else if (emoji === 'smeshno.gif' && allowAnimatedImages) {
                    reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/smeshno.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
                } else {
                    const displayEmoji = customFallbacks[emoji] || emoji;
                    reactionContent = `<span class="reaction-emoji" style="font-size:16px;line-height:1;">${displayEmoji}</span>`;
                }
                reactionItem.innerHTML = `
                    ${reactionContent}
                    <span class="reaction-count"></span>
                `;
                reactionItem.addEventListener('click', () => {
                    toggleReaction(emoji);
                });
                reactionElements.push(reactionItem);
            });
        }
        
        // Добавляем кнопку для выбора других реакций
        const moreButton = document.createElement('button');
        moreButton.type = 'button';
        moreButton.className = 'reaction-more';
        moreButton.innerHTML = '<span>+</span>';
        moreButton.setAttribute('aria-label', 'Выбрать другую реакцию');
        moreButton.addEventListener('click', showReactionPicker);
        
        // Добавляем задержку анимации для кнопки "еще" только при первичной загрузке
        if (isFirstLoad) {
            moreButton.style.setProperty('--item-index', reactionElements.length);
        }
        
        // Добавляем все элементы в контейнер в правильном порядке
        reactionElements.forEach(element => {
            reactionsList.appendChild(element);
        });
        
        reactionsList.appendChild(moreButton);
        reactionsContainer.appendChild(reactionsList);
    } else {
        // Если список реакций уже существует, обновляем только содержимое
        // Получаем все элементы реакций
        const reactionItems = reactionsList.querySelectorAll('.reaction-item');
        
        // Обновляем существующие реакции
        reactionItems.forEach(item => {
            const emoji = item.dataset.reaction;
            
            // Находим количество этой реакции
            const reactionData = sortedReactions.find(r => r.reaction === emoji);
            const count = reactionData ? reactionData.count : 0;
            
            // Обновляем счетчик
            const countElement = item.querySelector('.reaction-count');
            if (countElement) {
                countElement.textContent = count > 0 ? count : '';
            }
            item.setAttribute('aria-label', count > 0
                ? `Реакция ${emoji}, ${count}`
                : `Поставить реакцию ${emoji}`);
            
            // Обновляем состояние выбранной реакции
            const isSelected = userReactions && userReactions.includes(emoji);
            if (isSelected) {
                item.classList.add('selected');
            } else {
                item.classList.remove('selected');
            }
            item.setAttribute('aria-pressed', String(Boolean(isSelected)));
        });
    }
    
    // Сбрасываем флаг первичной загрузки после первого обновления
    isFirstLoad = false;
    // После успешной загрузки реакций:
    reactionsLoaded = true;
    reactionsContainer.style.display = '';
}

function buildOptimisticReactions(reaction, wasSelected) {
    const counts = new Map(latestReactions.map(item => [item.reaction, item.count]));
    const previousReaction = latestUserReactions[0];

    if (wasSelected) {
        counts.set(reaction, Math.max(0, (counts.get(reaction) || 0) - 1));
    } else {
        if (previousReaction && previousReaction !== reaction) {
            counts.set(previousReaction, Math.max(0, (counts.get(previousReaction) || 0) - 1));
        }
        counts.set(reaction, (counts.get(reaction) || 0) + 1);
    }

    return [...counts.entries()]
        .filter(([, count]) => count > 0)
        .map(([itemReaction, count]) => ({ reaction: itemReaction, count }));
}

function setReactionControlsBusy(reaction, busy) {
    const container = document.getElementById('schedule-reactions');
    const list = container && container.querySelector('.reactions-list');
    if (!list) return;

    list.classList.toggle('is-busy', busy);
    list.setAttribute('aria-busy', String(busy));
    list.querySelectorAll('button').forEach(button => {
        button.disabled = busy;
        button.classList.toggle('is-pending', busy && button.dataset.reaction === reaction);
    });
}

function showReactionFeedback(message) {
    let feedback = document.querySelector('.reaction-feedback');
    if (!feedback) {
        feedback = document.createElement('div');
        feedback.className = 'reaction-feedback';
        feedback.setAttribute('role', 'status');
        feedback.setAttribute('aria-live', 'polite');
        document.body.appendChild(feedback);
    }

    clearTimeout(reactionFeedbackTimer);
    feedback.textContent = message;
    requestAnimationFrame(() => feedback.classList.add('show'));
    reactionFeedbackTimer = setTimeout(() => feedback.classList.remove('show'), 2200);
}

// Переключаем реакцию оптимистично, но не допускаем повторных запросов.
async function toggleReaction(reaction) {
    const currentDate = getCurrentScheduleDate();
    const initData = tg_test.initData;

    if (!initData) {
        showReactionFeedback('Откройте приложение из Telegram');
        return;
    }
    if (reactionRequestInFlight) return;
    reactionRequestInFlight = true;

    const previousReactions = latestReactions.map(item => ({ ...item }));
    const previousUserReactions = [...latestUserReactions];
    const wasSelected = previousUserReactions.includes(reaction);
    const optimisticReactions = buildOptimisticReactions(reaction, wasSelected);
    const optimisticUserReactions = wasSelected ? [] : [reaction];

    tg_test.HapticFeedback?.impactOccurred?.('soft');
    updateReactionsUI(optimisticReactions, optimisticUserReactions);
    setReactionControlsBusy(reaction, true);

    try {
        const validatedData = await validateTelegramData(initData);
        const response = await fetch('/api/schedule/reactions/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tgWebAppData: validatedData,
                date: currentDate,
                reaction
            })
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Сервер не принял реакцию');
        }

        if (data.action === 'added') {
            rememberReaction(reaction);
        }
        updateReactionsUI(data.reactions || [], data.user_reactions || []);
        if (data.action === 'added') {
            const target = [...document.querySelectorAll('.reaction-item')]
                .find(item => item.dataset.reaction === reaction);
            if (target) requestAnimationFrame(() => createConfetti(target));
        }
    } catch (error) {
        console.error('Ошибка при переключении реакции:', error);
        updateReactionsUI(previousReactions, previousUserReactions);
        showReactionFeedback('Не удалось поставить реакцию');
        tg_test.HapticFeedback?.notificationOccurred?.('error');
        loadReactions(currentDate);
    } finally {
        reactionRequestInFlight = false;
        setReactionControlsBusy(reaction, false);
    }
}

// Функция для создания эффекта конфетти
function createConfetti(element) {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    // Получаем позицию элемента
    const rect = element.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    
    // Получаем эмодзи или гифку из элемента
    const emojiElement = element.querySelector('.reaction-emoji') || element.querySelector('.emoji');
    let isGif = false;
    let gifSrc = '';
    let gifStyle = '';
    if (emojiElement && emojiElement.tagName === 'IMG') {
        isGif = true;
        gifSrc = emojiElement.src;
        gifStyle = emojiElement.getAttribute('style') || '';
    }
    const emoji = (!isGif && emojiElement) ? emojiElement.textContent : '❤️'; // запасной вариант

    // Небольшой локальный отклик не перекрывает расписание на телефоне.
    for (let i = 0; i < 8; i++) {
        const confetti = document.createElement('div');
        confetti.className = 'confetti';
        if (isGif) {
            // Вставляем гифку
            const img = document.createElement('img');
            img.src = gifSrc;
            // Определяем размер для конфетти: 12-16px для всех гифок
            img.width = 12 + Math.floor(Math.random() * 5); // 12-16px
            img.height = img.width;
            img.style.verticalAlign = 'middle';
            img.style.borderRadius = '6px';
            img.style.pointerEvents = 'none';
            if (gifStyle) img.setAttribute('style', gifStyle + ';vertical-align:middle;border-radius:6px;pointer-events:none;');
            confetti.appendChild(img);
        } else {
            // Вставляем эмодзи-текст
            confetti.textContent = emoji;
            // Случайный размер от 10px до 14px
            const size = 10 + Math.random() * 4;
            confetti.style.fontSize = `${size}px`;
        }
        // Случайное направление и расстояние
        const angle = Math.random() * Math.PI * 2;
        const distance = 15 + Math.random() * 25;
        const tx = Math.cos(angle) * distance;
        const ty = Math.sin(angle) * distance;
        const tr = Math.random() * 360 - 180;
        // Случайная задержка анимации
        const delay = Math.random() * 0.2;
        // Устанавливаем CSS-переменные для анимации
        confetti.style.setProperty('--tx', `${tx}px`);
        confetti.style.setProperty('--ty', `${ty}px`);
        confetti.style.setProperty('--tr', `${tr}deg`);
        // Позиционируем конфетти в центре элемента
        confetti.style.left = `${centerX}px`;
        confetti.style.top = `${centerY}px`;
        // Добавляем анимацию с задержкой
        confetti.style.animation = `confetti 0.8s ease-out ${delay}s forwards`;
        // Добавляем конфетти на страницу
        document.body.appendChild(confetti);
        // Удаляем конфетти после завершения анимации
        setTimeout(() => {
            if (confetti.parentNode) {
                confetti.parentNode.removeChild(confetti);
            }
        }, 800 + delay * 1000);
    }
}

// Функция для отображения пикера реакций
function showReactionPicker(event) {
    if (document.querySelector('.reaction-picker-modal')) return;

    const serverReactions = latestReactions.map(item => ({ ...item }));
    const userReactions = [...latestUserReactions];
    const defaultEmojis = [
        '👍', '👎', '❤️', '🔥', '😂', '😮', '😢', '😡', '🎉', '👏',
        '🙏', '👀', '🤔', '🤮', '💩', '👻', '👽', '🤖', '🤡', '🍑'
    ];
    const allowAnimatedImages = !(window.kkepikApp && window.kkepikApp.lowData);
    const customFallbacks = { 'AAA.webm': '✨', 'mirbi.gif': '🙂', 'smeshno.gif': '😂' };
    tg_test.HapticFeedback?.impactOccurred?.('soft');

    // Создаем модальное окно
    const modal = document.createElement('div');
    modal.className = 'reaction-picker-modal';
    // Создаем контейнер для пикера
    const picker = document.createElement('section');
    picker.className = 'reaction-picker';
    picker.setAttribute('role', 'dialog');
    picker.setAttribute('aria-modal', 'true');
    picker.setAttribute('aria-labelledby', 'reaction-picker-title');
    picker.tabIndex = -1;
    const handle = document.createElement('div');
    handle.className = 'reaction-picker-handle';
    handle.setAttribute('aria-hidden', 'true');
    // Добавляем заголовок
    const header = document.createElement('h2');
    header.className = 'reaction-picker-header';
    header.id = 'reaction-picker-title';
    header.textContent = 'Выберите реакцию';
    // Добавляем список эмодзи
    const emojiList = document.createElement('div');
    emojiList.className = 'emoji-list';
    // Формируем итоговый список эмодзи
    const serverEmojis = [...serverReactions]
        .sort((left, right) => right.count - left.count)
        .map(item => item.reaction);
    const allReactions = uniqueReactionList([
        ...userReactions,
        ...readRecentReactions(),
        ...serverEmojis,
        'AAA.webm',
        'mirbi.gif',
        'smeshno.gif',
        ...defaultEmojis
    ]);
    // Добавляем каждый эмодзи или gif
    allReactions.forEach(emoji => {
        const emojiItem = document.createElement('button');
        emojiItem.type = 'button';
        emojiItem.className = 'emoji-item animate';
        emojiItem.dataset.reaction = emoji;
        const isSelected = userReactions.includes(emoji);
        if (isSelected) {
            emojiItem.classList.add('selected');
        }
        emojiItem.setAttribute('aria-pressed', String(isSelected));
        const reactionData = serverReactions.find(r => r.reaction === emoji);
        const count = reactionData ? reactionData.count : 0;
        emojiItem.setAttribute('aria-label', count > 0
            ? `Реакция ${emoji}, ${count}`
            : `Поставить реакцию ${emoji}`);
        let emojiContent = '';
        if (emoji === 'AAA.webm' && allowAnimatedImages) {
            emojiContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/AAA.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
        } else if (emoji === 'mirbi.gif' && allowAnimatedImages) {
            emojiContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/mirbi.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
        } else if (emoji === 'smeshno.gif' && allowAnimatedImages) {
            emojiContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/smeshno.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
        } else {
            emojiContent = `<span class="emoji" style="font-size:20px;line-height:1;">${customFallbacks[emoji] || emoji}</span>`;
        }
        emojiItem.innerHTML = `
            ${emojiContent}
            ${count > 0 ? `<span class="count">${count}</span>` : ''}
        `;
        emojiItem.addEventListener('click', () => {
            toggleReaction(emoji);
            closeModal(modal);
        });
        emojiList.appendChild(emojiItem);
    });
    // Добавляем кнопку закрытия
    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'reaction-picker-close';
    closeButton.textContent = 'Отмена';
    closeButton.addEventListener('click', () => {
        closeModal(modal);
    });
    // Собираем пикер
    picker.appendChild(handle);
    picker.appendChild(header);
    picker.appendChild(emojiList);
    picker.appendChild(closeButton);
    // Добавляем пикер в модальное окно
    modal.appendChild(picker);
    // Добавляем модальное окно на страницу
    modal.__returnFocus = event?.currentTarget || document.activeElement;
    document.body.classList.add('reaction-picker-open');
    document.body.appendChild(modal);
    // Добавляем обработчик клика вне пикера для закрытия
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            closeModal(modal);
        }
    });
    modal.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeModal(modal);
    });
    requestAnimationFrame(() => picker.focus({ preventScroll: true }));
}

// Функция для закрытия модального окна с анимацией
function closeModal(modal) {
    if (!modal || modal.classList.contains('closing')) return;
    modal.classList.add('closing');
    document.body.classList.remove('reaction-picker-open');

    // Удаляем модальное окно после завершения анимации
    setTimeout(() => {
        const returnFocus = modal.__returnFocus;
        if (modal.parentNode) {
            modal.parentNode.removeChild(modal);
        }
        if (returnFocus && returnFocus.isConnected) returnFocus.focus({ preventScroll: true });
    }, 200);
}

// Функция для настройки кнопок реакций
function setupReactionButtons(date, initData) {
    // Эта функция будет вызываться при инициализации
    // и может быть использована для дополнительной настройки
}

// Функция для обновления только количества реакций без изменения случайных реакций
function updateReactionCounts(reactions, userReactions) {
    const reactionsContainer = document.getElementById('schedule-reactions');
    if (!reactionsContainer) return;
    
    // Получаем все элементы реакций
    const reactionItems = reactionsContainer.querySelectorAll('.reaction-item');
    
    // Обновляем количество для каждой реакции
    reactionItems.forEach(item => {
        const emoji = item.dataset.reaction;
        
        // Находим количество этой реакции
        const reactionData = reactions.find(r => r.reaction === emoji);
        const count = reactionData ? reactionData.count : 0;
        
        // Обновляем счетчик
        const countElement = item.querySelector('.reaction-count');
        if (countElement) {
            countElement.textContent = count > 0 ? count : '';
        }
        
        // Обновляем состояние выбранной реакции
        const isSelected = userReactions && userReactions.includes(emoji);
        if (isSelected) {
            item.classList.add('selected');
        } else {
            item.classList.remove('selected');
        }
    });
}

// Функция для определения iOS/macOS/Safari
function isIOSorMacSafari() {
    return (
        /iPad|iPhone|iPod|Macintosh/.test(navigator.userAgent) &&
        (
            /Safari/.test(navigator.userAgent) ||
            /AppleWebKit/.test(navigator.userAgent)
        )
    );
}

// Инициализация при загрузке страницы
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initScheduleReactions, { once: true });
} else {
    initScheduleReactions();
}
