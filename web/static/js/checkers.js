// === Telegram WebApp init ===
const tg = window.Telegram && Telegram.WebApp ? Telegram.WebApp : null;

// === DOM элементы ===
const lobby = document.getElementById('lobby');
const availableGamesList = document.getElementById('available-games-list');
const createGameBtn = document.getElementById('create-game-btn');
const waiting = document.getElementById('waiting');
const gameDiv = document.getElementById('game');
const boardDiv = document.getElementById('checkers-board');
const gameStatus = document.getElementById('game-status');
const exitBtn = document.getElementById('exit-btn');
const header = document.querySelector('.header');

let gameId = null;
let playerId = null;
let playerColor = null;
let pollInterval = null;
let gameState = null;

// === Механика ходов с подсветкой и взятием ===
let selectedCell = null;
let possibleMoves = [];

let colorModalShown = false;

let lobbyInterval = null;

// === Модальное окно результата ===
let resultModal = null;

// === Лобби ===
async function fetchAvailableGames() {
    try {
        const res = await fetch('/api/checkers/games');
        const data = await res.json();
        renderAvailableGames(data.games || {});
    } catch (e) {
        availableGamesList.innerHTML = '<div class="no-games-message">Ошибка загрузки игр</div>';
    }
}

function renderAvailableGames(games) {
    availableGamesList.innerHTML = '';
    const gameIds = Object.keys(games);
    if (!gameIds.length) {
        availableGamesList.innerHTML = '<div class="no-games-message">Нет доступных игр</div>';
        return;
    }
    // Сортируем по времени создания (от новых к старым)
    const sortedGames = Object.values(games).sort((a, b) => b.created_at - a.created_at);
    sortedGames.forEach(g => {
        const el = document.createElement('div');
        el.className = 'game-item';
        // Время создания
        const createdTime = new Date(g.created_at * 1000);
        const timeString = createdTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        // Имя создателя
        const creatorName = g.host_name ? `${g.host_name}` : 'Игра';
        // Инфо
        const info = document.createElement('div');
        info.className = 'game-info';
        info.innerHTML = `<div class="game-id">${creatorName}</div><div class="game-time">Создана: ${timeString}</div>`;
        // Кнопка
        const btn = document.createElement('button');
        btn.className = 'btn primary small';
        btn.textContent = 'Войти';
        btn.onclick = e => {
            e.stopPropagation();
            joinGame(g.id);
            if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
        };
        el.appendChild(info);
        el.appendChild(btn);
        el.onclick = () => {
            joinGame(g.id);
            if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
        };
        availableGamesList.appendChild(el);
    });
}

// === Создание игры ===
createGameBtn.onclick = async () => {
    createGameBtn.disabled = true;
    try {
        const user = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
        const res = await fetch('/api/checkers/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                userId: user ? user.id : Math.random().toString(36).slice(2),
                username: user ? user.username : '',
                first_name: user ? user.first_name : ''
            })
        });
        const data = await res.json();
        if (data.success) {
            gameId = data.gameId;
            playerId = data.playerId;
            playerColor = 'white';
            showWaiting();
            pollGameState();
        } else {
            alert(data.error || 'Ошибка создания игры');
        }
    } catch (e) {
        alert('Ошибка соединения с сервером');
    }
    createGameBtn.disabled = false;
};

// === Присоединение к игре ===
async function joinGame(id) {
    try {
        const user = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
        const res = await fetch('/api/checkers/join', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                gameId: id,
                userId: user ? user.id : Math.random().toString(36).slice(2)
            })
        });
        const data = await res.json();
        if (data.success) {
            gameId = data.gameId;
            playerId = data.playerId;
            playerColor = 'black';
            showGame();
            pollGameState();
        } else {
            alert(data.error || 'Ошибка присоединения');
        }
    } catch (e) {
        alert('Ошибка соединения с сервером');
    }
}

