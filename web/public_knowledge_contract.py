"""OpenAPI contract for the structured, public knowledge API (v2)."""


def build_openapi():
    ref = lambda name: {'$ref': f'#/components/schemas/{name}'}
    entity_type = {'type': 'string', 'enum': ['group', 'teacher', 'audience']}
    entity = {'type': 'string', 'minLength': 1, 'maxLength': 200, 'example': '103-Д9-3ИНС'}
    iso_date = {'type': 'string', 'format': 'date', 'example': '2026-10-09'}
    date_query = {'type': 'string', 'description': 'ГГГГ-ММ-ДД либо today, tomorrow, yesterday. Europe/Moscow.'}
    parameter = lambda name, schema, required=False: {'name': name, 'in': 'query', 'required': required, 'schema': schema}
    common = [parameter('entity_type', entity_type, True), parameter('entity', entity, True)]
    error = {'description': 'Безопасная ошибка без внутренних путей и персональных данных',
             'content': {'application/json': {'schema': ref('Error')}}}

    def operation(name, summary, schema, params=(), description=''):
        return {'get': {'operationId': name, 'summary': summary, 'description': description,
                        'parameters': list(params), 'security': [],
                        'responses': {'200': {'description': 'Успешный ответ', 'content': {
                            'application/json': {'schema': schema}}},
                            **{str(code): error for code in (400, 404, 429, 502, 503, 504)}}}}

    source = {'type': ['object', 'null'], 'properties': {
        'id': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}, 'revision': {'type': 'integer', 'minimum': 1},
        'received_at': {'type': ['string', 'null'], 'format': 'date-time'},
        'published_at': {'type': ['string', 'null'], 'format': 'date-time'},
        'parsed_at': {'type': ['string', 'null'], 'format': 'date-time'}, 'academic_year': {'type': ['string', 'null']},
        'archived_import': {'type': 'boolean'},
        'received_at_semantics': {'type': 'string', 'enum': ['local_file_mtime', 'ingested_at']},
        'revision_order': {'type': 'string', 'enum': ['archive_import_order', 'observation_order']},
        'url': {'type': 'string', 'format': 'uri'}}}
    lesson = {'type': 'object', 'required': ['pair_number', 'subject', 'subgroup'], 'properties': {
        'pair_number': {'type': 'integer'}, 'subject': {'type': 'string'}, 'group': {'type': ['string', 'null']},
        'teacher': {'type': ['string', 'null']}, 'audience': {'type': ['string', 'null']},
        'subgroup': {'type': ['integer', 'string', 'null']}, 'start_time': {'type': ['string', 'null']},
        'end_time': {'type': ['string', 'null']}, 'association_status': {'type': ['string', 'null']},
        'audiences': {'type': 'array', 'items': {'type': 'string'}}, 'lesson_status': {'const': 'scheduled'}}}
    day = {'type': 'object', 'required': ['date', 'status'], 'properties': {
        'date': iso_date, 'status': {'type': 'string', 'enum': ['available', 'no_lessons', 'missing', 'invalid_source', 'entity_not_found']},
        'lessons': {'type': 'array', 'items': ref('Lesson')}, 'source': ref('Source'),
        'warning': {'type': 'string'}, 'note': {'type': 'string'}}}
    catalog = {'type': 'object', 'required': ['college', 'timezone', 'groups', 'teachers', 'audiences', 'dates'],
               'properties': {'college': ref('College'), 'timezone': {'const': 'Europe/Moscow'},
                              'updated_at': {'type': ['string', 'null'], 'format': 'date-time'},
                              **{kind: {'type': 'array', 'items': {'type': 'string'}} for kind in ('groups', 'teachers', 'audiences')},
                              'dates': {'type': 'array', 'items': iso_date},
                              'group_dates': {'type': 'object', 'description': 'Только даты, где группа присутствует в валидном источнике.',
                                              'additionalProperties': {'type': 'array', 'items': iso_date}}}}
    schedule = {'type': 'object', 'required': ['college', 'timezone', 'entity_type', 'entity', 'range', 'days', 'coverage'],
                'properties': {'college': ref('College'), 'timezone': {'const': 'Europe/Moscow'}, 'entity_type': entity_type,
                               'entity': entity, 'range': ref('Range'), 'days': {'type': 'array', 'items': ref('Day')},
                               'coverage': ref('Summary'), 'lesson_status': {'const': 'scheduled'}, 'note': {'type': 'string'}}}
    coverage = {'type': 'object', 'properties': {'college': ref('College'), 'timezone': {'const': 'Europe/Moscow'},
                'range': ref('Range'), 'days': {'type': 'array', 'items': ref('Day')},
                **{kind: {'type': 'array', 'items': iso_date} for kind in ('dates', 'available_dates', 'missing_dates')},
                'summary': ref('Summary'), 'range_is_calendar': {'type': 'boolean'},
                'history_complete': {'type': 'boolean'}, 'note': {'type': 'string'}}}
    changes = {'type': 'object', 'properties': {'date': iso_date, 'entity_type': entity_type, 'entity': entity,
        'history_complete': {'type': 'boolean', 'description': 'Ранние редакции могут быть утрачены; false не означает, что изменений не было.'},
        'note': {'type': 'string'}, 'order_basis': {'const': 'observation_order_not_publication'},
        'college': ref('College'), 'timezone': {'const': 'Europe/Moscow'},
        'revisions': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'revision': {'type': 'integer'}, 'status': {'type': 'string', 'enum': ['parsed', 'failed']},
            'source': ref('Source'), 'lessons': {'type': 'array', 'items': ref('Lesson')}}}},
        'changes': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'from_revision': {'type': 'integer'}, 'to_revision': {'type': 'integer'},
            'added': {'type': 'array', 'items': ref('Lesson')}, 'removed': {'type': 'array', 'items': ref('Lesson')}}}}}}
    paths = {
        '/api/v2/catalog': operation('get_college_catalog', 'Колледж, справочники и опубликованные даты', ref('Catalog')),
        '/api/v2/schedule': operation('get_structured_schedule', 'Структурированное расписание на день или диапазон', ref('Schedule'),
            common + [parameter('date', date_query), parameter('start', iso_date), parameter('end', iso_date)],
            'Без даты: сегодня. date и start/end взаимоисключающие. Диапазон: максимум 31 день, включая границы. '
            'Каждый запрошенный день имеет статус. missing — отсутствие данных, не отсутствие пар. '
            'Это опубликованное расписание, а не подтверждение состоявшихся занятий.'),
        '/api/v2/resolve': operation('resolve_college_entity', 'Поиск точного названия без угадывания', ref('Resolution'),
            [parameter('entity_type', entity_type, True), parameter('query', entity, True)]),
        '/api/v2/coverage': operation('get_archive_coverage', 'Покрытие архива и пропущенные даты', ref('Coverage'),
            [parameter('entity_type', entity_type), parameter('entity', entity), parameter('start', iso_date), parameter('end', iso_date)],
            'entity_type/entity и start/end передаются попарно. Максимум 366 дней. Без диапазона: известные архивные даты.'),
        '/api/v2/changes': operation('get_schedule_changes', 'Сохранённые редакции и изменения', ref('Changes'),
            common + [parameter('date', date_query, True)], 'Сохранённая история может быть неполной; смотрите history_complete и note.'),
        '/api/v2/sources/{source_id}': {'get': {'operationId': 'get_schedule_source', 'summary': 'Исходный PDF или Excel по SHA-256',
            'security': [], 'parameters': [{'name': 'source_id', 'in': 'path', 'required': True,
                                           'schema': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}],
            'responses': {'200': {'description': 'Исходный публичный документ', 'content': {
                mime: {'schema': {'type': 'string', 'format': 'binary'}} for mime in (
                    'application/pdf', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/vnd.ms-excel')}},
                **{str(code): error for code in (404, 429, 502, 503, 504)}}}},
    }
    return {'openapi': '3.1.0', 'info': {'title': 'ККЭПик — публичные учебные данные', 'version': '2.0.0',
        'description': 'Открытый read-only API без ключей. Расписания ККЭП (Краснодар), архив, источники и сохранённые изменения. '
                       'Часовой пояс Europe/Moscow. Не предоставляет домашку, личные данные, оценки или посещаемость. '
                       'Лимит: 10 запросов/секунду на IP и маршрут, исходники — 20 запросов/минуту. При 429 соблюдайте Retry-After.'},
        'servers': [{'url': 'https://kkepik.rub1kub.ru'}], 'paths': paths, 'components': {'schemas': {
            'College': {'type': 'object', 'required': ['id', 'name', 'city', 'aliases'], 'properties': {
                'id': {'const': 'kkep'}, 'name': {'type': 'string'}, 'city': {'type': 'string'},
                'aliases': {'type': 'array', 'items': {'type': 'string'}}}},
            'Catalog': catalog, 'Lesson': lesson, 'Day': day, 'Source': source, 'Schedule': schedule, 'Coverage': coverage,
            'Changes': changes, 'Range': {'type': 'object', 'properties': {'start': iso_date, 'end': iso_date}},
            'Resolution': {'type': 'object', 'properties': {'entity_type': entity_type, 'query': entity,
                'matches': {'type': 'array', 'items': {'type': 'string'}}, 'ambiguous': {'type': 'boolean'}, 'truncated': {'type': 'boolean'}}},
            'Summary': {'type': 'object', 'properties': {'complete': {'type': 'boolean'},
                        **{key: {'type': 'integer', 'minimum': 0} for key in ('total_days', 'available', 'no_lessons', 'missing', 'invalid_source', 'entity_not_found')}}},
            'Error': {'type': 'object', 'required': ['error', 'code'], 'properties': {'error': {'type': 'string'}, 'code': {'type': 'string'}}},
        }}}
