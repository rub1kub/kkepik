// group_selector.js

// --- Константы и переменные ---
const tgWebAppData = window.tgWebAppData || (window.Telegram && Telegram.WebApp && Telegram.WebApp.initData) || '';
let favorites = [];
let currentEntity = null; // {entity_type, entity_id, entity_name}
let isDropdownOpen = false;
let userEntity = null; // Кэш для личной группы/ФИО пользователя

// --- DOM элементы ---
const groupSelectorContainer = document.getElementById('groupSelectorContainer');
const currentGroupName = document.getElementById('currentGroupName');
const groupQuickSwitch = document.getElementById('groupQuickSwitch');
const groupSelectorManage = document.getElementById('groupSelectorManage');
const groupSelectorArrow = document.getElementById('groupSelectorArrow');
const groupSelectorDropdown = document.getElementById('groupSelectorDropdown');

// --- Модальное окно для добавления ---
let addModal = null;
let addInput = null;
let addTypeSelect = null;
let addResults = null;
let addConfirmBtn = null;
let addCloseBtn = null;
let addError = null;
let searchTimeout = null;
let searchList = [];
let selectedSearchItem = null;

function telegramAuthHeaders() {
    return tgWebAppData ? { 'X-Telegram-Init-Data': tgWebAppData } : {};
}

function entityKey(entity) {
    return entity && entity.entity_type && entity.entity_id
        ? `${entity.entity_type}:${entity.entity_id}`
        : '';
}

function sameEntity(left, right) {
    return Boolean(entityKey(left) && entityKey(left) === entityKey(right));
}

function getTrackedEntities() {
    const tracked = [];
    const seen = new Set();
    [userEntity, ...favorites].forEach(entity => {
        const key = entityKey(entity);
        if (!key || seen.has(key)) return;
        seen.add(key);
        tracked.push(entity);
    });
    return tracked;
}

function selectEntity(entity) {
    if (!entityKey(entity)) return;
    currentEntity = entity;
    addToHistory(entity);
    renderCurrentEntity();
    closeDropdown();
    window.Telegram?.WebApp?.HapticFeedback?.impactOccurred?.('soft');
    window.updateScheduleForEntity?.(entity);
}

function applyBootstrapData(data) {
    if (!data) return;
    if (Array.isArray(data.favorites)) favorites = data.favorites;
    if (data.user && data.user.name_or_group) {
        userEntity = {
            entity_type: data.user.role === 'teacher' ? 'teacher' : 'group',
            entity_id: data.user.name_or_group,
            entity_name: data.user.name_or_group
        };
    }
}

document.addEventListener('kkepik:bootstrap-updated', function (event) {
    applyBootstrapData(event.detail);
    if (!currentEntity && userEntity) currentEntity = userEntity;
    renderCurrentEntity();
});

// --- Работа с историей выбранных сущностей ---
const HISTORY_KEY = 'groupSelectorHistory';
function getHistory() {
    try {
        const raw = localStorage.getItem(HISTORY_KEY);
        if (!raw) return [];
        return JSON.parse(raw);
    } catch (e) { return []; }
}
function saveHistory(history) {
    try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
    } catch (e) {}
}
function addToHistory(entity) {
    if (!entity || !entity.entity_id || !entity.entity_type) return;
    let history = getHistory();
    // Удаляем дубликаты
    history = history.filter(e => !(e.entity_id === entity.entity_id && e.entity_type === entity.entity_type));
    history.unshift({ entity_type: entity.entity_type, entity_id: entity.entity_id, entity_name: entity.entity_name });
    // Ограничим историю 10 элементами
    if (history.length > 10) history = history.slice(0, 10);
    saveHistory(history);
}
function removeFromHistory(entity) {
    if (!entity || !entity.entity_id || !entity.entity_type) return;
    let history = getHistory();
    history = history.filter(e => !(e.entity_id === entity.entity_id && e.entity_type === entity.entity_type));
    saveHistory(history);
}

