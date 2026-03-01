let tg = window.Telegram.WebApp;
tg.expand();

tg.BackButton.show();
tg.BackButton.onClick(() => {

    window.location.href = `/?tgWebAppData=${encodeURIComponent(tg.initData)}`;
});

const monthNames = [
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
];

let currentDate = new Date();
let currentGroupId = window.location.pathname.split('/').pop();

function handleTableScroll() {
    const wrapper = document.querySelector('.attendance-table-wrapper');
    const nameColumns = document.querySelectorAll('.name-column');

    wrapper.addEventListener('scroll', () => {
        const isScrolled = wrapper.scrollLeft > 0;

        nameColumns.forEach(column => {
            if (isScrolled) {
                column.classList.add('scrolled');
            } else {
                column.classList.remove('scrolled');
            }
        });

        if (isScrolled && !wrapper.dataset.wasScrolled) {
            tg.HapticFeedback.impactOccurred('light');
            wrapper.dataset.wasScrolled = 'true';
        } else if (!isScrolled && wrapper.dataset.wasScrolled) {
            tg.HapticFeedback.impactOccurred('light');
            wrapper.dataset.wasScrolled = '';
        }
    });

    let touchStartX = 0;
    let initialScroll = 0;

    wrapper.addEventListener('touchstart', (e) => {
        touchStartX = e.touches[0].clientX;
        initialScroll = wrapper.scrollLeft;
        wrapper.style.scrollBehavior = 'auto'; 
    });

    wrapper.addEventListener('touchmove', (e) => {
        const touchCurrentX = e.touches[0].clientX;
        const diff = touchStartX - touchCurrentX;
        wrapper.scrollLeft = initialScroll + diff;
    });

    wrapper.addEventListener('touchend', () => {
        wrapper.style.scrollBehavior = 'smooth'; 
    });
}

async function loadAttendance() {
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth() + 1;

    try {
        const response = await fetch(`/api/attendance/get_attendance/${currentGroupId}/${year}/${month}`);
        const data = await response.json();

        if (data.success) {
            renderAttendance(data);

            handleTableScroll();
        } else {
            alert(data.error || 'Ошибка при загрузке данных');
        }
    } catch (error) {
        alert('Ошибка при загрузке данных');
    }
}

function renderAttendance(data) {
    document.getElementById('groupName').textContent = data.group_name;
    document.getElementById('currentMonth').textContent = 
        `${monthNames[currentDate.getMonth()]} ${currentDate.getFullYear()}`;

    const dateRow = document.getElementById('dateRow');
    const tbody = document.getElementById('attendanceBody');

    while (dateRow.children.length > 2) { 
        dateRow.removeChild(dateRow.lastChild);
    }
    tbody.innerHTML = '';

    const daysInMonth = new Date(
        currentDate.getFullYear(),
        currentDate.getMonth() + 1,
        0
    ).getDate();

    for (let day = 1; day <= daysInMonth; day++) {
        const date = new Date(currentDate.getFullYear(), currentDate.getMonth(), day);
        const th = document.createElement('th');
        th.setAttribute('colspan', '2');
        th.textContent = day;
        if (date.getDay() === 0) {
            th.classList.add('sunday');
        }
        dateRow.appendChild(th);
    }

    data.students.forEach(student => {
        const tr = document.createElement('tr');

        const nameTd = document.createElement('td');
        nameTd.className = 'name-column';
        nameTd.textContent = student.name;
        tr.appendChild(nameTd);

        const totalTd = document.createElement('td');
        totalTd.className = 'total-label-column';
        totalTd.innerHTML = `
            <div class="total-row">Н</div>
            <div class="total-row">У</div>
        `;
        tr.appendChild(totalTd);

        const totalValueTd = document.createElement('td');
        totalValueTd.className = 'total-value-column';
        totalValueTd.innerHTML = `
            <div class="total-row">${student.total_absences || 0}</div>
            <div class="total-row excused">${student.total_excused || 0}</div>
        `;
        tr.appendChild(totalValueTd);

        for (let day = 1; day <= daysInMonth; day++) {
            const date = new Date(currentDate.getFullYear(), currentDate.getMonth(), day);
            const td = document.createElement('td');
            td.className = 'attendance-cell';
            td.setAttribute('colspan', '2');

            if (date.getDay() === 0) { 
                td.classList.add('sunday');
            } else {

                const attendance = student.attendance[day] || { absences: 0, excused: 0 };

                const container = document.createElement('div');
                container.className = 'attendance-inputs-container';

                const normalInput = document.createElement('input');
                normalInput.type = 'text';
                normalInput.pattern = '[0-9]*';
                normalInput.maxLength = 2;
                normalInput.value = attendance.absences || '';
                normalInput.dataset.type = 'normal';
                normalInput.className = 'attendance-input';

                const excusedInput = document.createElement('input');
                excusedInput.type = 'text';
                excusedInput.pattern = '[0-9]*';
                excusedInput.maxLength = 2;
                excusedInput.value = attendance.excused || '';
                excusedInput.dataset.type = 'excused';
                excusedInput.className = 'attendance-input excused';

                container.appendChild(normalInput);
                container.appendChild(excusedInput);
                td.appendChild(container);

                [normalInput, excusedInput].forEach(input => {
                    input.addEventListener('input', async (e) => {
                        const value = parseInt(e.target.value) || 0;
                        if (value >= 0 && value <= 99) {
                            try {
                                const response = await fetch('/api/attendance/update', {
                                    method: 'POST',
                                    headers: {
                                        'Content-Type': 'application/json'
                                    },
                                    body: JSON.stringify({
                                        student_id: student.id,
                                        date: `${currentDate.getFullYear()}-${(currentDate.getMonth() + 1).toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`,
                                        absences: normalInput.value ? parseInt(normalInput.value) : 0,
                                        excused_absences: excusedInput.value ? parseInt(excusedInput.value) : 0,
                                        tgWebAppData: tg.initData
                                    })
                                });

                                if (response.ok) {
                                    tg.HapticFeedback.impactOccurred('light');

                                    const data = await response.json();
                                    if (data.success) {

                                        input.value = value;

                                        const totalRow = tr.querySelector('.total-value-column');
                                        if (totalRow) {
                                            const rows = totalRow.querySelectorAll('.total-row');
                                            if (rows[0]) rows[0].textContent = data.total_absences || '0';
                                            if (rows[1]) rows[1].textContent = data.total_excused || '0';
                                        }
                                    }
                                }
                            } catch (error) {
                                console.error('Ошибка при сохранении:', error);
                                alert('Ошибка при сохранении');
                            }
                        }
                    });
                });
            }

            tr.appendChild(td);
        }

        tbody.appendChild(tr);
    });
}

