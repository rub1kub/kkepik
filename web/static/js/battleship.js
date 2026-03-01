let tg = window.Telegram.WebApp;
tg.expand();

tg.BackButton.show();
tg.BackButton.onClick(function() {

    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('medium');
    }

    if (gameState) {
        switch (gameState.status) {
            case 'playing':
            case 'placement':
                showConfirmExitModal();
                break;
            case 'waiting':

                window.location.href = '/';
                break;
            case 'finished':
                window.location.href = '/';
                break;
            default:
                window.location.href = '/';
        }
    } else {

        window.location.href = '/';
    }
});

function showConfirmExitModal() {
    const modal = document.getElementById('confirm-exit-modal');
    if (modal) {
        modal.style.display = 'flex';
        modal.classList.remove('hidden');
        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('medium');
        }
    } else {
        alert('Ошибка: не найдено модальное окно подтверждения выхода!');
    }
}

function hideConfirmExitModal() {
    const modal = document.getElementById('confirm-exit-modal');
    if (modal) {
        modal.style.display = 'none';

        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('light');
        }
    }
}

document.getElementById('cancel-exit').addEventListener('click', hideConfirmExitModal);
document.getElementById('confirm-exit').addEventListener('click', function() {
    exitGameAndNotify();
    hideConfirmExitModal();
});

if (tg && tg.colorScheme) {
    document.documentElement.setAttribute('data-theme', tg.colorScheme);
}

let gameId = null;
let playerId = null;
let isHost = false;
let gameState = null;
let selectedShip = null;
let shipOrientation = 'horizontal'; 
let playerBoard = null;
let opponentBoard = null;
let playerShips = [];
let opponentShips = [];
let playerHits = 0;
let opponentHits = 0;
let isPlayerTurn = false;
let gameOver = false;
let draggedShip = null;
let draggedShipCells = [];
let draggedShipStartRow = -1;
let draggedShipStartCol = -1;
let selectedShipOnBoard = null; 
let turnTimer = null; 
let turnTimeLeft = 120; 
let opponentTurnTimeLeft = 120; 
let opponentTimer = null; 
let shipsPlaced = {}; // Добавляем объявление переменной shipsPlaced

const BOARD_SIZE = 10;
const SHIP_TYPES = [
    { size: 4, count: 1 },
    { size: 3, count: 2 },
    { size: 2, count: 3 },
    { size: 1, count: 4 }
];

document.addEventListener('DOMContentLoaded', function() {
    console.log('[LOG] DOMContentLoaded: инициализация приложения');

    if (tg) {
        console.log('[LOG] DOMContentLoaded: Telegram WebApp доступен');
        tg.BackButton.show();
        tg.BackButton.onClick(function() {
            console.log('[LOG] DOMContentLoaded: нажата кнопка "Назад"');

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }

            if (gameState) {
                console.log('[LOG] DOMContentLoaded: текущий статус игры:', gameState.status);
                switch (gameState.status) {
                    case 'playing':
                    case 'placement':
                        console.log('[LOG] DOMContentLoaded: показываем модальное окно подтверждения выхода');
                        showConfirmExitModal();
                        break;
                    case 'waiting':
                        console.log('[LOG] DOMContentLoaded: возвращаемся в лобби');
                        window.location.href = '/';
                        break;
                    case 'finished':
                        console.log('[LOG] DOMContentLoaded: возвращаемся в лобби');
                        window.location.href = '/';
                        break;
                    default:
                        console.log('[LOG] DOMContentLoaded: возвращаемся в лобби');
                        window.location.href = '/';
                }
            } else {
                console.log('[LOG] DOMContentLoaded: нет состояния игры, возвращаемся в лобби');
                window.location.href = '/';
            }
        });
    } else {
        console.log('[LOG] DOMContentLoaded: Telegram WebApp недоступен');
    }

    const confirmExitModal = document.getElementById('confirm-exit-modal');
    const cancelExitBtn = document.getElementById('cancel-exit');
    const confirmExitBtn = document.getElementById('confirm-exit');

    if (cancelExitBtn) {
        console.log('[LOG] DOMContentLoaded: добавляем обработчик для кнопки отмены выхода');
        cancelExitBtn.addEventListener('click', function() {
            hideConfirmExitModal();
        });
    }

    if (confirmExitBtn) {
        console.log('[LOG] DOMContentLoaded: добавляем обработчик для кнопки подтверждения выхода');
        confirmExitBtn.addEventListener('click', function() {
            exitGameAndNotify();
            hideConfirmExitModal();
        });
    }

    console.log('[LOG] DOMContentLoaded: запускаем авторизацию');
    authorize();

    console.log('[LOG] DOMContentLoaded: инициализируем доски');
    initBoards();

    // Инициализируем обработчики событий
    console.log('[LOG] DOMContentLoaded: инициализируем обработчики событий');
    initEventListeners();
    
    // Загружаем список доступных игр при загрузке страницы
    console.log('[LOG] DOMContentLoaded: загружаем список доступных игр');
    fetchAvailableGames();
    
    // Устанавливаем интервал для автоматического обновления списка игр каждые 5 секунд
    console.log('[LOG] DOMContentLoaded: устанавливаем интервал обновления списка игр');
    setInterval(fetchAvailableGames, 5000);
    
    // Проверяем начальное состояние экранов
    console.log('[LOG] DOMContentLoaded: проверка начального состояния экранов:');
    console.log('[LOG] DOMContentLoaded: lobby display =', document.getElementById('lobby').style.display);
    console.log('[LOG] DOMContentLoaded: placement display =', document.getElementById('placement').style.display);
    console.log('[LOG] DOMContentLoaded: waiting display =', document.getElementById('waiting').style.display);
    console.log('[LOG] DOMContentLoaded: game display =', document.getElementById('game').style.display);
    console.log('[LOG] DOMContentLoaded: result display =', document.getElementById('result').style.display);
});

async function authorize() {
    console.log('[LOG] authorize: начало авторизации');
    const initData = tg.initData;
    if (!initData) {
        console.error('[LOG] authorize: нет данных инициализации Telegram WebApp');
        window.location.href = '/api/docs';
        return;
    }
    
    console.log('[LOG] authorize: отправка запроса на валидацию данных');
    try {
        const resp = await fetch('/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tgWebAppData: initData })
        });
        
        console.log('[LOG] authorize: получен ответ от сервера, статус:', resp.status);
        const data = await resp.json();
        console.log('[LOG] authorize: данные ответа:', data);
        
        if (data.success) {
            console.log('[LOG] authorize: авторизация успешна');
            document.getElementById('main-content').style.display = '';
            
            // Проверяем состояние экранов после авторизации
            console.log('[LOG] authorize: проверка состояния экранов после авторизации:');
            console.log('[LOG] authorize: lobby display =', document.getElementById('lobby').style.display);
            console.log('[LOG] authorize: placement display =', document.getElementById('placement').style.display);
            console.log('[LOG] authorize: waiting display =', document.getElementById('waiting').style.display);
            console.log('[LOG] authorize: game display =', document.getElementById('game').style.display);
            console.log('[LOG] authorize: result display =', document.getElementById('result').style.display);
        } else {
            console.error('[LOG] authorize: ошибка авторизации:', data.error || 'Неизвестная ошибка');
            showAuthError(data.error || 'Ошибка авторизации.');
        }
    } catch (e) {
        console.error('[LOG] authorize: исключение при авторизации:', e);
        showAuthError('Ошибка соединения с сервером.');
    }
}

function showAuthError(msg) {
    console.log('[LOG] showAuthError: отображение ошибки авторизации:', msg);
    document.getElementById('auth-error').textContent = msg;
    document.getElementById('auth-error').style.display = '';
    document.getElementById('main-content').style.display = 'none';
}

function initBoards() {
    console.log('[LOG] initBoards: начало инициализации игровых досок');
    
    // Инициализация массивов досок
    playerBoard = Array(BOARD_SIZE).fill().map(() => Array(BOARD_SIZE).fill(0));
    opponentBoard = Array(BOARD_SIZE).fill().map(() => Array(BOARD_SIZE).fill(0));
    console.log('[LOG] initBoards: созданы массивы досок размером', BOARD_SIZE, 'x', BOARD_SIZE);
    
    // Инициализация счетчика размещенных кораблей
    shipsPlaced = {};
    console.log('[LOG] initBoards: инициализирован счетчик размещенных кораблей');
    
    // Создание досок в DOM
    console.log('[LOG] initBoards: создание доски размещения');
    createBoard('placement-board', playerBoard);
    
    console.log('[LOG] initBoards: создание доски противника');
    createBoard('opponent-board', opponentBoard);
    
    // Добавление обработчиков событий
    console.log('[LOG] initBoards: добавление обработчиков событий для ячеек');
    addCellEventListeners();
    
    console.log('[LOG] initBoards: инициализация игровых досок завершена');
}

function createBoard(boardId, boardData) {
    console.log('[LOG] createBoard: начало создания доски', boardId);
    console.log('[LOG] createBoard: данные доски:', boardData);
    
    const boardElement = document.getElementById(boardId);
    if (!boardElement) {
        console.error('[LOG] createBoard: элемент не найден:', boardId);
        return;
    }
    console.log('[LOG] createBoard: найден элемент доски');
    
    // Очищаем доску
    boardElement.innerHTML = '';
    console.log('[LOG] createBoard: очищена доска');
    
    // Создаем ячейки
    let cellCount = 0;
    for (let row = 0; row < BOARD_SIZE; row++) {
        for (let col = 0; col < BOARD_SIZE; col++) {
            const cell = document.createElement('div');
            cell.className = 'cell';
            cell.dataset.row = row;
            cell.dataset.col = col;
            boardElement.appendChild(cell);
            cellCount++;
        }
    }
    console.log(`[LOG] createBoard: создано ${cellCount} ячеек для доски ${boardId}`);
    
    // Проверяем, что ячейки действительно созданы
    const cells = boardElement.querySelectorAll('.cell');
    console.log(`[LOG] createBoard: в контейнере #${boardId} теперь ${cells.length} клеток`);
    
    // Проверяем, что ячейки имеют правильные атрибуты
    if (cells.length > 0) {
        const firstCell = cells[0];
        console.log('[LOG] createBoard: первая ячейка имеет атрибуты:', {
            row: firstCell.dataset.row,
            col: firstCell.dataset.col,
            className: firstCell.className
        });
    }
}