// --- Инициализация ---
async function initGroupSelector() {
    if (window.kkepikApp) {
        try {
            applyBootstrapData(await window.kkepikApp.ready);
        } catch (e) {}
    } else {
        await loadFavorites();
        if (tgWebAppData) {
            try {
                const resp = await fetch('/api/me', { headers: telegramAuthHeaders() });
                const data = await resp.json();
                applyBootstrapData({ user: data });
            } catch (e) {}
        }
    }
    await setDefaultEntity();
    renderCurrentEntity();
    setupDropdown();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGroupSelector, { once: true });
} else {
    initGroupSelector();
}

// --- Загрузка избранных ---
async function loadFavorites() {
    try {
        const resp = await fetch(`/api/favorites?tgWebAppData=${encodeURIComponent(tgWebAppData)}`);
        const data = await resp.json();
        if (data.success) {
            favorites = data.favorites;
            // Выводим все избранные группы и ФИО пользователя в консоль
            if (Array.isArray(favorites)) {
                console.log('Избранные группы/ФИО пользователя:', favorites.map(f => `${f.entity_type}: ${f.entity_id} (${f.entity_name})`));
            } else {
                console.log('Избранные группы/ФИО пользователя: []');
            }
        } else {
            favorites = [];
            console.log('Избранные группы/ФИО пользователя: [] (ошибка ответа сервера)');
        }
    } catch (e) {
        favorites = [];
        console.log('Избранные группы/ФИО пользователя: [] (ошибка запроса)');
    }
}

// --- Установка дефолтной сущности ---
async function setDefaultEntity() {
    if (userEntity) {
        currentEntity = userEntity;
        return;
    }
    if (favorites.length > 0) {
        currentEntity = favorites[0];
        return;
    }
    currentEntity = { entity_type: 'group', entity_id: '', entity_name: 'Ваша группа/ФИО' };
}

// --- Рендер текущей сущности ---
function renderCurrentEntity() {
    if (
        currentEntity &&
        userEntity &&
        currentEntity.entity_id === userEntity.entity_id &&
        currentEntity.entity_type === userEntity.entity_type
    ) {
        currentGroupName.textContent = userEntity.entity_name || 'Ваша группа/ФИО';
    } else if (currentEntity) {
        currentGroupName.textContent = currentEntity.entity_name || 'Ваша группа/ФИО';
    } else if (userEntity) {
        currentGroupName.textContent = userEntity.entity_name || 'Ваша группа/ФИО';
    } else {
        currentGroupName.textContent = 'Ваша группа/ФИО';
    }
    renderEntitySwitcher();
}

function renderEntitySwitcher() {
    if (!groupQuickSwitch || !groupSelectorManage || !groupSelectorArrow) return;

    const tracked = getTrackedEntities();
    const useQuickSwitch = tracked.length === 2;
    const useDropdown = tracked.length > 2;

    currentGroupName.hidden = useQuickSwitch;
    groupQuickSwitch.hidden = !useQuickSwitch;
    groupSelectorArrow.hidden = !useDropdown;
    groupSelectorManage.hidden = useDropdown;
    groupSelectorManage.textContent = useQuickSwitch ? 'Изменить' : 'Добавить';
    groupSelectorContainer.classList.toggle('quick-mode', useQuickSwitch);
    groupSelectorContainer.classList.toggle('dropdown-mode', useDropdown);

    groupQuickSwitch.replaceChildren();
    if (!useQuickSwitch) return;

    tracked.forEach(entity => {
        const button = document.createElement('button');
        const active = sameEntity(entity, currentEntity);
        button.type = 'button';
        button.className = 'group-quick-button';
        button.textContent = entity.entity_name || entity.entity_id;
        button.dataset.entityKey = entityKey(entity);
        button.classList.toggle('active', active);
        button.setAttribute('role', 'tab');
        button.setAttribute('aria-selected', String(active));
        button.addEventListener('click', () => {
            if (!sameEntity(entity, currentEntity)) selectEntity(entity);
        });
        groupQuickSwitch.appendChild(button);
    });
}

