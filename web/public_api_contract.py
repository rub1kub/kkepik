"""OpenAPI contract for the public schedule endpoints, not internal game APIs."""

SCHEDULE_ENTITIES = {
    'group': ('группы', '103-Д9-3ИНС'),
    'teacher': ('преподавателя', 'Дмитриева А.С.'),
    'audience': ('аудитории', '22'),
}
DATE_SCHEMA = {
    'type': 'string', 'pattern': r'^[0-9]{2}\.[0-9]{2}\.[0-9]{4}$',
    'example': '12.09.2026',
    'description': 'Существующая календарная дата ДД.ММ.ГГГГ. Для однозначного результата указывайте дату.',
}


def _ref(name):
    return {'$ref': f'#/components/schemas/{name}'}


def _response(schema, description='Успешный ответ'):
    return {'description': description, 'content': {'application/json': {'schema': schema}}}


def build_openapi():
    errors = {str(status): _response(_ref('Error'), description) for status, description in {
        400: 'Ошибка параметров запроса',
        404: 'Данные не найдены', 405: 'Метод не поддерживается',
        422: 'Параметры отклонены сервисом расписания', 429: 'Превышен лимит запросов',
        500: 'Внутренняя ошибка', 502: 'Некорректный ответ сервиса расписания',
        503: 'Сервис расписания недоступен', 504: 'Таймаут сервиса расписания',
    }.items()}
    errors['429']['headers'] = {'Retry-After': {'schema': {'type': 'integer'}, 'description': 'Секунд до повтора'}}
    paths = {}
    for entity in SCHEDULE_ENTITIES:
        properties = {
            entity: {'type': 'string', 'minLength': 1, 'maxLength': 200,
                     'example': SCHEDULE_ENTITIES[entity][1]},
            'date': DATE_SCHEMA,
        }
        description = (
            'Возвращает текстовые строки, без Telegram HTML. Если задана дата, '
            'возвращается только этот день либо 404; перехода на другую дату нет. '
        )
        description += ('Без даты возвращается расписание на сегодня.' if entity == 'audience'
                        else 'Без даты возвращается последнее загруженное актуальное расписание.')
        paths[f'/api/schedule/{entity}'] = {'get': {
            'operationId': f'get_{entity}_schedule', 'tags': ['Расписание'],
            'summary': f'Расписание {SCHEDULE_ENTITIES[entity][0]}',
            'description': description,
            'parameters': [
                {'name': key, 'in': 'query', 'required': key != 'date', 'schema': value}
                for key, value in properties.items()
            ],
            'responses': {'200': _response(_ref('Schedule')), **errors},
        }}
    for kind, label in [('groups', 'Группы'), ('teachers', 'Преподаватели'), ('audiences', 'Аудитории')]:
        schema = {'type': 'object', 'required': [kind], 'properties': {kind: {'type': 'array', 'items': {'type': 'string'}}}}
        paths[f'/api/{kind}'] = {'get': {
            'operationId': f'list_{kind}', 'tags': ['Справочники'], 'summary': label,
            'description': 'Список текущего учебного года. Если данных ещё нет: пустой массив. Сбой сервиса не маскируется пустым массивом.',
            'responses': {'200': _response(schema), **errors},
        }}
    paths['/api/schedule/catalog'] = {'get': {
        'operationId': 'get_schedule_catalog', 'tags': ['Справочники'], 'summary': 'Все справочники одним запросом',
        'description': 'Группы, преподаватели и аудитории. Кэш на 60 секунд. При сбое любой части возвращается ошибка, а не неполный справочник.',
        'responses': {'200': _response(_ref('Catalog')), **errors},
    }}
    paths['/api/schedule/download/{schedule_type}/{date}'] = {'get': {
        'operationId': 'download_schedule', 'tags': ['Файлы'], 'summary': 'Скачать исходный PDF или Excel',
        'parameters': [
            {'name': 'schedule_type', 'in': 'path', 'required': True, 'schema': {'type': 'string', 'enum': ['groups', 'teachers']}},
            {'name': 'date', 'in': 'path', 'required': True, 'schema': DATE_SCHEMA},
        ],
        'responses': {'200': {'description': 'Исходный файл; PDF имеет приоритет', 'content': {
            mime: {'schema': {'type': 'string', 'format': 'binary'}} for mime in (
                'application/pdf', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        }}, **errors},
    }}
    return {
        'openapi': '3.0.3',
        'info': {
            'title': 'Публичный API расписания', 'version': '1.1.1',
            'description': 'Открытый API для сторонних приложений: расписания групп, преподавателей '
            'и аудиторий, справочники и исходные файлы. Все операции используют GET. '
            'Регистрация и ключи доступа не нужны. Параметры передаются в строке запроса. '
            'Расписание: массив текстовых строк, не объектов занятий. '
            'Лимит: 10 запросов/секунду на IP и маршрут. '
            'При 429 используйте Retry-After; не опрашивайте API в бесконечном цикле. '
            'Ошибки содержат code и request_id; X-Request-ID есть также в заголовке. '
        },
        'servers': [{'url': '/', 'description': 'Этот сервер'}],
        'tags': [{'name': name} for name in ('Расписание', 'Справочники', 'Файлы')],
        'paths': paths,
        'components': {
            'schemas': {
                'Error': {'type': 'object', 'required': ['error', 'detail', 'code', 'request_id'], 'properties': {
                    'error': {'type': 'string'}, 'detail': {'type': 'string', 'description': 'Дубликат error для совместимости'},
                    'code': {'type': 'string', 'example': 'not_found'},
                    'request_id': {'type': 'string', 'description': 'ID для обращения к администратору'},
                }},
                'Schedule': {'type': 'object', 'required': ['date', 'schedule'], 'properties': {
                    'date': DATE_SCHEMA, **{key: {'type': 'string'} for key in SCHEDULE_ENTITIES},
                    'schedule': {'type': 'array', 'items': {'type': 'string'},
                                 'example': ['▪️1 пара – Математика – Иванов И.И. – 22']},
                }},
                'Catalog': {'type': 'object', 'required': ['groups', 'teachers', 'audiences'], 'properties': {
                    kind: {'type': 'array', 'items': {'type': 'string'}} for kind in ('groups', 'teachers', 'audiences')
                }},
            },
        },
    }
