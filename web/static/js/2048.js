let tg = window.Telegram.WebApp;
tg.expand();
tg.BackButton.show();
tg.BackButton.onClick(function() {
    window.location.href = '/';
});

const grid = document.getElementById('grid');
const tiltClassMap = {
    left:  'tilt-left',
    right: 'tilt-right',
    up:    'tilt-up',
    down:  'tilt-down'
  };
const scoreElement = document.getElementById('score');
const bestScoreElement = document.getElementById('best-score');
const newGameButton = document.getElementById('new-game');
const confirmModal = document.getElementById('confirmModal');
const confirmYes = document.getElementById('confirmYes');
const confirmNo = document.getElementById('confirmNo');

function tiltBoard(direction) {
    const cls = tiltClassMap[direction];
    if (!cls) return;
    grid.classList.add(cls);

    setTimeout(() => grid.classList.remove(cls), 300);
  }

newGameButton.addEventListener('touchstart', function(event) {
    event.preventDefault();
    event.stopPropagation();
    if (score > 0) {
        showConfirmModal();
    } else {
        window.location.reload();
    }
}, { passive: false });

newGameButton.addEventListener('click', function(event) {
    event.preventDefault();
    event.stopPropagation();
    if (score > 0) {
        showConfirmModal();
    } else {
        window.location.reload();
    }
});

confirmYes.addEventListener('touchstart', function(event) {
    event.preventDefault();
    event.stopPropagation();
    hideConfirmModal();
    window.location.reload();
    tg.HapticFeedback.impactOccurred('light');
}, { passive: false });

confirmYes.addEventListener('click', function(event) {
    event.preventDefault();
    event.stopPropagation();
    hideConfirmModal();
    window.location.reload();
    tg.HapticFeedback.impactOccurred('light');
});

confirmNo.addEventListener('touchstart', function(event) {
    event.preventDefault();
    event.stopPropagation();
    hideConfirmModal();
    tg.HapticFeedback.impactOccurred('light');
}, { passive: false });

confirmNo.addEventListener('click', function(event) {
    event.preventDefault();
    event.stopPropagation();
    hideConfirmModal();
    tg.HapticFeedback.impactOccurred('light');
});

confirmModal.addEventListener('touchstart', function(event) {
    event.preventDefault();
    event.stopPropagation();
    if (event.target === confirmModal) {
        hideConfirmModal();
    }
}, { passive: false });

confirmModal.addEventListener('click', function(event) {
    event.preventDefault();
    event.stopPropagation();
    if (event.target === confirmModal) {
        hideConfirmModal();
    }
});

document.querySelector('.confirm-modal-content').addEventListener('touchstart', function(event) {
    event.preventDefault();
    event.stopPropagation();
}, { passive: false });

document.querySelector('.confirm-modal-content').addEventListener('touchend', function(event) {
    event.preventDefault();
    event.stopPropagation();
}, { passive: false });

let gameId = null;
let userId = null;
let moveNumber = 0;
let lastMoveTime = Date.now();
let board = null;
let score = 0;
let bestScore = localStorage.getItem('2048-best-score') || 0;
let isGameOver = false;
let lastSuccessfulMoveNumber = 0;
let touchStartX = 0;
let touchStartY = 0;
let touchEndX = 0;
let touchEndY = 0;
let moveSpeed = 0;
let lastVibrationStyle = 'light';
let consecutiveMoves = 0;
let lastMoveDirection = null;

const minSwipeDistance = 50;
const minSwipeSpeed = 0.5;
let touchStartTime = 0;
let isGameAreaTouched = false;
let isMovePending = false;

function createMoveHash(gameId, userId, moveNumber, boardState, score) {
    const message = `${gameId}:${userId}:${moveNumber}:${boardState}:${score}`;
    let hash = 0;
    for (let i = 0; i < message.length; i++) {
        hash = (hash * 31 + message.charCodeAt(i)) >>> 0;
    }
    return hash.toString(16);
}

function initGame() {
    board = Array(4).fill().map(() => Array(4).fill(0));
    score = 0;
    isGameOver = false;
    moveNumber = 0;
    lastSuccessfulMoveNumber = 0;

    addNewTile();
    addNewTile();

    updateDisplay();
}

