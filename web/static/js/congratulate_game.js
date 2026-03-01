// ========================================
// ИГРА-КЛИКЕР ПОЗДРАВЛЕНИЙ
// ========================================

class CongratulateGame {
    constructor() {
        this.totalCongratulations = 0;
        this.userCongratulations = 0;
        this.rating = [];
        this.stats = {};
        this.lastClickTime = 0;
        this.clickCooldown = 100; // 100мс между кликами (10 раз в секунду)
        this.maxClicksPerSecond = 10; // Максимум 10 кликов в секунду
        this.clickHistory = [];
        this.userId = null;
        this.userName = null;
        this.isTilting = false;
        this.isPointerDown = false;
        this.lastPressIsCenter = false;
        this.effectColors = ['#977143', '#d0b27c', '#c5a26b', '#473219', '#20160b', '#e6cc96'];
        
        // Защита от множественных запросов
        this.isLoadingData = false;
        this.isUpdatingEnergy = false;
        // Сглаживание наклона
        this.tiltRX = 0; this.tiltRY = 0; this.tiltScale = 1;
        this.tiltTargetRX = 0; this.tiltTargetRY = 0; this.tiltTargetScale = 1;
        this.tiltRAF = null;

        // Локальная серия быстрых кликов и целевой шаг бейджа
        const savedBest = parseInt(localStorage.getItem('cg_best_streak') || '0', 10);
        this.streak = { count: 0, last: 0, best: isNaN(savedBest) ? 0 : savedBest };
        this.streakBadgeStep = 50; // каждые 50 кликов подряд — бейдж
        
        // Система Energy (только отображение, данные с сервера)
        this.energy = {
            current: 0,
            max: 0,
            regenRate: 1, // +1 энергия каждые regenInterval мс
            regenInterval: 20000, // 20 секунд
            costPerClick: 1,
            lastRegenTime: Date.now()
        };
        
        // Уровни апгрейдов
        this.upgrades = {
            capacity: 1, // уровень улучшения емкости
            speed: 1     // уровень улучшения скорости
        };
        
        // Система бонусов
        this.bonuses = {
            boostTime: {
                active: false,
                endTime: 0,
                multiplier: 2,
                duration: 30000 // 30 секунд
            },
            criticalClick: {
                chance: 0.1, // 10% шанс
                minMultiplier: 2,
                maxMultiplier: 3
            }
        };
        
        // Клиентский батчинг кликов (антиспам)
        this.clickQueue = 0;              // Сколько кликов накоплено к отправке
        this.flushTimer = null;            // Таймер отложенной отправки
        this.flushInFlight = false;        // Идет ли сейчас отправка
        this.flushIntervalMs = 450;        // Дебаунс-интервал перед отправкой пакета
        this.flushBackoffMs = 0;           // Бэкофф после ошибок
        this.maxQueue = 200;               // Защита от переполнения очереди
        
        this.init();
    }

    init() {
        this.setupTelegram();
        this.setupEventListeners();
        this.setupUpgradeListeners();
        this.setupModalListeners();
        this.setupBonusListeners();
        this.updateBirthdayCountdown();
        this.showGlobalLoading();
        
        // Принудительно скрываем лоадер через 10 секунд если что-то пошло не так
        this.loaderTimeout = setTimeout(() => {
            console.warn('Принудительное скрытие лоадера по таймауту');
            this.hideGlobalLoading();
        }, 10000);
        
        // Показываем начальное состояние энергии до загрузки данных
        this.updateEnergyUI();
        
        this.loadInitialData();
        this.startAutoUpdate();
        // updateEnergyUI() и updateUpgradeUI() вызываются в loadInitialData() после получения данных
    }

    setupTelegram() {
        if (window.Telegram && window.Telegram.WebApp) {
            const tg = window.Telegram.WebApp;
            tg.ready();
            tg.expand();
            
            this.userId = tg.initDataUnsafe?.user?.id || null;
            this.userName = tg.initDataUnsafe?.user?.first_name || 'Аноним';
            this.initData = tg.initData || '';
            this.sessionToken = null;
            
            // Фолбэк для получения initData из URL параметров
            if (!this.initData) {
                const urlParams = new URLSearchParams(window.location.search);
                this.initData = urlParams.get('tgWebAppData') || '';
                window.__tgInitData = this.initData;
            }
            
            // Устанавливаем черный header
            tg.setHeaderColor('#000000');
            
            // Настройка темы
            if (tg.colorScheme === 'dark') {
                document.documentElement.setAttribute('data-theme', 'dark');
            }
            
            // Настройка кнопки "Назад"
            tg.BackButton.show();
            tg.BackButton.onClick(() => {
                window.location.href = '/';
            });
        } else {
            // Фолбэк когда Telegram WebApp недоступен
            const urlParams = new URLSearchParams(window.location.search);
            this.initData = urlParams.get('tgWebAppData') || '';
            window.__tgInitData = this.initData;
            
            // Попробуем распарсить user данные из initData
            if (this.initData) {
                try {
                    const params = new URLSearchParams(this.initData);
                    const userStr = params.get('user');
                    if (userStr) {
                        const user = JSON.parse(decodeURIComponent(userStr));
                        this.userId = user.id || null;
                        this.userName = user.first_name || 'Аноним';
                    }
                } catch (e) {
                    console.log('Не удалось распарсить user данные:', e);
                }
            }
        }
    }

    setupEventListeners() {
        const button = document.getElementById('congratulateButton');
        if (button) {
            button.addEventListener('click', (e) => {
                this.handleClick(e);
                // Форс‑ресет для мышиных быстрых кликов
                setTimeout(() => { if (!this.isPointerDown) this.resetTilt(); }, 160);
            });
            button.addEventListener('touchstart', (e) => { this.handleTouchStart(e); document.body.style.overflow = 'hidden'; });
            button.addEventListener('touchend', (e) => { this.handleTouchEnd(e); document.body.style.overflow = ''; });
            button.addEventListener('touchmove', (e) => this.handleTouchMove(e));
            // для наклона по направлению нажатия
            button.addEventListener('pointerdown', (e) => {
                this.isPointerDown = true;
                try { button.setPointerCapture?.(e.pointerId); } catch (_) {}
                this.tiltButton(e);
            });
            button.addEventListener('pointermove', (e) => { if (this.isPointerDown) this.tiltButton(e); });
            const endPointer = (e) => {
                this.isPointerDown = false;
                try { button.releasePointerCapture?.(e.pointerId); } catch (_) {}
                this.resetTilt();
            };
            button.addEventListener('pointerup', endPointer);
            button.addEventListener('pointercancel', endPointer);
            button.addEventListener('pointerleave', endPointer);

            // Double-tap: вспышка
            let lastTap = 0;
            button.addEventListener('pointerdown', (e) => {
                const now = Date.now();
                if (now - lastTap < 300) {
                    this.createTapFlash(e);
                }
                lastTap = now;
            });

            // Long-press: золотое кольцо
            let lpTimer = null;
            const lpStart = (e) => {
                clearTimeout(lpTimer);
                lpTimer = setTimeout(() => this.createLongRing(e), 550);
            };
            const lpEnd = () => { clearTimeout(lpTimer); };
            button.addEventListener('pointerdown', lpStart);
            button.addEventListener('pointerup', lpEnd);
            button.addEventListener('pointercancel', lpEnd);
            button.addEventListener('pointerleave', lpEnd);
        }

        // Безопасная отправка очереди при уходе со страницы/свертывании
        const flushOnHide = () => {
            if (document.visibilityState === 'hidden') {
                this.flushQueuedClicks({ useBeacon: true });
            }
        };
        document.addEventListener('visibilitychange', flushOnHide);
        window.addEventListener('pagehide', () => this.flushQueuedClicks({ useBeacon: true }));
        window.addEventListener('beforeunload', () => this.flushQueuedClicks({ useBeacon: true }));
    }

