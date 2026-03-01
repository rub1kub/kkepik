// Файл для отслеживания свайпов и скроллов вниз
document.addEventListener('DOMContentLoaded', function() {
    // Переменные для отслеживания свайпов и скроллов
    let touchStartY = 0;
    let touchEndY = 0;
    let lastScrollY = window.scrollY;
    let actionCount = 0;
    const SWIPE_THRESHOLD = 50; // Минимальное расстояние для определения свайпа
    const SCROLL_THRESHOLD = 30; // Минимальное расстояние для определения скролла
    const ACTION_COUNT_THRESHOLD = 3; // Количество действий для активации
    const ACTION_TIMEOUT = 3000; // Таймаут для сброса счетчика (3 секунды)
    let lastActionTime = 0;
    
    // Функция для проверки, находится ли пользователь внизу страницы
    function isAtBottom() {
        return (window.innerHeight + window.scrollY) >= document.body.offsetHeight - 10;
    }
    
    // Функция для обработки действия (свайп или скролл)
    function handleAction() {
        const currentTime = Date.now();
        
        // Сбрасываем счетчик, если прошло больше времени, чем таймаут
        if (currentTime - lastActionTime > ACTION_TIMEOUT) {
            actionCount = 0;
        }
        
        // Увеличиваем счетчик действий
        actionCount++;
        lastActionTime = currentTime;
        
        console.log(`Действие вниз внизу страницы. Счетчик: ${actionCount}/${ACTION_COUNT_THRESHOLD}`);
        
        // Если достигли порогового значения, перенаправляем на test.html
        if (actionCount >= ACTION_COUNT_THRESHOLD) {
            console.log('Активация тестовой версии!');
            window.location.href = '/test';
        }
    }
    
    // Обработчик начала касания
    document.addEventListener('touchstart', function(e) {
        touchStartY = e.touches[0].clientY;
    }, false);
    
    // Обработчик окончания касания
    document.addEventListener('touchend', function(e) {
        touchEndY = e.changedTouches[0].clientY;
        
        // Проверяем, что это свайп вниз
        if (touchEndY - touchStartY > SWIPE_THRESHOLD) {
            // Проверяем, находится ли пользователь внизу страницы
            if (isAtBottom()) {
                handleAction();
            }
        }
    }, false);
    
    // Обработчик скролла
    window.addEventListener('scroll', function() {
        // Проверяем, что это скролл вниз
        if (window.scrollY > lastScrollY && window.scrollY - lastScrollY > SCROLL_THRESHOLD) {
            // Проверяем, находится ли пользователь внизу страницы
            if (isAtBottom()) {
                handleAction();
            }
        }
        
        // Обновляем последнюю позицию скролла
        lastScrollY = window.scrollY;
    }, { passive: true });
}); 