function addNewTile() {
    const emptyCells = [];
    for (let i = 0; i < 4; i++) {
        for (let j = 0; j < 4; j++) {
            if (board[i][j] === 0) {
                emptyCells.push({x: i, y: j});
            }
        }
    }
    if (emptyCells.length > 0) {
        const randomCell = emptyCells[Math.floor(Math.random() * emptyCells.length)];
        board[randomCell.x][randomCell.y] = Math.random() < 0.9 ? 2 : 4;
    }
}

function updateDisplay() {
    grid.innerHTML = '';
    for (let i = 0; i < 4; i++) {
        for (let j = 0; j < 4; j++) {
            const cell = document.createElement('div');
            cell.className = 'cell';
            if (board[i][j] !== 0) {
                cell.textContent = board[i][j];
                cell.style.backgroundColor = getTileColor(board[i][j]);
                cell.style.color = board[i][j] <= 4 ? '#776e65' : '#f9f6f2';
                cell.style.fontSize = board[i][j] >= 1000 ? '20px' : '24px';
                cell.dataset.value = board[i][j];

            }
            grid.appendChild(cell);
        }
    }
}

function getTileColor(value) {
    const colors = {
        2: '#eee4da',
        4: '#ede0c8',
        8: '#f2b179',
        16: '#f59563',
        32: '#f67c5f',
        64: '#f65e3b',
        128: '#edcf72',
        256: '#edcc61',
        512: '#edc850',
        1024: '#edc53f',
        2048: '#edc22e'
    };
    return colors[value] || '#edc22e';
}

function updateScore() {
    const scoreElement = document.getElementById('score');
    if (scoreElement) {

        scoreElement.classList.remove('score-updated');
        void scoreElement.offsetWidth; 
        scoreElement.classList.add('score-updated');
        scoreElement.textContent = score;
    }

    if (score > bestScore) {
        bestScore = score;
        localStorage.setItem('2048-best-score', bestScore);
    }
}

function getVibrationStyle() {
    const now = Date.now();
    moveSpeed = now - lastMoveTime;
    lastMoveTime = now;

    if (moveSpeed < 100) { 
        consecutiveMoves++;
        if (consecutiveMoves >= 3) {
            return 'medium';
        }
        return 'medium';
    } else if (moveSpeed < 200) { 
        consecutiveMoves++;
        if (consecutiveMoves >= 2) {
            return 'medium';
        }
        return 'light';
    } else { 
        consecutiveMoves = 0;
        return 'light';
    }
}

function moveLeft() {
    let moved = false;
    const newBoard = board.map(row => [...row]);

    for (let i = 0; i < 4; i++) {
        let row = newBoard[i];
        let newRow = row.filter(cell => cell !== 0);

        for (let j = 0; j < newRow.length - 1; j++) {
            if (newRow[j] === newRow[j + 1]) {
                newRow[j] *= 2;
                score += newRow[j];
                newRow.splice(j + 1, 1);
                moved = true;
                updateScore(); 

                if (newRow[j] >= 2048) {
                    tg.HapticFeedback.impactOccurred('heavy');
                    tg.HapticFeedback.impactOccurred('heavy');
                } else if (newRow[j] >= 512) {
                    tg.HapticFeedback.impactOccurred('medium');
                    tg.HapticFeedback.impactOccurred('medium');
                } else if (newRow[j] >= 128) {
                    tg.HapticFeedback.impactOccurred('medium');
                } else if (newRow[j] >= 32) {
                    tg.HapticFeedback.impactOccurred('soft');
                } else {
                    tg.HapticFeedback.impactOccurred('light');
                }
            }
        }

        while (newRow.length < 4) {
            newRow.push(0);
        }

        if (JSON.stringify(row) !== JSON.stringify(newRow)) {
            moved = true;
        }

        newBoard[i] = newRow;
    }

    if (moved) {
        board = newBoard;
    }

    return moved;
}

