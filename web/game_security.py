import json
import secrets


BOARD_SIZE_2048 = 4
SNAKE_GRID_SIZE = 20
SNAKE_INITIAL_LENGTH = 3
VALID_2048_DIRECTIONS = frozenset({'left', 'right', 'up', 'down'})
SNAKE_DIRECTION_DELTAS = {
    'up': (0, -1),
    'down': (0, 1),
    'left': (-1, 0),
    'right': (1, 0),
}
MAX_SNAKE_PATH_LENGTH = 2000


class InvalidGameState(ValueError):
    pass


def _is_plain_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def parse_2048_board(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise InvalidGameState('invalid board') from exc
    if (
        not isinstance(value, list)
        or len(value) != BOARD_SIZE_2048
        or any(not isinstance(row, list) or len(row) != BOARD_SIZE_2048 for row in value)
    ):
        raise InvalidGameState('invalid board')

    board = []
    for row in value:
        parsed_row = []
        for cell in row:
            if not _is_plain_int(cell) or cell < 0:
                raise InvalidGameState('invalid tile')
            if cell and (cell & (cell - 1) or cell > 131072):
                raise InvalidGameState('invalid tile')
            parsed_row.append(cell)
        board.append(parsed_row)
    return board


def _merge_2048_line(line):
    values = [value for value in line if value]
    merged = []
    gained = 0
    index = 0
    while index < len(values):
        if index + 1 < len(values) and values[index] == values[index + 1]:
            value = values[index] * 2
            merged.append(value)
            gained += value
            index += 2
        else:
            merged.append(values[index])
            index += 1
    return merged + [0] * (BOARD_SIZE_2048 - len(merged)), gained


def _transpose(board):
    return [list(column) for column in zip(*board)]


def _reverse_rows(board):
    return [list(reversed(row)) for row in board]


def move_2048(board, direction):
    board = parse_2048_board(board)
    if direction not in VALID_2048_DIRECTIONS:
        raise InvalidGameState('invalid direction')

    working = [row[:] for row in board]
    if direction in {'up', 'down'}:
        working = _transpose(working)
    if direction in {'right', 'down'}:
        working = _reverse_rows(working)

    moved_board = []
    gained = 0
    for row in working:
        merged, row_score = _merge_2048_line(row)
        moved_board.append(merged)
        gained += row_score

    if direction in {'right', 'down'}:
        moved_board = _reverse_rows(moved_board)
    if direction in {'up', 'down'}:
        moved_board = _transpose(moved_board)
    return moved_board, gained, moved_board != board


def add_2048_tile(board, randbelow=secrets.randbelow):
    board = parse_2048_board(board)
    empty = [
        (row, column)
        for row in range(BOARD_SIZE_2048)
        for column in range(BOARD_SIZE_2048)
        if board[row][column] == 0
    ]
    if not empty:
        return board
    row, column = empty[randbelow(len(empty))]
    board[row][column] = 4 if randbelow(10) == 0 else 2
    return board


def new_2048_board(randbelow=secrets.randbelow):
    board = [[0] * BOARD_SIZE_2048 for _ in range(BOARD_SIZE_2048)]
    board = add_2048_tile(board, randbelow)
    board = add_2048_tile(board, randbelow)
    return board


def can_move_2048(board):
    board = parse_2048_board(board)
    if any(0 in row for row in board):
        return True
    return any(move_2048(board, direction)[2] for direction in VALID_2048_DIRECTIONS)


def parse_snake(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise InvalidGameState('invalid snake') from exc
    if not isinstance(value, list) or not value or len(value) > SNAKE_GRID_SIZE ** 2:
        raise InvalidGameState('invalid snake')

    snake = []
    occupied = set()
    for segment in value:
        if not isinstance(segment, dict):
            raise InvalidGameState('invalid snake segment')
        x = segment.get('x')
        y = segment.get('y')
        if (
            not _is_plain_int(x)
            or not _is_plain_int(y)
            or not 0 <= x < SNAKE_GRID_SIZE
            or not 0 <= y < SNAKE_GRID_SIZE
            or (x, y) in occupied
        ):
            raise InvalidGameState('invalid snake segment')
        occupied.add((x, y))
        snake.append({'x': x, 'y': y})

    for previous, current in zip(snake, snake[1:]):
        distance = abs(previous['x'] - current['x']) + abs(previous['y'] - current['y'])
        if distance != 1:
            raise InvalidGameState('disconnected snake')
    return snake


def parse_snake_position(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise InvalidGameState('invalid position') from exc
    if not isinstance(value, dict):
        raise InvalidGameState('invalid position')
    x = value.get('x')
    y = value.get('y')
    if (
        not _is_plain_int(x)
        or not _is_plain_int(y)
        or not 0 <= x < SNAKE_GRID_SIZE
        or not 0 <= y < SNAKE_GRID_SIZE
    ):
        raise InvalidGameState('invalid position')
    return {'x': x, 'y': y}


def choose_snake_food(snake, randbelow=secrets.randbelow):
    snake = parse_snake(snake)
    occupied = {(segment['x'], segment['y']) for segment in snake}
    empty = [
        {'x': x, 'y': y}
        for x in range(SNAKE_GRID_SIZE)
        for y in range(SNAKE_GRID_SIZE)
        if (x, y) not in occupied
    ]
    return empty[randbelow(len(empty))] if empty else None


def advance_snake(previous_snake, target_food, directions, previous_score, elapsed_seconds):
    snake = parse_snake(previous_snake)
    target_food = parse_snake_position(target_food)
    if (
        not isinstance(directions, list)
        or not directions
        or len(directions) > MAX_SNAKE_PATH_LENGTH
        or any(direction not in SNAKE_DIRECTION_DELTAS for direction in directions)
    ):
        raise InvalidGameState('invalid path')
    if len(snake) != previous_score + SNAKE_INITIAL_LENGTH:
        raise InvalidGameState('invalid previous length')

    tick_seconds = max(0.05, 0.15 - previous_score * 0.002)
    if elapsed_seconds < max(0.04, len(directions) * tick_seconds * 0.65):
        raise InvalidGameState('movement is too fast')

    for index, direction in enumerate(directions):
        delta_x, delta_y = SNAKE_DIRECTION_DELTAS[direction]
        head = {
            'x': snake[0]['x'] + delta_x,
            'y': snake[0]['y'] + delta_y,
        }
        if not 0 <= head['x'] < SNAKE_GRID_SIZE or not 0 <= head['y'] < SNAKE_GRID_SIZE:
            raise InvalidGameState('snake left the board')
        if head in snake:
            raise InvalidGameState('snake collision')

        reached_food = head == target_food
        is_last_step = index == len(directions) - 1
        if reached_food != is_last_step:
            raise InvalidGameState('food must be reached on the last step')

        snake.insert(0, head)
        if not reached_food:
            snake.pop()

    return parse_snake(snake)
