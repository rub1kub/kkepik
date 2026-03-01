let tg = window.Telegram.WebApp;
tg.expand();

tg.BackButton.show();
tg.BackButton.onClick(() => {
    window.location.href = `/?tgWebAppData=${encodeURIComponent(tg.initData)}`;
});

const getKeyBtn = document.getElementById('getKeyBtn');
const keysList = document.getElementById('keysList');
const errorContainer = document.createElement('div');
errorContainer.id = 'errorContainer';
document.querySelector('.content-wrapper').appendChild(errorContainer);

function showError(message) {
    const errorElement = document.createElement('div');
    errorElement.className = 'error-message';
    errorElement.innerHTML = `
        <span class="icon">⚠️</span>
        ${message}
    `;
    errorContainer.appendChild(errorElement);

    setTimeout(() => {
        errorElement.remove();
    }, 3000);
}

function displayKey(key) {
    keysList.innerHTML = `
        <div class="key-block">
            <div class="key-display">
                <p class="key-text">${key}</p>
                <div class="button-group">
                    <button class="copy-button" onclick="copyKey('${key}')">
                        <span class="button-icon">📋</span>
                    </button>
                    <button class="delete-button" onclick="deleteKey()">
                        <span class="button-icon">🗑️</span>
                    </button>
                </div>
            </div>
        </div>
    `;
    getKeyBtn.style.display = 'none';
}

async function copyKey(key) {
    try {
        await navigator.clipboard.writeText(key);
        tg.HapticFeedback.impactOccurred('light');
        showNotification('Ключ скопирован');
    } catch (error) {
        console.error('Error:', error);
        showError('Ошибка при копировании ключа');
    }
}

async function deleteKey() {
    try {
        const response = await fetch('/api/vpn/delete_key', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                tgWebAppData: tg.initData
            })
        });

        const data = await response.json();
        if (data.success) {
            tg.HapticFeedback.impactOccurred('medium');
            showNotification('Ключ удален');
            getKeyBtn.style.display = 'block';
            keysList.innerHTML = '';
        } else {
            showError(data.error || 'Ошибка при удалении ключа');
        }
    } catch (error) {
        console.error('Error:', error);
        showError('Ошибка при удалении ключа');
    }
}

async function generateKey() {
    try {
        getKeyBtn.disabled = true;
        getKeyBtn.innerHTML = '<span class="button-icon">⏳</span> Создаем ключ...';

        const response = await fetch('/api/vpn/generate_key', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                tgWebAppData: tg.initData
            })
        });

        const data = await response.json();
        if (data.success) {
            displayKey(data.key);
            tg.HapticFeedback.impactOccurred('medium');
        } else {
            showError(data.error || 'Ошибка при генерации ключа');
            getKeyBtn.disabled = false;
            getKeyBtn.innerHTML = '<span class="button-icon">🔑</span><span class="button-text">Получить ключ</span>';
        }
    } catch (error) {
        console.error('Error:', error);
        showError('Ошибка при генерации ключа');
        getKeyBtn.disabled = false;
        getKeyBtn.innerHTML = '<span class="button-icon">🔑</span><span class="button-text">Получить ключ</span>';
    }
}

async function checkExistingKey() {
    try {
        const response = await fetch('/api/vpn/get_user_key', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                tgWebAppData: tg.initData
            })
        });

        const data = await response.json();
        if (data.success) {
            if (data.hasKey) {
                displayKey(data.key);
            } else {
                getKeyBtn.style.display = 'block';
                keysList.innerHTML = ''; 
            }
        } else {
            showError(data.error || 'Ошибка при проверке ключа');
            getKeyBtn.style.display = 'block';
            keysList.innerHTML = '';
        }
    } catch (error) {
        console.error('Error:', error);
        showError('Ошибка при проверке ключа');
        getKeyBtn.style.display = 'block';
        keysList.innerHTML = '';
    }
}

function showNotification(message) {
    const notification = document.createElement('div');
    notification.className = 'notification';
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.remove();
    }, 2000);
}

window.generateKey = generateKey;
window.copyKey = copyKey;
window.deleteKey = deleteKey;

getKeyBtn.addEventListener('click', generateKey);

document.addEventListener('DOMContentLoaded', () => {
    checkExistingKey();
});