function moveRight() {
    let moved = false;
    const newBoard = board.map(row => [...row]);

    for (let i = 0; i < 4; i++) {
        let row = newBoard[i];
        let newRow = row.filter(cell => cell !== 0);

        for (let j = newRow.length - 1; j > 0; j--) {
            if (newRow[j] === newRow[j - 1]) {
                newRow[j] *= 2;
                score += newRow[j];
                newRow.splice(j - 1, 1);
                moved = true;
                updateScore(); 
            }
        }

        while (newRow.length < 4) {
            newRow.unshift(0);
        }

        if (JSON.stringify(row) !== JSON.stringify(newRow)) {
            moved = true;
        }

        newBoard[i] = newRow;
    }

    if (moved) {
        board = newBoard;
    }

    return moved;
}

function moveUp() {
    let moved = false;
    const newBoard = board.map(row => [...row]);

    for (let j = 0; j < 4; j++) {
        let column = [newBoard[0][j], newBoard[1][j], newBoard[2][j], newBoard[3][j]];
        let newColumn = column.filter(cell => cell !== 0);

        for (let i = 0; i < newColumn.length - 1; i++) {
            if (newColumn[i] === newColumn[i + 1]) {
                newColumn[i] *= 2;
                score += newColumn[i];
                newColumn.splice(i + 1, 1);
                moved = true;
                updateScore(); 
            }
        }

        while (newColumn.length < 4) {
            newColumn.push(0);
        }

        if (JSON.stringify(column) !== JSON.stringify(newColumn)) {
            moved = true;
        }

        for (let i = 0; i < 4; i++) {
            newBoard[i][j] = newColumn[i];
        }
    }

    if (moved) {
        board = newBoard;
    }

    return moved;
}

function moveDown() {
    let moved = false;
    const newBoard = board.map(row => [...row]);

    for (let j = 0; j < 4; j++) {
        let column = [newBoard[0][j], newBoard[1][j], newBoard[2][j], newBoard[3][j]];
        let newColumn = column.filter(cell => cell !== 0);

        for (let i = newColumn.length - 1; i > 0; i--) {
            if (newColumn[i] === newColumn[i - 1]) {
                newColumn[i] *= 2;
                score += newColumn[i];
                newColumn.splice(i - 1, 1);
                moved = true;
                updateScore(); 
            }
        }

        while (newColumn.length < 4) {
            newColumn.unshift(0);
        }

        if (JSON.stringify(column) !== JSON.stringify(newColumn)) {
            moved = true;
        }

        for (let i = 0; i < 4; i++) {
            newBoard[i][j] = newColumn[i];
        }
    }

    if (moved) {
        board = newBoard;
    }

    return moved;
}

function handleMove(direction) {
    if (isGameOver || isMovePending) return false;

    let moved = false;
    const oldScore = score;

    switch(direction) {
        case 'left':
            moved = moveLeft();
            break;
        case 'right':
            moved = moveRight();
            break;
        case 'up':
            moved = moveUp();
            break;
        case 'down':
            moved = moveDown();
            break;
    }

    if (moved) {
        tiltBoard(direction);

        tg.HapticFeedback.impactOccurred('light');

        if (score - oldScore >= 128) {
            setTimeout(() => {
                tg.HapticFeedback.impactOccurred('medium');
            }, 50);
        }

        addNewTile();
        send2048Score(score);
        checkGameOver();
    }

    updateDisplay();
    return moved;
}

function checkGameOver() {
    if (!canMove()) {
        isGameOver = true;

        send2048Score(score);

        tg.HapticFeedback.impactOccurred('heavy');
        setTimeout(() => {
            tg.HapticFeedback.impactOccurred('heavy');
        }, 200);
        setTimeout(() => {
            tg.HapticFeedback.notificationOccurred('error');
        }, 400);

        setTimeout(() => {
            tg.showAlert(`Игра окончена! Ваш счёт: ${score}`);
        }, 500);
    }
}

function canMove() {

    if (board.some(row => row.some(cell => cell === 0))) {
        return true;
    }

    for (let i = 0; i < 4; i++) {
        for (let j = 0; j < 3; j++) {
            if (board[i][j] === board[i][j + 1]) {
                return true;
            }
        }
    }

    for (let i = 0; i < 3; i++) {
        for (let j = 0; j < 4; j++) {
            if (board[i][j] === board[i + 1][j]) {
                return true;
            }
        }
    }

    return false;
}

