document.addEventListener('DOMContentLoaded', function() {
    let tg = window.Telegram.WebApp;
    tg.expand();
    tg.BackButton.show();
    tg.BackButton.onClick(function() {
        window.location.href = '/';
    });

    let currentPlayer = 'x';
    let gameBoard = ['', '', '', '', '', '', '', '', ''];
    let gameActive = true;
    let scoreX = 0;
    let scoreO = 0;

    const cells = document.querySelectorAll('.cell');
    const scoreXElement = document.getElementById('scoreX');
    const scoreOElement = document.getElementById('scoreO');
    const currentPlayerElement = document.getElementById('currentPlayer');
    const resetButton = document.getElementById('resetButton');
    const newGameButton = document.getElementById('newGameButton');
    const modal = document.getElementById('gameModal');
    const modalTitle = document.getElementById('modalTitle');
    const modalMessage = document.getElementById('modalMessage');
    const modalButton = document.getElementById('modalButton');

    cells.forEach(cell => {
        cell.addEventListener('click', handleCellClick);
    });

    resetButton.addEventListener('click', resetGame);
    newGameButton.addEventListener('click', newGame);
    modalButton.addEventListener('click', () => {
        modal.style.display = 'none';
    });

    function showModal(title, message) {
        modalTitle.textContent = title;
        modalMessage.textContent = message;
        modal.style.display = 'flex';
    }

    function handleCellClick(e) {
        const cell = e.target;
        const index = parseInt(cell.getAttribute('data-index'));

        if (gameBoard[index] !== '' || !gameActive) return;

        gameBoard[index] = currentPlayer;
        cell.textContent = currentPlayer;
        cell.classList.add(currentPlayer);
        tg.HapticFeedback.impactOccurred('soft');

        if (checkWin()) {
            gameActive = false;
            if (currentPlayer === 'x') {
                scoreX++;
                scoreXElement.textContent = scoreX;
                setTimeout(() => {
                    showModal('Победа!', 'Крестики выиграли!');
                }, 100);
            } else {
                scoreO++;
                scoreOElement.textContent = scoreO;
                setTimeout(() => {
                    showModal('Победа!', 'Нолики выиграли!');
                }, 100);
            }
            return;
        }

        if (checkDraw()) {
            gameActive = false;
            setTimeout(() => {
                showModal('Ничья!', 'Игра закончилась вничью!');
            }, 100);
            return;
        }

        currentPlayer = currentPlayer === 'x' ? 'o' : 'x';
        currentPlayerElement.textContent = currentPlayer === 'x' ? 'Крестики' : 'Нолики';
    }

    function checkWin() {
        const winPatterns = [
            [0, 1, 2], [3, 4, 5], [6, 7, 8], 
            [0, 3, 6], [1, 4, 7], [2, 5, 8], 
            [0, 4, 8], [2, 4, 6] 
        ];

        return winPatterns.some(pattern => {
            const [a, b, c] = pattern;
            return gameBoard[a] && 
                   gameBoard[a] === gameBoard[b] && 
                   gameBoard[a] === gameBoard[c];
        });
    }

    function checkDraw() {
        return gameBoard.every(cell => cell !== '');
    }

    function resetGame() {
        gameBoard = ['', '', '', '', '', '', '', '', ''];
        gameActive = true;
        currentPlayer = 'x';
        currentPlayerElement.textContent = 'Крестики';
        cells.forEach(cell => {
            cell.textContent = '';
            cell.classList.remove('x', 'o');
        });
    }

    function newGame() {
        resetGame();
        scoreX = 0;
        scoreO = 0;
        scoreXElement.textContent = scoreX;
        scoreOElement.textContent = scoreO;
    }

    resetGame();
});

tg.ready();