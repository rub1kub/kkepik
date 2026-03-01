let startY = 0;
let isScrolling = false;

// Получаем основной контейнер
const attendanceContainer = document.querySelector('.attendance-container');

// Предотвращаем стандартное поведение свайпа вниз
document.addEventListener('gesturestart', function(e) {
    e.preventDefault();
}, { passive: false });

// После загрузки страницы прокручиваем контейнер на 1px вниз
window.addEventListener('DOMContentLoaded', function() {
    if (attendanceContainer) {
        attendanceContainer.scrollTop = 1;
    }
}); 