    async startAutoUpdate() {
        // Обновляем все данные каждые 20 секунд (объединяем запросы)
        setInterval(() => { this.loadData(); }, 20000);
        
        // Обновляем только энергию каждые 5 секунд (для плавной игры)
        setInterval(() => { this.updateEnergyFromServer(); }, 5000);
        
        // Обновляем обратный отсчет каждые 24 часа
        setInterval(() => {
            this.updateBirthdayCountdown();
        }, 24 * 60 * 60 * 1000);
        
        // Обновляем UI бонусов каждую секунду
        setInterval(() => {
            this.updateBoostUI();
        }, 1000);

        // Получаем короткий токен сессии кликера
        await this.ensureSessionToken();
    }

    async ensureSessionToken() {
        if (this.sessionToken) return;
        try {
            const res = await fetch('/api/session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': (this.initData || window.__tgInitData || '') },
                body: JSON.stringify({ initData: (this.initData || window.__tgInitData || undefined) })
            });
            if (res.ok) {
                const data = await res.json();
                if (data.success && data.token) {
                    this.sessionToken = data.token;
                }
            }
        } catch (_) {}
    }

    updateBirthdayCountdown() {
        const today = new Date();
        const currentYear = today.getFullYear();
        
        // 27 сентября текущего года
        let birthday = new Date(currentYear, 8, 27); // месяц 8 = сентябрь (0-индекс)
        
        // Если день рождения уже прошел в этом году, берем следующий год
        if (today > birthday) {
            birthday = new Date(currentYear + 1, 8, 27);
        }
        
        const timeDiff = birthday.getTime() - today.getTime();
        const hoursLeft = Math.ceil(timeDiff / (1000 * 3600));
        
        const countdownElement = document.getElementById('birthdayCountdown');
        if (countdownElement) {
            if (hoursLeft <= 0) {
                countdownElement.textContent = 'Сегодня день рождения разработчика!';
            } else if (hoursLeft === 1) {
                countdownElement.textContent = 'До дня рождения разработчика остался 1 час';
            } else if (hoursLeft < 24) {
                countdownElement.textContent = `До дня рождения разработчика осталось ${hoursLeft} часов`;
            } else {
                const daysLeft = Math.floor(hoursLeft / 24);
                const remainingHours = hoursLeft % 24;
                if (remainingHours === 0) {
                    countdownElement.textContent = `До дня рождения разработчика осталось ${daysLeft} дней`;
                } else {
                    countdownElement.textContent = `До дня рождения разработчика осталось ${daysLeft} дней и ${remainingHours} часов`;
                }
            }
        }
    }

    async handleClick(event) {
        event.preventDefault();
        event.stopPropagation();
        this.processClick(event);
    }

    handleTouchStart(event) {
        event.preventDefault();
        event.stopPropagation();
    }

    handleTouchEnd(event) {
        event.preventDefault();
        event.stopPropagation();
        this.processClick(event);
    }

    handleTouchMove(event) {
        event.preventDefault();
        event.stopPropagation();
    }

    async processClick(event) {
        const now = Date.now();
        
        // Проверка энергии
        if (!this.canClick()) {
            this.showEnergyWarning();
            return;
        }
        
        // Проверка кулдауна
        if (now - this.lastClickTime < this.clickCooldown) {
            return;
        }

        // Проверка лимита кликов в секунду
        this.clickHistory = this.clickHistory.filter(time => now - time < 1000);
        if (this.clickHistory.length >= this.maxClicksPerSecond) {
            return;
        }
        
        // Тратим энергию
        if (!this.consumeEnergy()) {
            return;
        }

        // Добавляем клик в историю
        this.clickHistory.push(now);
        this.lastClickTime = now;

        // streak: считаем быструю серию (<=300мс между кликами)
        if (now - this.streak.last <= 300) {
            this.streak.count += 1;
        } else {
            this.streak.count = 1;
        }
        this.streak.last = now;
        this.streak.best = Math.max(this.streak.best, this.streak.count);
        localStorage.setItem('cg_best_streak', String(this.streak.best));

        // Наклон монетки в сторону касания
        this.tiltButton(event);

        // Анимация кнопки
        this.animateButton();
        
        // Создаем частицы в точке касания
        this.createParticles(event);
        
        // Создаем дополнительные эффекты в точке касания
        this.createClickEffects(event);
        
        // Анимация счетчиков
        this.animateCounters();
        
        // Улучшенная тактильная обратная связь
        this.performHapticFeedback();

        // Вычисляем множитель от бонусов
        const multiplier = this.calculateClickMultiplier();
        const isCritical = multiplier > 2; // Критический если множитель больше 2
        
        // Показываем летящее число
        const rect = event.target.getBoundingClientRect();
        const x = event.clientX || (rect.left + rect.width / 2);
        const y = event.clientY || (rect.top + rect.height / 2);
        this.showClickNumber(x, y, multiplier, isCritical);

        // Локально увеличиваем счётчик с учетом бонусов (UI мгновенный)
        this.userCongratulations += multiplier;
                this.updateUI();

        // Кладём клик в очередь для батч-отправки (с учетом множителя)
        this.queueClick(multiplier);

        // Сброс наклона выполняется на pointerup/pointercancel
    }

    animateButton() {
        const button = document.getElementById('congratulateButton');
        if (button) {
            // Добавляем класс для анимации
            button.classList.add('clicked');
            
            // Если сейчас есть наклон, не вмешиваемся в transform
            if (!this.isTilting) {
                // Лёгкая тактильная анимация без свечения
                // подпрыгивать/утапливаться только при нажатии по центру
                button.style.transform = this.lastPressIsCenter ? 'scale(0.94)' : 'scale(1)';
            setTimeout(() => {
                button.classList.remove('clicked');
                button.style.transform = 'scale(1)';
                }, 120);
            } else {
                setTimeout(() => {
                    button.classList.remove('clicked');
                }, 120);
            }
        }
    }

    tiltButton(event) {
        const button = document.getElementById('congratulateButton');
        if (!button) return;

        // включаем will-change только во время жеста
        button.style.willChange = 'transform';

        const rect = button.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;
        const clientX = event.clientX ?? (event.touches && event.touches[0]?.clientX) ?? centerX;
        const clientY = event.clientY ?? (event.touches && event.touches[0]?.clientY) ?? centerY;
        const dx = clientX - centerX;
        const dy = clientY - centerY;

        // нормируем -1..1
        const nx = Math.max(-1, Math.min(1, dx / (rect.width / 2)));
        const ny = Math.max(-1, Math.min(1, dy / (rect.height / 2)));

        // мёртвая зона в центре — просто утапливаем
        const deadzone = 0.10;
        const dist = Math.hypot(nx, ny);

        // Вертикальный сильнее, горизонтальный спокойнее
        const maxTiltX = 16; // по X (вверх/вниз)
        const maxTiltY = 8;  // по Y (влево/вправо) — спокойнее
        let rx = 0, ry = 0, scale = 0.98;

        if (dist < deadzone) {
            // центр: без наклона, только утапливание
            rx = 0; ry = 0; scale = 0.93;
            this.lastPressIsCenter = true;
        } else {
            // нажали снизу (ny>0) — верхняя грань вперёд => rotateX отрицательный
            rx = -(ny * maxTiltX);
            // нажали справа (nx>0) — правая грань вперёд => rotateY положительный (визуально ощущается естественно)
            ry = nx * maxTiltY;
            // чем дальше от центра, тем сильнее утапливаем
            scale = 1 - Math.min(0.10, dist * 0.07);
            this.lastPressIsCenter = false;
        }

        // Обновляем цели сглаживания и запускаем цикл
        this.tiltTargetRX = rx;
        this.tiltTargetRY = ry;
        this.tiltTargetScale = scale;
        this.startTiltLoop();
        this.isTilting = true;
    }

    resetTilt() {
        const button = document.getElementById('congratulateButton');
        if (!button) return;
        // Устанавливаем нулевые цели — плавное возвращение через RAF
        this.tiltTargetRX = 0;
        this.tiltTargetRY = 0;
        this.tiltTargetScale = 1;
        this.startTiltLoop(() => {
            button.style.willChange = 'auto';
            const glow = document.querySelector('.coin-glow');
            if (glow) glow.style.transform = 'translate(-50%, -50%)';
        });
        this.isTilting = false;
    }

    startTiltLoop(onSettled) {
        const button = document.getElementById('congratulateButton');
        if (!button) return;
        const glow = document.querySelector('.coin-glow');
        // уже идёт цикл — просто обновятся цели
        if (this.tiltRAF) return;
        const lerp = (a, b, t) => a + (b - a) * t;
        const step = () => {
            // сглаживание: быстрее при нажатии, мягче при возврате
            const t = this.isPointerDown ? 0.22 : 0.12;
            this.tiltRX = lerp(this.tiltRX, this.tiltTargetRX, t);
            this.tiltRY = lerp(this.tiltRY, this.tiltTargetRY, t);
            this.tiltScale = lerp(this.tiltScale, this.tiltTargetScale, t);
            button.style.transform = `rotateX(${this.tiltRX}deg) rotateY(${this.tiltRY}deg) scale(${this.tiltScale})`;
            // динамическая тень — уплощается при вертикальном наклоне
            const shadow = document.querySelector('.coin-shadow');
            if (shadow) {
                const squash = 1 - Math.min(0.30, Math.abs(this.tiltRX) / 50);
                shadow.style.transform = `translate(-50%, -50%) scaleY(${squash})`;
                shadow.style.opacity = String(0.65 - Math.min(0.4, Math.abs(this.tiltScale - 1) * 2.5));
            }
            if (glow) {
                const px = -this.tiltRY * 2;
                const py = this.tiltRX * 2;
                glow.style.transform = `translate(calc(-50% + ${px}px), calc(-50% + ${py}px))`;
            }
            const near = Math.abs(this.tiltRX - this.tiltTargetRX) < 0.05 && Math.abs(this.tiltRY - this.tiltTargetRY) < 0.05 && Math.abs(this.tiltScale - this.tiltTargetScale) < 0.003;
            if (near) {
                // если цель — ноль, и мы почти на нуле, завершаем
                if (this.tiltTargetRX === 0 && this.tiltTargetRY === 0 && this.tiltTargetScale === 1) {
                    cancelAnimationFrame(this.tiltRAF);
                    this.tiltRAF = null;
                    if (onSettled) onSettled();
                    return;
                }
            }
            this.tiltRAF = requestAnimationFrame(step);
        };
        this.tiltRAF = requestAnimationFrame(step);
    }

    performHapticFeedback() {
        const H = window.Telegram?.WebApp?.HapticFeedback;
        if (H) {
            // Юбилейные — чуть сильнее и особые
            if (this.streak.count > 0 && this.streak.count % 50 === 0) {
                H.notificationOccurred('success');
            } else if (this.userCongratulations > 0 && this.userCongratulations % 10 === 0) {
                H.impactOccurred('heavy');
            } else if (this.userCongratulations > 0 && this.userCongratulations % 5 === 0) {
                H.impactOccurred('medium');
            } else {
                H.impactOccurred('light');
            }
            return;
        }
        // Fallback для устройств без Telegram Haptics
        if (navigator.vibrate) {
            if (this.userCongratulations % 10 === 0) navigator.vibrate(25);
            else if (this.userCongratulations % 5 === 0) navigator.vibrate(15);
            else navigator.vibrate(8);
        }
    }

    createClickEffects(event) {
        const pageX = event.pageX ?? (event.touches && event.touches[0]?.pageX);
        const pageY = event.pageY ?? (event.touches && event.touches[0]?.pageY);
        if (pageX == null || pageY == null) return;

        // Цветные кольца
        const rings = 2 + Math.floor(Math.random() * 2);
        for (let i = 0; i < rings; i++) {
            const ring = document.createElement('div');
            const color = this.effectColors[Math.floor(Math.random() * this.effectColors.length)];
            ring.style.cssText = `
                position: absolute;
                left: ${pageX}px;
                top: ${pageY}px;
                width: ${24 + i * 10}px;
                height: ${24 + i * 10}px;
                border: 3px solid ${color};
                border-radius: 50%;
                transform: translate(-50%, -50%);
                pointer-events: none;
                z-index: 1000;
                animation: waveExpand 0.6s ease-out forwards;
            `;
            document.body.appendChild(ring);
            setTimeout(() => ring.remove(), 650);
        }

        // Искры
        for (let i = 0; i < 10; i++) {
            const spark = document.createElement('div');
            const color = this.effectColors[Math.floor(Math.random() * this.effectColors.length)];
            const angle = Math.random() * Math.PI * 2;
            const distance = 30 + Math.random() * 40;
            const x = pageX + Math.cos(angle) * distance;
            const y = pageY + Math.sin(angle) * distance;
            const size = 4 + Math.random() * 3;
            spark.style.cssText = `
                position: absolute;
                left: ${x}px;
                top: ${y}px;
                width: ${size}px;
                height: ${size}px;
                background: ${color};
                border-radius: 50%;
                pointer-events: none;
                z-index: 1000;
                opacity: 1;
                animation: sparkleFloat 0.8s ease-out forwards;
            `;
            document.body.appendChild(spark);
            setTimeout(() => spark.remove(), 900);
        }
    }

    createWaveEffect(x, y) {
        const wave = document.createElement('div');
        wave.className = 'click-wave';
        wave.style.cssText = `
            position: fixed;
            left: ${x}px;
            top: ${y}px;
            width: 20px;
            height: 20px;
            border: 3px solid #d4af37;
            border-radius: 50%;
            transform: translate(-50%, -50%);
            pointer-events: none;
            z-index: 1000;
            animation: waveExpand 0.6s ease-out forwards;
        `;
        
        document.body.appendChild(wave);
        
        setTimeout(() => {
            if (wave.parentNode) {
                wave.parentNode.removeChild(wave);
            }
        }, 600);
    }

    createSparkleEffect(x, y) {
        const sparkles = ['✨', '💫', '⭐', '🌟'];
        
        for (let i = 0; i < 3; i++) {
            const sparkle = document.createElement('div');
            sparkle.textContent = sparkles[Math.floor(Math.random() * sparkles.length)];
            sparkle.style.cssText = `
                position: fixed;
                left: ${x + (Math.random() - 0.5) * 100}px;
                top: ${y + (Math.random() - 0.5) * 100}px;
                font-size: 20px;
                pointer-events: none;
                z-index: 1000;
                animation: sparkleFloat 1s ease-out forwards;
            `;
            
            document.body.appendChild(sparkle);
            
            setTimeout(() => {
                if (sparkle.parentNode) {
                    sparkle.parentNode.removeChild(sparkle);
                }
            }, 1000);
        }
    }

    animateCounters() {
        // Анимация общего счетчика
        const totalCounter = document.getElementById('totalCongratulations');
        if (totalCounter) {
            totalCounter.style.transform = 'scale(1.1)';
            totalCounter.style.color = '#2ecc71';
            setTimeout(() => {
                totalCounter.style.transform = 'scale(1)';
                totalCounter.style.color = '';
            }, 200);
        }

        // Пружинка у пользовательского счётчика
        const userCounter = document.getElementById('userCongratulations');
        if (userCounter) {
            userCounter.classList.remove('counter-bounce');
            // reflow to restart animation
            void userCounter.offsetWidth;
            userCounter.classList.add('counter-bounce');
        }
    }

    createParticles(event) {
        const pageX = event.pageX ?? (event.touches && event.touches[0]?.pageX);
        const pageY = event.pageY ?? (event.touches && event.touches[0]?.pageY);
        if (pageX == null || pageY == null) return;

        // Цветные точки вокруг монеты
        for (let i = 0; i < 6; i++) {
            const dot = document.createElement('div');
            const color = this.effectColors[Math.floor(Math.random() * this.effectColors.length)];
            dot.className = 'particle';
            const angle = (Math.PI * 2 * i) / 6;
            const distance = 40 + Math.random() * 25;
            const x = pageX + Math.cos(angle) * distance;
            const y = pageY + Math.sin(angle) * distance;
            dot.style.left = x + 'px';
            dot.style.top = y + 'px';
            dot.style.width = '8px';
            dot.style.height = '8px';
            dot.style.background = color;
            dot.style.borderRadius = '50%';
            document.body.appendChild(dot);
            setTimeout(() => dot.remove(), 1000);
        }
        
        // Дополнительные конфетти для особого эффекта
        this.createConfetti(pageX, pageY);
    }
    
    createConfetti(x, y) {
        const confettiCount = 15;
        const colors = this.effectColors;
        
        for (let i = 0; i < confettiCount; i++) {
            const confetti = document.createElement('div');
            confetti.style.cssText = `
                position: absolute;
                left: ${x}px;
                top: ${y}px;
                width: 8px;
                height: 8px;
                background: ${colors[Math.floor(Math.random() * colors.length)]};
                pointer-events: none;
                z-index: 1000;
                border-radius: 2px;
                animation: confettiFall ${1 + Math.random() * 2}s ease-out forwards;
            `;
            
            const angle = Math.random() * Math.PI * 2;
            const velocity = 50 + Math.random() * 100;
            const vx = Math.cos(angle) * velocity;
            const vy = Math.sin(angle) * velocity - 50;
            
            confetti.style.setProperty('--vx', vx + 'px');
            confetti.style.setProperty('--vy', vy + 'px');
            
            document.body.appendChild(confetti);
            
            setTimeout(() => {
                if (confetti.parentNode) {
                    confetti.parentNode.removeChild(confetti);
                }
            }, 3000);
        }
    }

    createTapFlash(event) {
        const pageX = event.pageX ?? (event.touches && event.touches[0]?.pageX);
        const pageY = event.pageY ?? (event.touches && event.touches[0]?.pageY);
        if (pageX == null || pageY == null) return;
        const flash = document.createElement('div');
        const color = this.effectColors[Math.floor(Math.random() * this.effectColors.length)];
        flash.style.cssText = `
            position: absolute;
            left: ${pageX}px; top: ${pageY}px;
            width: 14px; height: 14px;
            transform: translate(-50%, -50%);
            border-radius: 50%;
            background: ${color};
            filter: blur(4px);
            opacity: 0.9;
            pointer-events: none; z-index: 1000;
            animation: tapFlash 320ms ease-out forwards;
        `;
        document.body.appendChild(flash);
        setTimeout(() => flash.remove(), 360);
    }

    createLongRing(event) {
        const pageX = event.pageX ?? (event.touches && event.touches[0]?.pageX);
        const pageY = event.pageY ?? (event.touches && event.touches[0]?.pageY);
        if (pageX == null || pageY == null) return;
        const ring = document.createElement('div');
        const color = this.effectColors[Math.floor(Math.random() * this.effectColors.length)];
        ring.style.cssText = `
            position: absolute;
            left: ${pageX}px; top: ${pageY}px;
            width: 28px; height: 28px;
            transform: translate(-50%, -50%);
            border-radius: 50%;
            border: 3px solid ${color};
            pointer-events: none; z-index: 1000;
            animation: waveExpand 600ms ease-out forwards;
        `;
        document.body.appendChild(ring);
        setTimeout(() => ring.remove(), 650);
    }

    // Очередь и батч‑отправка кликов
    queueClick(multiplier = 1) {
        if (this.clickQueue < this.maxQueue) {
            this.clickQueue += multiplier; // Учитываем множитель
        }
        this.scheduleFlush();
    }

    scheduleFlush() {
        if (this.flushTimer) return;
        const delay = Math.max(this.flushIntervalMs + this.flushBackoffMs, 200);
        this.flushTimer = setTimeout(() => {
            this.flushTimer = null;
            this.flushQueuedClicks().catch(() => {});
        }, delay);
    }

    async flushQueuedClicks(options = {}) {
        const { useBeacon = false } = options;
        if (this.flushInFlight) return;
        const count = this.clickQueue;
        if (!count) return;
        this.flushInFlight = true;
        this.clickQueue = 0;

        const payload = {
            userId: this.userId,
            userName: this.userName,
            count,
            timestamp: Date.now()
        };

        try {
            if (useBeacon && navigator.sendBeacon) {
                const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
                navigator.sendBeacon('/api/congratulate', blob);
                this.flushBackoffMs = 0;
                this.flushInFlight = false;
                return;
            }

            const response = await fetch('/api/congratulate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Telegram-Init-Data': (this.initData || window.__tgInitData || ''),
                    ...(this.sessionToken ? { 'Authorization': `Bearer ${this.sessionToken}` } : {})
                },
                body: JSON.stringify({ ...payload, initData: (this.initData || window.__tgInitData || undefined) })
            });

            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            if (data && data.success && typeof data.total === 'number') {
                this.totalCongratulations = data.total;
                
                // Обновляем энергию с сервера
                if (data.energy) {
                    this.energy.current = data.energy.current;
                    this.energy.max = data.energy.max;
                    this.energy.regenRate = data.energy.regen_rate;
                    this.energy.regenInterval = data.energy.regen_interval;
                    this.energy.lastRegenTime = data.energy.last_regen;
                    this.updateEnergyUI();
                }
            }
            this.flushBackoffMs = 0;
        } catch (err) {
            console.error('Ошибка батч‑отправки:', err);
            
            // Если это ошибка недостатка энергии - не возвращаем клики в очередь
            if (err.message && err.message.includes('HTTP 400')) {
                // Попробуем получить детали ошибки
                try {
                    // Обновляем энергию с сервера
                    this.updateEnergyFromServer();
                } catch (e) {
                    console.log('Не удалось обновить энергию:', e);
                }
                // Не возвращаем клики в очередь при проблемах с энергией
            } else if (err.message && err.message.includes('HTTP 429')) {
                // Rate limiting - возвращаем клики в очередь с большой задержкой
                this.clickQueue += count;
                this.flushBackoffMs = Math.min(5000, (this.flushBackoffMs || 1000) * 2);
                console.log(`Rate limit достигнут, повтор через ${this.flushBackoffMs}ms`);
                this.scheduleFlush();
                
                // Обновляем энергию при rate limiting
                try {
                    this.updateEnergyFromServer();
                } catch (e) {
                    console.log('Не удалось обновить энергию при rate limit:', e);
                }
            } else {
                // Возвращаем клики обратно в очередь для других ошибок
            this.clickQueue += count;
            this.flushBackoffMs = Math.min(3000, (this.flushBackoffMs || 300) * 2);
            this.scheduleFlush();
                
                // Обновляем энергию при ошибках
                try {
                    this.updateEnergyFromServer();
                } catch (e) {
                    console.log('Не удалось обновить энергию при ошибке:', e);
                }
            }
        } finally {
            this.flushInFlight = false;
        }
    }

    async loadInitialData() {
        console.log('Начинаем загрузку данных...');
        try {
            const headers = {};
            if (this.userId) headers['X-User-ID'] = this.userId;
            const initData = (this.initData || window.__tgInitData || '');
            if (initData) headers['X-Telegram-Init-Data'] = initData;
            
            // Используем XMLHttpRequest для лучшей совместимости с Telegram WebApp
            const data = await new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('GET', '/api/congratulate-data');
                
                // Устанавливаем заголовки
                Object.keys(headers).forEach(key => {
                    xhr.setRequestHeader(key, headers[key]);
                });
                
                xhr.onload = function() {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        try {
                            resolve(JSON.parse(xhr.responseText));
                        } catch (e) {
                            reject(new Error('Invalid JSON response'));
                        }
                    } else {
                        reject(new Error(`HTTP error! status: ${xhr.status}`));
                    }
                };
                
                xhr.onerror = function() {
                    reject(new Error('Network error'));
                };
                
                xhr.send();
            });
            
            if (data.success) {
                this.totalCongratulations = data.total;
                this.userCongratulations = data.userCount || 0; // Инициализируем только при первой загрузке
                this.rating = data.rating || [];
                this.stats = data.stats || {};
                
                // Синхронизируем энергию с сервером
                if (data.energy) {
                    this.energy.current = data.energy.current;
                    this.energy.max = data.energy.max;
                    this.energy.regenRate = data.energy.regen_rate;
                    this.energy.regenInterval = data.energy.regen_interval;
                    this.energy.lastRegenTime = data.energy.last_regen;
                } else {
                    // Если energy null, показываем дефолтные значения для нового игрока
                    console.warn('Energy data is null in loadInitialData - new player, setting full energy');
                    this.energy.current = 150;
                    this.energy.max = 150;
                }
                
                // Синхронизируем апгрейды с сервером
                if (data.upgrades) {
                    this.upgrades = data.upgrades;
                }
                
                console.log('Инициализация: userCongratulations =', this.userCongratulations, 'userId =', this.userId);
                this.updateUI();
                this.updateEnergyUI();
                this.updateUpgradeUI();
                
                // Скрываем лоадер сразу после загрузки основных данных (баланс + энергия)
                console.log('Основные данные загружены, скрываем лоадер');
                this.hideGlobalLoading();
            } else {
                console.error('Ошибка загрузки данных:', data.message);
                this.showError('Ошибка загрузки данных');
                console.log('Скрываем лоадер из-за ошибки данных');
                this.hideGlobalLoading();
            }
        } catch (error) {
            console.error('Ошибка при загрузке данных:', error);
            this.showError('Ошибка соединения');
            console.log('Скрываем лоадер из-за ошибки соединения');
            this.hideGlobalLoading();
        }
    }

    async loadData() {
        // Защита от множественных запросов
        if (this.isLoadingData) {
            console.log('loadData: запрос уже выполняется, пропускаем');
            return;
        }
        
        this.isLoadingData = true;
        try {
            const headers = {};
            if (this.userId) headers['X-User-ID'] = this.userId;
            const initData = (this.initData || window.__tgInitData || '');
            if (initData) headers['X-Telegram-Init-Data'] = initData;
            
            // Используем XMLHttpRequest для лучшей совместимости с Telegram WebApp
            const data = await new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('GET', '/api/congratulate-data');
                
                // Устанавливаем заголовки
                Object.keys(headers).forEach(key => {
                    xhr.setRequestHeader(key, headers[key]);
                });
                
                xhr.onload = function() {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        try {
                            resolve(JSON.parse(xhr.responseText));
                        } catch (e) {
                            reject(new Error('Invalid JSON response'));
                        }
                    } else {
                        reject(new Error(`HTTP error! status: ${xhr.status}`));
                    }
                };
                
                xhr.onerror = function() {
                    reject(new Error('Network error'));
                };
                
                xhr.send();
            });
            
            if (data.success) {
                this.totalCongratulations = data.total;
                // Не перезаписываем userCongratulations при автообновлении
                // this.userCongratulations остается локальным значением
                this.rating = data.rating || [];
                this.stats = data.stats || {};
                
                // Обновляем энергию
                if (data.energy) {
                    this.energy.current = data.energy.current;
                    this.energy.max = data.energy.max;
                    this.energy.regenRate = data.energy.regen_rate;
                    this.energy.regenInterval = data.energy.regen_interval;
                    this.energy.lastRegenTime = data.energy.last_regen;
                    this.updateEnergyUI();
                } else {
                    console.warn('Energy data is null in loadData');
                    this.energy.current = 0;
                    this.energy.max = 100;
                    this.updateEnergyUI();
                }
                
                // Обновляем баланс пользователя
                if (data.userCount !== undefined) {
                    this.userCongratulations = data.userCount;
                }
                
                // Обновляем апгрейды
                if (data.upgrades) {
                    this.upgrades = data.upgrades;
                    this.updateUpgradeUI();
                }
                
                this.updateUI();
            } else {
                console.error('Ошибка загрузки данных:', data.message);
                this.showError('Ошибка загрузки данных');
            }
        } catch (error) {
            console.error('Ошибка при загрузке данных:', error);
            this.showError('Ошибка соединения');
        } finally {
            this.isLoadingData = false;
        }
    }

    updateUI() {
        // Обновляем счетчик пользователя в монетке
        const userCounter = document.getElementById('userCongratulations');
        if (userCounter) {
            userCounter.textContent = this.userCongratulations.toLocaleString();
            
            // Анимация монетки убрана
        }

        // Обновляем рейтинг
        this.updateRating();
        
        // Обновляем статистику
        this.updateStats();
        
        // Обновляем апгрейды
        this.updateUpgradeUI();
    }

    updateRating() {
        const ratingTable = document.getElementById('ratingTable');
        if (!ratingTable) {
            console.error('Элемент ratingTable не найден');
            return;
        }

        if (this.rating.length === 0) {
            ratingTable.innerHTML = '<div class="rating-loading">Пока нет поздравлений</div>';
            return;
        }

        // Определяем позицию текущего пользователя
        const myIndex = this.rating.findIndex(u => u.userId === this.userId);

        // Формируем HTML списка с возможным закрепом пользователя сверху
        let html = '';

        // Если пользователь вне топ-3 — показываем закреплённую строку сверху
        if (myIndex >= 3) {
            const me = this.rating[myIndex];
            const myPosition = myIndex + 1;
            html += `
                <div class="rating-item current-user">
                    <div class="rating-position">#${myPosition}</div>
                    <div class="rating-name">${this.escapeHtml(me.userName)}</div>
                    <div class="rating-count">${me.count}</div>
                </div>
            `;
        }

        this.rating.forEach((user, index) => {
            const isCurrentUser = user.userId === this.userId;
            const position = index + 1;
            const medal = position === 1 ? '🥇' : position === 2 ? '🥈' : position === 3 ? '🥉' : '';
            // Если уже закрепили пользователя сверху — не дублируем его в списке
            if (myIndex >= 3 && isCurrentUser) return;
            html += `
                <div class="rating-item ${isCurrentUser ? 'current-user' : ''}">
                    <div class="rating-position">${medal} ${position}</div>
                    <div class="rating-name">${this.escapeHtml(user.userName)}</div>
                    <div class="rating-count">${user.count}</div>
                </div>
            `;
        });

        ratingTable.innerHTML = html;

        // Позиция пользователя под таблицей
        const userPosEl = document.getElementById('ratingUserPos');
        if (userPosEl) {
            if (myIndex >= 0) {
                if (myIndex >= 3) {
                    // Уже показан закреп — текст снизу не нужен
                    userPosEl.textContent = '';
                } else {
                    const me = this.rating[myIndex];
                    userPosEl.textContent = `Вы: #${myIndex + 1}, ${me.count.toLocaleString()} кликов`;
                }
            } else {
                userPosEl.textContent = '';
            }
        }
    }

    updateStats() {
        const statsGrid = document.getElementById('statsGrid');
        if (!statsGrid) return;

        if (Object.keys(this.stats).length === 0) {
            statsGrid.innerHTML = '<div class="stats-loading"><div class="loader-spinner"></div><div class="loader-text">Загрузка...</div></div>';
            return;
        }

        const stats = this.stats;
        let html = `
            <div class="stats-item">
                <div class="stats-value">${stats.total_congratulations?.toLocaleString() || 0}</div>
                <div class="stats-label">Всего поздравлений</div>
            </div>
            <div class="stats-item">
                <div class="stats-value">${this.userCongratulations.toLocaleString()}</div>
                <div class="stats-label">Ваших поздравлений</div>
            </div>
            <div class="stats-item">
                <div class="stats-value">${stats.unique_users || 0}</div>
                <div class="stats-label">Уникальных пользователей</div>
            </div>
            <div class="stats-item">
                <div class="stats-value">${stats.online_users || 0}</div>
                <div class="stats-label">Онлайн сейчас</div>
            </div>
            <div class="stats-item">
                <div class="stats-value">${stats.recent_congratulations || 0}</div>
                <div class="stats-label">За последний час</div>
            </div>
        `;

        statsGrid.innerHTML = html;
    }

    showGlobalLoading() {
        const globalLoader = document.getElementById('globalLoader');
        if (globalLoader) {
            globalLoader.classList.remove('hidden');
        }
    }

    hideGlobalLoading() {
        // Очищаем таймаут принудительного скрытия
        if (this.loaderTimeout) {
            clearTimeout(this.loaderTimeout);
            this.loaderTimeout = null;
        }
        
        const globalLoader = document.getElementById('globalLoader');
        if (globalLoader && !globalLoader.classList.contains('hidden')) {
            console.log('Скрываем глобальный лоадер');
            globalLoader.classList.add('hidden');
            // Полностью скрываем через 300ms после анимации
            setTimeout(() => {
                globalLoader.style.display = 'none';
            }, 300);
        }
    }

    showLoading() {
        const ratingTable = document.getElementById('ratingTable');
        if (ratingTable) {
            ratingTable.innerHTML = '<div class="rating-loading">Загрузка рейтинга...</div>';
        }
        
        const statsGrid = document.getElementById('statsGrid');
        if (statsGrid) {
            statsGrid.innerHTML = '<div class="stats-loading">Загрузка статистики...</div>';
        }
    }

    showError(message) {
        const ratingTable = document.getElementById('ratingTable');
        if (ratingTable) {
            ratingTable.innerHTML = `<div class="rating-loading" style="color: var(--secondary-color);">${message}</div>`;
        }
        
        const statsGrid = document.getElementById('statsGrid');
        if (statsGrid) {
            statsGrid.innerHTML = `<div class="stats-loading" style="color: var(--secondary-color);">${message}</div>`;
        }
    }

    showMessage(text, type = 'info') {
        // Создаем уведомление
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.textContent = text;
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: var(--tg-theme-bg-color);
            color: var(--tg-theme-text-color);
            padding: 12px 20px;
            border-radius: 12px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
            z-index: 1000;
            font-weight: 600;
            max-width: 90%;
            text-align: center;
        `;

        if (type === 'success') {
            notification.style.background = 'var(--accent-color)';
            notification.style.color = 'white';
        } else if (type === 'warning') {
            notification.style.background = '#ff9800';
            notification.style.color = 'white';
        } else if (type === 'error') {
            notification.style.background = 'var(--secondary-color)';
            notification.style.color = 'white';
        }

        document.body.appendChild(notification);

        // Удаляем уведомление через 3 секунды
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 3000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    // ========================================
    // MODAL SYSTEM
    // ========================================
    
    setupModalListeners() {
        // Shop Modal
        const shopBtn = document.getElementById('shopBtn');
        const shopModal = document.getElementById('shopModal');
        const shopModalClose = document.getElementById('shopModalClose');
        
        if (shopBtn && shopModal && shopModalClose) {
            shopBtn.addEventListener('click', () => this.openModal('shopModal'));
            shopModalClose.addEventListener('click', () => this.closeModal('shopModal'));
            shopModal.addEventListener('click', (e) => {
                if (e.target === shopModal || e.target.classList.contains('modal-backdrop')) {
                    this.closeModal('shopModal');
                }
            });
        }
        
        // Rating Modal
        const ratingBtn = document.getElementById('ratingBtn');
        const ratingModal = document.getElementById('ratingModal');
        const ratingModalClose = document.getElementById('ratingModalClose');
        
        if (ratingBtn && ratingModal && ratingModalClose) {
            ratingBtn.addEventListener('click', () => this.openModal('ratingModal'));
            ratingModalClose.addEventListener('click', () => this.closeModal('ratingModal'));
            ratingModal.addEventListener('click', (e) => {
                if (e.target === ratingModal || e.target.classList.contains('modal-backdrop')) {
                    this.closeModal('ratingModal');
                }
            });
        }
        
        // Stats Modal
        const statsBtn = document.getElementById('statsBtn');
        const statsModal = document.getElementById('statsModal');
        const statsModalClose = document.getElementById('statsModalClose');
        
        if (statsBtn && statsModal && statsModalClose) {
            statsBtn.addEventListener('click', () => this.openModal('statsModal'));
            statsModalClose.addEventListener('click', () => this.closeModal('statsModal'));
            statsModal.addEventListener('click', (e) => {
                if (e.target === statsModal || e.target.classList.contains('modal-backdrop')) {
                    this.closeModal('statsModal');
                }
            });
        }
    }
    
    openModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.style.display = 'block';
            document.body.style.overflow = 'hidden';
            
            // Запускаем анимацию появления
            requestAnimationFrame(() => {
                modal.classList.add('show');
            });
            
            // Тактильная обратная связь
            try {
                if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) {
                    Telegram.WebApp.HapticFeedback.impactOccurred('light');
                }
            } catch (e) {}
        }
    }
    
    closeModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            // Запускаем анимацию исчезновения
            modal.classList.remove('show');
            
            // Ждем окончания анимации перед скрытием
            setTimeout(() => {
                modal.style.display = 'none';
                document.body.style.overflow = '';
            }, 300);
        }
    }
    
    // ========================================
    // ENERGY SYSTEM (SERVER-SIDE)
    // ========================================
    
    async updateEnergyFromServer() {
        // Защита от множественных запросов
        if (this.isUpdatingEnergy) {
            console.log('updateEnergyFromServer: запрос уже выполняется, пропускаем');
            return;
        }
        
        this.isUpdatingEnergy = true;
        try {
            const headers = {};
            if (this.userId) headers['X-User-ID'] = this.userId;
            const initData = (this.initData || window.__tgInitData || '');
            if (initData) headers['X-Telegram-Init-Data'] = initData;
            
            // Используем XMLHttpRequest для лучшей совместимости с Telegram WebApp
            const data = await new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('GET', '/api/congratulate-data');
                
                // Устанавливаем заголовки
                Object.keys(headers).forEach(key => {
                    xhr.setRequestHeader(key, headers[key]);
                });
                
                xhr.onload = function() {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        try {
                            resolve(JSON.parse(xhr.responseText));
                        } catch (e) {
                            reject(new Error('Invalid JSON response'));
                        }
                    } else {
                        reject(new Error(`HTTP error! status: ${xhr.status}`));
                    }
                };
                
                xhr.onerror = function() {
                    reject(new Error('Network error'));
                };
                
                xhr.send();
            });
            
            if (data.success) {
                // Обновляем энергию
                if (data.energy) {
                    this.energy.current = data.energy.current;
                    this.energy.max = data.energy.max;
                    this.energy.regenRate = data.energy.regen_rate;
                    this.energy.regenInterval = data.energy.regen_interval;
                    this.energy.lastRegenTime = data.energy.last_regen;
                    this.updateEnergyUI();
                } else {
                    // Если energy null, показываем дефолтные значения для нового игрока
                    console.warn('Energy data is null in updateEnergyFromServer - new player, setting full energy');
                    this.energy.current = 150;
                    this.energy.max = 150;
                    this.updateEnergyUI();
                }
                
                // Обновляем баланс и апгрейды
                if (data.userCount !== undefined) {
                    this.userCongratulations = data.userCount;
                    this.updateUI();
                }
                
                if (data.upgrades) {
                    this.upgrades = data.upgrades;
                    this.updateUpgradeUI();
                }
            }
        } catch (error) {
            // Тихо игнорируем ошибки при обновлении энергии
            console.log('Energy update failed:', error);
        } finally {
            this.isUpdatingEnergy = false;
        }
    }
    
    canClick() {
        return this.energy.current >= this.energy.costPerClick;
    }
    
    consumeEnergy() {
        // Энергия потребляется только на сервере
        // Клиент только проверяет возможность клика
        return this.canClick();
    }
    
    updateEnergyUI() {
        const energyValue = document.getElementById('energyValue');
        const energyBarFill = document.getElementById('energyBarFill');
        const energyRegenTime = document.getElementById('energyRegenTime');
        
        // Если энергия еще не загружена (0/0), показываем загрузку
        if (this.energy.max === 0) {
            if (energyValue) {
                energyValue.textContent = '—/—';
            }
            if (energyBarFill) {
                energyBarFill.style.width = '0%';
            }
            if (energyRegenTime) {
                energyRegenTime.textContent = '—';
            }
            return;
        }
        
        if (energyValue) {
            energyValue.textContent = `${this.energy.current}/${this.energy.max}`;
        }
        
        if (energyBarFill) {
            const percentage = Math.max(0, (this.energy.current / this.energy.max) * 100);
            energyBarFill.style.width = `${percentage}%`;
        }
        
        if (energyRegenTime) {
            // Отображаем время восстановления: +1 энергия / X секунд
            const regenSeconds = Math.round((this.energy.regenInterval || 20000) / 1000);
            const regenRate = this.energy.regenRate || 1;
            energyRegenTime.textContent = `+${regenRate}/${regenSeconds}с`;
        }
        
        // Визуальная индикация недостатка энергии
        const button = document.getElementById('congratulateButton');
        if (button) {
            if (this.energy.current < this.energy.costPerClick) {
                button.style.opacity = '0.5';
                button.style.pointerEvents = 'none';
            } else {
                button.style.opacity = '1';
                button.style.pointerEvents = 'auto';
            }
        }
    }
    
    showEnergyWarning() {
        const energySection = document.querySelector('.energy-section');
        if (energySection) {
            energySection.style.animation = 'shake 0.5s ease-in-out';
            setTimeout(() => {
                energySection.style.animation = '';
            }, 500);
        }
        
        // Тактильная обратная связь
        try {
            if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) {
                Telegram.WebApp.HapticFeedback.notificationOccurred('error');
            }
        } catch (e) {}
    }
    
    // === СИСТЕМА БОНУСОВ ===
    
    activateBoostTime() {
        this.bonuses.boostTime.active = true;
        this.bonuses.boostTime.endTime = Date.now() + this.bonuses.boostTime.duration;
        
        // Визуальный эффект
        this.showBoostEffect();
        
        // Автоматическое отключение
        setTimeout(() => {
            this.bonuses.boostTime.active = false;
            this.updateBoostUI();
        }, this.bonuses.boostTime.duration);
        
        this.updateBoostUI();
    }
    
    showBoostEffect() {
        const button = document.getElementById('congratulateButton');
        if (button) {
            button.classList.add('boost-active');
            // Убираем эффект через 30 секунд
            setTimeout(() => {
                button.classList.remove('boost-active');
            }, this.bonuses.boostTime.duration);
        }
    }
    
    updateBoostUI() {
        const boostButton = document.getElementById('boostTimeButton');
        if (boostButton) {
            if (this.bonuses.boostTime.active) {
                const timeLeft = Math.max(0, this.bonuses.boostTime.endTime - Date.now());
                const secondsLeft = Math.ceil(timeLeft / 1000);
                boostButton.textContent = `Буст (${secondsLeft}с)`;
                boostButton.disabled = true;
            } else {
                boostButton.textContent = 'Boost Time (30с)';
                boostButton.disabled = false;
            }
        }
    }
    
    
    calculateClickMultiplier() {
        let multiplier = 1;
        
        // Boost Time
        if (this.bonuses.boostTime.active && Date.now() < this.bonuses.boostTime.endTime) {
            multiplier *= this.bonuses.boostTime.multiplier;
        }
        
        // Critical Click (случайный, всегда активен)
        if (Math.random() < this.bonuses.criticalClick.chance) {
            const critMultiplier = this.bonuses.criticalClick.minMultiplier + 
                Math.random() * (this.bonuses.criticalClick.maxMultiplier - this.bonuses.criticalClick.minMultiplier);
            multiplier *= critMultiplier;
            
            // Показываем критический эффект
            this.showCriticalEffect();
        }
        
        return Math.round(multiplier);
    }
    
    showCriticalEffect() {
        const button = document.getElementById('congratulateButton');
        if (button) {
            button.classList.add('critical-hit');
            setTimeout(() => {
                button.classList.remove('critical-hit');
            }, 500);
        }
    }
    
    
    setupBonusListeners() {
        const boostTimeButton = document.getElementById('boostTimeButton');
        
        if (boostTimeButton) {
            boostTimeButton.addEventListener('click', () => {
                if (!this.bonuses.boostTime.active) {
                    this.activateBoostTime();
                }
            });
        }
    }
    
    showClickNumber(x, y, value, isCritical = false) {
        const numberElement = document.createElement('div');
        numberElement.className = 'click-number' + (isCritical ? ' critical' : '');
        numberElement.textContent = `+${value}`;
        
        // Случайное смещение от места клика (в радиусе 50px)
        const randomOffsetX = (Math.random() - 0.5) * 100; // -50px до +50px
        const randomOffsetY = (Math.random() - 0.5) * 100; // -50px до +50px
        
        // Случайная скорость полета (1.2-2.0 секунды)
        const randomDuration = 1200 + Math.random() * 800;
        
        // Случайное горизонтальное смещение при полете
        const randomDrift = (Math.random() - 0.5) * 40; // -20px до +20px
        
        // Позиционируем относительно клика со случайным смещением
        numberElement.style.position = 'fixed';
        numberElement.style.left = (x + randomOffsetX) + 'px';
        numberElement.style.top = (y + randomOffsetY) + 'px';
        numberElement.style.pointerEvents = 'none';
        numberElement.style.zIndex = '10000';
        
        // Устанавливаем случайную длительность анимации
        numberElement.style.setProperty('--animation-duration', randomDuration + 'ms');
        numberElement.style.setProperty('--drift-x', randomDrift + 'px');
        
        document.body.appendChild(numberElement);
        
        // Анимация исчезновения с учетом случайной длительности
        setTimeout(() => {
            numberElement.remove();
        }, randomDuration);
    }
    
    // ========================================
    // UPGRADE SYSTEM
    // ========================================
    
    setupUpgradeListeners() {
        const buyCapacity = document.getElementById('buyCapacity');
        const buySpeed = document.getElementById('buySpeed');
        
        if (buyCapacity) {
            buyCapacity.addEventListener('click', () => this.buyUpgrade('capacity'));
        }
        
        if (buySpeed) {
            buySpeed.addEventListener('click', () => this.buyUpgrade('speed'));
        }
    }
    
    getUpgradeCost(type) {
        if (type === 'capacity') {
            return 50 * Math.pow(2, this.upgrades.capacity - 1);
        } else if (type === 'speed') {
            return 30 * Math.pow(2, this.upgrades.speed - 1);
        }
        return 0;
    }
    
    canAffordUpgrade(type) {
        return this.userCongratulations >= this.getUpgradeCost(type);
    }
    
    async buyUpgrade(type) {
        const cost = this.getUpgradeCost(type);
        
        if (!this.canAffordUpgrade(type)) {
            this.showError('Недостаточно поздравлений');
            return;
        }
        
        try {
            const headers = {
                'Content-Type': 'application/json'
            };
            
            if (this.userId) headers['X-User-ID'] = this.userId;
            const initData = (this.initData || window.__tgInitData || '');
            if (initData) headers['X-Telegram-Init-Data'] = initData;
            
            const response = await fetch('/api/buy-upgrade', {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    upgrade_type: type,
                    initData: initData || undefined
                })
            });
            
            if (!response.ok) {
                const errorData = await response.json();
                this.showError(errorData.message || 'Ошибка покупки');
                return;
            }
            
            const data = await response.json();
            
            if (data.success) {
                // Обновляем данные с сервера
                this.userCongratulations = data.new_balance;
                this.upgrades = data.upgrades;
                
                if (data.energy) {
                    this.energy.current = data.energy.current;
                    this.energy.max = data.energy.max;
                    this.energy.regenRate = data.energy.regen_rate;
                    this.energy.regenInterval = data.energy.regen_interval;
                    this.energy.lastRegenTime = data.energy.last_regen;
                }
                
                // Обновляем UI
                this.updateUI();
                this.updateUpgradeUI();
                this.updateEnergyUI();
                
                // Обновляем только рейтинг без полной перезагрузки данных
                setTimeout(() => { this.loadData(); }, 3000);
                
                // Тактильная обратная связь
                try {
                    if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) {
                        Telegram.WebApp.HapticFeedback.notificationOccurred('success');
                    }
                } catch (e) {}
                
                console.log(`Успешно купил апгрейд ${type}, новый уровень: ${data.new_level}`);
            } else {
                this.showError(data.message || 'Ошибка покупки');
            }
            
        } catch (error) {
            console.error('Ошибка при покупке апгрейда:', error);
            this.showError('Ошибка соединения');
        }
    }
    
    updateUpgradeUI() {
        const capacityCost = document.getElementById('capacityCost');
        const speedCost = document.getElementById('speedCost');
        const buyCapacity = document.getElementById('buyCapacity');
        const buySpeed = document.getElementById('buySpeed');
        
        if (capacityCost) {
            capacityCost.textContent = this.getUpgradeCost('capacity');
        }
        
        if (speedCost) {
            speedCost.textContent = this.getUpgradeCost('speed');
        }
        
        if (buyCapacity) {
            buyCapacity.disabled = !this.canAffordUpgrade('capacity');
        }
        
        if (buySpeed) {
            buySpeed.disabled = !this.canAffordUpgrade('speed');
        }
        
        // Обновляем UI бонусов
        this.updateBoostUI();
    }
}

// Ensure we can get initData from Telegram or URL
(function(){
  function getInitData(){
    if (window.Telegram && Telegram.WebApp && Telegram.WebApp.initData) return Telegram.WebApp.initData;
    const urlParam = new URLSearchParams(location.search).get('tgWebAppData');
    return urlParam || '';
  }
  window.__tgInitData = getInitData();
})();

// patch: use window.__tgInitData headers in requests
(async function(){
  const originalEnsureSessionToken = CongratulateGame.prototype.ensureSessionToken;
  CongratulateGame.prototype.ensureSessionToken = async function(){
    if (this.sessionToken) return;
    try {
      const res = await fetch('/api/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': (this.initData || window.__tgInitData || '') },
        body: JSON.stringify({ initData: (this.initData || window.__tgInitData || undefined) })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.success && data.token) { this.sessionToken = data.token; }
      }
    } catch(_) {}
  };

  const originalLoadInitialData = CongratulateGame.prototype.loadInitialData;
  CongratulateGame.prototype.loadInitialData = async function(){
    try {
      const headers = {};
      if (this.userId) headers['X-User-ID'] = this.userId;
      const initData = (this.initData || window.__tgInitData || '');
      if (initData) headers['X-Telegram-Init-Data'] = initData;
      const response = await fetch('/api/congratulate-data', { headers });
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const data = await response.json();
      if (data.success) {
        this.totalCongratulations = data.total;
        this.userCongratulations = data.userCount || 0;
        this.rating = data.rating || [];
        this.stats = data.stats || {};
        this.updateUI();
      }
    } catch(e){ this.showError('Ошибка соединения'); }
  };

})();

// Инициализация игры при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    new CongratulateGame();
});

// === Info button / modal for season details ===
document.addEventListener('DOMContentLoaded', () => {
  const infoBtn = document.getElementById('seasonInfoBtn');
  const infoModal = document.getElementById('seasonInfoModal');
  const infoClose = document.getElementById('seasonInfoClose');
  const show = () => { if (infoModal){ infoModal.style.display='block'; requestAnimationFrame(()=>{ const c=infoModal.querySelector('.info-content'); if(c) c.classList.add('is-appearing');}); }};
  const hide = () => { if (infoModal){ infoModal.style.display='none'; }};
  if (infoBtn) infoBtn.addEventListener('click', show);
  if (infoClose) infoClose.addEventListener('click', hide);
  if (infoModal) {
    infoModal.addEventListener('click', (e)=>{ 
      if (e.target === infoModal || e.target.classList.contains('info-backdrop')) {
        hide(); 
      }
    });
  }
});