function startLobbyAutoUpdate() {
    if (lobbyInterval) clearInterval(lobbyInterval);
    lobbyInterval = setInterval(() => {
        if (lobby.style.display !== 'none') fetchAvailableGames();
    }, 5000);
}

function stopLobbyAutoUpdate() {
    if (lobbyInterval) clearInterval(lobbyInterval);
    lobbyInterval = null;
}

// === Ожидание соперника ===
function showWaiting() {
    stopLobbyAutoUpdate();
    lobby.style.display = 'none';
    waiting.style.display = '';
    gameDiv.style.display = 'none';
    Telegram.WebApp.BackButton.hide();
    if (header) header.classList.remove('no-border');
}

// === Показать игру ===
function showGame() {
    stopLobbyAutoUpdate();
    lobby.style.display = 'none';
    waiting.style.display = 'none';
    gameDiv.style.display = '';
    Telegram.WebApp.BackButton.hide();
    // Показываем модальное окно с цветом игрока (один раз)
    if (!colorModalShown && gameState && gameState.players && gameState.players[playerId]) {
        colorModalShown = true;
        const color = gameState.players[playerId].color;
        const modal = document.getElementById('colorModal');
        const icon = document.getElementById('colorModalIcon');
        const text = document.getElementById('colorModalText');
        icon.innerHTML = color === 'white' ? '⬜️' : '⬛️';
        text.textContent = `Вы играете за ${color === 'white' ? 'белых' : 'чёрных'}!`;
        modal.style.display = 'flex';
        document.getElementById('colorModalOk').onclick = () => {
            modal.style.display = 'none';
        };
    }
    if (header) header.classList.add('no-border');
}

function showLobby() {
    stopLobbyAutoUpdate();
    if (pollInterval) clearTimeout(pollInterval);
    pollInterval = null;
    gameId = null;
    playerId = null;
    playerColor = null;
    gameState = null;
    colorModalShown = false;
    lobby.style.display = '';
    waiting.style.display = 'none';
    gameDiv.style.display = 'none';
    fetchAvailableGames();
    startLobbyAutoUpdate();
    Telegram.WebApp.BackButton.show();
    if (header) header.classList.remove('no-border');
}

// === Периодический опрос состояния игры ===
function pollGameState() {
    if (pollInterval) clearTimeout(pollInterval);
    if (!gameId || !playerId) return;
    fetch(`/api/checkers/state/${gameId}/${playerId}`)
        .then(res => res.json())
        .then(data => {
            if (!data.success) {
                pollInterval = setTimeout(pollGameState, 1400); // если ошибка — чуть медленнее
                return;
            }
            gameState = data.gameState;
            if (gameState.status === 'waiting') {
                showWaiting();
            } else if (gameState.status === 'playing') {
                showGame();
                renderBoard(gameState.board);
                updateStatus();
            } else if (gameState.status === 'finished') {
                showGame();
                renderBoard(gameState.board);
                // Показываем модальное окно результата
                if (gameState.winner) {
                    showResultModal(gameState.winner === playerId);
                }
                gameStatus.innerHTML = gameState.winner === playerId ? 'Вы победили!' : 'Поражение или соперник вышел.';
            }
            pollInterval = setTimeout(pollGameState, 700); // ускоренный опрос
        })
        .catch(() => {
            pollInterval = setTimeout(pollGameState, 1400);
        });
}

function renderGameInfoBar() {
    if (!gameState || !gameState.players) return;
    const infoBar = document.getElementById('game-info-bar');
    if (!infoBar) return;
    const my = gameState.players[playerId];
    const oppId = Object.keys(gameState.players).find(id => id !== playerId);
    const opp = gameState.players[oppId];
    // Счёт
    let whiteCount = 0, blackCount = 0;
    for (let row = 0; row < 8; row++) for (let col = 0; col < 8; col++) {
        if (gameState.board[row][col] === 1 || gameState.board[row][col] === 3) whiteCount++;
        if (gameState.board[row][col] === 2 || gameState.board[row][col] === 4) blackCount++;
    }
    // Съедено шашек
    const whiteEaten = 12 - whiteCount;
    const blackEaten = 12 - blackCount;
    infoBar.innerHTML = `
        <span class="score-info">Вы съели: <b>${my.color === 'white' ? blackEaten : whiteEaten}</b> | Соперник съел: <b>${my.color === 'white' ? whiteEaten : blackEaten}</b></span>
    `;
}