function addCellEventListeners() {
    console.log('[LOG] addCellEventListeners: начало добавления обработчиков событий');
    
    // Добавление обработчиков для ячеек доски размещения
    const placementCells = document.querySelectorAll('#placement-board .cell');
    console.log('[LOG] addCellEventListeners: найдено ячеек на доске размещения:', placementCells.length);
    
    placementCells.forEach(cell => {
        const row = parseInt(cell.dataset.row);
        const col = parseInt(cell.dataset.col);
        
        cell.addEventListener('click', function() {
            console.log('[LOG] addCellEventListeners: клик по ячейке размещения', row, col);
            handlePlacementCellClick(row, col);
        });
        
        cell.addEventListener('mousedown', function(e) {
            if (this.classList.contains('ship')) {
                console.log('[LOG] addCellEventListeners: начало перетаскивания корабля с ячейки', row, col);
                startDragging(row, col, e);
            }
        });
        
        cell.addEventListener('touchstart', function(e) {
            if (this.classList.contains('ship')) {
                console.log('[LOG] addCellEventListeners: начало перетаскивания корабля с ячейки (тач)', row, col);
                startDragging(row, col, e);
            }
        });
    });
    
    // Добавление обработчиков для ячеек доски противника
    const opponentCells = document.querySelectorAll('#opponent-board .cell');
    console.log('[LOG] addCellEventListeners: найдено ячеек на доске противника:', opponentCells.length);
    
    opponentCells.forEach(cell => {
        const row = parseInt(cell.dataset.row);
        const col = parseInt(cell.dataset.col);
        
        cell.addEventListener('click', function() {
            console.log('[LOG] addCellEventListeners: клик по ячейке противника', row, col);
            handleGameCellClick(row, col);
        });
    });
    
    // Добавление глобальных обработчиков для перетаскивания
    console.log('[LOG] addCellEventListeners: добавление глобальных обработчиков для перетаскивания');
    document.addEventListener('mousemove', handleDrag);
    document.addEventListener('touchmove', handleDrag);
    document.addEventListener('mouseup', endDragging);
    document.addEventListener('touchend', endDragging);
    
    console.log('[LOG] addCellEventListeners: добавление обработчиков событий завершено');
}

function handlePlacementCellClick(row, col) {

    if (selectedShip !== null) {

        if (playerBoard[row][col] === 1) {
            selectShipOnBoard(row, col);
            return;
        }

        if (typeof shipsPlaced === 'undefined') {
            shipsPlaced = {};
        }

        if (!shipsPlaced[selectedShip]) {
            shipsPlaced[selectedShip] = 0;
        }

        const maxShips = SHIP_TYPES.find(type => type.size === selectedShip)?.count || 0;

        if (shipsPlaced[selectedShip] >= maxShips) {

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('heavy');
            }
            return;
        }

        placeShip(row, col, selectedShip, shipOrientation);
    } else if (playerBoard[row][col] === 1) {

        selectShipOnBoard(row, col);
    } else if (selectedShipOnBoard) {

        const { size, orientation } = selectedShipOnBoard;

        if (canPlaceShip(row, col, size, orientation)) {

            selectedShipOnBoard.cells.forEach(cell => {
                playerBoard[cell.row][cell.col] = 0;
                const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
                cellElement.classList.remove('ship', 'selected');
            });

            const newCells = [];
            for (let i = 0; i < size; i++) {
                const r = orientation === 'horizontal' ? row : row + i;
                const c = orientation === 'horizontal' ? col + i : col;

                playerBoard[r][c] = 1;
                const cell = document.querySelector(`#placement-board .cell[data-row="${r}"][data-col="${c}"]`);
                cell.classList.add('ship');
                cell.classList.add('selected');
                newCells.push({ row: r, col: c });
            }

            selectedShipOnBoard.cells = newCells;
            selectedShipOnBoard.startRow = row;
            selectedShipOnBoard.startCol = col;

            updateShipsList();

            updateAvailableShips();

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        } else {

            const cells = document.querySelectorAll('.cell.ship.selected');
            cells.forEach(cell => {
                cell.classList.add('invalid');
                setTimeout(() => {
                    cell.classList.remove('invalid');
                }, 500);
            });

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        }
    } else {

        if (selectedShipOnBoard) {
            selectedShipOnBoard.cells.forEach(cell => {
                const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
                cellElement.classList.remove('selected');
            });
            selectedShipOnBoard = null;
        }
    }
}

function handleGameCellClick(row, col) {

    if (isPlayerTurn && !gameOver) {
        makeMove(row, col);
    }
}

async function createGame() {
    try {
        const user = tg.initDataUnsafe.user;
        const response = await fetch('/api/battleship/create', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                userId: user.id,
                username: user.username,
                first_name: user.first_name
            })
        });

        const data = await response.json();

        if (data.success) {
            gameId = data.gameId;
            playerId = data.playerId;
            isHost = true;

            document.getElementById('lobby').style.display = 'none';
            document.getElementById('waiting').style.display = 'block';

            pollGameState();
            
            // Обновляем список доступных игр
            fetchAvailableGames();
        } else {
            alert('Ошибка при создании игры: ' + data.error);
        }
    } catch (error) {
        console.error('Ошибка при создании игры:', error);
        alert('Ошибка при создании игры. Попробуйте еще раз.');
    }
}

async function joinGame() {
    console.log('[LOG] joinGame вызвана');
    const gameIdInput = document.getElementById('game-id-input').value.trim().toUpperCase();
    console.log('[LOG] joinGame: введенный код игры:', gameIdInput);

    if (!gameIdInput) {
        console.log('[LOG] joinGame: код игры не введен');
        alert('Введите код игры');
        return;
    }

    try {
        const userId = tg.initDataUnsafe.user.id;
        console.log('[LOG] joinGame: userId =', userId);
        
        console.log('[LOG] joinGame: отправка запроса на присоединение к игре');
        const response = await fetch('/api/battleship/join', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                gameId: gameIdInput,
                userId
            })
        });

        console.log('[LOG] joinGame: получен ответ от сервера, статус:', response.status);
        const data = await response.json();
        console.log('[LOG] joinGame: данные ответа:', data);

        if (data.success) {
            console.log('[LOG] joinGame: успешное присоединение к игре');
            gameId = data.gameId;
            playerId = data.playerId;
            isHost = false;
            
            console.log('[LOG] joinGame: gameId =', gameId, 'playerId =', playerId, 'isHost =', isHost);

            console.log('[LOG] joinGame: скрываем лобби и показываем экран размещения кораблей');
            document.getElementById('lobby').style.display = 'none';
            safeShowPlacementScreen();
            
            // Проверяем, что экраны действительно изменились
            console.log('[LOG] joinGame: проверка видимости экранов:');
            console.log('[LOG] joinGame: lobby display =', document.getElementById('lobby').style.display);
            console.log('[LOG] joinGame: placement display =', document.getElementById('placement').style.display);
            console.log('[LOG] joinGame: waiting display =', document.getElementById('waiting').style.display);

            console.log('[LOG] joinGame: запускаем pollGameState');
            pollGameState();
        } else {
            console.error('[LOG] joinGame: ошибка при присоединении к игре:', data.error);
            alert('Ошибка при присоединении к игре: ' + data.error);
        }
    } catch (error) {
        console.error('[LOG] joinGame: исключение при присоединении к игре:', error);
        alert('Ошибка при присоединении к игре. Попробуйте еще раз.');
    }
}

function copyGameId() {
    const gameIdElement = document.getElementById('game-id-display');
    const gameId = gameIdElement.textContent;

    navigator.clipboard.writeText(gameId)
        .then(() => {
            const copyButton = document.getElementById('copy-game-id');
            const originalText = copyButton.textContent;
            copyButton.textContent = 'Скопировано!';

            setTimeout(() => {
                copyButton.textContent = originalText;
            }, 2000);
        })
        .catch(err => {
            console.error('Ошибка при копировании кода игры:', err);
        });
}

async function cancelWaiting() {
    try {
        const response = await fetch('/api/battleship/cancel', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                gameId,
                playerId
            })
        });

        const data = await response.json();

        if (data.success) {

            document.getElementById('waiting').style.display = 'none';
            document.getElementById('lobby').style.display = 'block';

            gameId = null;
            playerId = null;
            isHost = false;
        } else {
            alert('Ошибка при отмене игры: ' + data.error);
        }
    } catch (error) {
        console.error('Ошибка при отмене игры:', error);
        alert('Ошибка при отмене игры. Попробуйте еще раз.');
    }
}

