# ККЭПик: публичный MCP расписаний

Endpoint после настройки reverse proxy: **https://kkepik.rub1kub.ru/mcp**.
Это дополнительный интерфейс для подключаемых ИИ-агентов. Он не включает
автоматическое обнаружение MCP каждым чатом ChatGPT: публичные страницы и
поисковая индексация работают независимо от него.

Сервис только читает публичный API `http://127.0.0.1:8000/knowledge/*`.
Не импортирует код бота, не читает его БД/сессии/токены, не вызывает ИИ-модели,
не принимает пользовательские URL, не имеет операций записи. Авторизация не
нужна: информация совпадает с публичным расписанием на сайте. Личное ДЗ,
профили, участники групп и переписки не публикуются.

## Инструменты

| Инструмент | Назначение |
| --- | --- |
| `get_college_catalog` | Колледж, группы, преподаватели, аудитории, даты архива |
| `resolve_entity` | Точное название; явная неоднозначность совпадений |
| `get_schedule` | День (`YYYY-MM-DD`, `today`, `tomorrow`, `yesterday`) либо до 31 дня |
| `get_schedule_changes` | Сохранившиеся редакции одного дня и различия |
| `get_archive_coverage` | Доступные/отсутствующие дни; диапазон до 366 дней |
| `get_next_lesson` | Первая опубликованная пара начиная с даты, окно до 31 дня |

Параметры `entity_type`: `group`, `teacher`, `audience`. Названия — до 100
символов, фильтр предмета — до 150. Входные JSON Schema имеют
`additionalProperties: false`, те же ограничения применяются сервером.
У всех инструментов `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`, `openWorldHint=false`.

`today` и другие относительные дни вычисляются в **Europe/Moscow**, даже если
сервер находится в UTC. `get_next_lesson` ищет по календарным дням, не по
текущему времени: первая сегодняшняя пара могла уже пройти. Если перед
результатом отсутствуют источники или используется предыдущая корректная
редакция, результат отмечается `tentative=true`.

`missing`, `invalid_source` и `entity_not_found` не означают выходной. Прошлое
расписание сообщает запланированные занятия, не подтверждённое проведение.
`history_complete=false` предупреждает о неполной истории. Ответы сохраняют
ссылки `source.url` на исходники для цитирования.

## Установка и запуск

Требуется Python 3.12. Используется **официальный MCP Python SDK 2.3.0**.
В SDK 2 прежний high-level `FastMCP` называется `MCPServer`; это не сторонняя
библиотека `fastmcp`. SDK и его зависимости изолированы от окружения бота.

```sh
python3 -m venv agent_mcp/.venv
agent_mcp/.venv/bin/pip install -r agent_mcp/requirements.txt
agent_mcp/.venv/bin/python -m agent_mcp.server
```

Слушает только `127.0.0.1:18792`; MCP — `/mcp`, liveness — `/healthz`.
Шаблон systemd: `deployment/kkepik-mcp.service`. Он не устанавливается самим
пакетом. Для production пакет вместе с отдельным окружением устанавливается в
`/opt/kkepik-mcp/agent_mcp`; рабочая директория — `/opt/kkepik-mcp`. Служба
запускается от отдельного системного пользователя и группы `kkepik-mcp`, без
доступа к `/root`, `.env` бота и его сессиям (`ProtectHome=true`). В unit вызывается
интерпретатор `python -m uvicorn`, а не console-script с абсолютным shebang:
это позволяет переносить созданное окружение в production-каталог.

Для тестов:

```sh
agent_mcp/.venv/bin/pip install -r agent_mcp/requirements-dev.txt
agent_mcp/.venv/bin/python -m pytest -q agent_mcp/tests
```

Тесты используют синтетический upstream и реальное TCP-соединение официального
SDK-клиента: legacy initialize/list/call и современный discover. Не обращаются
к Telegram/MAX и не читают учебные или личные БД.