// --- Выпадающий список ---
function setupDropdown() {
    groupSelectorArrow.addEventListener('click', toggleDropdown);
    currentGroupName.addEventListener('click', () => {
        if (getTrackedEntities().length > 2) toggleDropdown();
    });
    groupSelectorManage.addEventListener('click', () => {
        if (getTrackedEntities().length === 2) {
            openDropdown();
        } else {
            openAddModal();
        }
    });
    document.addEventListener('click', (e) => {
        if (!groupSelectorContainer.contains(e.target) && !groupSelectorDropdown.contains(e.target)) {
            closeDropdown();
        }
    });
}

function toggleDropdown() {
    if (getTrackedEntities().length <= 2) return;
    if (isDropdownOpen) {
        closeDropdown();
    } else {
        openDropdown();
    }
}

function openDropdown() {
    isDropdownOpen = true;
    groupSelectorDropdown.classList.add('open');
    groupSelectorArrow.classList.add('open');
    // Позиционирование
    const rect = groupSelectorContainer.getBoundingClientRect();
    groupSelectorDropdown.style.top = (rect.bottom + window.scrollY + 4) + 'px';
    groupSelectorDropdown.style.left = (rect.left + window.scrollX) + 'px';
    groupSelectorDropdown.style.minWidth = rect.width + 'px';
    renderDropdownContent();
}

function closeDropdown() {
    if (!isDropdownOpen) return;
    isDropdownOpen = false;
    groupSelectorDropdown.classList.remove('open');
    groupSelectorArrow.classList.remove('open');
    groupSelectorDropdown.classList.add('closing');
    groupSelectorDropdown.addEventListener('transitionend', function handler(e) {
        if (e.propertyName === 'opacity') {
            groupSelectorDropdown.classList.remove('closing');
            groupSelectorDropdown.removeEventListener('transitionend', handler);
        }
    });
}

async function getUserEntity() {
    // Теперь просто возвращаем кэшированное значение
    return userEntity;
}

async function renderDropdownContent() {
    groupSelectorDropdown.innerHTML = '';
    let favs = [...favorites];
    const userEntity = await getUserEntity();
    // Получаем историю
    let history = getHistory();
    // Исключаем личную группу/ФИО из истории
    if (userEntity) {
        history = history.filter(e => !(e.entity_id === userEntity.entity_id && e.entity_type === userEntity.entity_type));
    }
    // Исключаем дубликаты (оставляем только уникальные по entity_id+entity_type)
    const seen = new Set();
    history = history.filter(e => {
        const key = e.entity_type + ':' + e.entity_id;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
    // Рендерим личную группу/ФИО
    if (userEntity) {
        const item = document.createElement('div');
        item.className = 'dropdown-item';
        item.textContent = userEntity.entity_name;
        if (currentEntity && userEntity.entity_id === currentEntity.entity_id && userEntity.entity_type === currentEntity.entity_type) {
            // item.style.fontWeight = 'bold';
        }
        item.onclick = () => {
            selectEntity(userEntity);
        };
        groupSelectorDropdown.appendChild(item);
    }
    // Рендерим избранные (favorites) кроме userEntity
    favs.forEach(fav => {
        if (userEntity && fav.entity_id === userEntity.entity_id && fav.entity_type === userEntity.entity_type) return;
        const key = fav.entity_type + ':' + fav.entity_id;
        if (seen.has(key)) return; // не дублируем с историей
        seen.add(key);
        const item = document.createElement('div');
        item.className = 'dropdown-item';
        item.textContent = fav.entity_name;
        if (currentEntity && fav.entity_id === currentEntity.entity_id && fav.entity_type === currentEntity.entity_type) {
            // item.style.fontWeight = 'bold';
        }
        item.onclick = () => {
            selectEntity(fav);
        };
        // Кнопка удаления
        const delBtn = document.createElement('span');
        delBtn.textContent = '✕';
        delBtn.className = 'dropdown-del-btn';
        delBtn.title = 'Удалить из избранного';
        delBtn.onclick = (e) => {
            e.stopPropagation();
            removeFavorite(fav);
        };
        item.appendChild(delBtn);
        groupSelectorDropdown.appendChild(item);
    });
    // Рендерим историю (оставшиеся элементы)
    history.forEach(fav => {
        const item = document.createElement('div');
        item.className = 'dropdown-item';
        item.textContent = fav.entity_name;
        if (currentEntity && fav.entity_id === currentEntity.entity_id && fav.entity_type === currentEntity.entity_type) {
            // item.style.fontWeight = 'bold';
        }
        item.onclick = () => {
            selectEntity(fav);
        };
        // Кнопка удаления только если элемент есть в избранном
        const isFavorite = favorites.some(f => f.entity_id === fav.entity_id && f.entity_type === fav.entity_type);
        if (isFavorite) {
            const delBtn = document.createElement('span');
            delBtn.textContent = '✕';
            delBtn.className = 'dropdown-del-btn';
            delBtn.title = 'Удалить из избранного';
            delBtn.onclick = (e) => {
                e.stopPropagation();
                removeFavorite(fav);
            };
            item.appendChild(delBtn);
        }
        groupSelectorDropdown.appendChild(item);
    });
    // Кнопка добавить
    const addBtn = document.createElement('button');
    addBtn.textContent = 'Добавить';
    addBtn.className = 'dropdown-add-btn';
    addBtn.onclick = () => { closeDropdown(); openAddModal(); };
    groupSelectorDropdown.appendChild(addBtn);
}

// --- Добавление в избранное ---
async function addFavorite(entity) {
    try {
        const resp = await fetch('/api/favorites/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tgWebAppData,
                entity_type: entity.entity_type,
                entity_id: entity.entity_id,
                entity_name: entity.entity_name
            })
        });
        const data = await resp.json();
        if (data.success) {
            await loadFavorites();
            currentEntity = entity;
            addToHistory(entity);
            renderCurrentEntity();
            if (window.updateScheduleForEntity) {
                window.updateScheduleForEntity(entity);
            }
        } else {
            alert(data.error || 'Ошибка при добавлении');
        }
    } catch (e) {
        alert('Ошибка при добавлении');
    }
}