async function pollGameState() {
    if (!gameId || !playerId) {
        console.log('[LOG] pollGameState: нет gameId или playerId, выход из функции');
        return;
    }
    
    console.log('[LOG] pollGameState: запрос состояния игры для gameId =', gameId, 'playerId =', playerId);
    try {
        const response = await fetch(`/api/battleship/state/${gameId}/${playerId}`);
        console.log('[LOG] pollGameState: получен ответ от сервера, статус:', response.status);
        const data = await response.json();
        console.log('[LOG] pollGameState: данные ответа:', data);
        
        if (data.success) {
            gameState = data.gameState;
            console.log('[LOG] pollGameState: получено состояние игры:', gameState);

            if (gameState.status === 'playing' && (!gameState.boards || Object.keys(gameState.boards).length === 0)) {
                console.warn('[LOG] pollGameState: отсутствуют данные о досках игроков');

                try {
                    const boardsResponse = await fetch(`/api/battleship/boards/${gameId}`);
                    const boardsData = await boardsResponse.json();
                    if (boardsData.success && boardsData.boards) {
                        gameState.boards = boardsData.boards;
                        console.log('[LOG] pollGameState: получены данные о досках игроков');
                    }
                } catch (error) {
                    console.error('[LOG] pollGameState: ошибка при получении данных о досках:', error);
                }
            }

            console.log('[LOG] pollGameState: обработка статуса игры:', gameState.status);
            switch (gameState.status) {
                case 'waiting':
                    console.log('[LOG] pollGameState: статус "waiting"');
                    if (!isHost) {
                        console.error('[LOG] pollGameState: ошибка - игра не найдена или уже началась');
                        alert('Ошибка: игра не найдена или уже началась');
                        document.getElementById('placement').style.display = 'none';
                        document.getElementById('lobby').style.display = 'block';
                        return;
                    }
                    break;
                case 'placement':
                    console.log('[LOG] pollGameState: статус "placement"');
                    console.log('[LOG] pollGameState: isHost =', isHost, 'playerId =', playerId);
                    console.log('[LOG] pollGameState: игрок готов?', gameState.players[playerId].ready);
                    
                    if (gameState.players[playerId].ready) {
                        console.log('[LOG] pollGameState: игрок готов, показываем экран ожидания');
                        document.getElementById('placement').style.display = 'none';
                        document.getElementById('waiting').style.display = 'block';
                    } else {
                        console.log('[LOG] pollGameState: игрок не готов, показываем экран размещения кораблей');
                        document.getElementById('lobby').style.display = 'none';
                        document.getElementById('waiting').style.display = 'none';
                        document.getElementById('placement').style.display = 'block';
                        
                        // Проверяем, что экраны действительно изменились
                        console.log('[LOG] pollGameState: проверка видимости экранов:');
                        console.log('[LOG] pollGameState: lobby display =', document.getElementById('lobby').style.display);
                        console.log('[LOG] pollGameState: placement display =', document.getElementById('placement').style.display);
                        console.log('[LOG] pollGameState: waiting display =', document.getElementById('waiting').style.display);
                    }
                    break;
                case 'playing':
                    console.log('[LOG] pollGameState: статус "playing"');
                    document.getElementById('placement').style.display = 'none';
                    document.getElementById('waiting').style.display = 'none';
                    document.getElementById('game').style.display = 'block';

                    if (!document.querySelector('#player-board .cell')) {
                        console.log('[LOG] Клетки на #player-board отсутствуют, вызываю createBoard');
                        createBoard('player-board', playerBoard);
                        createBoard('opponent-board', opponentBoard);
                        addCellEventListeners();

                        updatePlayerBoard();
                        updateOpponentBoard();
                    }
                    updateGameState();
                    break;
                case 'finished':
                    console.log('[LOG] pollGameState: статус "finished"');
                    document.getElementById('game').style.display = 'none';
                    document.getElementById('result').style.display = 'block';
                    showGameResult();
                    break;
            }

            if (gameState.status === 'playing') {
                const opponentId = Object.keys(gameState.players).find(id => id !== playerId);
                if (opponentId && gameState.players[opponentId].exit) {
                    console.log('[LOG] Противник вышел из игры, показываем экран победы');
                    gameState.status = 'finished';
                    gameState.winner = playerId;
                    gameState.exit_reason = 'opponent_exit';
                    document.getElementById('game').style.display = 'none';
                    document.getElementById('result').style.display = 'block';
                    showGameResult();
                }
            }

            if (gameState.status !== 'finished') {
                console.log('[LOG] pollGameState: планируем следующий запрос через 2 секунды');
                setTimeout(pollGameState, 2000);
            }
        } else {
            console.error('[LOG] pollGameState: ошибка при получении состояния игры:', data.error);
        }
    } catch (error) {
        console.error('[LOG] pollGameState: ошибка при опросе состояния игры:', error);
    }
}

function selectShip(shipSize) {

    if (selectedShip === null && document.querySelector('.cell.ship.selected')) {
        document.querySelectorAll('.cell.ship.selected').forEach(cell => {
            cell.classList.remove('selected');
        });
        selectedShipOnBoard = null;
        return;
    }

    selectedShip = shipSize;
    selectedShipOnBoard = null;

    document.querySelectorAll('.ship-item').forEach(item => {
        item.classList.remove('selected-ship');
    });

    const selected = document.querySelector('.ship-item[data-ship="' + shipSize + '"]');
    if (selected) selected.classList.add('selected-ship');

    document.querySelectorAll('.cell.ship.selected').forEach(cell => {
        cell.classList.remove('selected');
    });

    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('medium');
    }
}

function selectShipOnBoard(row, col) {

    if (selectedShipOnBoard) {

        const isSameShip = selectedShipOnBoard.cells.some(cell => cell.row === row && cell.col === col);

        selectedShipOnBoard.cells.forEach(cell => {
            const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
            cellElement.classList.remove('selected');
        });

        if (isSameShip) {
            selectedShipOnBoard = null;

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('light');
            }
            return;
        }
    }

    let orientation = 'horizontal';
    let size = 1;

    let horizontalSize = 1;
    let leftCol = col - 1;
    while (leftCol >= 0 && playerBoard[row][leftCol] === 1) {
        horizontalSize++;
        leftCol--;
    }

    let rightCol = col + 1;
    while (rightCol < BOARD_SIZE && playerBoard[row][rightCol] === 1) {
        horizontalSize++;
        rightCol++;
    }

    let verticalSize = 1;
    let topRow = row - 1;
    while (topRow >= 0 && playerBoard[topRow][col] === 1) {
        verticalSize++;
        topRow--;
    }

    let bottomRow = row + 1;
    while (bottomRow < BOARD_SIZE && playerBoard[bottomRow][col] === 1) {
        verticalSize++;
        bottomRow++;
    }

    if (horizontalSize > verticalSize) {
        orientation = 'horizontal';
        size = horizontalSize;
    } else {
        orientation = 'vertical';
        size = verticalSize;
    }

    let startRow = row;
    let startCol = col;

    if (orientation === 'horizontal') {
        startCol = leftCol + 1;
    } else {
        startRow = topRow + 1;
    }

    const cells = [];
    for (let i = 0; i < size; i++) {
        const r = orientation === 'horizontal' ? startRow : startRow + i;
        const c = orientation === 'horizontal' ? startCol + i : startCol;
        cells.push({ row: r, col: c });
    }

    cells.forEach(cell => {
        const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
        cellElement.classList.add('selected');
    });

    selectedShipOnBoard = {
        size,
        orientation,
        startRow,
        startCol,
        cells
    };

    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('light');
    }
}

function rotateShip() {
    console.log('rotateShip вызвана');

    if (selectedShipOnBoard) {
        console.log('Выбран корабль на поле:', selectedShipOnBoard);
        const { size, orientation, startRow, startCol } = selectedShipOnBoard;

        let newOrientation;
        let newRow = startRow;
        let newCol = startCol;

        if (orientation === 'horizontal') {

            newOrientation = 'vertical';
        } else {

            newOrientation = 'horizontal';

            newCol = startCol;
        }

        console.log('Текущая ориентация:', orientation);
        console.log('Новая ориентация:', newOrientation);
        console.log('Позиция:', startRow, startCol);
        console.log('Размер:', size);

        let canPlace = canPlaceShip(newRow, newCol, size, newOrientation);
        console.log('Можно ли повернуть корабль в текущей позиции:', canPlace);

        if (!canPlace) {

            if (newOrientation === 'vertical' && newRow + size > BOARD_SIZE) {

                newRow = BOARD_SIZE - size;
                console.log('Пробуем сдвинуть корабль вверх:', newRow, newCol);
                canPlace = canPlaceShip(newRow, newCol, size, newOrientation);
            } else if (newOrientation === 'horizontal' && newCol + size > BOARD_SIZE) {

                newCol = BOARD_SIZE - size;
                console.log('Пробуем сдвинуть корабль влево:', newRow, newCol);
                canPlace = canPlaceShip(newRow, newCol, size, newOrientation);
            }
        }

        if (canPlace) {
            console.log('Начинаем поворот корабля');

            selectedShipOnBoard.cells.forEach(cell => {
                const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
                if (cellElement) {
                    cellElement.classList.add('rotating');
                }
            });

            setTimeout(() => {

                console.log('Удаляем корабль со старой позиции');
                selectedShipOnBoard.cells.forEach(cell => {
                    console.log('Удаляем клетку:', cell.row, cell.col);
                    playerBoard[cell.row][cell.col] = 0;
                    const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
                    if (cellElement) {
                        cellElement.classList.remove('ship', 'selected', 'rotating');
                        console.log('Классы удалены с клетки:', cell.row, cell.col);
                    } else {
                        console.error('Клетка не найдена:', cell.row, cell.col);
                    }
                });

                console.log('Размещаем корабль в новой ориентации');
                const newCells = [];
                for (let i = 0; i < size; i++) {
                    const r = newOrientation === 'horizontal' ? newRow : newRow + i;
                    const c = newOrientation === 'horizontal' ? newCol + i : newCol;

                    console.log('Добавляем клетку:', r, c);
                    playerBoard[r][c] = 1;
                    const cell = document.querySelector(`#placement-board .cell[data-row="${r}"][data-col="${c}"]`);
                    if (cell) {
                        cell.classList.add('ship', 'selected', 'rotated');
                        console.log('Классы добавлены к клетке:', r, c);
                    } else {
                        console.error('Клетка не найдена:', r, c);
                    }
                    newCells.push({ row: r, col: c });
                }

                setTimeout(() => {
                    newCells.forEach(cell => {
                        const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
                        if (cellElement) {
                            cellElement.classList.remove('rotated');
                        }
                    });
                }, 500);

                selectedShipOnBoard.orientation = newOrientation;
                selectedShipOnBoard.cells = newCells;
                selectedShipOnBoard.startRow = newRow;
                selectedShipOnBoard.startCol = newCol;

                console.log('Корабль повернут, обновляем информацию:', selectedShipOnBoard);

                updateShipsList();

                updateAvailableShips();

                console.log('Проверка после поворота:');
                const shipCells = document.querySelectorAll('#placement-board .cell.ship');
                console.log('Количество клеток с классом ship:', shipCells.length);
                shipCells.forEach(cell => {
                    console.log('Клетка с классом ship:', cell.dataset.row, cell.dataset.col);
                });

                if (tg && tg.HapticFeedback) {
                    tg.HapticFeedback.impactOccurred('medium');
                }
            }, 300);
        } else {
            console.log('Нельзя повернуть корабль, показываем анимацию тряски');

            const cells = document.querySelectorAll('.cell.ship.selected');
            cells.forEach(cell => {
                cell.classList.add('invalid');
                setTimeout(() => {
                    cell.classList.remove('invalid');
                }, 500);
            });

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        }
    } else if (selectedShip !== null) {
        console.log('Выбран корабль из панели:', selectedShip);

        shipOrientation = shipOrientation === 'horizontal' ? 'vertical' : 'horizontal';
        console.log('Новая ориентация корабля из панели:', shipOrientation);

        updatePlacementPreview();

        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('light');
        }
    } else {
        console.log('Нет выбранного корабля');
    }
}