## Reverse proxy и ограничения

- Сохранять Host `kkepik.rub1kub.ru`, проксировать `/mcp` на одноимённый endpoint
  без redirect HTTP/HTTPS и без добавления завершающего слеша. При необходимости
  `/mcp/` снаружи также направлять непосредственно на внутренний `/mcp`.
- Apache `ProxyAddHeaders On` должен добавлять реальный адрес клиента в правый
  конец `X-Forwarded-For`. uvicorn доверяет forwarded headers **только
  127.0.0.1**; не задавать `forwarded_allow_ips=*`.
- SDK проверяет Host и Origin: разрешены публичный домен и localhost;
  публичные Origin только `https://kkepik.rub1kub.ru`. Серверные MCP-клиенты
  обычно не отправляют Origin. CORS для чужих сайтов не включён.
- Входной MCP body: 64 KiB; upstream response: 2 MiB; timeout: 8 секунд
  (подключение: 2 секунды); максимум 8 параллельных upstream запросов.
- 120 HTTP-запросов/минуту на IP и 600/минуту суммарно. 429 содержит
  `Retry-After: 60`. Память счётчиков ограничена 1024 IP, health exempt.
- Один uvicorn worker с максимум 40 одновременными соединениями. Streamable
  HTTP stateless + JSON не требует sticky sessions и не сохраняет пользовательские
  сессии. Совместим с клиентами legacy-протокола 2025-11-25 и SDK 2.x.
- Production unit ограничивает память: `MemoryHigh=128M`, `MemoryMax=192M`,
  swap до 64 MiB и максимум 64 задачи — отдельный MCP не должен вытеснять бота.
- Redirect, ошибочные upstream body, stack traces и приватные конфигурации не
  передаются клиенту. API ключ OpenAI не требуется.

Проверенное правило Apache (до `WSGIScriptAlias`):

```apache
ProxyPassMatch "^(/mcp)/?$" "http://127.0.0.1:18792$1" connectiontimeout=3 timeout=30
ProxyPassReverse "/mcp" "http://127.0.0.1:18792/mcp"
<LocationMatch "^/mcp/?$">
    ProxyPreserveHost On
    LimitRequestBody 65536
    RequestHeader unset X-Forwarded-For
</LocationMatch>
```

Ссылка `$1` обязательна: правило без backreference может дописать исходный путь
к destination и отправить `/mcp/mcp` вместо `/mcp`.

Read-only проверка после публикации: `agent_mcp/.venv/bin/python
scripts/qa_knowledge_live.py`. Проверяет API v2, старый API, исходник по SHA,
неоднозначные названия, защищённый `/api/me` и все шесть MCP-инструментов через
внешний HTTPS (оба варианта `/mcp` и `/mcp/`). Browser smoke:
`node scripts/qa_public_knowledge.cjs` — SSR без JavaScript, мобильная/десктопная
ширина и optional WebMCP. На малом сервере браузер запускать отдельно, с лимитом
памяти, не одновременно с импортом всего архива.

## Подключение агента

Для клиента, поддерживающего remote Streamable HTTP MCP: добавить URL
`https://kkepik.rub1kub.ru/mcp`, authentication **None**. Затем агент может
вызывать `resolve_entity` и `get_schedule`; публичную информацию можно читать
также обычным поиском по сайту без подключения MCP.

Описание ККЭПика не следует выдавать за официальную интеграцию OpenAI или
подтверждение колледжа.

## Первичные источники SDK

- [SDK: инструменты](https://py.sdk.modelcontextprotocol.io/servers/tools/)
- [SDK: deployment, Host/Origin, reverse proxy](https://py.sdk.modelcontextprotocol.io/run/deploy/)
- [SDK: тестирование официальным клиентом](https://py.sdk.modelcontextprotocol.io/get-started/testing/)
- [Миграция SDK 1 → 2](https://py.sdk.modelcontextprotocol.io/migration/)
