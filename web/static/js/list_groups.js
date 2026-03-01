let tg = window.Telegram.WebApp;
tg.expand();

const isMainPage = window.location.pathname === '/';
if (!isMainPage) {
    tg.BackButton.show();
    tg.BackButton.onClick(() => {
        window.location.href = '/?tgWebAppData=' + encodeURIComponent(tg.initData);
    });
} else {
    tg.BackButton.hide();
}

const groupsList = document.getElementById('groupsList');

async function loadGroups() {
    try {
        const response = await fetch(`/api/attendance/list_groups?tgWebAppData=${encodeURIComponent(tg.initData)}`);
        const data = await response.json();

        if (data.success) {
            if (data.groups.length === 0) {
                showEmptyState();
            } else {
                renderGroups(data.groups);
            }
        } else {
            showError(data.error || 'Ошибка при загрузке групп');
        }
    } catch (error) {
        showError('Ошибка при загрузке данных');
    }
}

function renderGroups(groups) {
    groupsList.innerHTML = '';

    groups.forEach(group => {
        const date = new Date(group.created_at);
        const formattedDate = date.toLocaleDateString('ru-RU', {
            year: 'numeric',
            month: 'long',
            day: 'numeric'
        });

        const card = document.createElement('a');
        card.href = `/attendance/${group.id}`;
        card.className = 'group-card';

        card.innerHTML = `
            <div class="group-info">
                <div class="group-name">${group.name}</div>
                <div class="group-meta">
                    ${group.student_count} студентов • Создана ${formattedDate}
                </div>
            </div>
            <div class="group-arrow">→</div>
        `;

        card.addEventListener('click', () => {
            tg.HapticFeedback.impactOccurred('light');
        });

        groupsList.appendChild(card);
    });
}

function showEmptyState() {
    groupsList.innerHTML = `
        <div class="empty-state">
            <div class="empty-state-icon">📝</div>
            <div class="empty-state-text">У вас пока нет групп</div>
        </div>
    `;
}

function showError(message) {
    groupsList.innerHTML = `
        <div class="empty-state">
            <div class="empty-state-icon">❌</div>
            <div class="empty-state-text">${message}</div>
        </div>
    `;
}

loadGroups();