function updatePlacementPreview() {
    console.log('updatePlacementPreview вызвана');

    if (!selectedShip) {
        console.log('Нет выбранного корабля, выходим из функции');
        return;
    }

    console.log('Выбранный корабль:', selectedShip);
    console.log('Ориентация корабля:', shipOrientation);

    document.querySelectorAll('.cell.preview').forEach(cell => {
        cell.classList.remove('preview');
    });

    const cells = document.querySelectorAll('#placement-board .cell');
    console.log('Найдено клеток на доске:', cells.length);

    cells.forEach(cell => {
        cell.addEventListener('mouseover', function() {
            if (!selectedShip) return;

            const row = parseInt(this.dataset.row);
            const col = parseInt(this.dataset.col);

            console.log('Наведение на клетку:', row, col);

            const canPlace = canPlaceShip(row, col, selectedShip, shipOrientation);
            console.log('Можно ли разместить корабль:', canPlace);

            document.querySelectorAll('.cell.preview').forEach(c => {
                c.classList.remove('preview');
                c.classList.remove('invalid');
            });

            if (canPlace) {
                for (let i = 0; i < selectedShip; i++) {
                    const r = shipOrientation === 'horizontal' ? row : row + i;
                    const c = shipOrientation === 'horizontal' ? col + i : col;

                    if (r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE) {
                        const cell = document.querySelector(`#placement-board .cell[data-row="${r}"][data-col="${c}"]`);
                        if (cell) {
                            cell.classList.add('preview');
                            console.log('Добавлен класс preview к клетке:', r, c);
                        } else {
                            console.error('Клетка не найдена:', r, c);
                        }
                    }
                }
            } else {
                for (let i = 0; i < selectedShip; i++) {
                    const r = shipOrientation === 'horizontal' ? row : row + i;
                    const c = shipOrientation === 'horizontal' ? col + i : col;

                    if (r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE) {
                        const cell = document.querySelector(`#placement-board .cell[data-row="${r}"][data-col="${c}"]`);
                        if (cell) {
                            cell.classList.add('preview');
                            cell.classList.add('invalid');
                            console.log('Добавлены классы preview и invalid к клетке:', r, c);
                        } else {
                            console.error('Клетка не найдена:', r, c);
                        }
                    }
                }
            }
        });

        cell.addEventListener('mouseout', function() {

            document.querySelectorAll('.cell.preview').forEach(c => {
                c.classList.remove('preview');
                c.classList.remove('invalid');
            });
        });
    });
}

function canPlaceShip(row, col, shipSize, orientation) {
    console.log('canPlaceShip вызвана:', { row, col, shipSize, orientation });

    if (orientation === 'horizontal' && col + shipSize > BOARD_SIZE) {
        console.log('Корабль не помещается по горизонтали');
        return false;
    }

    if (orientation === 'vertical' && row + shipSize > BOARD_SIZE) {
        console.log('Корабль не помещается по вертикали');
        return false;
    }

    const shipCells = [];
    for (let i = 0; i < shipSize; i++) {
        const r = orientation === 'horizontal' ? row : row + i;
        const c = orientation === 'horizontal' ? col + i : col;
        shipCells.push({row: r, col: c});
    }

    console.log('Клетки, которые будет занимать корабль:', shipCells);

    for (let i = 0; i < shipSize; i++) {
        const r = orientation === 'horizontal' ? row : row + i;
        const c = orientation === 'horizontal' ? col + i : col;

        if (playerBoard[r][c] !== 0) {

            if (selectedShipOnBoard && selectedShipOnBoard.cells.some(cell => cell.row === r && cell.col === c)) {
                console.log('Пропускаем проверку для клетки текущего корабля:', r, c);
                continue;
            }
            console.log('Клетка занята:', r, c);
            return false;
        }

        for (let dr = -1; dr <= 1; dr++) {
            for (let dc = -1; dc <= 1; dc++) {
                const nr = r + dr;
                const nc = c + dc;

                if (nr >= 0 && nr < BOARD_SIZE && nc >= 0 && nc < BOARD_SIZE) {

                    if (selectedShipOnBoard && selectedShipOnBoard.cells.some(cell => cell.row === nr && cell.col === nc)) {
                        console.log('Пропускаем проверку соседней клетки текущего корабля:', nr, nc);
                        continue;
                    }

                    if (playerBoard[nr][nc] !== 0) {
                        console.log('Соседняя клетка занята:', nr, nc);
                        return false;
                    }
                }
            }
        }
    }

    console.log('Корабль можно разместить');
    return true;
}

function placeShip(row, col, size, orientation) {

    if (!canPlaceShip(row, col, size, orientation)) {

        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('heavy');
        }
        return false;
    }

    if (typeof shipsPlaced === 'undefined') {
        shipsPlaced = {};
    }

    if (!shipsPlaced[size]) {
        shipsPlaced[size] = 0;
    }

    const maxShips = SHIP_TYPES.find(type => type.size === size)?.count || 0;

    if (shipsPlaced[size] >= maxShips) {

        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('heavy');
        }
        return false;
    }

    if (orientation === 'horizontal') {
        for (let i = 0; i < size; i++) {
            playerBoard[row][col + i] = 1;
            const cell = document.querySelector(`#placement-board .cell[data-row="${row}"][data-col="${col + i}"]`);
            cell.classList.add('ship');
        }
    } else {
        for (let i = 0; i < size; i++) {
            playerBoard[row + i][col] = 1;
            const cell = document.querySelector(`#placement-board .cell[data-row="${row + i}"][data-col="${col}"]`);
            cell.classList.add('ship');
        }
    }

    shipsPlaced[size]++;

    updateAvailableShips();

    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('medium');
    }

    return true;
}

function removeShipAt(row, col) {

    let size = 1;
    let orientation = null;

    if (col < BOARD_SIZE - 1 && playerBoard[row][col + 1] === 1) orientation = 'horizontal';
    else if (row < BOARD_SIZE - 1 && playerBoard[row + 1][col] === 1) orientation = 'vertical';

    if (orientation === 'horizontal') {
        let c = col;
        while (c >= 0 && playerBoard[row][c] === 1) c--;
        let left = c + 1;
        c = col;
        while (c < BOARD_SIZE && playerBoard[row][c] === 1) c++;
        let right = c - 1;
        size = right - left + 1;
        for (let i = left; i <= right; i++) playerBoard[row][i] = 0;
    } else if (orientation === 'vertical') {
        let r = row;
        while (r >= 0 && playerBoard[r][col] === 1) r--;
        let top = r + 1;
        r = row;
        while (r < BOARD_SIZE && playerBoard[r][col] === 1) r++;
        let bottom = r - 1;
        size = bottom - top + 1;
        for (let i = top; i <= bottom; i++) playerBoard[i][col] = 0;
    } else {
        playerBoard[row][col] = 0;
    }

    let counter = document.querySelector('.ship-item[data-ship="' + size + '"] .ship-count');
    if (counter) counter.textContent = parseInt(counter.textContent) + 1;
}

function updateShipCounters() {

    const placedShips = {};
    playerShips.forEach(ship => {
        placedShips[ship.size] = (placedShips[ship.size] || 0) + 1;
    });

    document.querySelectorAll('.ship-item').forEach(item => {
        const shipSize = parseInt(item.dataset.ship);
        const totalCount = parseInt(item.dataset.count);
        const placedCount = placedShips[shipSize] || 0;

        item.querySelector('.ship-count').textContent = `x${totalCount - placedCount}`;

        if (placedCount >= totalCount) {
            item.style.opacity = '0.5';
            item.style.pointerEvents = 'none';
        } else {
            item.style.opacity = '1';
            item.style.pointerEvents = 'auto';
        }
    });
}

function checkAllShipsPlaced() {
    console.log('checkAllShipsPlaced вызвана');
    console.log('Текущее состояние shipsPlaced:', shipsPlaced);

    let totalPlaced = 0;
    let totalRequired = 0;

    for (const size in shipsPlaced) {
        totalPlaced += shipsPlaced[size];
        console.log(`Размещено кораблей размера ${size}: ${shipsPlaced[size]}`);
    }

    SHIP_TYPES.forEach(type => {
        totalRequired += type.count;
        console.log(`Требуется кораблей размера ${type.size}: ${type.count}`);
    });

    console.log(`Всего размещено: ${totalPlaced}, требуется: ${totalRequired}`);

    const result = totalPlaced === totalRequired;
    console.log('Все корабли размещены:', result);

    return result;
}

function randomPlacement() {

    resetBoard();

    SHIP_TYPES.forEach(type => {
        for (let i = 0; i < type.count; i++) {
            let placed = false;

            while (!placed) {
                const row = Math.floor(Math.random() * BOARD_SIZE);
                const col = Math.floor(Math.random() * BOARD_SIZE);
                const orientation = Math.random() < 0.5 ? 'horizontal' : 'vertical';

                if (canPlaceShip(row, col, type.size, orientation)) {

                    const ship = {
                        size: type.size,
                        cells: []
                    };

                    for (let j = 0; j < type.size; j++) {
                        const r = orientation === 'horizontal' ? row : row + j;
                        const c = orientation === 'horizontal' ? col + j : col;

                        playerBoard[r][c] = 1;
                        ship.cells.push({ row: r, col: c });

                        const cell = document.querySelector(`#placement-board .cell[data-row="${r}"][data-col="${c}"]`);
                        cell.classList.add('ship');
                    }

                    playerShips.push(ship);
                    placed = true;
                }
            }
        }
    });

    updateShipCounters();
    updateShipsList();
    updateAvailableShips();

    checkAllShipsPlaced();
}

function resetBoard() {

    playerBoard = Array(BOARD_SIZE).fill().map(() => Array(BOARD_SIZE).fill(0));

    playerShips = [];

    document.querySelectorAll('#placement-board .cell').forEach(cell => {
        cell.classList.remove('ship');
    });

    document.querySelectorAll('.ship-item').forEach(item => {
        const shipSize = parseInt(item.dataset.ship);
        const totalCount = parseInt(item.dataset.count);

        item.querySelector('.ship-count').textContent = `x${totalCount}`;
        item.style.opacity = '1';
        item.style.pointerEvents = 'auto';
    });

    document.getElementById('start-game').disabled = true;
}

