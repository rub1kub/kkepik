// Логика для добавления картинки test.png в правый нижний угол карточки .row

window.addCornerImageToRows = function(container) {
    if (!container) return;
    if (window.kkepikApp && window.kkepikApp.lowData) return;
    // Получаем название группы из currentGroupName
    let groupText = '';
    const currentGroupNameElem = document.getElementById('currentGroupName');
    if (currentGroupNameElem) {
        groupText = currentGroupNameElem.textContent.trim();
    }
    container.querySelectorAll('.row').forEach(function(row) {
        if (!row.querySelector('.corner-image')) {
            // Определяем предмет
            let subject = '';
            const lessonName = row.querySelector('.lesson-name .group');
            if (lessonName) {
                subject = lessonName.textContent.trim().toLowerCase();
            }
            // Не добавлять картинку, если пары нет
            if (!subject || subject === 'нет') {
                return;
            }
            let imgSrc = '/static/img/test.png';
            let isDefaultIcon = true;
            if (subject === 'история') {
                imgSrc = '/static/img/history.png';
                isDefaultIcon = false;
            } else if (subject === 'физ-ра' || subject === 'физическая культура') {
                imgSrc = '/static/img/PE.png';
                isDefaultIcon = false;
            } else if (subject === 'информатика' || subject === 'информационные технологии') {
                imgSrc = '/static/img/informatics.png';
                isDefaultIcon = false;
            } else if (subject === 'химия') {
                imgSrc = '/static/img/chemistry.png';
                isDefaultIcon = false;
            } else if (subject === 'биология') {
                imgSrc = '/static/img/biology.png';
                isDefaultIcon = false;
            } else if (subject === 'обществознание') {
                imgSrc = '/static/img/social.png';
                isDefaultIcon = false;
            } else if (
                subject === 'английский язык' ||
                subject === 'английский' ||
                subject === 'ин. яз' ||
                subject === 'ин яз'
            ) {
                imgSrc = '/static/img/english.png';
                isDefaultIcon = false;
            } else if (subject === 'русский язык' || subject === 'русский') {
                imgSrc = '/static/img/russian.png';
                isDefaultIcon = false;
            } else if (subject === 'литература' || subject === 'лит-ра') {
                imgSrc = '/static/img/literature.png';
                isDefaultIcon = false;
            } else if (subject === 'физика') {
                imgSrc = '/static/img/physics.png';
                isDefaultIcon = false;
            } else if (subject === 'математика') {
                imgSrc = '/static/img/math.png';
                isDefaultIcon = false;
            } else if (subject === 'география') {
                imgSrc = '/static/img/geography.png';
                isDefaultIcon = false;
            } else if (subject === 'инд. проект' || subject === 'индивидуальный проект') {
                imgSrc = '/static/img/project.png';
                isDefaultIcon = false;
            } else if (subject === 'организация, принципы построения и функционирования компьютерных сетей') {
                imgSrc = '/static/img/network.png';
                isDefaultIcon = false;
            } else if (subject === 'администрирование сетевых операционных систем') {
                imgSrc = '/static/img/os-admin.png';
                isDefaultIcon = false;
            } else if (subject === 'практика') {
                imgSrc = '/static/img/practice.png';
                isDefaultIcon = false;
            } else if (subject === 'обж' || subject === 'основы безопасности и защиты родины') {
                imgSrc = '/static/img/safety.png';
                isDefaultIcon = false;
            } else if (subject === 'мет.провед.станд.и сертиф.испы') {
                imgSrc = '/static/img/certification-tests.png';
                isDefaultIcon = false;
            } else if (subject === 'радиоприемн. и радиопередающ.у') {
                imgSrc = '/static/img/rs-units.png';
                isDefaultIcon = false;
            } else if (subject === 'основы философии') {
                imgSrc = '/static/img/philosophy.png';
                isDefaultIcon = false;
            }
            // Определяем курс по названию группы (ищем в currentGroupName)
            let course = null;
            const match = groupText.match(/-(\d)[^\d\s-]*/i);
            if (match) {
                course = parseInt(match[1], 10);
            }
            // Если иконка дефолтная и курс 2, 3 или 4 — не добавлять картинку
            if (isDefaultIcon && (course === 2 || course === 3 || course === 4)) {
                return;
            }
            const img = document.createElement('img');
            img.src = imgSrc.replace('/static/img/', '/static/img/optimized/').replace('.png', '.webp') + '?v=20260825-2';
            img.onerror = function () {
                img.onerror = null;
                img.src = imgSrc;
            };
            img.alt = 'corner';
            img.className = 'corner-image';
            img.loading = 'lazy';
            img.decoding = 'async';
            img.fetchPriority = 'low';
            // Вставляем перед первым div (время)
            const firstDiv = row.querySelector('div');
            if (firstDiv) {
                row.insertBefore(img, firstDiv);
            } else {
                row.appendChild(img);
            }
        }
    });
};

window.addCornerImageToRows(document.querySelector('#bell_list .row-container'));