// --- Удаление из избранного ---
async function removeFavorite(entity) {
    if (!confirm('Удалить из избранного?')) return;
    try {
        const resp = await fetch('/api/favorites/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tgWebAppData,
                entity_type: entity.entity_type,
                entity_id: entity.entity_id
            })
        });
        const data = await resp.json();
        if (data.success) {
            await loadFavorites();
            removeFromHistory(entity);
            // Если удалили текущую — сбросить на личную группу/ФИО пользователя, если возможно
            if (currentEntity && entity.entity_id === currentEntity.entity_id && entity.entity_type === currentEntity.entity_type) {
                if (userEntity) {
                    currentEntity = userEntity;
                    addToHistory(currentEntity);
                    renderCurrentEntity();
                    if (window.updateScheduleForEntity) {
                        window.updateScheduleForEntity(currentEntity);
                    }
                } else if (favorites.length > 0) {
                    currentEntity = favorites[0];
                    addToHistory(currentEntity);
                    renderCurrentEntity();
                } else {
                    currentEntity = { entity_type: 'group', entity_id: '', entity_name: 'Ваша группа/ФИО' };
                    addToHistory(currentEntity);
                    renderCurrentEntity();
                }
            }
            renderCurrentEntity();
            renderDropdownContent();
        } else {
            alert(data.error || 'Ошибка при удалении');
        }
    } catch (e) {
        alert('Ошибка при удалении');
    }
}

// --- Модальное окно для добавления ---
function openAddModal() {
    if (!addModal) createAddModal();
    addModal.style.display = 'flex';
    addInput.value = '';
    addResults.innerHTML = '';
    addError.textContent = '';
    selectedSearchItem = null;
    addConfirmBtn.disabled = true;
    document.body.style.overflow = 'hidden'; // Запретить скролл
    // Фокус на инпут
    setTimeout(() => addInput.focus(), 100);
}

function closeAddModal() {
    if (!addModal) return;
    addModal.classList.add('closing');
    const modalContent = addModal.querySelector('.modal-content');
    if (modalContent) modalContent.classList.add('closing');
    document.body.style.overflow = ''; // Разрешить скролл
    addModal.addEventListener('animationend', function handler(e) {
        if (e.animationName === 'fadeOut') {
            addModal.style.display = 'none';
            addModal.classList.remove('closing');
            if (modalContent) modalContent.classList.remove('closing');
            addModal.removeEventListener('animationend', handler);
            // Прокручиваем страницу наверх после закрытия модального окна
            window.scrollTo(0, 0);
        }
    });
}