// === Отрисовка доски ===
function renderBoard(board) {
    if (!board) return;
    renderGameInfoBar();
    boardDiv.innerHTML = '';
    // Определяем ориентацию: если чёрный — переворачиваем
    const myColor = gameState && gameState.players && gameState.players[playerId] ? gameState.players[playerId].color : 'white';
    const isBlack = myColor === 'black';
    // Для переворота: массивы индексов строк и колонок
    const rows = isBlack ? [...Array(8).keys()].reverse() : [...Array(8).keys()];
    const cols = isBlack ? [...Array(8).keys()].reverse() : [...Array(8).keys()];
    for (let i = 0; i < 8; i++) {
        for (let j = 0; j < 8; j++) {
            const row = rows[i];
            const col = cols[j];
            const cell = document.createElement('div');
            cell.className = 'cell ' + ((row + col) % 2 === 1 ? 'dark' : 'light');
            cell.dataset.row = row;
            cell.dataset.col = col;
            // Фигуры
            const v = board[row][col];
            if (v === 1) cell.innerHTML = '<div class="piece white"></div>';
            if (v === 2) cell.innerHTML = '<div class="piece black"></div>';
            if (v === 3) cell.innerHTML = '<div class="piece white king"></div>';
            if (v === 4) cell.innerHTML = '<div class="piece black king"></div>';
            // Подсветка выбранной шашки
            if (selectedCell && selectedCell[0] == row && selectedCell[1] == col) {
                cell.style.outline = '2px solid #FFD700';
                cell.style.zIndex = 2;
            }
            // Подсветка возможных ходов
            if (possibleMoves.some(m => m.to[0] === row && m.to[1] === col)) {
                cell.style.border = '2px solid #2ecc71';
                cell.style.boxShadow = '0 0 8px 2px #2ecc71cc';
                cell.style.cursor = 'pointer';
            }
            cell.onclick = () => onCellClick(row, col);
            boardDiv.appendChild(cell);
        }
    }
    // Обновляем статус под доской
    const turnStatus = document.getElementById('turn-status');
    if (turnStatus && gameState && gameState.players) {
        const isMyTurn = gameState.current_player === playerId;
        turnStatus.textContent = isMyTurn ? 'Ваш ход' : 'Ход соперника';
        turnStatus.style.color = isMyTurn ? '#229ED9' : '#e74c3c';
    }
}

function onCellClick(row, col) {
    if (!gameState || gameState.status !== 'playing') return;
    const board = gameState.board;
    const piece = board[row][col];
    const myColor = gameState.players[playerId].color;
    const isMyTurn = gameState.current_player === playerId;
    // Если клик по своей шашке в свой ход — выделяем и подсвечиваем ходы
    if (isMyTurn && ((myColor === 'white' && (piece === 1 || piece === 3)) || (myColor === 'black' && (piece === 2 || piece === 4)))) {
        selectedCell = [row, col];
        fetchPossibleMoves(row, col);
        if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
        return;
    }
    // Если выбрана шашка и клик по допустимой клетке — делаем ход
    if (selectedCell && isMyTurn && possibleMoves.some(m => m.to[0] === row && m.to[1] === col)) {
        const [fromRow, fromCol] = selectedCell;
        makeMove(fromRow, fromCol, row, col);
    }
}

