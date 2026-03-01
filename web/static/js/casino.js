let tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

function getTgWebAppData() {
    if (tg && tg.initData) return tg.initData;
    const urlParam = new URLSearchParams(window.location.search).get('tgWebAppData');
    if (urlParam) return urlParam;
    return null;
}

document.addEventListener('DOMContentLoaded', function() {
    if (tg) tg.expand();
    const balanceElement = document.getElementById('casinoBalance');
    const betForm = document.getElementById('casinoBetForm');
    const betInput = document.getElementById('casinoBetInput');
    const resultElement = document.getElementById('casinoResult');
    const ratingList = document.getElementById('casinoRatingList');
    const slotMachine = document.getElementById('casinoSlotMachine');
    const reels = [
        document.getElementById('reel1'),
        document.getElementById('reel2'),
        document.getElementById('reel3')
    ];
    const symbols = ['🍒', '🍊', '🍋', '🍇', '🍉', '🍎', '🍓', '🍍', '🍌'];
    let maxBet = 10;

    function updateMaxBet(balance) {
        if (balance > 10) {
            maxBet = balance;
        } else {
            maxBet = 10;
        }
        betInput.max = maxBet;
        betInput.placeholder = `Ставка (до ${maxBet})`;
    }

    function loadBalance() {
        fetch('/api/casino/balance', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tgWebAppData: getTgWebAppData() })
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                balanceElement.textContent = data.score;
                updateMaxBet(data.score);
            } else {
                balanceElement.textContent = '0';
                updateMaxBet(0);
            }
        })
        .catch(() => { balanceElement.textContent = '0'; updateMaxBet(0); });
    }

    function loadRating() {
        fetch('/api/casino/rating')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                ratingList.innerHTML = '';
                data.rating.forEach(row => {
                    let name = row.first_name || row.username || 'Игрок';
                    if (row.username) name += ` (@${row.username})`;
                    const li = document.createElement('li');
                    li.innerHTML = `<span>${name}</span><span>${row.score}</span>`;
                    ratingList.appendChild(li);
                });
            }
        });
    }

    function spinReels() {
        reels.forEach(reel => reel.classList.add('spinning'));
        setTimeout(() => {
            reels[0].classList.remove('spinning');
            setTimeout(() => {
                reels[1].classList.remove('spinning');
                setTimeout(() => {
                    reels[2].classList.remove('spinning');
                }, 500);
            }, 500);
        }, 500);
    }
    function getRandomSymbol() {
        return symbols[Math.floor(Math.random() * symbols.length)];
    }
    function updateReelSymbols() {
        reels.forEach(reel => {
            const symbols = reel.querySelectorAll('.casino-slot-symbol');
            symbols.forEach(symbol => {
                symbol.textContent = getRandomSymbol();
            });
        });
    }
    betForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const betAmount = parseInt(betInput.value);
        if (isNaN(betAmount) || betAmount <= 0) {
            resultElement.textContent = 'Введите корректную сумму ставки';
            resultElement.className = 'casino-result lose';
            return;
        }
        if (betAmount > maxBet) {
            resultElement.textContent = `Максимальная ставка: ${maxBet}`;
            resultElement.className = 'casino-result lose';
            betInput.value = maxBet;
            return;
        }
        spinReels();
        updateReelSymbols();
        fetch('/api/casino/bet', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tgWebAppData: getTgWebAppData(), bet: betAmount })
        })
        .then(r => r.json())
        .then(data => {
            setTimeout(() => {
                if (data.success) {
                    if (data.type === 'superwin') {
                        resultElement.textContent = `🎉 СУПЕРВЫИГРЫШ! Вы получаете ${data.win_amount} очков!`;
                        resultElement.className = 'casino-result win';
                        slotMachine.classList.add('superwin');
                        if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
                        setTimeout(() => slotMachine.classList.remove('superwin'), 2000);
                    } else if (data.type === 'win') {
                        resultElement.textContent = `Поздравляем! Вы выиграли ${data.win_amount || betAmount} очков!`;
                        resultElement.className = 'casino-result win';
                        if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('heavy');
                    } else if (data.type === 'lose') {
                        resultElement.textContent = `Вы проиграли ${data.loss_amount || Math.floor(betAmount/2)} очков. Попробуйте еще раз!`;
                        resultElement.className = 'casino-result lose';
                        if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
                    }
                    loadBalance();
                    loadRating();
                    if (typeof data.max_bet !== 'undefined') {
                        updateMaxBet(data.max_bet);
                    }
                } else {
                    resultElement.textContent = data.error || data.message || 'Произошла ошибка';
                    resultElement.className = 'casino-result lose';
                    if (typeof data.max_bet !== 'undefined') {
                        updateMaxBet(data.max_bet);
                    }
                }
            }, 2000);
        })
        .catch(() => {
            resultElement.textContent = 'Произошла ошибка при отправке ставки';
            resultElement.className = 'casino-result lose';
        });
    });
    loadBalance();
    loadRating();
    const betButton = document.querySelector('.casino-bet-btn');
    betButton.addEventListener('click', function() {
        if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
    });
}); 