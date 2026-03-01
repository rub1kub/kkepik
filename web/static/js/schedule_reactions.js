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

// Функция для получения текущей даты из расписания
function getCurrentScheduleDate() {
    // Проверяем, есть ли элемент с заголовком расписания
    const scheduleTitle = document.querySelector('.schedule-title');
    if (!scheduleTitle) {
        // Если нет, возвращаем текущую дату
        return new Date().toISOString().split('T')[0];
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
            
            // Форматируем дату в ISO строку (YYYY-MM-DD)
            return date.toISOString().split('T')[0];
        }
    }
    
    // Если не удалось извлечь дату, возвращаем текущую дату
    return new Date().toISOString().split('T')[0];
}

function getInitialScheduleDate() {
    // Если есть глобальный window.scheduleState.nextDate, используем его
    if (window.scheduleState && window.scheduleState.nextDate) {
        return window.scheduleState.nextDate;
    }
    // Фолбэк — сегодня
    return new Date().toISOString().split('T')[0];
}

function setScheduleTitleToToday() {
    const months = [
        'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
        'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
    ];
    // Используем getInitialScheduleDate вместо new Date()
    const dateStr = getInitialScheduleDate();
    const now = new Date(dateStr);
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
    // Загружаем реакции при инициализации с актуальной датой
    loadReactions(getInitialScheduleDate());
    
    // Обновляем реакции каждые 5 секунд
    setInterval(loadReactions, 5000);
    
    // Добавляем обработчик события изменения даты в расписании
    document.addEventListener('scheduleDateChanged', (event) => {
        // Сбрасываем флаг первичной загрузки при изменении даты
        isFirstLoad = true;
        // Загружаем реакции для новой даты
        loadReactions();
    });
}

// Функция для валидации данных Telegram
function validateTelegramData(initData) {
    return fetch('/validate', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            tgWebAppData: initData
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            return initData; // Возвращаем валидированные данные
        } else {
            throw new Error(data.error || 'Ошибка валидации данных');
        }
    });
}

// Функция для загрузки реакций
function loadReactions(dateOverride) {
    // Получаем текущую дату из расписания или используем переданную
    const currentDate = dateOverride || getCurrentScheduleDate();
    const initData = tg_test.initData;
    if (!initData) return;

    lastRequestId += 1;
    const requestId = lastRequestId;

    validateTelegramData(initData)
        .then(validatedData => {
            fetch(`/api/schedule/reactions?date=${currentDate}&tgWebAppData=${encodeURIComponent(validatedData)}`)
                .then(response => response.json())
                .then(data => {
                    if (requestId !== lastRequestId) return; // Не актуальный ответ
                    if (data.success) {
                        if (!data.reactions || data.reactions.length === 0) {
                            console.warn('Сервер вернул пустой массив реакций, делаем повторный запрос');
                            setTimeout(() => {
                                fetch(`/api/schedule/reactions?date=${currentDate}&tgWebAppData=${encodeURIComponent(validatedData)}`)
                                    .then(response => response.json())
                                    .then(retryData => {
                                        if (requestId !== lastRequestId) return;
                                        if (retryData.success) {
                                            if (retryData.reactions && retryData.reactions.length > 0) {
                                                updateReactionsUI(retryData.reactions, retryData.user_reactions);
                                            } else {
                                                console.warn('Повторный запрос вернул пустой массив реакций, показываем случайные реакции');
                                                updateReactionsUI([], retryData.user_reactions || []);
                                            }
                                        }
                                    });
                            }, 500);
                            return;
                        }
                        updateReactionsUI(data.reactions, data.user_reactions);
                        if (isFirstLoad && data.reactions.length < 3) {
                            setTimeout(() => {
                                fetch(`/api/schedule/reactions?date=${currentDate}&tgWebAppData=${encodeURIComponent(validatedData)}`)
                                    .then(response => response.json())
                                    .then(retryData => {
                                        if (requestId !== lastRequestId) return;
                                        if (retryData.success && retryData.reactions.length > data.reactions.length) {
                                            updateReactionsUI(retryData.reactions, retryData.user_reactions);
                                        }
                                    });
                            }, 1000);
                        }
                    } else {
                        console.error('Ошибка при загрузке реакций:', data.error);
                    }
                })
                .catch(error => {
                    if (requestId !== lastRequestId) return;
                    console.error('Ошибка при загрузке реакций:', error);
                });
        })
        .catch(error => {
            if (requestId !== lastRequestId) return;
            console.error('Ошибка валидации данных:', error);
        });
}

