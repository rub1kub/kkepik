#!/usr/bin/env python3
"""Read-only production smoke test. Uses the isolated MCP client environment."""
import asyncio
import base64
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from mcp import Client

ORIGIN = "https://kkepik.rub1kub.ru"
GROUP = "103-Д9-3ИНС"
DAY = "2026-10-09"


def request(path, params=None, headers=None, data=None):
    url = ORIGIN + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "KKEPIK-read-only-QA/1.0", **(headers or {})})
    try:
        response = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), response.read()


def get_json(path, params=None):
    status, _, body = request(path, params)
    assert status == 200, (path, status)
    return json.loads(body)


async def main():
    catalog = get_json("/api/v2/catalog")
    assert catalog["timezone"] == "Europe/Moscow" and GROUP in catalog["groups"]
    schedule = get_json("/api/v2/schedule", {"entity_type": "group", "entity": GROUP, "date": DAY})
    day = schedule["days"][0]
    assert day["date"] == DAY and day["status"] == "available" and day["lessons"]
    source = day["source"]
    path = urllib.parse.urlsplit(source["url"]).path
    status, headers, data = request(path)
    assert status == 200 and hashlib.sha256(data).hexdigest() == source["id"]
    assert headers.get("X-Content-Type-Options") == "nosniff"
    resolved = get_json("/api/v2/resolve", {"entity_type": "group", "query": "103"})
    assert resolved["ambiguous"] and len(resolved["matches"]) > 1
    week = get_json("/api/v2/schedule", {"entity_type": "group", "entity": GROUP, "start": "2026-10-05", "end": "2026-10-11"})
    assert len(week["days"]) == 7 and any(value["status"] == "missing" for value in week["days"])
    missing = get_json("/api/v2/schedule", {"entity_type": "group", "entity": GROUP, "date": "2026-10-11"})
    assert missing["days"][0]["status"] == "missing"
    changes = get_json("/api/v2/changes", {"entity_type": "group", "entity": GROUP, "date": DAY})
    assert changes["history_complete"] is False
    for alias in ("today", "tomorrow", "yesterday"):
        relative = get_json("/api/v2/schedule", {"entity_type": "group", "entity": GROUP, "date": alias})
        assert relative["timezone"] == "Europe/Moscow"
    group_path = "/colleges/kkep/groups/" + urllib.parse.quote(GROUP, safe="")
    for suffix in ("", "/" + DAY, "/archive", "/week/2026-10-05"):
        status, _, body = request(group_path + suffix)
        assert status == 200 and GROUP in body.decode()
    status, headers, body = request(group_path + "/2026-10-11")
    assert status == 200 and "noindex" in headers.get("X-Robots-Tag", "")
    for room in [value for value in catalog["audiences"] if "/" in value]:
        segment = "~" + base64.urlsafe_b64encode(room.encode()).decode().rstrip("=")
        for suffix in ("/" + DAY, "/archive"):
            assert request("/colleges/kkep/audiences/" + segment + suffix)[0] == 200
    assert request(group_path + "/0001-01-01")[0] == 400
    assert request(group_path + "/week/9999-12-31")[0] == 400
    for path in ("/", "/settings", "/agents", "/llms.txt", "/robots.txt", "/api/v2/openapi.json"):
        assert request(path)[0] == 200, path
    assert request("/api/me")[0] == 401
    sitemap_status, _, sitemap = request("/sitemap.xml")
    assert sitemap_status == 200
    entries = ET.fromstring(sitemap).findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url")
    assert len(entries) > len(catalog["groups"])
    assert b"%2F" not in sitemap
    legacy = get_json("/api/schedule/group", {"group": GROUP, "date": "09.10.2026"})
    assert legacy["group"] == GROUP and legacy["schedule"]
    calls = {
        "get_college_catalog": {},
        "resolve_entity": {"entity_type": "group", "query": "103"},
        "get_schedule": {"entity_type": "group", "entity": GROUP, "date": "tomorrow"},
        "get_schedule_changes": {"entity_type": "group", "entity": GROUP, "date": DAY},
        "get_archive_coverage": {"entity_type": "group", "entity": GROUP, "start": "2026-10-05", "end": "2026-10-11"},
        "get_next_lesson": {"entity_type": "group", "entity": GROUP, "from_date": DAY, "days": 1},
    }
    for endpoint, mode in (("/mcp", "legacy"), ("/mcp/", "legacy"), ("/mcp", "auto")):
        async with Client(ORIGIN + endpoint, mode=mode) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == set(calls)
            for tool in tools:
                assert tool.annotations.read_only_hint
                assert tool.input_schema["additionalProperties"] is False
            for name, arguments in calls.items():
                result = await client.call_tool(name, arguments)
                assert not result.is_error and result.structured_content, name
                if name == "get_college_catalog":
                    assert "group_dates" not in result.structured_content
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "read-only-QA", "version": "1"}}}).encode()
    mcp_headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    assert request("/mcp", headers={**mcp_headers, "Origin": "https://evil.invalid"}, data=payload)[0] in (400, 403)
    assert request("/mcp", headers=mcp_headers, data=b"x" * 65537)[0] == 413
    print(json.dumps({"status": "passed", "dates": len(catalog["dates"]), "groups": len(catalog["groups"]), "teachers": len(catalog["teachers"]), "audiences": len(catalog["audiences"]), "sitemap_entries": len(entries), "source_sha_verified": True, "private_routes_require_auth": True, "legacy_api": "passed", "mcp_tools": list(calls), "mcp_endpoints": ["/mcp", "/mcp/"], "mcp_client_modes": ["legacy", "auto"], "homework": "excluded"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
