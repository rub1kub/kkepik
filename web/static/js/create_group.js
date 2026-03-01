let tg = window.Telegram.WebApp;
tg.expand();

tg.BackButton.show();
tg.BackButton.onClick(() => {
    window.location.href = `/attendance?tgWebAppData=${encodeURIComponent(tg.initData)}`;
});

const studentsList = document.getElementById('studentsList');
const addStudentBtn = document.getElementById('addStudent');
const saveGroupBtn = document.getElementById('saveGroup');

function createStudentInput() {
    const div = document.createElement('div');
    div.className = 'student-input';

    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = 'Имя студента';
    input.required = true;

    const removeBtn = document.createElement('button');
    removeBtn.className = 'remove-student';
    removeBtn.title = 'Удалить';
    removeBtn.textContent = '×';
    removeBtn.onclick = () => {
        div.remove();
        tg.HapticFeedback.impactOccurred('light');
    };

    div.appendChild(input);
    div.appendChild(removeBtn);
    return div;
}

addStudentBtn.addEventListener('click', () => {
    const input = createStudentInput();
    studentsList.appendChild(input);
    input.querySelector('input').focus();
    tg.HapticFeedback.impactOccurred('light');
});

saveGroupBtn.addEventListener('click', async () => {
    const groupName = document.getElementById('groupName').value.trim();
    if (!groupName) {
        alert('Введите название группы');
        return;
    }

    const students = [];
    const inputs = studentsList.querySelectorAll('input');
    for (const input of inputs) {
        const name = input.value.trim();
        if (name) {
            students.push(name);
        }
    }

    if (students.length === 0) {
        alert('Добавьте хотя бы одного студента');
        return;
    }

    try {
        const response = await fetch('/api/attendance/create_group', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                name: groupName,
                students: students,
                tgWebAppData: tg.initData
            })
        });

        const data = await response.json();
        if (data.success) {
            tg.HapticFeedback.impactOccurred('medium');
            window.location.href = `/attendance/${data.group_id}`;
        } else {
            alert(data.error || 'Ошибка при создании группы');
        }
    } catch (error) {
        alert('Ошибка при сохранении');
    }
});

studentsList.appendChild(createStudentInput());