async function startGame() {
    try {

        if (!checkAllShipsPlaced()) {
            alert('Разместите все корабли перед началом игры!');

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
            return;
        }

        const boardToSend = JSON.parse(JSON.stringify(playerBoard));

        const response = await fetch('/api/battleship/ready', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                gameId,
                playerId,
                board: boardToSend
            })
        });

        const data = await response.json();

        if (data.success) {
            // Скрываем экран размещения кораблей
            document.getElementById('placement').style.display = 'none';
            // Показываем экран ожидания
            document.getElementById('waiting').style.display = 'block';
            // Не показываем модальное окно ожидания
            // document.getElementById('waiting-modal').style.display = 'flex';

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        } else {
            alert('Ошибка при начале игры: ' + data.error);

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        }
    } catch (error) {
        console.error('Ошибка при начале игры:', error);
        alert('Ошибка при начале игры. Попробуйте еще раз.');

        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('medium');
        }
    }
}

function updateGameState() {
    if (!gameState) return;

    document.getElementById('game-status').textContent = isPlayerTurn ? 'Ваш ход' : 'Ход противника';

    updatePlayerBoard();

    updateOpponentBoard();

    updateShipsInfo();

    const wasPlayerTurn = isPlayerTurn;
    isPlayerTurn = (String(gameState.current_player) === String(playerId));

    if (isPlayerTurn && !wasPlayerTurn) {
        startTurnTimer();
        stopOpponentTimer();
    } else if (!isPlayerTurn && wasPlayerTurn) {

        stopTurnTimer();
        startOpponentTimer();
    }

    if (gameState.winner) {
        gameOver = true;
        stopTurnTimer(); 
        stopOpponentTimer(); 
        document.getElementById('game').style.display = 'none';
        document.getElementById('result').style.display = 'block';
        showGameResult();
    }

    if (gameState.status === 'playing') {
        document.getElementById('waiting-modal').style.display = 'none';
    }
}

function updatePlayerBoard() {
    console.log('[LOG] updatePlayerBoard', { gameState, playerId });
    const playerBoardContainer = document.getElementById('player-board');
    if (!playerBoardContainer || !gameState || !gameState.boards || !gameState.boards[playerId]) {
        console.warn('[LOG] updatePlayerBoard: нет нужных данных для отрисовки');
        return;
    }

    for (let i = 0; i < 10; i++) {
        for (let j = 0; j < 10; j++) {
            const cell = document.querySelector(`#player-board .cell[data-row="${i}"][data-col="${j}"]`);
            if (cell) {
                cell.className = 'cell';
            }
        }
    }

    const board = gameState.boards[playerId];

    for (let i = 0; i < 10; i++) {
        for (let j = 0; j < 10; j++) {
            const cell = document.querySelector(`#player-board .cell[data-row="${i}"][data-col="${j}"]`);
            if (cell) {
                if (board[i][j] === 1) {
                    cell.classList.add('ship');
                } else if (board[i][j] === 2) {
                    cell.classList.add('hit');
                } else if (board[i][j] === 3) {
                    cell.classList.add('miss');
                }
            }
        }
    }
    console.log('[LOG] updatePlayerBoard: доска обновлена');
}

function updateOpponentBoard() {
    console.log('[LOG] updateOpponentBoard', { gameState, playerId });

    for (let i = 0; i < 10; i++) {
        for (let j = 0; j < 10; j++) {
            const cell = document.querySelector(`#opponent-board .cell[data-row="${i}"][data-col="${j}"]`);
            if (cell) {
                cell.className = 'cell';
            }
        }
    }

    const opponentId = Object.keys(gameState.players).find(id => id !== playerId);
    if (!opponentId) {
        console.warn('[LOG] updateOpponentBoard: не найден ID противника');
        return;
    }

    if (!gameState.boards || !gameState.boards[opponentId]) {
        console.warn('[LOG] updateOpponentBoard: нет данных о доске противника');
        return;
    }

    const board = gameState.boards[opponentId];

    for (let i = 0; i < 10; i++) {
        for (let j = 0; j < 10; j++) {
            const cell = document.querySelector(`#opponent-board .cell[data-row="${i}"][data-col="${j}"]`);
            if (cell) {
                if (board[i][j] === 2) {
                    cell.classList.add('hit');
                } else if (board[i][j] === 3) {
                    cell.classList.add('miss');
                }
            }
        }
    }
    console.log('[LOG] updateOpponentBoard: доска обновлена');
}

function updateShipsInfo() {
    if (!gameState) return;

    const playerBoardData = gameState.boards[playerId];
    const opponentId = Object.keys(gameState.boards).find(id => id !== playerId);
    const opponentBoardData = gameState.boards[opponentId];

    playerHits = 0;
    opponentHits = 0;

    for (let row = 0; row < BOARD_SIZE; row++) {
        for (let col = 0; col < BOARD_SIZE; col++) {
            if (playerBoardData[row][col] === 2) {
                playerHits++;
            }

            if (opponentBoardData[row][col] === 2) {
                opponentHits++;
            }
        }
    }

    const playerShipsStatus = document.getElementById('player-ships-status');
    playerShipsStatus.innerHTML = '';

    const sunkShips = countSunkShips(playerBoardData);

    SHIP_TYPES.forEach(type => {
        const shipStatus = document.createElement('div');
        shipStatus.className = 'ship-status';

        const indicator = document.createElement('div');
        indicator.className = 'ship-status-indicator';

        if (sunkShips[type.size] >= type.count) {
            indicator.classList.add('sunk');
        }

        const label = document.createElement('div');
        label.textContent = `${type.size}-палубный: ${sunkShips[type.size] || 0}/${type.count}`;

        shipStatus.appendChild(indicator);
        shipStatus.appendChild(label);
        playerShipsStatus.appendChild(shipStatus);
    });

    const opponentShipsStatus = document.getElementById('opponent-ships-status');
    opponentShipsStatus.innerHTML = '';

    const opponentSunkShips = countSunkShips(opponentBoardData);

    SHIP_TYPES.forEach(type => {
        const shipStatus = document.createElement('div');
        shipStatus.className = 'ship-status';

        const indicator = document.createElement('div');
        indicator.className = 'ship-status-indicator';

        if (opponentSunkShips[type.size] >= type.count) {
            indicator.classList.add('sunk');
        }

        const label = document.createElement('div');
        label.textContent = `${type.size}-палубный: ${opponentSunkShips[type.size] || 0}/${type.count}`;

        shipStatus.appendChild(indicator);
        shipStatus.appendChild(label);
        opponentShipsStatus.appendChild(shipStatus);
    });
}

function countSunkShips(board) {
    const sunkShips = {};

    const shipHits = {};

    for (let row = 0; row < BOARD_SIZE; row++) {
        for (let col = 0; col < BOARD_SIZE; col++) {
            if (board[row][col] === 1) {

                let shipSize = 0;

                let size = 1;
                let c = col - 1;
                while (c >= 0 && board[row][c] === 1) {
                    size++;
                    c--;
                }

                c = col + 1;
                while (c < BOARD_SIZE && board[row][c] === 1) {
                    size++;
                    c++;
                }

                if (size > shipSize) {
                    shipSize = size;
                }

                size = 1;
                let r = row - 1;
                while (r >= 0 && board[r][col] === 1) {
                    size++;
                    r--;
                }

                r = row + 1;
                while (r < BOARD_SIZE && board[r][col] === 1) {
                    size++;
                    r++;
                }

                if (size > shipSize) {
                    shipSize = size;
                }

                if (shipSize > 0) {
                    shipHits[shipSize] = (shipHits[shipSize] || 0) + 1;
                }
            }
        }
    }

    for (const size in shipHits) {
        const hits = shipHits[size];
        const count = Math.floor(hits / size);

        if (count > 0) {
            sunkShips[size] = count;
        }
    }

    return sunkShips;
}

async function makeMove(row, col) {
    if (!isPlayerTurn || gameOver) return;

    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('medium');
    }

    try {
        const response = await fetch('/api/battleship/move', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                gameId,
                playerId,
                row,
                col
            })
        });

        const data = await response.json();

        if (data.success) {

            gameState = data.gameState;
            updateGameState();
        } else {
            alert('Ошибка при выполнении хода: ' + data.error);

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        }
    } catch (error) {
        console.error('Ошибка при выполнении хода:', error);
        alert('Ошибка при выполнении хода. Попробуйте еще раз.');

        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('medium');
        }
    }
}

function showGameResult() {
    if (!gameState) return;

    const resultMessage = document.getElementById('result-message');
    const playerHitsElement = document.getElementById('player-hits');
    const opponentHitsElement = document.getElementById('opponent-hits');

    // Сравниваем всегда как строки
    const isWinner = String(gameState.winner) === String(playerId);
    console.log('[LOG] showGameResult: winner =', gameState.winner, 'playerId =', playerId, 'isWinner =', isWinner);

    if (gameState.exit_reason) {
        if (gameState.exit_reason === 'opponent_exit') {
            resultMessage.textContent = 'Противник вышел из игры. Вы победили!';
            resultMessage.style.color = 'var(--accent-color)'; 
        } else if (gameState.exit_reason === 'player_exit') {
            resultMessage.textContent = 'Вы вышли из игры. Вы проиграли.';
            resultMessage.style.color = 'var(--secondary-color)'; 
        } else if (gameState.exit_reason === 'timeout') {
            if (isWinner) {
                resultMessage.textContent = 'Противник не сделал ход вовремя. Вы победили!';
                resultMessage.style.color = 'var(--accent-color)'; 
            } else {
                resultMessage.textContent = 'Вы не сделали ход вовремя. Вы проиграли.';
                resultMessage.style.color = 'var(--secondary-color)'; 
            }
        }
    } else {
        resultMessage.textContent = isWinner ? 'Поздравляем! Вы победили!' : 'К сожалению, вы проиграли.';
        resultMessage.style.color = isWinner ? 'var(--accent-color)' : 'var(--secondary-color)';
    }

    playerHitsElement.textContent = playerHits;
    opponentHitsElement.textContent = opponentHits;
}

