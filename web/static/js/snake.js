let tg = window.Telegram.WebApp;
let gameId = null;
let moveNumber = 0;

function createSnakeSession() {
    return fetch('/api/snake/new_game', {
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
            return data;
        }
        return null;
    });
}

document.addEventListener('DOMContentLoaded', function() {

    tg.expand(); 

    tg.BackButton.show();
    tg.BackButton.onClick(function() {
        window.location.href = '/';
    });

    const canvas = document.getElementById('gameCanvas');
    const ctx = canvas.getContext('2d');
    const gridSize = 20;

    function resizeCanvas() {
        const gameArea = document.querySelector('.game-area');
        const size = Math.min(gameArea.clientWidth, gameArea.clientHeight);
        canvas.width = size;
        canvas.height = size;
    }

    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    let snake = [{x: 10, y: 10}];
    let food = {x: 15, y: 15};
    let direction = 'right';
    let nextDirection = 'right';
    let score = 0;
    let gameSpeed = 150;
    let isGameRunning = false;
    let isPaused = false;
    let isFoodRequestPending = false;
    let pendingMovePath = [];
    let lastTime;
    let touchStartX = 0;
    let touchStartY = 0;

    function startNewGame() {

        const modal = document.getElementById('gameOverModal');
        modal.style.display = 'none';

        createSnakeSession().then(session => {
            if (session) {
                moveNumber = 0;
                initGame(session.food_position, session.snake_state);
                isGameRunning = true;
                gameLoop(0);
                document.getElementById('startButton').disabled = true;
                document.getElementById('pauseButton').disabled = false;
            } else {
                tg.showAlert('Ошибка при создании игры');
            }
        })
        .catch(error => {
            console.error('Failed to start new game:', error);
            tg.showAlert('Ошибка при создании игры');
        });
    }

    function sendSnakeScore() {
        if (!gameId) return Promise.reject(new Error('No game session'));

        const nextMoveNumber = moveNumber + 1;
        const movePath = pendingMovePath.slice();
        return fetch('/api/submit/snake', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tgWebAppData: tg.initData,
                game_id: gameId,
                move_number: nextMoveNumber,
                move_path: movePath
            })
        })
        .then(async response => {
            const data = await response.json();
            if (!response.ok || data.error) {
                throw new Error(data.error || 'Score submission failed');
            }
            return data;
        })
        .then(data => {
            moveNumber = data.move_number;
            score = data.score;
            document.getElementById('score').textContent = score;
            snake = data.snake_state;
            pendingMovePath = [];
            if (data.game_over) {
                gameOver();
            }
            food = data.food_position;
            return loadSnakeRating();
        }).then(() => {
            isFoodRequestPending = false;
        }).catch(error => {
            isFoodRequestPending = false;
            isGameRunning = false;
            document.getElementById('startButton').disabled = false;
            document.getElementById('pauseButton').disabled = true;
            tg.showAlert('Игра остановлена: результат не подтверждён сервером.');
            throw error;
        });
    }

    function loadSnakeRating() {
        fetch('/api/rating/snake')
            .then(r => r.json())
            .then(data => {
                const tbody = document.querySelector('#snake-rating-table tbody');
                if (!tbody) {
                    console.warn('Таблица рейтинга не найдена');
                    return;
                }
                tbody.innerHTML = '';
                data.rating.forEach((row, i) => {
                    const name = row.first_name || '';
                    const displayName = name.length > 13 ? name.substring(0, 13) + '...' : name;
                    const tr = document.createElement('tr');
                    const positionCell = document.createElement('td');
                    const nameCell = document.createElement('td');
                    const scoreCell = document.createElement('td');
                    positionCell.textContent = String(i + 1);
                    nameCell.textContent = displayName;
                    scoreCell.textContent = String(row.score);
                    tr.append(positionCell, nameCell, scoreCell);
                    tbody.appendChild(tr);
                });
            })
            .catch(error => {
                console.error('Ошибка при загрузке рейтинга:', error);
            });
    }

    document.addEventListener('keydown', function(e) {
        if(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space'].includes(e.code)) {
            e.preventDefault();

            if (!isGameRunning) return;

            switch(e.code) {
                case 'ArrowUp':
                    if (direction !== 'down') nextDirection = 'up';
                    break;
                case 'ArrowDown':
                    if (direction !== 'up') nextDirection = 'down';
                    break;
                case 'ArrowLeft':
                    if (direction !== 'right') nextDirection = 'left';
                    break;
                case 'ArrowRight':
                    if (direction !== 'left') nextDirection = 'right';
                    break;
                case 'Space':
                    togglePause();
                    break;
            }
        }
    });

    canvas.addEventListener('touchstart', function(e) {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
        e.preventDefault();
    }, { passive: false });

    canvas.addEventListener('touchmove', function(e) {
        e.preventDefault();
    }, { passive: false });

    canvas.addEventListener('touchend', function(e) {
        const touchEndX = e.changedTouches[0].clientX;
        const touchEndY = e.changedTouches[0].clientY;
        const dx = touchEndX - touchStartX;
        const dy = touchEndY - touchStartY;

        if (Math.abs(dx) > Math.abs(dy)) {
            if (dx > 0 && direction !== 'left') {
                nextDirection = 'right';
            } else if (dx < 0 && direction !== 'right') {
                nextDirection = 'left';
            }
        } else {
            if (dy > 0 && direction !== 'up') {
                nextDirection = 'down';
            } else if (dy < 0 && direction !== 'down') {
                nextDirection = 'up';
            }
        }
        e.preventDefault();
    }, { passive: false });

    document.getElementById('startButton').addEventListener('click', function() {
        if (!isGameRunning) {
            this.disabled = true;
            document.getElementById('pauseButton').disabled = false;
            startNewGame();
        }
    });

    document.getElementById('pauseButton').addEventListener('click', togglePause);

    function initGame(initialFood, initialSnake) {

        snake = initialSnake;
        food = initialFood;
        direction = 'right';
        nextDirection = 'right';
        score = 0;
        gameSpeed = 150;
        moveNumber = 0;
        isFoodRequestPending = false;
        pendingMovePath = [];
        lastTime = null;
        document.getElementById('score').textContent = score;
    }

    function update(currentTime) {
        if (isFoodRequestPending) return;
        if (!lastTime) lastTime = currentTime;
        const deltaTime = currentTime - lastTime;

        if (deltaTime >= gameSpeed) {
            lastTime = currentTime;
            direction = nextDirection;

            const head = {...snake[0]};
            switch(direction) {
                case 'up': head.y--; break;
                case 'down': head.y++; break;
                case 'left': head.x--; break;
                case 'right': head.x++; break;
            }

            if (head.x < 0 || head.x >= 20 || head.y < 0 || head.y >= 20) {
                gameOver();
                return;
            }

            if (snake.some(segment => segment.x === head.x && segment.y === head.y)) {
                gameOver();
                return;
            }

            pendingMovePath.push(direction);
            snake.unshift(head);

            if (head.x === food.x && head.y === food.y) {
                score++;
                document.getElementById('score').textContent = score;
                gameSpeed = Math.max(50, 150 - (score * 2));
                tg.HapticFeedback.impactOccurred('soft');
                isFoodRequestPending = true;
                sendSnakeScore().catch(error => {
                    console.error('Failed to submit score:', error);
                });
            } else {
                snake.pop();
            }
        }
    }

    function draw() {

        ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--tg-theme-bg-color');
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        snake.forEach((segment, index) => {
            ctx.fillStyle = index === 0 ? '#2ecc71' : '#27ae60';
            ctx.fillRect(
                segment.x * (canvas.width / 20),
                segment.y * (canvas.height / 20),
                (canvas.width / 20) - 1,
                (canvas.height / 20) - 1
            );
        });

        ctx.fillStyle = '#e74c3c';
        ctx.fillRect(
            food.x * (canvas.width / 20),
            food.y * (canvas.height / 20),
            (canvas.width / 20) - 1,
            (canvas.height / 20) - 1
        );
    }

    function gameLoop(currentTime) {
        if (isGameRunning && !isPaused) {
            update(currentTime);
            draw();
            requestAnimationFrame(gameLoop);
        }
    }

    function gameOver() {
        isGameRunning = false;
        tg.HapticFeedback.impactOccurred('heavy');

        const modal = document.getElementById('gameOverModal');
        document.getElementById('finalScore').textContent = score;
        modal.style.display = 'flex';

        document.getElementById('startButton').disabled = false;
        document.getElementById('pauseButton').disabled = true;
    }

    document.getElementById('playAgainButton').addEventListener('click', function() {
        const modal = document.getElementById('gameOverModal');
        modal.style.display = 'none';
        startNewGame();
    });

    function togglePause() {
        if (!isGameRunning) return;

        isPaused = !isPaused;
        const pauseButton = document.getElementById('pauseButton');
        pauseButton.textContent = isPaused ? 'Продолжить' : 'Пауза';

        if (!isPaused) {
            lastTime = null;
            gameLoop(0);
        }
    }

    loadSnakeRating();
});