function send2048Score(score) {
    if (!gameId || !userId) {
        console.error('No game session');
        return;
    }
    if (isMovePending) {
        return;
    }
    isMovePending = true;
    const currentTime = Date.now();
    if (currentTime - lastMoveTime < 50) {
        isMovePending = false;
        return;
    }
    lastMoveTime = currentTime;
    const nextMoveNumber = lastSuccessfulMoveNumber + 1;
    const boardState = JSON.stringify(board);
    const moveHash = createMoveHash(
        gameId,
        userId,
        nextMoveNumber,
        boardState,
        score
    );
    console.log('Sending move:', {
        gameId,
        userId,
        moveNumber: nextMoveNumber,
        boardState,
        score,
        moveHash
    });
    fetch('/api/submit/2048', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            tgWebAppData: tg.initData,
            game_id: gameId,
            move_number: nextMoveNumber,
            board_state: boardState,
            score: score,
            move_hash: moveHash
        })
    })
    .then(response => response.json())
    .then(data => {
        isMovePending = false;
        if (data.error) {
            console.error('Score submission error:', data.error);
            if (data.error.toLowerCase().includes('move number')) {
                load2048Rating();
                alert('Ошибка: рассинхронизация ходов! Попробуйте сделать следующий ход или перезапустить игру.');
            } else {
                alert('Ошибка отправки результата: ' + data.error);
            }
        } else {
            lastSuccessfulMoveNumber = nextMoveNumber;
        }
        return load2048Rating();
    })
    .catch(error => {
        isMovePending = false;
        console.error('Failed to submit score:', error);
        alert('Ошибка соединения с сервером!');
    });
}

function load2048Rating() {
    fetch('/api/rating/2048')
        .then(r => r.json())
        .then(data => {
            const tbody = document.querySelector('#rating-2048-table tbody');
            if (!tbody) {
                console.warn('Таблица рейтинга не найдена');
                return;
            }
            tbody.innerHTML = '';
            data.rating.forEach((row, i) => {
                const name = row.first_name || '';
                const displayName = name.length > 13 ? name.substring(0, 13) + '...' : name;
                const tr = document.createElement('tr');
                tr.innerHTML = `<td>${i+1}</td><td>${displayName}</td><td>${row.score}</td>`;
                tbody.appendChild(tr);
            });
        })
        .catch(error => {
            console.error('Ошибка при загрузке рейтинга:', error);
        });
}

document.addEventListener('keydown', function(e) {
    if(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
        e.preventDefault();

        switch(e.key) {
            case 'ArrowLeft':
                handleMove('left');
                break;
            case 'ArrowRight':
                handleMove('right');
                break;
            case 'ArrowUp':
                handleMove('up');
                break;
            case 'ArrowDown':
                handleMove('down');
                break;
        }
    }
});

function handleTouchStart(event) {
    isGameAreaTouched = true;
    touchStartX = event.touches[0].clientX;
    touchStartY = event.touches[0].clientY;
    touchStartTime = Date.now();
    lastMoveTime = Date.now();
    consecutiveMoves = 0;

    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.setBackButtonVisible(false);
        window.Telegram.WebApp.expand();
        window.Telegram.WebApp.disableClosingConfirmation();
    }

    document.body.style.overflow = 'hidden';
    event.preventDefault();
}

function handleTouchMove(event) {
    if (!isGameAreaTouched) return;

    touchEndX = event.touches[0].clientX;
    touchEndY = event.touches[0].clientY;

    event.preventDefault();
}

function handleTouchEnd(event) {
    if (!isGameAreaTouched) return;

    const touchEndTime = Date.now();
    const deltaTime = touchEndTime - touchStartTime;
    const deltaX = touchEndX - touchStartX;
    const deltaY = touchEndY - touchStartY;

    const swipeSpeed = Math.sqrt(deltaX * deltaX + deltaY * deltaY) / deltaTime;

    if ((Math.abs(deltaX) > minSwipeDistance || Math.abs(deltaY) > minSwipeDistance) && 
        swipeSpeed > minSwipeSpeed) {
        if (Math.abs(deltaX) > Math.abs(deltaY) * 1.5) {
            if (deltaX > 0) {
                handleMove('right');
            } else {
                handleMove('left');
            }
        } else if (Math.abs(deltaY) > Math.abs(deltaX) * 1.5) {
            if (deltaY > 0) {
                handleMove('down');
            } else {
                handleMove('up');
            }
        }
    }

    isGameAreaTouched = false;
    document.body.style.overflow = 'auto';

    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.setBackButtonVisible(true);
        window.Telegram.WebApp.enableClosingConfirmation();
    }

    event.preventDefault();
}