// Функция для обновления UI с реакциями
function updateReactionsUI(reactions, userReactions) {
    const reactionsContainer = document.getElementById('schedule-reactions');
    // Теперь всегда продолжаем выполнение, даже если массив реакций пустой
    
    // Список популярных эмодзи для случайного выбора
    const defaultEmojis = [
        '👍', '👎', '❤️', '🔥', '😂', '😮', '😢', '😡', '🎉', '👏',
        '🙏', '👀', '🤔', '🤮', '💩', '👻', '👽', '🤖', '🤡', '🍑'
    ];
    
    // Отладочный вывод для проверки входящих данных
    console.log('Входящие реакции:', reactions);
    
    // Убедимся, что все реакции имеют числовое значение count
    const normalizedReactions = reactions.map(r => ({
        ...r,
        count: parseInt(r.count) || 0
    }));
    
    // Сортируем реакции по количеству (от большего к меньшему)
    const sortedReactions = [...normalizedReactions].sort((a, b) => b.count - a.count);
    
    // Отладочный вывод для проверки сортировки
    console.log('Отсортированные реакции:', sortedReactions);
    
    // Берем только топ-3 реакции
    const topReactions = sortedReactions.slice(0, 3);
    
    // Отладочный вывод для проверки топ-3 реакций
    console.log('Топ-3 реакции:', topReactions);
    
    // Определяем, сколько случайных реакций нужно добавить
    let randomReactionsCount = 0;
    if (topReactions.length === 0) {
        randomReactionsCount = 3; // Если нет реакций, показываем 3 случайные
    } else if (topReactions.length === 1) {
        randomReactionsCount = 2; // Если есть 1 реакция, добавляем 2 случайные
    } else if (topReactions.length === 2) {
        randomReactionsCount = 1; // Если есть 2 реакции, добавляем 1 случайную
    }
    // Если есть 3 или более реакций, не добавляем случайные
    
    // Проверяем, существует ли уже список реакций
    let reactionsList = reactionsContainer.querySelector('.reactions-list');
    
    // Если это первичная загрузка (смена дня), всегда пересоздаём список реакций
    if (isFirstLoad) {
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
            
            const reactionItem = document.createElement('div');
            reactionItem.className = 'reaction-item';
            reactionItem.dataset.reaction = reaction;
            
            // Добавляем класс animate и задержку анимации только при первичной загрузке
            if (isFirstLoad) {
                reactionItem.classList.add('animate');
            }
            
            // Проверяем, выбрал ли пользователь эту реакцию
            const isSelected = userReactions && userReactions.includes(reaction);
            if (isSelected) {
                reactionItem.classList.add('selected');
            }
            
            // Создаем элемент с эмодзи или gif и счетчиком
            let reactionContent = '';
            if (reaction === 'AAA.webm') {
                reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/AAA.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
            } else if (reaction === 'mirbi.gif') {
                reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/mirbi.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
            } else if (reaction === 'smeshno.gif') {
                reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/smeshno.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
            } else {
                reactionContent = `<span class="reaction-emoji" style="font-size:16px;line-height:1;">${reaction}</span>`;
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
        
        // Добавляем случайные реакции, если нужно
        if (randomReactionsCount > 0) {
            // Фильтруем эмодзи, которые уже используются
            const usedEmojis = new Set(topReactions.map(r => r.reaction));
            const availableEmojis = defaultEmojis.filter(emoji => !usedEmojis.has(emoji));
            // Добавляем кастомную реакцию AAA.webm, если её нет среди топ-реакций
            if (!usedEmojis.has('AAA.webm')) {
                availableEmojis.unshift('AAA.webm');
            }
            if (!usedEmojis.has('mirbi.gif')) {
                availableEmojis.unshift('mirbi.gif');
            }
            if (!usedEmojis.has('smeshno.gif')) {
                availableEmojis.unshift('smeshno.gif');
            }
            // Перемешиваем массив доступных эмодзи
            const shuffledEmojis = [...availableEmojis].sort(() => Math.random() - 0.5);
            // Берем нужное количество случайных эмодзи
            const randomEmojis = shuffledEmojis.slice(0, randomReactionsCount);
            // Добавляем случайные реакции
            randomEmojis.forEach((emoji, index) => {
                const reactionItem = document.createElement('div');
                reactionItem.className = 'reaction-item';
                reactionItem.dataset.reaction = emoji;
                if (isFirstLoad) {
                    reactionItem.classList.add('animate');
                }
                let reactionContent = '';
                if (emoji === 'AAA.webm') {
                    reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/AAA.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
                } else if (emoji === 'mirbi.gif') {
                    reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/mirbi.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
                } else if (emoji === 'smeshno.gif') {
                    reactionContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/smeshno.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
                } else {
                    reactionContent = `<span class="reaction-emoji" style="font-size:16px;line-height:1;">${emoji}</span>`;
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
        const moreButton = document.createElement('div');
        moreButton.className = 'reaction-more';
        moreButton.innerHTML = '<span>+</span>';
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
            
            // Обновляем состояние выбранной реакции
            const isSelected = userReactions && userReactions.includes(emoji);
            if (isSelected) {
                item.classList.add('selected');
            } else {
                item.classList.remove('selected');
            }
        });
    }
    
    // Сбрасываем флаг первичной загрузки после первого обновления
    isFirstLoad = false;
    // После успешной загрузки реакций:
    reactionsLoaded = true;
    // Скрываем лоадер и показываем реакции
    const loader = document.getElementById('loader');
    if (loader) loader.classList.add('fade-out');
    setTimeout(() => { if (loader) loader.style.display = 'none'; }, 750);
    reactionsContainer.style.display = '';
}

// Функция для переключения реакции
function toggleReaction(reaction) {
    // Получаем текущую дату из расписания
    const currentDate = getCurrentScheduleDate();
    const initData = tg_test.initData;
    
    if (!initData) {
        console.error('initData не найден в Telegram WebApp');
        return;
    }
    
    // Добавляем тактильную обратную связь при клике
    if (tg_test.HapticFeedback) {
        tg_test.HapticFeedback.impactOccurred('soft');
    }
    
    // Находим элемент реакции, по которому кликнули
    const reactionItems = document.querySelectorAll('.reaction-item');
    let clickedItem = null;
    
    reactionItems.forEach(item => {
        if (item.dataset.reaction === reaction) {
            clickedItem = item;
        }
    });
    
    // Если нашли элемент, добавляем анимацию изменения цвета и подъема
    if (clickedItem) {
        // Добавляем класс для анимации изменения цвета
        clickedItem.classList.add('exploding');
        // Добавляем анимацию подъема
        clickedItem.classList.add('animate-up');
        // Создаем эффект конфетти
        createConfetti(clickedItem);
        // Удаляем классы анимации после завершения
        setTimeout(() => {
            clickedItem.classList.remove('exploding');
            clickedItem.classList.remove('animate-up');
        }, 350);
    }
    
    // Сначала валидируем данные
    validateTelegramData(initData)
        .then(validatedData => {
            // Отправляем запрос на сервер для переключения реакции
            // На сервере реализована логика, которая:
            // 1. Если реакция уже выбрана - удаляет её
            // 2. Если реакция не выбрана - удаляет все предыдущие реакции пользователя и добавляет новую
            // Таким образом, пользователь может иметь только одну активную реакцию
            fetch('/api/schedule/reactions/toggle', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    tgWebAppData: validatedData,
                    date: currentDate,
                    reaction: reaction
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // Обновляем UI с новыми данными о реакциях
                    updateReactionsUI(data.reactions, data.user_reactions);

                    // Если выбор был из модалки — запускаем конфетти на элементе из списка под расписанием
                    if (window.__pendingReactionConfetti) {
                        const reactionsContainer = document.getElementById('schedule-reactions');
                        let target = null;
                        if (reactionsContainer) {
                            target = reactionsContainer.querySelector(`.reaction-item[data-reaction="${window.__pendingReactionConfetti}"]`)
                                  || reactionsContainer.querySelector('.reaction-more')
                                  || reactionsContainer;
                        }
                        if (target) {
                            requestAnimationFrame(() => createConfetti(target));
                        }
                        window.__pendingReactionConfetti = null;
                    }
                } else {
                    console.error('Ошибка при переключении реакции:', data.error);
                }
            })
            .catch(error => {
                console.error('Ошибка при переключении реакции:', error);
            });
        })
        .catch(error => {
            console.error('Ошибка валидации данных:', error);
        });
}

// Функция для создания эффекта конфетти
function createConfetti(element) {
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

    // Создаем 15 частиц конфетти
    for (let i = 0; i < 15; i++) {
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
async function showReactionPicker() {
    // Получаем текущие реакции с сервера ДО создания пикера
    const currentDate = getCurrentScheduleDate();
    const initData = tg_test.initData;
    let serverReactions = [];
    let userReactions = [];
    const defaultEmojis = [
        '👍', '👎', '❤️', '🔥', '😂', '😮', '😢', '😡', '🎉', '👏',
        '🙏', '👀', '🤔', '🤮', '💩', '👻', '👽', '🤖', '🤡', '🍑'
    ];
    if (initData) {
        try {
            const validatedData = await validateTelegramData(initData);
            const response = await fetch(`/api/schedule/reactions?date=${currentDate}&tgWebAppData=${encodeURIComponent(validatedData)}`);
            const data = await response.json();
            if (data.success) {
                serverReactions = data.reactions;
                userReactions = data.user_reactions;
            }
        } catch (e) {
            // Ошибка — просто покажем дефолтные эмодзи
        }
    }
    // Создаем модальное окно
    const modal = document.createElement('div');
    modal.className = 'reaction-picker-modal';
    // Создаем контейнер для пикера
    const picker = document.createElement('div');
    picker.className = 'reaction-picker';
    // Добавляем заголовок
    const header = document.createElement('div');
    header.className = 'reaction-picker-header';
    header.textContent = 'Выберите реакцию';
    // Добавляем список эмодзи
    const emojiList = document.createElement('div');
    emojiList.className = 'emoji-list';
    // Формируем итоговый список эмодзи
    const serverEmojis = serverReactions.map(r => r.reaction);
    const allReactions = [...defaultEmojis];
    // Добавляем кастомную реакцию AAA.webm, если её нет
    if (!allReactions.includes('AAA.webm')) {
        allReactions.unshift('AAA.webm');
    }
    if (!allReactions.includes('mirbi.gif')) {
        allReactions.unshift('mirbi.gif');
    }
    if (!allReactions.includes('smeshno.gif')) {
        allReactions.unshift('smeshno.gif');
    }
    serverEmojis.forEach(reaction => {
        if (!allReactions.includes(reaction)) {
            allReactions.push(reaction);
        }
    });
    // Добавляем каждый эмодзи или gif
    allReactions.forEach((emoji, index) => {
        const emojiItem = document.createElement('div');
        emojiItem.className = 'emoji-item animate';
        emojiItem.dataset.reaction = emoji;
        if (userReactions && userReactions.includes(emoji)) {
            emojiItem.classList.add('selected');
        }
        const reactionData = serverReactions.find(r => r.reaction === emoji);
        const count = reactionData ? reactionData.count : 0;
        let emojiContent = '';
        if (emoji === 'AAA.webm') {
            emojiContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/AAA.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
        } else if (emoji === 'mirbi.gif') {
            emojiContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/mirbi.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
        } else if (emoji === 'smeshno.gif') {
            emojiContent = `<img class="reaction-gif reaction-emoji" src="/static/emoji/smeshno.gif" width="16" height="16" style="vertical-align:middle;border-radius:6px;">`;
        } else {
            emojiContent = `<span class="emoji" style="font-size:20px;line-height:1;">${emoji}</span>`;
        }
        emojiItem.innerHTML = `
            ${emojiContent}
            ${count > 0 ? `<span class="count">${count}</span>` : ''}
        `;
        emojiItem.addEventListener('click', () => {
            if (tg_test.HapticFeedback) {
                tg_test.HapticFeedback.impactOccurred('soft');
            }
            // Запускаем конфетти не здесь (в модалке), а после обновления
            // основного списка реакций под расписанием
            window.__pendingReactionConfetti = emoji;
            // Небольшая локальная подсветка клика
            emojiItem.classList.add('exploding');
            setTimeout(() => {
                emojiItem.classList.remove('exploding');
            }, 300);
            toggleReaction(emoji);
            closeModal(modal);
        });
        emojiList.appendChild(emojiItem);
    });
    // Добавляем кнопку закрытия
    const closeButton = document.createElement('div');
    closeButton.className = 'reaction-picker-close';
    closeButton.textContent = '✕';
    closeButton.addEventListener('click', () => {
        closeModal(modal);
    });
    // Собираем пикер
    picker.appendChild(header);
    picker.appendChild(emojiList);
    picker.appendChild(closeButton);
    // Добавляем пикер в модальное окно
    modal.appendChild(picker);
    // Добавляем модальное окно на страницу
    document.body.appendChild(modal);
    // Добавляем обработчик клика вне пикера для закрытия
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            closeModal(modal);
        }
    });
}

// Функция для закрытия модального окна с анимацией
function closeModal(modal) {
    const picker = modal.querySelector('.reaction-picker');
    modal.style.animation = 'fadeOut 0.2s ease forwards';
    picker.style.animation = 'scaleOut 0.2s ease forwards';
    
    // Удаляем модальное окно после завершения анимации
    setTimeout(() => {
        if (modal.parentNode) {
            modal.parentNode.removeChild(modal);
        }
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
document.addEventListener('DOMContentLoaded', initScheduleReactions); 