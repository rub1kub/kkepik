# Public Schedule API

Base URL: `https://kkepik.rub1kub.ru`

- Interactive documentation: `/api/docs/` (local Swagger UI assets).
- OpenAPI 3.0.3: `/api/openapi.json`.
- Legacy schema URL: `/static/swagger.json` (generated copy of the same contract).
- All public operations use GET. No registration, API key or authentication headers are required.

## Read Schedule Data

```sh
curl --get 'https://kkepik.rub1kub.ru/api/schedule/group' \
  --data-urlencode 'group=103-Д9-3ИНС' \
  --data-urlencode 'date=12.09.2026'

curl 'https://kkepik.rub1kub.ru/api/schedule/catalog'
```

Schedules contain `date` and `schedule` (an array of plain text strings), plus
`group`, `teacher`, or `audience`. They do not contain parsed lesson objects.
Use explicit dates (`DD.MM.YYYY`) for predictable results: an unavailable date
returns 404, never a different day's schedule. When omitted, defaults retain
the bot service's existing behavior (latest current schedule for groups and
teachers, today for audiences).

The public interface contains eight GET routes: three schedule searches
(`/api/schedule/group`, `/api/schedule/teacher`, `/api/schedule/audience`),
three entity lists (`/api/groups`, `/api/teachers`, `/api/audiences`),
the combined `/api/schedule/catalog`, and
`/api/schedule/download/{schedule_type}/{date}` for source files.
Query parameters belong in the URL; there is no JSON request body.

## Errors And Limits

Errors have `error` and `detail` strings (same message for compatibility),
`code` and `request_id`. The same ID appears in `X-Request-ID` and server logs.
Internal exception text, credentials and server file listings are not returned.

- 400: invalid parameters/date; 404: no matching data; 405: unsupported method.
- 429: rate limit; retry after `Retry-After` seconds.
- 502: malformed upstream response; 503: service unavailable; 504: upstream timeout.

Limit: 10 requests/second per source IP and route.
OPTIONS does not consume this limit. Catalogs may be cached for 60 seconds.
A genuinely empty catalog returns 200 with empty arrays. Service failures
return errors instead of a cacheable empty result.

The current Apache HTTPS deployment uses one WSGI daemon process, so the
configured in-memory limiter is local to that process and resets on restart.
Before scaling to multiple processes/instances, configure shared storage with
`SCHEDULE_RATE_LIMIT_STORAGE_URI` in the WSGI environment and install its client
dependency. This is a basic application limit, not network-level DDoS protection.
Forwarded IP headers are not trusted automatically.

Upstream connections have a 3.05-second connect timeout and a 30-second read
timeout. Downloads stream with the original PDF/XLSX MIME type and close the
upstream connection when complete or disconnected. No automatic request retry
or schedule broadcast occurs in this layer.

## Maintenance

`public_api_contract.py` is the source of truth for the OpenAPI document.
After changing it, regenerate the compatibility copy:

```sh
venv/bin/python -c "import json; from pathlib import Path; from public_api_contract import build_openapi; Path('static/swagger.json').write_text(json.dumps(build_openapi(), ensure_ascii=False, indent=2) + '\n')"
venv/bin/python -m unittest discover -s tests -q
```

Tests enforce the public GET-only contract, validation, rate limits, catalog
failures, download cleanup, and route/spec consistency.

## Internal Compatibility Boundary

The Mini App still uses protected personal endpoints and legacy POST handlers.
These remain implemented at their existing URLs for compatibility, not as
supported public operations. Their signature checks and response formats have
not been removed. Tests verify their compatibility and authentication separately
from the public contract. Do not use them for new third-party integrations.
Bot internals on loopback port 8000 must not be reverse-proxied directly.