function createAddModal() {
    addModal = document.createElement('div');
    addModal.className = 'modal';
    addModal.style.display = 'none';
    addModal.innerHTML = `
        <div class="modal-content" style="max-width:400px;">
            <span class="close" id="closeAddModal">&times;</span>
            <h2>Добавить группу или преподавателя</h2>
            <div style="margin-bottom:10px;">
                <div class="select-wrapper" style="position:relative;">
                    <select id="addTypeSelect" class="addTypeSelect">
                        <option value="group">Группа</option>
                        <option value="teacher">Преподаватель</option>
                    </select>
                    <span class="select-arrow" style="position:absolute;right:16px;top:50%;transform:translateY(-50%) rotate(90deg);font-size:22px;color:var(--tg-theme-hint-color);pointer-events:none;">&#8250;</span>
                </div>
            </div>
            <div class="input-group" style="position:relative;">
                <input type="text" id="addInput" class="search-input" placeholder="Начните вводить...">
                <div class="autocomplete-dropdown" id="addAutocomplete"></div>
            </div>
            <div id="addError" style="color:red;margin-top:6px;"></div>
            <button id="addConfirmBtn" class="search-button" style="margin-top:12px;">Добавить в избранное</button>
        </div>
    `;
    document.body.appendChild(addModal);
    addInput = addModal.querySelector('#addInput');
    addTypeSelect = addModal.querySelector('#addTypeSelect');
    addResults = addModal.querySelector('#addAutocomplete');
    addConfirmBtn = addModal.querySelector('#addConfirmBtn');
    addCloseBtn = addModal.querySelector('#closeAddModal');
    addError = addModal.querySelector('#addError');

    // Клик по оверлею закрывает модалку
    addModal.addEventListener('mousedown', (e) => {
        if (e.target === addModal) closeAddModal();
    });
    addCloseBtn.onclick = closeAddModal;
    addConfirmBtn.onclick = () => {
        if (selectedSearchItem) {
            addFavorite(selectedSearchItem);
            closeAddModal();
        }
    };
    addInput.oninput = onAddInputChange;
    addTypeSelect.onchange = onAddInputChange;
}

function onAddInputChange() {
    const type = addTypeSelect.value;
    const query = addInput.value.trim();
    addResults.innerHTML = '';
    addResults.classList.remove('open');
    addConfirmBtn.disabled = true;
    selectedSearchItem = null;
    if (!query) return;
    if (searchTimeout) clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => searchEntities(type, query), 300);
}

async function searchEntities(type, query) {
    let url = type === 'group' ? '/api/groups' : '/api/teachers';
    try {
        const resp = await fetch(url);
        const data = await resp.json();
        let list = (type === 'group') ? (data.groups || []) : (data.teachers || []);
        // Фильтруем по подстроке
        list = list.filter(item => item.toLowerCase().includes(query.toLowerCase()));
        searchList = list;
        renderSearchResults(type, list, query);
    } catch (e) {
        addError.textContent = 'Ошибка загрузки списка';
    }
}

function renderSearchResults(type, list, query) {
    addResults.innerHTML = '';
    if (list.length === 0) {
        addResults.classList.remove('open');
        return;
    }
    addResults.classList.add('open');
    list.forEach(item => {
        const div = document.createElement('div');
        div.className = 'dropdown-item';
        div.textContent = item;
        div.onclick = () => {
            selectedSearchItem = {
                entity_type: type,
                entity_id: item, // для групп — название, для преподов — ФИО
                entity_name: item
            };
            addInput.value = item;
            addConfirmBtn.disabled = false;
            // Подсветка выбранного
            Array.from(addResults.children).forEach(child => child.classList.remove('selected'));
            div.classList.add('selected');
            addResults.classList.remove('open');
        };
        addResults.appendChild(div);
    });
}

// --- Стили для выпадающего и модального окна ---
// УДАЛЕНО: динамическое добавление <style> со стилями, теперь всё в main.css
