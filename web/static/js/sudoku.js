let tg = window.Telegram.WebApp;
tg.expand();
tg.BackButton.show();
tg.BackButton.onClick(function() {
    window.location.href = '/';
});

class SudokuGame {
    constructor() {
        this.board = Array(9).fill().map(() => Array(9).fill(0));
        this.solution = Array(9).fill().map(() => Array(9).fill(0));
        this.originalBoard = Array(9).fill().map(() => Array(9).fill(0));
        this.selectedCell = null;
        this.notesMode = false;
        this.notes = Array(9).fill().map(() => Array(9).fill().map(() => new Set()));
        this.startTime = Date.now();
        this.timer = null;
        this.difficulty = 'easy';
        this.hintsUsed = 0;
        this.maxHints = 3;
        this.gameId = null;
        this.userId = null;
        this.isGameCompleted = false;
        this.completionTime = 0;
        this.difficultySelected = false;
        this.lives = 3;
        this.maxLives = 3;
        this.errors = new Set(); // Множество для отслеживания ошибок
        
        this.init();
    }
    
    init() {
        this.createBoard();
        this.bindEvents();
        this.showDifficultyModal();
        this.loadRating();
    }
    
    createBoard() {
        const board = document.getElementById('sudokuBoard');
        board.innerHTML = '';
        
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                const cell = document.createElement('div');
                cell.className = 'sudoku-cell';
                cell.dataset.row = row;
                cell.dataset.col = col;
                cell.addEventListener('click', () => this.selectCell(row, col));
                board.appendChild(cell);
            }
        }
        
        // Добавляем толстые линии для разделения блоков 3x3
        const gridLines = document.createElement('div');
        gridLines.className = 'sudoku-grid-lines';
        gridLines.innerHTML = `
            <div class="thick-line vertical" style="left: 33.33%;"></div>
            <div class="thick-line vertical" style="left: 66.66%;"></div>
            <div class="thick-line horizontal" style="top: 33.33%;"></div>
            <div class="thick-line horizontal" style="top: 66.66%;"></div>
        `;
        board.appendChild(gridLines);
    }
    
    bindEvents() {
        // Кнопки с цифрами уже используют onclick в HTML
        
        // Кнопки теперь используют onclick обработчики в HTML
        
        // Клавиатура
        document.addEventListener('keydown', (e) => {
            if (e.key >= '1' && e.key <= '9') {
                this.inputNumber(parseInt(e.key));
            } else if (e.key === 'Delete' || e.key === 'Backspace') {
                this.clearCell();
            } else if (e.key === 'n' || e.key === 'N') {
                this.toggleNotesMode();
            } else if (e.key === 'h' || e.key === 'H') {
                this.getHint();
            } else if (e.key === ' ') {
                e.preventDefault();
                this.getHint();
            }
        });
    }
    
    async generatePuzzle() {
        // Создаем новую игру на сервере
        try {
            const response = await fetch('/api/sudoku/new_game', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    tgWebAppData: tg.initData,
                    difficulty: this.difficulty
                })
            });
            const data = await response.json();
            
            if (data.success) {
                this.gameId = data.game_id;
                this.userId = data.user_id;
                this.board = data.puzzle;
                this.solution = data.solution;
                this.originalBoard = data.puzzle.map(row => [...row]);
                this.updateDisplay();
            } else {
                // Если сервер недоступен, генерируем локально
                this.generatePuzzleLocally();
            }
        } catch (error) {
            console.error('Failed to create game on server:', error);
            this.generatePuzzleLocally();
        }
        
        // Игра готова к началу
    }
    
    generatePuzzleLocally() {
        // Генерируем полное решение
        this.generateSolution();
        
        // Копируем решение
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                this.solution[row][col] = this.board[row][col];
            }
        }
        
        // Удаляем числа в зависимости от сложности
        const cellsToRemove = this.difficulty === 'easy' ? 40 : 
                             this.difficulty === 'medium' ? 50 : 60;
        
        let removed = 0;
        while (removed < cellsToRemove) {
            const row = Math.floor(Math.random() * 9);
            const col = Math.floor(Math.random() * 9);
            
            if (this.board[row][col] !== 0) {
                this.board[row][col] = 0;
                removed++;
            }
        }
        
        // Отмечаем исходные числа (те, что остались после удаления)
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                if (this.board[row][col] !== 0) {
                    this.originalBoard[row][col] = this.board[row][col];
                } else {
                    this.originalBoard[row][col] = 0;
                }
            }
        }
        
        this.updateDisplay();
    }
    
    generateSolution() {
        // Очищаем доску
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                this.board[row][col] = 0;
            }
        }
        
        // Заполняем диагональные блоки 3x3
        this.fillDiagonalBoxes();
        
        // Заполняем остальные клетки
        this.solveRemaining(0, 0);
    }
    
    fillDiagonalBoxes() {
        for (let box = 0; box < 9; box += 3) {
            this.fillBox(box, box);
        }
    }
    
    fillBox(row, col) {
        const numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9];
        this.shuffleArray(numbers);
        
        let index = 0;
        for (let i = 0; i < 3; i++) {
            for (let j = 0; j < 3; j++) {
                this.board[row + i][col + j] = numbers[index++];
            }
        }
    }
    
    shuffleArray(array) {
        for (let i = array.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [array[i], array[j]] = [array[j], array[i]];
        }
    }
    
    solveRemaining(row, col) {
        if (row === 9) return true;
        if (col === 9) return this.solveRemaining(row + 1, 0);
        if (this.board[row][col] !== 0) return this.solveRemaining(row, col + 1);
        
        for (let num = 1; num <= 9; num++) {
            if (this.isValidMove(row, col, num)) {
                this.board[row][col] = num;
                if (this.solveRemaining(row, col + 1)) return true;
                this.board[row][col] = 0;
            }
        }
        return false;
    }
    
    isValidMove(row, col, num) {
        // Проверяем строку (исключая текущую позицию)
        for (let c = 0; c < 9; c++) {
            if (c !== col && this.board[row][c] === num) return false;
        }
        
        // Проверяем столбец (исключая текущую позицию)
        for (let r = 0; r < 9; r++) {
            if (r !== row && this.board[r][col] === num) return false;
        }
        
        // Проверяем блок 3x3 (исключая текущую позицию)
        const boxRow = Math.floor(row / 3) * 3;
        const boxCol = Math.floor(col / 3) * 3;
        for (let r = boxRow; r < boxRow + 3; r++) {
            for (let c = boxCol; c < boxCol + 3; c++) {
                if ((r !== row || c !== col) && this.board[r][c] === num) return false;
            }
        }
        
        return true;
    }
    
    // Функция для проверки всей доски (для тестирования)
    isBoardValid() {
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                const num = this.board[row][col];
                if (num !== 0 && !this.isValidMove(row, col, num)) {
                    return false;
                }
            }
        }
        return true;
    }
    
    selectCell(row, col) {
        if (this.originalBoard[row][col] !== 0) return; // Нельзя изменять исходные числа
        
        // Убираем выделение с предыдущей клетки
        if (this.selectedCell) {
            const prevCell = document.querySelector(`[data-row="${this.selectedCell[0]}"][data-col="${this.selectedCell[1]}"]`);
            if (prevCell) prevCell.classList.remove('selected');
        }
        
        // Выделяем новую клетку
        this.selectedCell = [row, col];
        const cell = document.querySelector(`[data-row="${row}"][data-col="${col}"]`);
        if (cell) cell.classList.add('selected');
        
        // Подсвечиваем связанные клетки
        this.highlightRelatedCells(row, col);
    }
    
    highlightRelatedCells(row, col) {
        // Убираем предыдущую подсветку
        document.querySelectorAll('.sudoku-cell').forEach(cell => {
            cell.classList.remove('highlight');
        });
        
        // Подсвечиваем строку, столбец и блок
        for (let i = 0; i < 9; i++) {
            // Строка
            const rowCell = document.querySelector(`[data-row="${row}"][data-col="${i}"]`);
            if (rowCell) rowCell.classList.add('highlight');
            
            // Столбец
            const colCell = document.querySelector(`[data-row="${i}"][data-col="${col}"]`);
            if (colCell) colCell.classList.add('highlight');
        }
        
        // Блок 3x3
        const boxRow = Math.floor(row / 3) * 3;
        const boxCol = Math.floor(col / 3) * 3;
        for (let r = boxRow; r < boxRow + 3; r++) {
            for (let c = boxCol; c < boxCol + 3; c++) {
                const boxCell = document.querySelector(`[data-row="${r}"][data-col="${c}"]`);
                if (boxCell) boxCell.classList.add('highlight');
            }
        }
    }
    
    inputNumber(number) {
        if (!this.selectedCell) return;
        
        // Проверяем, что number - это валидное число
        if (isNaN(number) || number < 1 || number > 9) {
            console.error('Invalid number:', number);
            return;
        }
        
        const [row, col] = this.selectedCell;
        
        if (this.notesMode) {
            this.toggleNote(row, col, number);
        } else {
            this.board[row][col] = number;
            this.notes[row][col].clear();
            this.updateDisplay();
            this.addCellAnimation(row, col);
            
            // Сразу проверяем ошибку для этой ячейки
            if (!this.isValidMove(row, col, number)) {
                const cell = document.querySelector(`[data-row="${row}"][data-col="${col}"]`);
                if (cell) {
                    cell.classList.add('error');
                }
                // Добавляем ошибку в множество
                const cellKey = `${row}-${col}`;
                this.errors.add(cellKey);
                // Отнимаем жизнь за ошибку
                if (this.lives > 0) {
                    this.loseLife();
                }
            }
            
            this.checkForErrors();
            this.checkWin();
        }
        
        tg.HapticFeedback.impactOccurred('soft');
    }

    updateLivesDisplay() {
        const livesContainer = document.getElementById('lives');
        if (!livesContainer) return;

        const lifeElements = livesContainer.querySelectorAll('.life');
        lifeElements.forEach((life, index) => {
            if (index < this.lives) {
                life.classList.remove('lost');
                life.textContent = '❤️';
            } else {
                life.classList.add('lost');
                life.textContent = '💔';
            }
        });
    }

    loseLife() {
        if (this.lives > 0) {
            this.lives--;
            this.updateLivesDisplay();
            
            // Вибрация при потере жизни
            tg.HapticFeedback.impactOccurred('heavy');
            
            // Показываем модальное окно только когда жизни заканчиваются
            if (this.lives === 0) {
                this.gameOver();
            }
        }
    }

    gameOver() {
        this.isGameCompleted = true;
        this.stopTimer();
        
        // Показываем модальное окно приложения
        const gameOverModal = document.getElementById('gameOverModal');
        if (gameOverModal) {
            gameOverModal.style.display = 'flex';
        }
    }

    closeGameOverModal() {
        const gameOverModal = document.getElementById('gameOverModal');
        if (gameOverModal) {
            gameOverModal.style.display = 'none';
        }
    }

    addCellAnimation(row, col) {
        const cell = document.querySelector(`[data-row="${row}"][data-col="${col}"]`);
        if (cell) {
            cell.classList.add('just-filled');
            setTimeout(() => {
                cell.classList.remove('just-filled');
            }, 200);
        }
    }
    
    toggleNote(row, col, number) {
        if (this.notes[row][col].has(number)) {
            this.notes[row][col].delete(number);
        } else {
            this.notes[row][col].add(number);
        }
        this.updateDisplay();
    }
    
    clearCell() {
        if (!this.selectedCell) return;
        
        const [row, col] = this.selectedCell;
        this.board[row][col] = 0;
        this.notes[row][col].clear();
        this.updateDisplay();
        this.checkForErrors();
        
        // Убираем ошибку из множества, если она была
        const cellKey = `${row}-${col}`;
        this.errors.delete(cellKey);
        
        tg.HapticFeedback.impactOccurred('heavy');
    }

    
    toggleNotes() {
        this.notesMode = !this.notesMode;
        const btn = document.getElementById('notesBtn');
        btn.classList.toggle('notes-mode', this.notesMode);
        btn.textContent = this.notesMode ? 'Цифры' : 'Заметки';
    }
    
    getHint() {
        if (this.hintsUsed >= this.maxHints) {
            tg.showAlert('Подсказки закончились!');
            return;
        }
        
        // Находим пустую клетку
        const emptyCells = [];
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                if (this.board[row][col] === 0) {
                    emptyCells.push([row, col]);
                }
            }
        }
        
        if (emptyCells.length === 0) return;
        
        const randomCell = emptyCells[Math.floor(Math.random() * emptyCells.length)];
        const [row, col] = randomCell;
        
        this.board[row][col] = this.solution[row][col];
        this.notes[row][col].clear();
        this.hintsUsed++;
        
        this.updateDisplay();
        this.checkWin();
        
        tg.HapticFeedback.impactOccurred('heavy');
    }
    
    updateDisplay() {
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                const cell = document.querySelector(`[data-row="${row}"][data-col="${col}"]`);
                if (!cell) continue;
                
                // Очищаем классы (кроме error - он должен оставаться)
                cell.classList.remove('given');
                cell.innerHTML = '';
                
                const value = this.board[row][col];
                const isOriginal = this.originalBoard[row][col] !== 0;
                const cellKey = `${row}-${col}`;
                
                // Проверяем, есть ли ошибка в этой ячейке
                const hasError = this.errors.has(cellKey);
                
                if (isOriginal) {
                    cell.classList.add('given');
                    cell.textContent = value;
                } else if (value !== 0) {
                    cell.textContent = value;
                    // Добавляем класс ошибки если есть ошибка
                    if (hasError) {
                        cell.classList.add('error');
                    }
                } else if (this.notes[row][col].size > 0) {
                    // Показываем заметки
                    const notesDiv = document.createElement('div');
                    notesDiv.className = 'cell-notes';
                    
                    for (let num = 1; num <= 9; num++) {
                        const noteDiv = document.createElement('div');
                        noteDiv.className = 'note-number';
                        if (this.notes[row][col].has(num)) {
                            noteDiv.textContent = num;
                        }
                        notesDiv.appendChild(noteDiv);
                    }
                    
                    cell.appendChild(notesDiv);
                }
            }
        }
    }
    
    checkForErrors() {
        const currentErrors = new Set();
        
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                const cell = document.querySelector(`[data-row="${row}"][data-col="${col}"]`);
                if (!cell) continue;
                
                const value = this.board[row][col];
                const isOriginal = this.originalBoard[row][col] !== 0;
                const cellKey = `${row}-${col}`;
                
                // Убираем класс ошибки
                cell.classList.remove('error');
                
                // Проверяем только неоригинальные клетки с числами
                if (!isOriginal && value !== 0) {
                    if (!this.isValidMove(row, col, value)) {
                        cell.classList.add('error');
                        currentErrors.add(cellKey);
                    }
                }
            }
        }
        
        // Обновляем множество ошибок
        this.errors = currentErrors;
        
        return currentErrors.size > 0;
    }
    
    checkWin() {
        // Проверяем, заполнена ли доска
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                if (this.board[row][col] === 0) return;
            }
        }
        
        // Проверяем правильность решения
        for (let row = 0; row < 9; row++) {
            for (let col = 0; col < 9; col++) {
                if (!this.isValidMove(row, col, this.board[row][col])) {
                    return;
                }
            }
        }
        
        // Победа!
        this.gameWon();
    }
    
    async gameWon() {
        if (this.isGameCompleted) return;
        this.isGameCompleted = true;
        
        this.completionTime = Math.floor((Date.now() - this.startTime) / 1000);
        this.stopTimer();
        
        // Добавляем анимацию победы
        const board = document.getElementById('sudokuBoard');
        if (board) {
            board.classList.add('win');
        }
        
        // Показываем уведомление о победе через alert
        tg.showAlert(`🎉 Поздравляем! Вы решили судоку за ${this.formatTime(this.completionTime)}!`);
        
        // Запускаем конфетти
        if (window.launchConfetti) {
            window.launchConfetti();
        }
        
        tg.HapticFeedback.notificationOccurred('success');
        
        // Отправляем результат на сервер
        await this.submitScore();
        
        // Обновляем рейтинг
        this.loadRating();
        
        setTimeout(() => {
            tg.showAlert(`Отлично! Вы решили судоку за ${this.formatTime(this.completionTime)}!`);
        }, 1000);
    }
    
    async submitScore() {
        if (!this.gameId || !this.userId) return;
        
        try {
            const response = await fetch('/api/sudoku/submit_score', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    tgWebAppData: tg.initData,
                    game_id: this.gameId,
                    completion_time: this.completionTime,
                    difficulty: this.difficulty,
                    hints_used: this.hintsUsed
                })
            });
            
            const data = await response.json();
            if (data.success) {
                console.log('Score submitted successfully');
            }
        } catch (error) {
            console.error('Failed to submit score:', error);
        }
    }
    
    async loadRating() {
        try {
            const response = await fetch('/api/sudoku/rating');
            const data = await response.json();
            
            if (data.success) {
                // Группируем по сложностям
                const ratings = {
                    easy: [],
                    medium: [],
                    hard: []
                };
                
                data.rating.forEach(player => {
                    if (ratings[player.difficulty]) {
                        ratings[player.difficulty].push(player);
                    }
                });
                
                // Сортируем каждую группу по времени
                Object.keys(ratings).forEach(difficulty => {
                    ratings[difficulty].sort((a, b) => a.best_time - b.best_time);
                });
                
                // Обновляем HTML
                this.updateRatingDisplay(ratings);
            }
        } catch (error) {
            console.error('Failed to load rating:', error);
        }
    }
    
    updateRatingDisplay(ratings) {
        const container = document.querySelector('.rating-section');
        container.innerHTML = `
            <div class="rating-title">🏆 Рейтинг игроков</div>
            
            <div class="rating-tables">
                <div class="rating-table-container">
                    <h3>🥉 Легкий</h3>
                    <table class="rating-table">
                        <thead>
                            <tr><th>#</th><th>Игрок</th><th>Время</th></tr>
                        </thead>
                        <tbody id="ratingEasyBody"></tbody>
                    </table>
                </div>
                
                <div class="rating-table-container">
                    <h3>🥈 Средний</h3>
                    <table class="rating-table">
                        <thead>
                            <tr><th>#</th><th>Игрок</th><th>Время</th></tr>
                        </thead>
                        <tbody id="ratingMediumBody"></tbody>
                    </table>
                </div>
                
                <div class="rating-table-container">
                    <h3>🥇 Сложный</h3>
                    <table class="rating-table">
                        <thead>
                            <tr><th>#</th><th>Игрок</th><th>Время</th></tr>
                        </thead>
                        <tbody id="ratingHardBody"></tbody>
                    </table>
                </div>
            </div>
        `;
        
        // Заполняем таблицы
        this.fillRatingTable('ratingEasyBody', ratings.easy.slice(0, 10));
        this.fillRatingTable('ratingMediumBody', ratings.medium.slice(0, 10));
        this.fillRatingTable('ratingHardBody', ratings.hard.slice(0, 10));
    }
    
    fillRatingTable(tableId, players) {
        const tbody = document.getElementById(tableId);
        tbody.innerHTML = '';
        
        if (players.length === 0) {
            const row = document.createElement('tr');
            row.innerHTML = '<td colspan="3" style="text-align: center; color: #666;">Нет записей</td>';
            tbody.appendChild(row);
            return;
        }
        
        players.forEach((player, index) => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${index + 1}</td>
                <td>${player.first_name || 'Игрок'}</td>
                <td>${this.formatTime(player.best_time)}</td>
            `;
            tbody.appendChild(row);
        });
    }
    
    formatTime(seconds) {
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
    }
    
    newGame() {
        // Закрываем модальное окно окончания игры если оно открыто
        this.closeGameOverModal();
        
        if (confirm('Начать новую игру? Текущий прогресс будет потерян.')) {
            this.resetGame();
            this.showDifficultyModal();
        }
    }
    
    resetGame() {
        this.board = Array(9).fill().map(() => Array(9).fill(0));
        this.solution = Array(9).fill().map(() => Array(9).fill(0));
        this.originalBoard = Array(9).fill().map(() => Array(9).fill(0));
        this.selectedCell = null;
        this.notes = Array(9).fill().map(() => Array(9).fill().map(() => new Set()));
        this.startTime = Date.now();
        this.hintsUsed = 0;
        this.isGameCompleted = false;
        this.completionTime = 0;
        this.lives = this.maxLives;
        this.errors = new Set();
        
        // Очищаем отображение
        document.querySelectorAll('.sudoku-cell').forEach(cell => {
            cell.classList.remove('selected', 'highlight', 'given', 'error');
            cell.innerHTML = '';
        });
        
        // Обновляем отображение жизней
        this.updateLivesDisplay();
        
        // Статус больше не используется
        
        this.stopTimer();
        this.hideDifficultyButtons();
    }
    
    startTimer() {
        this.startTime = Date.now();
        this.timer = setInterval(() => {
            const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
            document.getElementById('timer').textContent = this.formatTime(elapsed);
        }, 1000);
    }
    
    stopTimer() {
        if (this.timer) {
            clearInterval(this.timer);
            this.timer = null;
        }
    }
    
    setDifficulty(difficulty) {
        // Эта функция больше не используется - сложность выбирается в модальном окне
        console.log('setDifficulty called but not used anymore');
    }
    
    hideDifficultyButtons() {
        const difficultyButtons = document.querySelector('.sudoku-controls:last-child');
        if (difficultyButtons) {
            difficultyButtons.style.display = 'none';
        }
    }
    
    showDifficultyButtons() {
        const difficultyButtons = document.querySelector('.sudoku-controls:last-child');
        if (difficultyButtons) {
            difficultyButtons.style.display = 'flex';
        }
    }
    
    showDifficultyModal() {
        const modal = document.getElementById('difficultyModal');
        const gameContainer = document.getElementById('gameContainer');
        
        if (modal && gameContainer) {
            modal.style.display = 'flex';
            gameContainer.style.display = 'none';
            
            // Добавляем обработчики для кнопок сложности
            const difficultyOptions = modal.querySelectorAll('.difficulty-option');
            difficultyOptions.forEach(option => {
                option.addEventListener('click', () => {
                    const difficulty = option.dataset.difficulty;
                    this.selectDifficulty(difficulty);
                });
            });
        }
    }
    
    hideDifficultyModal() {
        const modal = document.getElementById('difficultyModal');
        const gameContainer = document.getElementById('gameContainer');
        
        if (modal && gameContainer) {
            modal.style.display = 'none';
            gameContainer.style.display = 'block';
        }
    }
    
    selectDifficulty(difficulty) {
        this.difficulty = difficulty;
        this.difficultySelected = true;
        
        // Обновляем отображение сложности
        const difficultyNames = {
            'easy': 'Легкий',
            'medium': 'Средний',
            'hard': 'Сложный'
        };
        const difficultyElement = document.getElementById('difficulty');
        if (difficultyElement) {
            difficultyElement.textContent = difficultyNames[difficulty];
        }
        
        // Скрываем модальное окно и показываем игру
        this.hideDifficultyModal();
        
        // Генерируем головоломку и запускаем игру
        this.generatePuzzle();
        this.startTimer();
        
        // Тактильная обратная связь
        if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred('medium');
        }
    }
}

// Функция для тестирования логики
function testSudokuLogic(game) {
    console.log('🧪 Тестирование логики судоку...');
    
    // Тест 1: Валидная доска должна быть валидной
    const validBoard = [
        [1, 2, 3, 4, 5, 6, 7, 8, 9],
        [4, 5, 6, 7, 8, 9, 1, 2, 3],
        [7, 8, 9, 1, 2, 3, 4, 5, 6],
        [2, 3, 4, 5, 6, 7, 8, 9, 1],
        [5, 6, 7, 8, 9, 1, 2, 3, 4],
        [8, 9, 1, 2, 3, 4, 5, 6, 7],
        [3, 4, 5, 6, 7, 8, 9, 1, 2],
        [6, 7, 8, 9, 1, 2, 3, 4, 5],
        [9, 1, 2, 3, 4, 5, 6, 7, 8]
    ];
    
    game.board = validBoard.map(row => [...row]);
    
    if (game.isBoardValid()) {
        console.log('✅ Тест 1 пройден: Валидная доска корректно распознается');
    } else {
        console.error('❌ Тест 1 провален: Валидная доска не распознается');
    }
    
    // Тест 2: Дублирование в строке должно быть невалидным
    const rowDuplicateBoard = [
        [1, 2, 3, 4, 5, 6, 7, 8, 9],
        [4, 5, 6, 7, 8, 9, 1, 2, 3],
        [7, 8, 9, 1, 2, 3, 4, 5, 6],
        [2, 3, 4, 5, 6, 7, 8, 9, 1],
        [5, 6, 7, 8, 9, 1, 2, 3, 4],
        [8, 9, 1, 2, 3, 4, 5, 6, 7],
        [3, 4, 5, 6, 7, 8, 9, 1, 2],
        [6, 7, 8, 9, 1, 2, 3, 4, 5],
        [9, 1, 2, 3, 4, 5, 6, 7, 8]
    ];
    rowDuplicateBoard[0][0] = 2; // Дублируем 2 в первой строке
    game.board = rowDuplicateBoard.map(row => [...row]);
    
    if (!game.isBoardValid()) {
        console.log('✅ Тест 2 пройден: Дублирование в строке корректно обнаружено');
    } else {
        console.error('❌ Тест 2 провален: Дублирование в строке не обнаружено');
    }
    
    // Тест 3: Дублирование в столбце должно быть невалидным
    const colDuplicateBoard = [
        [1, 2, 3, 4, 5, 6, 7, 8, 9],
        [4, 5, 6, 7, 8, 9, 1, 2, 3],
        [7, 8, 9, 1, 2, 3, 4, 5, 6],
        [2, 3, 4, 5, 6, 7, 8, 9, 1],
        [5, 6, 7, 8, 9, 1, 2, 3, 4],
        [8, 9, 1, 2, 3, 4, 5, 6, 7],
        [3, 4, 5, 6, 7, 8, 9, 1, 2],
        [6, 7, 8, 9, 1, 2, 3, 4, 5],
        [9, 1, 2, 3, 4, 5, 6, 7, 8]
    ];
    colDuplicateBoard[1][0] = 1; // Дублируем 1 в первом столбце
    game.board = colDuplicateBoard.map(row => [...row]);
    
    if (!game.isBoardValid()) {
        console.log('✅ Тест 3 пройден: Дублирование в столбце корректно обнаружено');
    } else {
        console.error('❌ Тест 3 провален: Дублирование в столбце не обнаружено');
    }
    
    // Тест 4: Дублирование в блоке должно быть невалидным
    const blockDuplicateBoard = [
        [1, 2, 3, 4, 5, 6, 7, 8, 9],
        [4, 5, 6, 7, 8, 9, 1, 2, 3],
        [7, 8, 9, 1, 2, 3, 4, 5, 6],
        [2, 3, 4, 5, 6, 7, 8, 9, 1],
        [5, 6, 7, 8, 9, 1, 2, 3, 4],
        [8, 9, 1, 2, 3, 4, 5, 6, 7],
        [3, 4, 5, 6, 7, 8, 9, 1, 2],
        [6, 7, 8, 9, 1, 2, 3, 4, 5],
        [9, 1, 2, 3, 4, 5, 6, 7, 8]
    ];
    blockDuplicateBoard[0][1] = 1; // Дублируем 1 в первом блоке 3x3
    game.board = blockDuplicateBoard.map(row => [...row]);
    
    if (!game.isBoardValid()) {
        console.log('✅ Тест 4 пройден: Дублирование в блоке корректно обнаружено');
    } else {
        console.error('❌ Тест 4 провален: Дублирование в блоке не обнаружено');
    }
    
    console.log('🧪 Тестирование завершено');
}

// Инициализация игры
document.addEventListener('DOMContentLoaded', function() {
    // Запускаем тесты с временным объектом
    const testGame = new SudokuGame();
    testSudokuLogic(testGame);
});