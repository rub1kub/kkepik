from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
import uvicorn
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from agent_mcp.server import (
    MAX_BODY_BYTES, MAX_RESPONSE_BYTES, KnowledgeClient, RequestLimits,
    _date, _range, create_server,
)

COLLEGE = {"id": "kkep", "name": "ККЭП", "city": "Краснодар"}
SOURCE = {"id": "a" * 64, "revision": 1, "received_at": "2026-10-08T10:00:00+03:00",
    "published_at": None, "url": "https://kkepik.rub1kub.ru/api/v2/sources/" + "a" * 64}
LESSON = {"group": "103-Д9-3ИНС", "pair_number": 1, "subject": "Математика",
    "teacher": "Емельянова Г.Ф.", "audience": "48", "subgroup": None,
    "start_time": "08:45", "end_time": "10:05", "association_status": "source_columns"}


def payload(request):
    path, query = request.url.path.rsplit("/", 1)[-1], dict(request.url.params)
    assert request.method == "GET"
    assert str(request.url).startswith("http://127.0.0.1:8000/knowledge/")
    assert "authorization" not in request.headers and "cookie" not in request.headers
    if path == "catalog":
        return {"college": COLLEGE, "timezone": "Europe/Moscow", "groups": ["103-Д9-3ИНС"],
            "teachers": [LESSON["teacher"]], "audiences": ["48"], "dates": ["2026-10-09"],
            "group_dates": {"103-Д9-3ИНС": ["2026-10-09"]},
            "updated_at": "2026-10-08T10:00:00+03:00"}
    if path == "resolve":
        matches = ["103-Д9-3ИНС", "103-Д9-2ИНС"] if query["query"] == "103" else [query["query"]]
        return {"entity_type": query["entity_type"], "query": query["query"],
            "matches": matches, "ambiguous": len(matches) > 1}
    if path == "schedule":
        start = query.get("date", query.get("start"))
        end = query.get("date", query.get("end"))
        day = {"date": start, "status": "available", "lessons": [LESSON], "source": SOURCE}
        if start == "2026-10-08" and start != end:
            days = [{"date": start, "status": "missing", "lessons": [], "source": None},
                {**day, "date": "2026-10-09"}]
        elif start == "2026-10-01":
            days = [{"date": start, "status": "missing", "lessons": [], "source": None}]
        else:
            days = [day]
        return {"college": COLLEGE, "timezone": "Europe/Moscow", "entity_type": query["entity_type"],
            "entity": query["entity"], "range": {"start": start, "end": end}, "days": days,
            "coverage": {"complete": all(d["status"] == "available" for d in days)}}
    if path == "coverage":
        return {"college": COLLEGE, "timezone": "Europe/Moscow", "range": query,
            "dates": ["2026-10-09"], "days": [{"date": "2026-10-09", "status": "available"}],
            "available_dates": ["2026-10-09"], "missing_dates": [], "summary": {"available": 1}}
    if path == "changes":
        return {"date": query["date"], "entity_type": query["entity_type"], "entity": query["entity"],
            "revisions": [{"revision": 1, "source": SOURCE, "lessons": [LESSON]}],
            "changes": [], "history_complete": False, "note": "Ранее история не сохранялась."}
    raise AssertionError(path)


@asynccontextmanager
async def running(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        yield url
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


@pytest_asyncio.fixture
async def endpoint():
    calls = []

    async def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=payload(request))

    knowledge = KnowledgeClient(transport=httpx.MockTransport(upstream))
    server, app = create_server(knowledge)
    async with running(app) as url:
        yield url, server, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_real_sdk_initialize_list_and_call(endpoint, mode):
    url, _, _ = endpoint
    async with Client(url + "/mcp", mode=mode) as client:
        assert client.server_info.name == "ККЭПик"
        tools = (await client.list_tools()).tools
        assert len(tools) == 6
        for tool in tools:
            assert tool.input_schema["additionalProperties"] is False
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert tool.annotations.idempotent_hint is True
            assert tool.annotations.open_world_hint is False
        result = await client.call_tool("get_schedule", {"entity_type": "group",
            "entity": "103-Д9-3ИНС", "date": "2026-10-09"})
        assert not result.is_error
        data = result.structured_content
        assert data["timezone"] == "Europe/Moscow"
        assert data["days"][0]["lessons"][0]["subject"] == "Математика"
        assert data["days"][0]["source"]["url"] == SOURCE["url"]


@pytest.mark.asyncio
async def test_catalog_does_not_fill_agent_context_with_sitemap_cross_reference(endpoint):
    url, _, _ = endpoint
    async with Client(url + "/mcp", mode="legacy") as client:
        result = await client.call_tool("get_college_catalog", {})
        assert not result.is_error
        assert "group_dates" not in result.structured_content
        assert result.structured_content["groups"] == ["103-Д9-3ИНС"]
        assert result.structured_content["dates"] == ["2026-10-09"]