function startDragging(row, col, event) {

    if (selectedShip !== null) {
        selectedShip = null;
        document.querySelectorAll('.ship-item').forEach(item => {
            item.classList.remove('selected-ship');
        });
    }

    let orientation = null;

    let left = col;
    let right = col;
    while (left > 0 && playerBoard[row][left - 1] === 1) left--;
    while (right < BOARD_SIZE - 1 && playerBoard[row][right + 1] === 1) right++;

    let top = row;
    let bottom = row;
    while (top > 0 && playerBoard[top - 1][col] === 1) top--;
    while (bottom < BOARD_SIZE - 1 && playerBoard[bottom + 1][col] === 1) bottom++;

    draggedShipCells = [];

    if (right - left > bottom - top) {

        orientation = 'horizontal';

        for (let i = left; i <= right; i++) {
            draggedShipCells.push({ row, col: i });
        }
    } else if (bottom - top > right - left) {

        orientation = 'vertical';

        for (let i = top; i <= bottom; i++) {
            draggedShipCells.push({ row: i, col });
        }
    } else {

        if (right - left === bottom - top) {

            if (right - left > 0) {

                orientation = 'horizontal'; 

                for (let r = top; r <= bottom; r++) {
                    for (let c = left; c <= right; c++) {
                        draggedShipCells.push({ row: r, col: c });
                    }
                }
            } else {

                orientation = 'horizontal'; 
                draggedShipCells.push({ row, col });
            }
        } else {

            orientation = 'horizontal'; 
            draggedShipCells.push({ row, col });
        }
    }

    draggedShipStartRow = row;
    draggedShipStartCol = col;

    shipOrientation = orientation || 'horizontal';

    draggedShip = draggedShipCells.length;

    draggedShipCells.forEach(cell => {
        const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
        cellElement.classList.add('dragging');
    });

    if (!selectedShipOnBoard || !selectedShipOnBoard.cells.some(cell => cell.row === row && cell.col === col)) {

        if (selectedShipOnBoard) {
            selectedShipOnBoard.cells.forEach(cell => {
                const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
                cellElement.classList.remove('selected');
            });
        }

        draggedShipCells.forEach(cell => {
            const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
            cellElement.classList.add('selected');
        });

        selectedShipOnBoard = {
            size: draggedShip,
            orientation: shipOrientation,
            startRow: draggedShipStartRow,
            startCol: draggedShipStartCol,
            cells: [...draggedShipCells]
        };
    }

    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('medium');
    }

    if (event.type === 'touchstart') {
        event.preventDefault();
    }
}

function handleDrag(event) {
    if (!draggedShip) return;

    let clientX, clientY;
    if (event.type === 'mousemove') {
        clientX = event.clientX;
        clientY = event.clientY;
    } else if (event.type === 'touchmove') {
        clientX = event.touches[0].clientX;
        clientY = event.touches[0].clientY;
    }

    const placementBoard = document.getElementById('placement-board');
    const boardRect = placementBoard.getBoundingClientRect();

    const col = Math.floor((clientX - boardRect.left) / (boardRect.width / BOARD_SIZE));
    const row = Math.floor((clientY - boardRect.top) / (boardRect.height / BOARD_SIZE));

    if (row >= 0 && row < BOARD_SIZE && col >= 0 && col < BOARD_SIZE) {

        document.querySelectorAll('.cell.drag-over').forEach(cell => {
            cell.classList.remove('drag-over');
        });

        const canPlace = canPlaceShip(row, col, draggedShip, shipOrientation);

        if (canPlace) {
            for (let i = 0; i < draggedShip; i++) {
                const r = shipOrientation === 'horizontal' ? row : row + i;
                const c = shipOrientation === 'horizontal' ? col + i : col;

                if (r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE) {
                    const cell = document.querySelector(`#placement-board .cell[data-row="${r}"][data-col="${c}"]`);
                    cell.classList.add('drag-over');
                }
            }
        }
    }

    if (event.type === 'touchmove') {
        event.preventDefault();
    }
}

function endDragging(event) {
    if (!draggedShip) return;

    let clientX, clientY;
    if (event.type === 'mouseup') {
        clientX = event.clientX;
        clientY = event.clientY;
    } else if (event.type === 'touchend') {
        if (event.changedTouches.length > 0) {
            clientX = event.changedTouches[0].clientX;
            clientY = event.changedTouches[0].clientY;
        } else {

            resetDragging();
            return;
        }
    }

    const placementBoard = document.getElementById('placement-board');
    const boardRect = placementBoard.getBoundingClientRect();

    const col = Math.floor((clientX - boardRect.left) / (boardRect.width / BOARD_SIZE));
    const row = Math.floor((clientY - boardRect.top) / (boardRect.height / BOARD_SIZE));

    if (row >= 0 && row < BOARD_SIZE && col >= 0 && col < BOARD_SIZE) {

        const canPlace = canPlaceShip(row, col, draggedShip, shipOrientation);

        if (canPlace) {

            draggedShipCells.forEach(cell => {
                playerBoard[cell.row][cell.col] = 0;
                const cellElement = document.querySelector(`#placement-board .cell[data-row="${cell.row}"][data-col="${cell.col}"]`);
                cellElement.classList.remove('ship', 'selected', 'dragging');
            });

            const newCells = [];
            for (let i = 0; i < draggedShip; i++) {
                const r = shipOrientation === 'horizontal' ? row : row + i;
                const c = shipOrientation === 'horizontal' ? col + i : col;

                playerBoard[r][c] = 1;
                const cell = document.querySelector(`#placement-board .cell[data-row="${r}"][data-col="${c}"]`);
                cell.classList.add('ship');
                cell.classList.add('selected');
                newCells.push({ row: r, col: c });
            }

            selectedShipOnBoard = {
                size: draggedShip,
                orientation: shipOrientation,
                startRow: row,
                startCol: col,
                cells: newCells
            };

            updateShipsList();

            updateAvailableShips();

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        } else {

            const cells = document.querySelectorAll('.cell.ship.dragging');
            cells.forEach(cell => {
                cell.classList.add('invalid');
                setTimeout(() => {
                    cell.classList.remove('invalid');
                }, 500);
            });

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        }
    }

    document.querySelectorAll('.cell.dragging, .cell.drag-over').forEach(cell => {
        cell.classList.remove('dragging', 'drag-over');
    });

    draggedShip = null;
    draggedShipCells = [];
    draggedShipStartRow = -1;
    draggedShipStartCol = -1;

    if (event.type === 'touchend') {
        event.preventDefault();
    }
}

function resetDragging() {

    document.querySelectorAll('.cell.dragging, .cell.drag-over').forEach(cell => {
        cell.classList.remove('dragging', 'drag-over');
    });

    draggedShip = null;
    draggedShipCells = [];
    draggedShipStartRow = -1;
    draggedShipStartCol = -1;
}

function updateShipsList() {
    console.log('updateShipsList вызвана');

    const ships = [0, 0, 0, 0];
    for (let row = 0; row < BOARD_SIZE; row++) {
        for (let col = 0; col < BOARD_SIZE; col++) {
            if (playerBoard[row][col] === 1) {

                if ((col === 0 || playerBoard[row][col - 1] === 0) && (row === 0 || playerBoard[row - 1][col] === 0)) {

                    let size = 1;
                    let isHorizontal = false;

                    if (col < BOARD_SIZE - 1 && playerBoard[row][col + 1] === 1) {
                        isHorizontal = true;
                        let c = col + 1;
                        while (c < BOARD_SIZE && playerBoard[row][c] === 1) {
                            size++;
                            c++;
                        }
                    } else if (row < BOARD_SIZE - 1 && playerBoard[row + 1][col] === 1) {

                        let r = row + 1;
                        while (r < BOARD_SIZE && playerBoard[r][col] === 1) {
                            size++;
                            r++;
                        }
                    }

                    if (size >= 1 && size <= 4) {
                        ships[size - 1]++;
                        console.log(`Найден корабль размера ${size} в позиции (${row}, ${col})`);
                    } else {
                        console.warn(`Обнаружен корабль недопустимого размера: ${size} в позиции (${row}, ${col})`);
                    }
                }
            }
        }
    }
    console.log('Подсчитанные корабли:', ships);

    if (typeof shipsPlaced === 'undefined') shipsPlaced = {};
    for (let i = 0; i < 4; i++) {
        shipsPlaced[i + 1] = ships[i];
        console.log(`Обновлено shipsPlaced[${i + 1}] = ${ships[i]}`);
    }

    for (let i = 0; i < 4; i++) {
        const shipItem = document.querySelector(`.ship-item[data-ship="${i + 1}"]`);
        if (!shipItem) {
            console.error(`Элемент .ship-item[data-ship="${i + 1}"] не найден`);
            continue;
        }
        const count = shipItem.querySelector('.ship-count');
        if (!count) {
            console.error(`Элемент .ship-count не найден в .ship-item[data-ship="${i + 1}"]`);
            continue;
        }

        const totalCount = SHIP_TYPES.find(type => type.size === i + 1)?.count || 0;

        const available = Math.max(0, totalCount - ships[i]);
        count.textContent = `${ships[i]}/${totalCount}`;
        console.log(`Обновлен счетчик для корабля размера ${i + 1}: ${ships[i]}/${totalCount}`);

        if (ships[i] >= totalCount) {
            shipItem.classList.add('disabled');
            shipItem.style.opacity = '0.5';
            shipItem.style.cursor = 'not-allowed';
            console.log(`Корабль размера ${i + 1} отключен (все размещены)`);
        } else {
            shipItem.classList.remove('disabled');
            shipItem.style.opacity = '1';
            shipItem.style.cursor = 'pointer';
            console.log(`Корабль размера ${i + 1} активирован (осталось ${available})`);
        }
    }
}