async function fetchPossibleMoves(row, col) {
    possibleMoves = [];
    if (!gameId || !playerId) return renderBoard(gameState.board);
    try {
        const res = await fetch('/api/checkers/moves', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gameId, playerId, row, col })
        });
        const data = await res.json();
        if (data.success) {
            possibleMoves = data.moves || [];
        } else {
            possibleMoves = [];
        }
    } catch (e) {
        possibleMoves = [];
    }
    renderBoard(gameState.board);
}

async function makeMove(fromRow, fromCol, toRow, toCol) {
    if (!gameId || !playerId) return;
    try {
        const res = await fetch('/api/checkers/move', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                gameId,
                playerId,
                fromRow,
                fromCol,
                toRow,
                toCol
            })
        });
        const data = await res.json();
        if (data.success) {
            selectedCell = null;
            possibleMoves = [];
            gameState = data.gameState;
            renderBoard(gameState.board);
            updateStatus();
            // Вибрация: взятие — heavy, обычный ход — medium
            if (data.moreCapture) {
                if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('heavy');
                // Если есть ещё взятия, выделяем шашку автоматически
                selectedCell = [toRow, toCol];
                fetchPossibleMoves(toRow, toCol);
            } else {
                if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
            }
        } else {
            if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('rigid');
            alert(data.error || 'Ошибка хода');
        }
    } catch (e) {
        if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('rigid');
    }
}

function updateStatus() {
    if (!gameState) return;
    const color = gameState.players[playerId].color === 'white' ? 'белые' : 'чёрные';
    const turnColor = gameState.players[gameState.current_player].color === 'white' ? 'белых' : 'чёрных';
    gameStatus.innerHTML = `Вы играете за <b>${color}</b>. Сейчас ход <b>${turnColor}</b>.`;
}

// === Кнопка выхода ===
exitBtn.onclick = async () => {
    if (!gameId || !playerId) return;
    await fetch('/api/checkers/exit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gameId, playerId })
    });
    location.reload();
};

// === Модальное окно результата ===
function showResultModal(win) {
    if (!resultModal) {
        resultModal = document.createElement('div');
        resultModal.className = 'modal';
        resultModal.style.display = 'flex';
        resultModal.innerHTML = `<div class="modal-content"><div style="font-size:2.2em;margin-bottom:12px;">${win ? '🏆' : '😢'}</div><div style="font-size:1.2em;margin-bottom:18px;">${win ? 'Вы выиграли!' : 'Вы проиграли!'}</div><button class="btn primary" id="resultModalOk">В лобби</button></div>`;
        document.body.appendChild(resultModal);
    } else {
        resultModal.querySelector('.modal-content div:nth-child(1)').innerHTML = win ? '🏆' : '😢';
        resultModal.querySelector('.modal-content div:nth-child(2)').innerHTML = win ? 'Вы выиграли!' : 'Вы проиграли!';
        resultModal.style.display = 'flex';
    }
    document.getElementById('resultModalOk').onclick = () => {
        resultModal.style.display = 'none';
        if (tg && tg.BackButton) {
            tg.BackButton.hide();
        }
        showLobby();
    };
}

// === Кнопка отмены ожидания ===
const cancelWaitingBtn = document.getElementById('cancel-waiting');
if (cancelWaitingBtn) {
    cancelWaitingBtn.onclick = async () => {
        if (!gameId || !playerId) return;
        await fetch('/api/checkers/exit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gameId, playerId })
        });
        showLobby();
    };
}

// === Telegram BackButton ===
document.addEventListener('DOMContentLoaded', function() {
    if (window.Telegram && Telegram.WebApp) {
        Telegram.WebApp.BackButton.show();
        Telegram.WebApp.BackButton.onClick(function() {
            if (Telegram.WebApp.HapticFeedback) Telegram.WebApp.HapticFeedback.impactOccurred('medium');
            window.location.href = '/';
        });
    }
});

// === Инициализация ===
fetchAvailableGames();
startLobbyAutoUpdate(); 