@pytest.mark.asyncio
async def test_each_public_tool(endpoint):
    url, _, _ = endpoint
    async with Client(url + "/mcp", mode="legacy") as client:
        catalog = (await client.call_tool("get_college_catalog", {})).structured_content
        assert catalog["public_url"] == "https://kkepik.rub1kub.ru/colleges/kkep"
        resolved = (await client.call_tool("resolve_entity", {"entity_type": "group", "query": "103"})).structured_content
        assert resolved["ambiguous"] is True and len(resolved["matches"]) == 2
        changes = (await client.call_tool("get_schedule_changes", {"entity_type": "teacher",
            "entity": "Емельянова Г.Ф.", "date": "2026-10-09"})).structured_content
        assert changes["history_complete"] is False
        coverage = (await client.call_tool("get_archive_coverage", {})).structured_content
        assert coverage["available_dates"] == ["2026-10-09"]
        next_lesson = (await client.call_tool("get_next_lesson", {"entity_type": "group",
            "entity": "103-Д9-3ИНС", "from_date": "2026-10-08"})).structured_content
        assert next_lesson["date"] == "2026-10-09"
        assert next_lesson["tentative"] is True
        assert next_lesson["unavailable_before_result"] == ["2026-10-08"]
        missing = (await client.call_tool("get_schedule", {"entity_type": "group",
            "entity": "103-Д9-3ИНС", "date": "2026-10-01"})).structured_content
        assert missing["days"][0]["status"] == "missing" and missing["days"][0]["lessons"] == []
        no_match = (await client.call_tool("get_next_lesson", {"entity_type": "group",
            "entity": "103-Д9-3ИНС", "subject": "несуществующий предмет", "from_date": "2026-10-09"})).structured_content
        assert no_match["lesson"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["today", "tomorrow", "yesterday"])
@pytest.mark.parametrize("tool", ["get_schedule", "get_schedule_changes", "get_next_lesson"])
async def test_relative_date_aliases_through_real_sdk_schema(endpoint, tool, alias):
    url, _, calls = endpoint
    date_param = "from_date" if tool == "get_next_lesson" else "date"
    query_param = "start" if tool == "get_next_lesson" else "date"
    async with Client(url + "/mcp", mode="legacy") as client:
        result = await client.call_tool(tool, {"entity_type": "group", "entity": "103-Д9-3ИНС", date_param: alias})
        assert not result.is_error
    assert calls[-1].url.params[query_param] == _date(alias, relative=True)


@pytest.mark.asyncio
async def test_next_lesson_default_today(endpoint):
    url, _, calls = endpoint
    async with Client(url + "/mcp", mode="legacy") as client:
        result = await client.call_tool("get_next_lesson", {"entity_type": "group", "entity": "103-Д9-3ИНС"})
        assert not result.is_error
    assert calls[-1].url.params["start"] == _date("today", relative=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments", [
    ("get_college_catalog", {"url": "https://attacker.test"}),
    ("resolve_entity", {"entity_type": "student", "query": "103"}),
    ("resolve_entity", {"entity_type": "group", "query": " "}),
    ("resolve_entity", {"entity_type": "group", "query": "a" * 101}),
    ("resolve_entity", {"entity_type": "group", "query": "103\n"}),
    ("get_schedule", {"entity_type": "group", "entity": "103"}),
    ("get_schedule", {"entity_type": "group", "entity": "103", "date": "2026-02-30"}),
    ("get_schedule", {"entity_type": "group", "entity": "103", "date": "20261009"}),
    ("get_schedule", {"entity_type": "group", "entity": "103", "date": "2026-10-09", "start": "2026-10-01", "end": "2026-10-02"}),
    ("get_schedule", {"entity_type": "group", "entity": "103", "start": "2026-10-01"}),
    ("get_schedule", {"entity_type": "group", "entity": "103", "start": "2026-10-01", "end": "2026-11-01"}),
    ("get_schedule", {"entity_type": "group", "entity": "103", "start": "2026-10-10", "end": "2026-10-01"}),
    ("get_schedule", {"entity_type": "group", "entity": "103", "date": "2026-10-09", "user_id": 123}),
    ("get_archive_coverage", {"entity_type": "group"}),
    ("get_archive_coverage", {"start": "2026-01-01", "end": "2027-01-02"}),
    ("get_next_lesson", {"entity_type": "group", "entity": "103", "days": 0}),
    ("get_next_lesson", {"entity_type": "group", "entity": "103", "days": 32}),
    ("get_next_lesson", {"entity_type": "group", "entity": "103", "days": "14"}),
    ("get_next_lesson", {"entity_type": "group", "entity": "103", "from_date": "9999-12-31", "days": 2}),
])
async def test_bad_arguments_rejected_before_upstream(endpoint, name, arguments):
    url, _, calls = endpoint
    async with Client(url + "/mcp", mode="legacy") as client:
        result = await client.call_tool(name, arguments)
        assert result.is_error
    assert calls == []