async function exitGameAndNotify() {
    if (!gameId || !playerId) {

        if (tg && tg.WebApp) {
            tg.WebApp.close();
        } else {
            window.location.href = '/';
        }
        return;
    }
    try {
        console.log('[LOG] Отправка запроса на выход из игры');
        const response = await fetch('/api/battleship/exit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                gameId, 
                playerId,
                reason: 'player_exit' 
            })
        });

        if (!response.ok) {
            console.error(`[LOG] Ошибка HTTP при выходе из игры: ${response.status} ${response.statusText}`);

            if (tg && tg.WebApp) {
                tg.WebApp.close();
            } else {
                window.location.href = '/';
            }
            return;
        }

        const data = await response.json();
        if (data.success) {
            console.log('[LOG] Успешный выход из игры');

            if (tg && tg.WebApp) {
                tg.WebApp.close();
            } else {
                window.location.href = '/';
            }
        } else {
            console.error('[LOG] Ошибка при выходе из игры:', data.error);

            if (tg && tg.WebApp) {
                tg.WebApp.close();
            } else {
                window.location.href = '/';
            }
        }
    } catch (e) {
        console.error('[LOG] Ошибка при выходе из игры:', e);

        if (tg && tg.WebApp) {
            tg.WebApp.close();
        } else {
            window.location.href = '/';
        }
    }
}

function handleRotateButtonClick() {
    rotateShip();
}

function handleShipItemClick(size) {

    if (selectedShipOnBoard) {
        selectedShipOnBoard = null;
        document.querySelectorAll('.cell.ship.selected').forEach(cell => {
            cell.classList.remove('selected');
        });
    }

    document.querySelectorAll('.ship-item').forEach(item => {
        item.classList.remove('selected-ship');
    });

    selectedShip = size;
    document.querySelector(`.ship-item[data-ship="${size}"]`).classList.add('selected-ship');

    shipOrientation = 'horizontal';

    updatePlacementPreview();

    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('medium');
    }
}

function handleReadyButtonClick() {
    if (!gameId || !playerId || !playerBoard) {
        alert('Ошибка: не инициализированы код игры, ID игрока или доска. Возвращаемся в лобби.');
        showScreen('lobby');
        return;
    }

    if (!checkAllShipsPlaced()) {
        alert('Разместите все корабли перед началом игры!');

        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('medium');
        }
        return;
    }

    fetch('/api/battleship/ready', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            gameId, 
            playerId,
            board: playerBoard 
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Скрываем экран размещения кораблей
            document.getElementById('placement').style.display = 'none';
            // Показываем экран ожидания
            document.getElementById('waiting').style.display = 'block';
            // Не показываем модальное окно ожидания
            // document.getElementById('waiting-modal').style.display = 'flex';

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        } else {
            console.error('Ошибка при отправке готовности:', data.error);

            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        }
    })
    .catch(error => {
        console.error('Ошибка при отправке готовности:', error);

        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('medium');
        }
    });
}

function handleBackToLobbyClick() {
    backToLobby();

    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('light');
    }
}

function initEventListeners() {
    console.log('[LOG] initEventListeners: инициализация обработчиков событий');

    const createGameBtn = document.getElementById('create-game-btn');
    if (createGameBtn) {
        console.log('[LOG] initEventListeners: добавляем обработчик для кнопки создания игры');
        createGameBtn.addEventListener('click', createGame);
    } else {
        console.error('[LOG] initEventListeners: кнопка создания игры не найдена');
    }

    const startGameBtn = document.getElementById('start-game');
    if (startGameBtn) {
        console.log('[LOG] initEventListeners: добавляем обработчик для кнопки начала игры');
        startGameBtn.addEventListener('click', handleReadyButtonClick);
    } else {
        console.error('[LOG] initEventListeners: кнопка начала игры не найдена');
    }

    const rotateShipBtn = document.getElementById('rotate-ship');
    if (rotateShipBtn) {
        console.log('[LOG] initEventListeners: добавляем обработчик для кнопки поворота корабля');
        rotateShipBtn.addEventListener('click', handleRotateButtonClick);
    } else {
        console.error('[LOG] initEventListeners: кнопка поворота корабля не найдена');
    }

    const backToLobbyBtn = document.getElementById('back-to-lobby');
    if (backToLobbyBtn) {
        console.log('[LOG] initEventListeners: добавляем обработчик для кнопки возврата в лобби');
        backToLobbyBtn.addEventListener('click', handleBackToLobbyClick);
    } else {
        console.error('[LOG] initEventListeners: кнопка возврата в лобби не найдена');
    }

    const cancelWaitingBtn = document.getElementById('cancel-waiting');
    if (cancelWaitingBtn) {
        console.log('[LOG] initEventListeners: добавляем обработчик для кнопки отмены ожидания');
        cancelWaitingBtn.addEventListener('click', cancelWaiting);
    } else {
        console.error('[LOG] initEventListeners: кнопка отмены ожидания не найдена');
    }
    
    // Добавляем обработчик для кнопки "Играть снова"
    const playAgainBtn = document.getElementById('play-again');
    if (playAgainBtn) {
        console.log('[LOG] initEventListeners: добавляем обработчик для кнопки "Играть снова"');
        playAgainBtn.addEventListener('click', playAgain);
    } else {
        console.error('[LOG] initEventListeners: кнопка "Играть снова" не найдена');
    }
    
    // Добавляем обработчик для кнопки "Случайно"
    const randomPlacementBtn = document.getElementById('random-placement');
    if (randomPlacementBtn) {
        console.log('[LOG] initEventListeners: добавляем обработчик для кнопки "Случайно"');
        randomPlacementBtn.addEventListener('click', randomPlacement);
    } else {
        console.error('[LOG] initEventListeners: кнопка "Случайно" не найдена');
    }

    const shipItems = document.querySelectorAll('.ship-item');
    console.log('[LOG] initEventListeners: найдено элементов .ship-item:', shipItems.length);
    shipItems.forEach(item => {
        item.addEventListener('click', function() {
            const size = parseInt(this.getAttribute('data-ship'));
            console.log('[LOG] initEventListeners: клик по кораблю размера', size);
            handleShipItemClick(size);
        });
    });

    const placementCells = document.querySelectorAll('#placement-board .cell');
    console.log('[LOG] initEventListeners: найдено клеток на доске размещения:', placementCells.length);
    placementCells.forEach(cell => {
        cell.addEventListener('click', function() {
            const row = parseInt(this.getAttribute('data-row'));
            const col = parseInt(this.getAttribute('data-col'));
            console.log('[LOG] initEventListeners: клик по клетке размещения:', row, col);
            handlePlacementCellClick(row, col);
        });
    });

    const gameCells = document.querySelectorAll('#game-board .cell');
    console.log('[LOG] initEventListeners: найдено клеток на игровой доске:', gameCells.length);
    gameCells.forEach(cell => {
        cell.addEventListener('click', function() {
            const row = parseInt(this.getAttribute('data-row'));
            const col = parseInt(this.getAttribute('data-col'));
            console.log('[LOG] initEventListeners: клик по игровой клетке:', row, col);
            handleGameCellClick(row, col);
        });
    });
    
    console.log('[LOG] initEventListeners: инициализация обработчиков событий завершена');
}

function updateAvailableShips() {
    console.log('updateAvailableShips вызвана');
    console.log('Текущее состояние shipsPlaced:', shipsPlaced);

    const shipItems = document.querySelectorAll('.ship-item');
    console.log('Найдено элементов .ship-item:', shipItems.length);

    shipItems.forEach(item => {
        const size = parseInt(item.getAttribute('data-ship'));
        const count = parseInt(item.getAttribute('data-count'));
        console.log(`Обработка корабля размера ${size}, максимальное количество: ${count}`);

        const placed = shipsPlaced[size] || 0;
        const available = count - placed;
        console.log(`Размещено: ${placed}, доступно: ${available}`);

        const countElement = item.querySelector('.ship-count');
        if (countElement) {
            countElement.textContent = available;
            console.log(`Обновлен счетчик для корабля размера ${size}: ${available}`);
        } else {
            console.error(`Элемент .ship-count не найден в .ship-item[data-ship="${size}"]`);
        }

        if (available <= 0) {
            item.classList.add('disabled');
            item.style.opacity = '0.5';
            item.style.cursor = 'not-allowed';
            console.log(`Корабль размера ${size} отключен (все размещены)`);
        } else {
            item.classList.remove('disabled');
            item.style.opacity = '1';
            item.style.cursor = 'pointer';
            console.log(`Корабль размера ${size} активирован (осталось ${available})`);
        }
    });

    const allShipsPlaced = checkAllShipsPlaced();
    console.log('Все корабли размещены:', allShipsPlaced);

    const startGameButton = document.getElementById('start-game');
    if (startGameButton) {
        startGameButton.disabled = !allShipsPlaced;
        console.log(`Кнопка "Начать игру" ${allShipsPlaced ? 'активирована' : 'деактивирована'}`);
    } else {
        console.error('Элемент #start-game не найден');
    }
}

function checkAllShipsPlaced() {

    let totalPlaced = 0;
    let totalRequired = 0;

    for (const size in shipsPlaced) {
        totalPlaced += shipsPlaced[size];
    }

    SHIP_TYPES.forEach(type => {
        totalRequired += type.count;
    });

    return totalPlaced === totalRequired;
} 

function startTurnTimer() {

    turnTimeLeft = 120; 

    updateTimerDisplay();

    if (turnTimer) {
        clearInterval(turnTimer);
    }

    turnTimer = setInterval(() => {
        turnTimeLeft--;
        updateTimerDisplay();

        if (turnTimeLeft <= 0) {
            stopTurnTimer();

            surrenderGame();
        }
    }, 1000);
}

function stopTurnTimer() {
    if (turnTimer) {
        clearInterval(turnTimer);
        turnTimer = null;
    }
}

function startOpponentTimer() {

    opponentTurnTimeLeft = 120; 

    updateTimerDisplay();

    if (opponentTimer) {
        clearInterval(opponentTimer);
    }

    opponentTimer = setInterval(() => {
        opponentTurnTimeLeft--;
        updateTimerDisplay();

        if (opponentTurnTimeLeft <= 0) {
            stopOpponentTimer();

        }
    }, 1000);
}

function stopOpponentTimer() {
    if (opponentTimer) {
        clearInterval(opponentTimer);
        opponentTimer = null;
    }
}

