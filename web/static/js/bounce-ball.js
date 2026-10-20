document.addEventListener('DOMContentLoaded', function() {
    let tg = window.Telegram.WebApp;
    tg.expand();
    tg.BackButton.show();
    tg.BackButton.onClick(function() {
        window.location.href = '/';
    });

    const canvas = document.getElementById('gameCanvas');
    const ctx = canvas.getContext('2d');

    let isGameRunning = false;
    let lastTime = 0;
    let ballSpeed = 5;
    let paddleSpeed = 8;
    let scores = { player1: 0, player2: 0 };
    let activeTouches = new Map();

    const ball = {
        x: 0,
        y: 0,
        radius: 10,
        dx: 0,
        dy: 0
    };

    const paddle1 = {
        width: 100,
        height: 20,
        x: 0,
        y: 0,
        targetX: 0
    };

    const paddle2 = {
        width: 100,
        height: 20,
        x: 0,
        y: 0,
        targetX: 0
    };

    function resizeCanvas() {
        const container = document.querySelector('.game-area');
        canvas.width = container.clientWidth;
        canvas.height = window.innerHeight * 0.8;
        if (isGameRunning) {
            paddle1.y = 20;
            paddle2.y = canvas.height - paddle2.height - 20;
            if (ball.y > canvas.height) {
                ball.y = canvas.height - ball.radius;
            }
        }
    }

    function clamp(val, min, max) {
        return Math.max(min, Math.min(max, val));
    }

    function getRandomAngle(minDeg, maxDeg) {
        const min = minDeg * Math.PI / 180;
        const max = maxDeg * Math.PI / 180;
        return Math.random() * (max - min) + min;
    }

    function setBallVelocity(speed, angle, up) {
        ball.dx = speed * Math.cos(angle) * (Math.random() < 0.5 ? 1 : -1);
        ball.dy = speed * Math.sin(angle) * (up ? 1 : -1);
    }

    const BALL_MAX_SPEED = 12;
    const BALL_MIN_SPEED = 4;
    const BALL_MIN_ANGLE = 25;

    function initGame() {
        ball.x = canvas.width / 2;
        ball.y = canvas.height / 2;
        let angle = getRandomAngle(BALL_MIN_ANGLE, 90 - BALL_MIN_ANGLE);
        setBallVelocity(ballSpeed, angle, Math.random() < 0.5);
        paddle1.x = canvas.width / 2 - paddle1.width / 2;
        paddle1.y = 20;
        paddle1.targetX = paddle1.x;
        paddle2.x = canvas.width / 2 - paddle2.width / 2;
        paddle2.y = canvas.height - paddle2.height - 20;
        paddle2.targetX = paddle2.x;
        scores = { player1: 0, player2: 0 };
        document.getElementById('player1-score').textContent = scores.player1;
        document.getElementById('player2-score').textContent = scores.player2;
        ballSpeed = 5;
    }

    canvas.addEventListener('touchstart', function(e) {
        e.preventDefault();
        const touches = e.touches;
        for (let i = 0; i < touches.length; i++) {
            const touch = touches[i];
            const rect = canvas.getBoundingClientRect();
            const touchY = touch.clientY - rect.top;
            if (touchY < canvas.height / 2) {
                paddle1.targetX = touch.clientX - rect.left - paddle1.width / 2;
            } else {
                paddle2.targetX = touch.clientX - rect.left - paddle2.width / 2;
            }
            activeTouches.set(touch.identifier, touchY < canvas.height / 2 ? 'paddle1' : 'paddle2');
        }
    });

    canvas.addEventListener('touchmove', function(e) {
        e.preventDefault();
        const touches = e.touches;
        for (let i = 0; i < touches.length; i++) {
            const touch = touches[i];
            const paddle = activeTouches.get(touch.identifier);
            if (!paddle) continue;
            const rect = canvas.getBoundingClientRect();
            const targetX = touch.clientX - rect.left - (paddle === 'paddle1' ? paddle1.width : paddle2.width) / 2;
            if (paddle === 'paddle1') {
                paddle1.targetX = targetX;
            } else {
                paddle2.targetX = targetX;
            }
        }
    });

    canvas.addEventListener('touchend', function(e) {
        e.preventDefault();
        const touches = e.changedTouches;
        for (let i = 0; i < touches.length; i++) {
            activeTouches.delete(touches[i].identifier);
        }
    });

    function draw() {
        ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--tg-theme-bg-color');
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.beginPath();
        ctx.setLineDash([5, 5]);
        ctx.moveTo(0, canvas.height / 2);
        ctx.lineTo(canvas.width, canvas.height / 2);
        ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue('--tg-theme-hint-color');
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.arc(ball.x, ball.y, ball.radius, 0, Math.PI * 2);
        ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--tg-theme-text-color');
        ctx.fill();
        ctx.closePath();
        ctx.fillStyle = '#3498db';
        ctx.fillRect(paddle1.x, paddle1.y, paddle1.width, paddle1.height);
        ctx.fillStyle = '#e74c3c';
        ctx.fillRect(paddle2.x, paddle2.y, paddle2.width, paddle2.height);
    }

    function update(currentTime) {
        if (!lastTime) lastTime = currentTime;
        const deltaTime = (currentTime - lastTime) / 1000;
        lastTime = currentTime;
        const dx1 = paddle1.targetX - paddle1.x;
        paddle1.x += dx1 * paddleSpeed * deltaTime;
        const dx2 = paddle2.targetX - paddle2.x;
        paddle2.x += dx2 * paddleSpeed * deltaTime;
        if (paddle1.x < 0) paddle1.x = 0;
        if (paddle1.x + paddle1.width > canvas.width) {
            paddle1.x = canvas.width - paddle1.width;
        }
        if (paddle2.x < 0) paddle2.x = 0;
        if (paddle2.x + paddle2.width > canvas.width) {
            paddle2.x = canvas.width - paddle2.width;
        }
        ball.x += ball.dx;
        ball.y += ball.dy;
        if (ball.x + ball.radius > canvas.width || ball.x - ball.radius < 0) {
            ball.dx = -ball.dx;
        }
        if (ball.y - ball.radius < paddle1.y + paddle1.height && ball.x > paddle1.x && ball.x < paddle1.x + paddle1.width && ball.dy < 0) {
            let hit = (ball.x - (paddle1.x + paddle1.width / 2)) / (paddle1.width / 2);
            hit = clamp(hit, -1, 1);
            let angle = hit * (Math.PI / 3);
            let speed = clamp(Math.sqrt(ball.dx * ball.dx + ball.dy * ball.dy) * 1.05, BALL_MIN_SPEED, BALL_MAX_SPEED);
            ball.dx = speed * Math.sin(angle);
            ball.dy = Math.abs(speed * Math.cos(angle));
            ballSpeed = speed;
            tg.HapticFeedback.impactOccurred('soft');
        }
        if (ball.y + ball.radius > paddle2.y && ball.x > paddle2.x && ball.x < paddle2.x + paddle2.width && ball.dy > 0) {
            let hit = (ball.x - (paddle2.x + paddle2.width / 2)) / (paddle2.width / 2);
            hit = clamp(hit, -1, 1);
            let angle = hit * (Math.PI / 3);
            let speed = clamp(Math.sqrt(ball.dx * ball.dx + ball.dy * ball.dy) * 1.05, BALL_MIN_SPEED, BALL_MAX_SPEED);
            ball.dx = speed * Math.sin(angle);
            ball.dy = -Math.abs(speed * Math.cos(angle));
            ballSpeed = speed;
            tg.HapticFeedback.impactOccurred('soft');
        }
        if (ball.y - ball.radius < 0) {
            scores.player2++;
            document.getElementById('player2-score').textContent = scores.player2;
            tg.HapticFeedback.impactOccurred('heavy');
            resetBall();
        }
        if (ball.y + ball.radius > canvas.height) {
            scores.player1++;
            document.getElementById('player1-score').textContent = scores.player1;
            tg.HapticFeedback.impactOccurred('heavy');
            resetBall();
        }
    }

    function resetBall() {
        ball.x = canvas.width / 2;
        ball.y = canvas.height / 2;
        let angle = getRandomAngle(BALL_MIN_ANGLE, 90 - BALL_MIN_ANGLE);
        setBallVelocity(ballSpeed, angle, Math.random() < 0.5);
    }

    function gameLoop(currentTime) {
        if (isGameRunning) {
            update(currentTime);
            draw();
            requestAnimationFrame(gameLoop);
        }
    }

    document.getElementById('startButton').addEventListener('click', function() {
        if (!isGameRunning) {
            isGameRunning = true;
            gameLoop(0);
        }
    });

    document.getElementById('resetButton').addEventListener('click', function() {
        isGameRunning = false;
        initGame();
    });

    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();
    initGame();

    const gameArea = document.querySelector('.game-area');
    if (gameArea) {
        gameArea.addEventListener('touchstart', function(e) {
            e.preventDefault();
        }, { passive: false });
        gameArea.addEventListener('touchmove', function(e) {
            e.preventDefault();
        }, { passive: false });
    }
});