document.getElementById('prevMonth').addEventListener('click', () => {
    currentDate.setMonth(currentDate.getMonth() - 1);
    loadAttendance().then(() => {

        const wrapper = document.querySelector('.attendance-table-wrapper');
        wrapper.scrollLeft = 0;
    });
    tg.HapticFeedback.impactOccurred('light');
});

document.getElementById('nextMonth').addEventListener('click', () => {
    currentDate.setMonth(currentDate.getMonth() + 1);
    loadAttendance().then(() => {

        const wrapper = document.querySelector('.attendance-table-wrapper');
        wrapper.scrollLeft = 0;
    });
    tg.HapticFeedback.impactOccurred('light');
});

loadAttendance();

document.addEventListener('DOMContentLoaded', function() {
    const editBtn = document.getElementById('editGroupBtn');
    const deleteBtn = document.getElementById('deleteGroupBtn');
    const editModal = document.getElementById('editModal');
    const deleteModal = document.getElementById('deleteModal');
    const cancelEditBtn = document.getElementById('cancelEditBtn');
    const saveGroupBtn = document.getElementById('saveGroupBtn');
    const cancelDeleteBtn = document.getElementById('cancelDeleteBtn');
    const confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
    const addStudentBtn = document.getElementById('addStudentBtn');
    const editStudentsList = document.getElementById('editStudentsList');

    function createStudentInput(name = '') {
        const div = document.createElement('div');
        div.className = 'student-input';

        const input = document.createElement('input');
        input.type = 'text';
        input.value = name;
        input.placeholder = 'Имя студента';

        const removeBtn = document.createElement('button');
        removeBtn.className = 'remove-student';
        removeBtn.innerHTML = '×';
        removeBtn.onclick = () => {
            div.remove();
            tg.HapticFeedback.impactOccurred('light');
        };

        div.appendChild(input);
        div.appendChild(removeBtn);
        return div;
    }

    const adminInput = document.getElementById('adminInput');
    const addAdminBtn = document.getElementById('addAdminBtn');
    const adminsList = document.getElementById('adminsList');

    function createAdminElement(admin) {
        const div = document.createElement('div');
        div.className = 'admin-item';

        const info = document.createElement('div');
        info.className = 'admin-info';

        const name = document.createElement('div');
        name.className = 'admin-name';
        name.textContent = admin.name || admin.first_name || 'Без имени';

        const username = document.createElement('div');
        username.className = 'admin-username';
        username.textContent = admin.username ? '@' + admin.username : 'ID: ' + admin.id;

        info.appendChild(name);
        info.appendChild(username);

        const removeBtn = document.createElement('button');
        removeBtn.className = 'remove-admin-btn';
        removeBtn.innerHTML = '×';
        removeBtn.onclick = async () => {
            try {
                if (!confirm('Вы уверены, что хотите удалить этого администратора?')) {
                    return;
                }

                const response = await fetch(`/api/attendance/remove_admin/${currentGroupId}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        admin_id: admin.id,
                        tgWebAppData: tg.initData
                    })
                });

                const data = await response.json();
                if (data.success) {
                    div.remove();

                    const adminsList = document.getElementById('adminsList');
                    if (adminsList.children.length === 0) {
                        adminsList.style.display = 'none';
                    }
                    tg.HapticFeedback.impactOccurred('light');
                } else {
                    alert(data.error || 'Ошибка при удалении администратора');
                }
            } catch (error) {
                console.error('Error:', error);
                alert('Ошибка при удалении администратора');
            }
        };

        div.appendChild(info);
        div.appendChild(removeBtn);
        return div;
    }

    editBtn.addEventListener('click', async () => {
        try {
            const response = await fetch(`/api/attendance/get_group/${currentGroupId}?tgWebAppData=${encodeURIComponent(tg.initData)}`);
            const data = await response.json();

            if (data.success) {
                document.getElementById('groupNameInput').value = data.group.name;

                const adminsList = document.getElementById('adminsList');
                adminsList.innerHTML = '';

                if (data.group.admins && data.group.admins.length > 0) {
                    data.group.admins.forEach(admin => {
                        adminsList.appendChild(createAdminElement(admin));
                    });
                }

                editStudentsList.innerHTML = '';
                if (data.group.students) {
                    data.group.students.forEach(student => {
                        editStudentsList.appendChild(createStudentInput(student.name));
                    });
                }

                editModal.style.display = 'flex';
                tg.HapticFeedback.impactOccurred('light');
            } else {
                alert(data.error || 'Ошибка при загрузке данных группы');
            }
        } catch (error) {
            console.error('Error:', error);
            alert('Ошибка при загрузке данных группы');
        }
    });

    addStudentBtn.addEventListener('click', () => {
        editStudentsList.appendChild(createStudentInput());
        tg.HapticFeedback.impactOccurred('light');
    });

    saveGroupBtn.addEventListener('click', async () => {
        const groupName = document.getElementById('groupNameInput').value.trim();
        const students = Array.from(editStudentsList.querySelectorAll('input')).map(input => input.value.trim());

        if (!groupName) {
            alert('Введите название группы');
            return;
        }

        if (students.length === 0) {
            alert('Добавьте хотя бы одного студента');
            return;
        }

        try {
            const response = await fetch(`/api/attendance/update_group/${currentGroupId}`, {
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
                editModal.style.display = 'none';
                loadAttendance(); 
            } else {
                alert(data.error || 'Ошибка при сохранении');
            }
        } catch (error) {
            alert('Ошибка при сохранении');
        }
    });

    deleteBtn.addEventListener('click', () => {
        tg.HapticFeedback.impactOccurred('light');
        deleteModal.style.display = 'flex';
    });

    confirmDeleteBtn.addEventListener('click', async () => {
        try {
            const response = await fetch(`/api/attendance/delete_group/${currentGroupId}`, {
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
                window.location.href = `/attendance?tgWebAppData=${encodeURIComponent(tg.initData)}`;
            } else {
                alert(data.error || 'Ошибка при удалении');
            }
        } catch (error) {
            alert('Ошибка при удалении');
        }
    });

    [cancelEditBtn, cancelDeleteBtn].forEach(btn => {
        btn.addEventListener('click', () => {
            editModal.style.display = 'none';
            deleteModal.style.display = 'none';
            tg.HapticFeedback.impactOccurred('light');
        });
    });

    [editModal, deleteModal].forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
                tg.HapticFeedback.impactOccurred('light');
            }
        });
    });

    addAdminBtn.addEventListener('click', async () => {
        const identifier = adminInput.value.trim();
        if (!identifier) {
            alert('Введите username или ID пользователя');
            return;
        }

        try {
            const response = await fetch(`/api/attendance/add_admin/${currentGroupId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    identifier: identifier,
                    tgWebAppData: tg.initData
                })
            });

            const data = await response.json();
            if (data.success) {
                const adminsList = document.getElementById('adminsList');

                adminsList.style.display = 'block';

                adminsList.appendChild(createAdminElement(data.admin));

                adminInput.value = '';
                tg.HapticFeedback.impactOccurred('medium');
            } else {
                alert(data.error || 'Ошибка при добавлении администратора');
            }
        } catch (error) {
            console.error('Error:', error);
            alert('Ошибка при добавлении администратора');
        }
    });
});