function updateTimerDisplay() {
    const timerElement = document.getElementById('turn-timer');
    if (timerElement) {

        const timeLeft = isPlayerTurn ? turnTimeLeft : opponentTurnTimeLeft;

        const minutes = Math.floor(timeLeft / 60);
        const seconds = timeLeft % 60;
        timerElement.textContent = `${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;

        if (timeLeft <= 30) {
            timerElement.classList.add('danger');
            timerElement.classList.remove('warning');
        } else if (timeLeft <= 60) {
            timerElement.classList.add('warning');
            timerElement.classList.remove('danger');
        } else {
            timerElement.classList.remove('warning', 'danger');
        }
    }
}

async function surrenderGame() {
    if (!gameId || !playerId || gameOver) return;

    try {
        console.log('[LOG] Отправка запроса на сдачу игры');
        const response = await fetch('/api/battleship/surrender', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                gameId, 
                playerId,
                reason: 'timeout' 
            })
        });

        if (!response.ok) {
            console.error(`[LOG] Ошибка HTTP при сдаче игры: ${response.status} ${response.statusText}`);

            const gameStatus = document.getElementById('game-status');
            if (gameStatus) {
                gameStatus.textContent = 'Ошибка при сдаче игры. Попробуйте обновить страницу.';
                gameStatus.style.color = 'red';
            }
            return;
        }

        const data = await response.json();
        if (data.success) {
            console.log('[LOG] Игра сдана из-за истечения времени');

            gameState = data.gameState;
            gameState.exit_reason = 'timeout';
            document.getElementById('game').style.display = 'none';
            document.getElementById('result').style.display = 'block';
            showGameResult();
        } else {
            console.error('[LOG] Ошибка при сдаче игры:', data.error);

            const gameStatus = document.getElementById('game-status');
            if (gameStatus) {
                gameStatus.textContent = `Ошибка: ${data.error || 'Неизвестная ошибка'}`;
                gameStatus.style.color = 'red';
            }
        }
    } catch (error) {
        console.error('[LOG] Ошибка при сдаче игры:', error);

        const gameStatus = document.getElementById('game-status');
        if (gameStatus) {
            gameStatus.textContent = 'Ошибка соединения. Попробуйте обновить страницу.';
            gameStatus.style.color = 'red';
        }
    }
}

function playAgain() {

    gameState = null;
    playerBoard = Array(BOARD_SIZE).fill().map(() => Array(BOARD_SIZE).fill(0));
    opponentBoard = Array(BOARD_SIZE).fill().map(() => Array(BOARD_SIZE).fill(0));
    playerShips = [];
    opponentShips = [];
    playerHits = 0;
    opponentHits = 0;
    isPlayerTurn = false;
    gameOver = false;
    selectedShip = null;
    shipOrientation = 'horizontal';
    selectedShipOnBoard = null;

    stopTurnTimer();
    stopOpponentTimer();

    document.getElementById('result').style.display = 'none';
    document.getElementById('lobby').style.display = 'block';

    document.querySelectorAll('.ship-item').forEach(item => {
        const shipSize = parseInt(item.dataset.ship);
        const totalCount = parseInt(item.dataset.count);
        item.querySelector('.ship-count').textContent = `x${totalCount}`;
        item.style.opacity = '1';
        item.style.pointerEvents = 'auto';
    });

    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('medium');
    }
}

function backToLobby() {

    if (tg && tg.WebApp) {
        window.location.href = '/';
    } else {

        window.location.reload();
    }
}

// Функция для получения списка доступных игр
async function fetchAvailableGames() {
    console.log('[LOG] fetchAvailableGames: запрос списка доступных игр');
    try {
        const response = await fetch('/api/battleship/games');
        console.log('[LOG] fetchAvailableGames: получен ответ от сервера, статус:', response.status);
        const data = await response.json();
        console.log('[LOG] fetchAvailableGames: данные ответа:', data);
        
        if (data.success) {
            console.log('[LOG] fetchAvailableGames: успешно получен список игр');
            displayAvailableGames(data.games);
        } else {
            console.error('[LOG] fetchAvailableGames: ошибка при получении списка игр:', data.error);
        }
    } catch (error) {
        console.error('[LOG] fetchAvailableGames: исключение при получении списка игр:', error);
    }
}

// Функция для отображения списка доступных игр
function displayAvailableGames(games) {
    console.log('[LOG] displayAvailableGames: отображение списка доступных игр');
    console.log('[LOG] displayAvailableGames: получено игр:', Object.keys(games).length);
    
    const gamesList = document.getElementById('available-games-list');
    if (!gamesList) {
        console.error('[LOG] displayAvailableGames: элемент #available-games-list не найден');
        return;
    }
    
    // Очищаем список
    gamesList.innerHTML = '';
    
    if (Object.keys(games).length === 0) {
        console.log('[LOG] displayAvailableGames: нет доступных игр');
        gamesList.innerHTML = '<div class="no-games-message">Нет доступных игр</div>';
        return;
    }
    
    // Сортируем игры по времени создания (от новых к старым)
    const sortedGames = Object.values(games).sort((a, b) => b.created_at - a.created_at);
    console.log('[LOG] displayAvailableGames: отсортировано игр:', sortedGames.length);
    
    // Добавляем каждую игру в список
    sortedGames.forEach(game => {
        console.log('[LOG] displayAvailableGames: добавление игры в список:', game.id);
        const gameItem = document.createElement('div');
        gameItem.className = 'game-item';
        gameItem.dataset.gameId = game.id;
        
        // Форматируем время создания
        const createdTime = new Date(game.created_at * 1000);
        const timeString = createdTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        
        // Получаем имя создателя игры
        const creatorName = game.host_name ? `${game.host_name}` : 'Игра';
        
        // Создаем кнопку "Присоединиться"
        const joinButton = document.createElement('button');
        joinButton.className = 'btn primary small';
        joinButton.textContent = 'Войти';
        joinButton.addEventListener('click', function(e) {
            e.stopPropagation(); // Предотвращаем всплытие события
            console.log('[LOG] displayAvailableGames: клик по кнопке присоединения к игре:', game.id);
            joinGameById(game.id);
            
            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        });
        
        // Создаем контейнер для информации об игре
        const gameInfo = document.createElement('div');
        gameInfo.className = 'game-info';
        gameInfo.innerHTML = `
            <div class="game-id">${creatorName}</div>
            <div class="game-time">Создана: ${timeString}</div>
        `;
        
        // Добавляем элементы в элемент игры
        gameItem.appendChild(gameInfo);
        gameItem.appendChild(joinButton);
        
        // Добавляем обработчик клика для подключения к игре (на случай, если пользователь кликнет на всю карточку)
        gameItem.addEventListener('click', function() {
            console.log('[LOG] displayAvailableGames: клик по игре:', game.id);
            joinGameById(game.id);
            
            if (tg && tg.HapticFeedback) {
                tg.HapticFeedback.impactOccurred('medium');
            }
        });
        
        gamesList.appendChild(gameItem);
    });
    
    console.log('[LOG] displayAvailableGames: список игр обновлен');
}

// Функция для подключения к игре по ID
async function joinGameById(joinId) {
    console.log('[LOG] joinGameById вызвана с gameId:', joinId);
    try {
        const userId = tg.initDataUnsafe.user.id;
        console.log('[LOG] joinGameById: userId =', userId);
        
        console.log('[LOG] joinGameById: отправка запроса на присоединение к игре');
        const response = await fetch('/api/battleship/join', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ gameId: joinId, userId })
        });

        console.log('[LOG] joinGameById: получен ответ от сервера, статус:', response.status);
        const data = await response.json();
        console.log('[LOG] joinGameById: данные ответа:', data);

        if (data.success) {
            console.log('[LOG] joinGameById: успешное присоединение к игре');
            gameId = data.gameId;
            playerId = data.playerId;
            isHost = false;
            
            console.log('[LOG] joinGameById: gameId =', gameId, 'playerId =', playerId, 'isHost =', isHost);

            console.log('[LOG] joinGameById: скрываем лобби и показываем экран размещения кораблей');
            document.getElementById('lobby').style.display = 'none';
            safeShowPlacementScreen();
            
            // Проверяем, что экраны действительно изменились
            console.log('[LOG] joinGameById: проверка видимости экранов:');
            console.log('[LOG] joinGameById: lobby display =', document.getElementById('lobby').style.display);
            console.log('[LOG] joinGameById: placement display =', document.getElementById('placement').style.display);
            console.log('[LOG] joinGameById: waiting display =', document.getElementById('waiting').style.display);

            console.log('[LOG] joinGameById: запускаем pollGameState');
            pollGameState();
            
            // Обновляем список доступных игр
            console.log('[LOG] joinGameById: обновляем список доступных игр');
            fetchAvailableGames();
        } else {
            console.error('[LOG] joinGameById: ошибка при присоединении к игре:', data.error);
            alert('Ошибка при присоединении к игре: ' + data.error);
        }
    } catch (error) {
        console.error('[LOG] joinGameById: исключение при присоединении к игре:', error);
        alert('Ошибка при присоединении к игре. Попробуйте еще раз.');
    }
}

function showScreen(screenId) {
    console.log('[LOG] showScreen: переключение на экран:', screenId);
    
    // Скрываем все экраны
    const screens = ['lobby', 'placement', 'waiting', 'game', 'result'];
    screens.forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            const currentDisplay = element.style.display;
            element.style.display = 'none';
            console.log('[LOG] showScreen: скрыт экран', id, 'было:', currentDisplay);
        } else {
            console.error('[LOG] showScreen: элемент не найден:', id);
        }
    });
    
    // Показываем нужный экран
    const targetScreen = document.getElementById(screenId);
    if (targetScreen) {
        targetScreen.style.display = 'block';
        console.log('[LOG] showScreen: показан экран', screenId);
        
        // Проверяем состояние всех экранов после переключения
        console.log('[LOG] showScreen: проверка состояния экранов после переключения:');
        screens.forEach(id => {
            const element = document.getElementById(id);
            if (element) {
                console.log('[LOG] showScreen: экран', id, 'display =', element.style.display);
            }
        });
    } else {
        console.error('[LOG] showScreen: целевой экран не найден:', screenId);
    }
}

// Добавим функцию для безопасного перехода на экран расстановки кораблей
function safeShowPlacementScreen() {
    if (!gameId || !playerId) {
        alert('Ошибка: не удалось инициализировать игру. Вернитесь в лобби и попробуйте снова.');
        showScreen('lobby');
        return false;
    }
    showScreen('placement');
    return true;
}