@pytest.mark.asyncio
async def test_input_url_never_becomes_upstream_url(endpoint):
    url, _, calls = endpoint
    async with Client(url + "/mcp", mode="legacy") as client:
        result = await client.call_tool("resolve_entity", {"entity_type": "group", "query": "http://169.254.169.254/latest/meta-data"})
        assert not result.is_error
    assert calls[0].url.host == "127.0.0.1"
    assert calls[0].url.path == "/knowledge/resolve"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 404, 422, 500, 503])
async def test_upstream_errors_do_not_leak(status):
    secret = "private_token=SECRET /root/private.db http://127.0.0.1:8000"
    knowledge = KnowledgeClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, text=secret,
        headers={"Location": "http://attacker.test"})))
    server, _ = create_server(knowledge)
    async with Client(server) as client:
        result = await client.call_tool("get_college_catalog", {})
        assert result.is_error
        assert "SECRET" not in str(result)
        assert "127.0.0.1:8000" not in str(result)
        assert "/root/" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["not-json", "[]", "\x00"])
async def test_invalid_upstream_json(response):
    knowledge = KnowledgeClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=response)))
    try:
        with pytest.raises(ToolError):
            await knowledge.get("catalog")
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_upstream_body_bound_and_path_allowlist():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    knowledge = KnowledgeClient(transport=httpx.MockTransport(upstream))
    try:
        with pytest.raises(ToolError, match="слишком большой"):
            await knowledge.get("catalog")
        with pytest.raises(ToolError, match="Неизвестный"):
            await knowledge.get("../../private")
        assert len(calls) == 1
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_http_security_health_and_malformed_requests(endpoint):
    url, _, _ = endpoint
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        health = await client.get(url + "/healthz")
        assert health.status_code == 200 and health.json()["read_only"] is True
        invalid_host = await client.post(url + "/mcp", headers={**headers, "Host": "evil.test"}, content="{}")
        assert invalid_host.status_code == 421
        invalid_origin = await client.post(url + "/mcp", headers={**headers, "Origin": "https://evil.test"}, content="{}")
        assert invalid_origin.status_code == 403
        malformed = await client.post(url + "/mcp", headers=headers, content="not json")
        assert malformed.status_code == 400
        oversized = await client.post(url + "/mcp", headers=headers, content="x" * (MAX_BODY_BYTES + 1))
        assert oversized.status_code == 413
        assert "Traceback" not in malformed.text
        assert "Traceback" not in oversized.text


@pytest.mark.asyncio
async def test_public_hostname_allowed(endpoint):
    url, _, _ = endpoint
    async with httpx.AsyncClient() as client:
        response = await client.post(url + "/mcp", headers={"Host": "kkepik.rub1kub.ru",
            "Origin": "https://kkepik.rub1kub.ru", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"}}})
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "ККЭПик"
        assert "mcp-session-id" not in response.headers


@pytest.mark.asyncio
async def test_rate_limit_and_bounded_memory():
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestLimits(downstream, per_peer=1, global_limit=10_000)
    scope = {"type": "http", "path": "/mcp", "client": ("127.0.0.1", 123), "headers": []}

    async def request(custom_scope):
        output = []

        async def send(message):
            output.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        await middleware(custom_scope, receive, send)
        return output

    assert (await request(scope))[0]["status"] == 200
    assert (await request(scope))[0]["status"] == 429
    assert (await request({**scope, "path": "/healthz"}))[0]["status"] == 200
    for i in range(1100):
        await request({**scope, "headers": [(b"x-forwarded-for", f"10.0.{i // 256}.{i % 256}".encode())]})
    assert len(middleware.windows) == 1024


def test_relative_dates_use_college_timezone():
    # UTC still Oct 8; Krasnodar has already entered Oct 9.
    now = datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc)
    assert _date("today", relative=True, now=now) == "2026-10-09"
    assert _date("tomorrow", relative=True, now=now) == "2026-10-10"
    assert _date("yesterday", relative=True, now=now) == "2026-10-08"


def test_range_boundaries():
    assert _range("2026-10-01", "2026-10-31", maximum=31)["end"] == "2026-10-31"
    with pytest.raises(ToolError):
        _range("2026-10-01", "2026-11-01", maximum=31)