const gameArea = document.querySelector('.game-area');
gameArea.addEventListener('touchstart', handleTouchStart, { passive: false });
gameArea.addEventListener('touchmove', handleTouchMove, { passive: false });
gameArea.addEventListener('touchend', handleTouchEnd, { passive: false });

document.addEventListener('touchcancel', () => {
    if (isGameAreaTouched) {
        isGameAreaTouched = false;
        document.body.style.overflow = 'auto';
        if (window.Telegram && window.Telegram.WebApp) {
            window.Telegram.WebApp.setBackButtonVisible(true);
            window.Telegram.WebApp.enableClosingConfirmation();
        }
    }
});

document.addEventListener('DOMContentLoaded', function() {
    tg.expand();
    tg.BackButton.show();
    tg.BackButton.onClick(function() {
        window.location.href = '/';
    });

    document.addEventListener('keydown', function(e) {
        if(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
            e.preventDefault(); 

            if (!isGameOver) {
                switch(e.key) {
                    case 'ArrowLeft':
                        handleMove('left');
                        break;
                    case 'ArrowRight':
                        handleMove('right');
                        break;
                    case 'ArrowUp':
                        handleMove('up');
                        break;
                    case 'ArrowDown':
                        handleMove('down');
                        break;
                }
            }
        }
    });

    let touchStartX = 0;
    let touchStartY = 0;

    gameArea.addEventListener('touchstart', function(e) {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
        e.preventDefault();
    }, { passive: false });

    gameArea.addEventListener('touchmove', function(e) {
        e.preventDefault();
    }, { passive: false });

    gameArea.addEventListener('touchend', function(e) {
        if (isGameOver) return;

        const touchEndX = e.changedTouches[0].clientX;
        const touchEndY = e.changedTouches[0].clientY;
        const dx = touchEndX - touchStartX;
        const dy = touchEndY - touchStartY;

        const minSwipeDistance = 30;

        if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > minSwipeDistance) {

            if (dx > 0) {
                handleMove('right');
            } else {
                handleMove('left');
            }
        } else if (Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > minSwipeDistance) {

            if (dy > 0) {
                handleMove('down');
            } else {
                handleMove('up');
            }
        }
        e.preventDefault();
    }, { passive: false });

    load2048Rating();
    startNewGame();
});

function startNewGame() {
    fetch('/api/2048/new_game', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            tgWebAppData: tg.initData
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.ok) {
            gameId = data.game_id;
            userId = data.user_id;
            lastSuccessfulMoveNumber = 0;
            initGame();
        } else {
            console.error('Failed to create new game:', data.error);
            tg.showAlert('Ошибка при создании игры');
        }
    })
    .catch(error => {
        console.error('Failed to start new game:', error);
        tg.showAlert('Ошибка при создании игры');
    });
}

function updateCSS() {
    const style = document.createElement('style');
    style.textContent = `
        body {
            overflow: hidden;
            position: fixed;
            width: 100%;
            height: 100%;
            -webkit-overflow-scrolling: touch;
        }

        .game-container {
            overflow-y: auto;
            max-height: 100vh;
            -webkit-overflow-scrolling: touch;
        }

        .game-area {
            touch-action: none;
            -webkit-touch-callout: none;
            -webkit-user-select: none;
            -khtml-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
            user-select: none;
        }
    `;
    document.head.appendChild(style);
}

updateCSS();

function showConfirmModal() {
    confirmModal.style.display = 'flex';
    confirmModal.style.opacity = '0';
    setTimeout(() => {
        confirmModal.style.opacity = '1';
    }, 10);
    tg.HapticFeedback.impactOccurred('light');
}

function hideConfirmModal() {
    confirmModal.style.opacity = '0';
    setTimeout(() => {
        confirmModal.style.display = 'none';
    }, 300);
}

initGame();