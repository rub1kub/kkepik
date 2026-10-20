#!/usr/bin/env python3
"""Bounded, anonymous HTTP QA; no bot imports, cookies, writes, or services.

Run: ./venv/bin/python scripts/qa_agent_web_access.py
The first request of every client/URL variant is to the deep URL, not a home
page. Raw Unicode is UTF-8 percent encoded once at the HTTP transport boundary.
Only ordinary requests are made; User-Agent variants never get fake content.
"""
import argparse
from collections import defaultdict, deque
from datetime import datetime, timedelta
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo


AGENTS = {
    "browser": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "ChatGPT-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot",
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.3; +https://openai.com/searchbot",
    "ClaudeBot": "ClaudeBot/1.0; +https://claude.ai/",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot",
    "Googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
}
QA_AGENT = "KKEPIK-anonymous-agent-access-QA/1.0"
MAX_BYTES = 26 * 1024 * 1024


def transport_url(url):
    """Preserve existing %XX sequences; encode only unencoded Unicode once."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ValueError("HTTP(S) origin required")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc,
        urllib.parse.quote(parts.path, safe="/%:@-._~!$&'()*+,;="),
        urllib.parse.quote(parts.query, safe="%/?@-._~!$&'()*+,;=:\""), ""))


class HTML(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.links, self.canonical, self.description = [], None, None
        self.anchors, self.current_anchor = [], None
        self.title, self.text, self._title = "", [], False
        self.lessons = 0
        self.feed(body.decode("utf-8"))

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
            self.current_anchor = {"href": attrs["href"], "text": ""}
            self.anchors.append(self.current_anchor)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href")
        if tag == "meta" and attrs.get("name") == "description":
            self.description = attrs.get("content")
        if tag == "title":
            self._title = True
        if tag == "li" and "lesson-card" in attrs.get("class", "").split():
            self.lessons += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        if tag == "a":
            self.current_anchor = None

    def handle_data(self, data):
        self.text.append(data)
        if self._title:
            self.title += data
        if self.current_anchor is not None:
            self.current_anchor["text"] += data


class Runner:
    def __init__(self, args):
        self.args = args
        self.origin = args.origin.rstrip("/")
        self.results, self.requests, self.windows = [], [], defaultdict(deque)
        self.html_fingerprints = {}
        self.clients = ["urllib"] + [name for name in ("requests", "httpx")
                                     if importlib.util.find_spec(name)]
        if len(self.clients) < 2:
            raise RuntimeError("Use a Python environment with requests or httpx installed; no packages are installed by this script")

    def url(self, path, params=None):
        value = urllib.parse.urljoin(self.origin + "/", path)
        if params:
            value += "?" + urllib.parse.urlencode(params)
        return value

    def check(self, name, function):
        try:
            details = function() or {}
            self.results.append({"name": name, "status": "passed", **details})
        except Exception as exc:
            # Never dump response bodies, client headers, or private exceptions.
            message = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
            self.results.append({"name": name, "status": "failed", "error": message[:250]})

    def fetch(self, url, method="GET", client="urllib", ua=QA_AGENT):
        url = transport_url(url)
        assert urllib.parse.urlsplit(url).netloc == urllib.parse.urlsplit(self.origin).netloc, "External links are not fetched"
        path = urllib.parse.urlsplit(url).path
        # A strict <= 55 requests/minute/path guard, even if the suite expands.
        window = self.windows[path]
        now = time.monotonic()
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= 55:
            raise AssertionError("Request budget exhausted for " + path)
        window.append(now)
        time.sleep(self.args.pace)
        headers = {"User-Agent": ua, "Accept": "*/*"}
        if client == "urllib":
            # A new opener has no cookies and no prior navigation.
            req = urllib.request.Request(url, headers=headers, method=method)
            try:
                response = urllib.request.build_opener().open(req, timeout=self.args.timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status, values, final = response.status, dict(response.headers), response.url
                data = response.read(MAX_BYTES + 1)
        elif client == "requests":
            import requests
            with requests.Session() as session:
                with session.request(method, url, headers=headers, timeout=self.args.timeout,
                                     allow_redirects=True, stream=True) as response:
                    status, values, final = response.status_code, dict(response.headers), response.url
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        data.extend(chunk)
                        assert len(data) <= MAX_BYTES, "Response too large"
                    data = bytes(data)
        else:
            import httpx
            with httpx.Client(timeout=self.args.timeout, follow_redirects=True) as session:
                with session.stream(method, url, headers=headers) as response:
                    status, values, final = response.status_code, dict(response.headers), str(response.url)
                    data = bytearray()
                    for chunk in response.iter_bytes(65536):
                        data.extend(chunk)
                        assert len(data) <= MAX_BYTES, "Response too large"
                    data = bytes(data)
        assert len(data) <= MAX_BYTES, "Response too large"
        values = {key.lower(): value for key, value in values.items()}
        assert "set-cookie" not in values, "Public access unexpectedly sets cookies"
        assert transport_url(final) == url, "Unexpected redirect for " + path
        if 200 <= status < 400 and path != "/":
            assert "retry-after" not in values, "Successful public response advertises Retry-After"
            if path.startswith("/api/v2/"):
                assert {value.strip() for value in values.get("access-control-allow-methods", "").split(",")} == {"GET", "HEAD", "OPTIONS"}, "API CORS advertises write methods"
            if path.startswith("/colleges/") or path == "/sitemap.xml" or (path.startswith("/api/v2/") and path != "/api/v2/openapi.json"):
                for header in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
                    assert header in values, "Rate-limit header lost: " + header
        ua_name = next((name for name, value in AGENTS.items() if value == ua), QA_AGENT)
        self.requests.append({"path": path, "method": method, "client": client,
                              "ua": ua_name,
                              "http_status": status, "content_type": values.get("content-type", ""),
                              "bytes": len(data)})
        return status, values, data

    def json(self, url, expected=200):
        status, headers, data = self.fetch(url)
        assert status == expected, f"Expected HTTP {expected}, received {status}"
        assert headers.get("content-type", "").startswith("application/json"), "API is not JSON"
        assert headers.get("x-content-type-options") == "nosniff", "Missing nosniff"
        if expected >= 400:
            assert "no-store" in headers.get("cache-control", ""), "Error was cacheable"
        return json.loads(data)

    def deep_response(self, path, method, client, ua):
        status, headers, body = self.fetch(self.url(path), method, client, ua)
        assert status == 200, f"Deep {method} returned {status}"
        assert headers.get("content-type", "").startswith("text/html"), "SSR response is not HTML"
        assert headers.get("x-content-type-options") == "nosniff", "Missing nosniff"
        if method == "HEAD":
            assert not body, "HEAD returned a body"
            return {"http_status": status}
        canonical_request = transport_url(self.url(path))
        fingerprint = hashlib.sha256(body).hexdigest()
        assert self.html_fingerprints.setdefault(canonical_request, fingerprint) == fingerprint, "HTML differs between User-Agents/clients"
        html = HTML(body)
        assert self.args.group in html.title and html.description, "Missing group title/description"
        assert html.canonical and transport_url(html.canonical) == transport_url(self.url(path)), "Invalid canonical URL"
        text = " ".join(html.text)
        assert "Europe/Moscow" in text, "College timezone absent"
        if path.endswith("/" + self.args.day):
            assert self.args.day in html.title and self.args.day in html.description, "Date metadata absent"
            assert html.lessons == 2, "Expected two server-rendered lessons on regression date"
            assert "Гринь" in text and "Копылова" in text, "Actual teachers absent from SSR"
            assert any("/api/v2/sources/" in href for href in html.links), "Source is not an HTML link"
        return {"http_status": status, "ssr_lessons": html.lessons, "identical_html_across_clients": True}

    def cold_api(self, method, client, unicode=False):
        if unicode:
            url = self.url("/api/v2/schedule") + "?entity_type=group&entity=" + self.args.group + "&date=" + self.args.day
        else:
            url = self.url("/api/v2/schedule", {"entity_type": "group", "entity": self.args.group, "date": self.args.day})
        status, headers, body = self.fetch(url, method=method, client=client)
        assert status == 200 and headers.get("content-type", "").startswith("application/json"), "Cold API request failed"
        if method == "HEAD":
            assert not body, "API HEAD returned a body"
        else:
            payload = json.loads(body)
            assert payload["entity"] == self.args.group and payload["days"][0]["date"] == self.args.day, "Cold API returned another entity/date"
            assert payload["days"][0]["status"] == "available" and len(payload["days"][0]["lessons"]) == 2, "Cold API schedule absent"
        return {"http_status": status}

    def run(self):
        raw = "/colleges/kkep/groups/" + self.args.group
        encoded = "/colleges/kkep/groups/" + urllib.parse.quote(self.args.group, safe="")
        # These are intentionally the first requests, ahead of /agents/home.
        for client in self.clients:
            for unicode in (True, False):
                for method in ("GET", "HEAD"):
                    self.check(f"cold_api_{client}_{'unicode' if unicode else 'percent_encoded'}_{method}",
                               lambda m=method, c=client, u=unicode: self.cold_api(m, c, u))
            for label, group_path in (("unicode", raw), ("percent_encoded", encoded)):
                for suffix in ("", "/" + self.args.day):
                    for method in ("GET", "HEAD"):
                        self.check(f"cold_{client}_{label}_{'day' if suffix else 'group'}_{method}",
                                   lambda p=group_path + suffix, m=method, c=client:
                                   self.deep_response(p, m, c, AGENTS["browser"]))
        for name, ua in AGENTS.items():
            for suffix in ("", "/" + self.args.day):
                self.check("user_agent_" + name + ("_day" if suffix else "_group"),
                           lambda p=encoded + suffix, u=ua: self.deep_response(p, "GET", "urllib", u))
        self.check("discovery_docs_catalog_resolve_schedule_source", self.discovery)
        self.check("robots_sitemap_llms", self.discovery_metadata)
        self.check("api_validation_relative_dates_and_coverage", self.api_contract)
        self.check("double_encoded_path_not_accepted", lambda: self.double_encoded(encoded))
        failed = [result for result in self.results if result["status"] == "failed"]
        print(json.dumps({"status": "failed" if failed else "passed", "origin": self.origin,
              "group": self.args.group, "day": self.args.day, "clients": self.clients,
              "missing_optional_clients": [name for name in ("requests", "httpx") if name not in self.clients],
              "checks": self.results, "requests": self.requests,
              "request_count": len(self.requests), "max_requests_per_path": max(map(len, self.windows.values())),
              "production_invalid_source_and_no_lessons": "fixture-tested; not manufactured in production"},
              ensure_ascii=False, indent=2))
        return bool(failed)

    def discovery(self):
        status, _, body = self.fetch(self.url("/"))
        assert status == 200, "Site root unavailable"
        root = HTML(body)
        assert root.canonical == self.url('/'), "Site root canonical missing or incorrect"
        docs_url = next((href for href in root.links if urllib.parse.urlsplit(href).path == "/agents"), None)
        assert docs_url, "Agent documentation undiscoverable from the site root"
        status, _, body = self.fetch(self.url(docs_url))
        assert status == 200, "Agent documentation unavailable"
        docs = HTML(body)
        self.docs_example_urls = {href for href in docs.links
                                  if urllib.parse.urlsplit(href).path.startswith('/api/v2/')
                                  and urllib.parse.urlsplit(href).query}
        group_params = {"entity_type": ["group"], "entity": [self.args.group]}
        exact_examples = [
            ("/api/v2/resolve", {"entity_type": ["group"], "query": ["103"]}, True),
            ("/api/v2/schedule", {**group_params, "date": ["tomorrow"]}, False),
            ("/api/v2/schedule", {**group_params, "date": [self.args.day]}, True),
            ("/api/v2/schedule", {**group_params, "start": ["2026-10-05"], "end": [self.args.day]}, False),
            ("/api/v2/schedule", {"entity_type": ["teacher"], "entity": ["Шульга Н.Н."], "date": [self.args.day]}, False),
            ("/api/v2/coverage", {**group_params, "start": ["2026-10-05"], "end": [self.args.day]}, True),
            ("/api/v2/changes", {**group_params, "date": [self.args.day]}, True),
        ]
        for path, params, copyable in exact_examples:
            anchors = [anchor for anchor in docs.anchors if urllib.parse.urlsplit(anchor["href"]).path == path
                       and urllib.parse.parse_qs(urllib.parse.urlsplit(anchor["href"]).query, keep_blank_values=True) == params]
            assert anchors, "Exact API example absent: " + path
            assert all(anchor["href"].startswith(self.origin + path + "?") for anchor in anchors), "API example is not self-contained absolute URL: " + path
            if copyable:
                assert any(anchor["href"] in anchor["text"] for anchor in anchors), "Full runnable URL is not visible: " + path
        paths = {urllib.parse.urlsplit(href).path: href for href in docs.links}
        for path in ("/api/v2/openapi.json", "/api/v2/catalog", "/colleges/kkep"):
            assert path in paths, "Documentation link absent: " + path
        specification = self.json(self.url(paths["/api/v2/openapi.json"]))
        catalog = self.json(self.url(paths["/api/v2/catalog"]))
        self.catalog = catalog
        assert catalog["timezone"] == "Europe/Moscow" and self.args.group in catalog["groups"], "Group not discoverable"
        assert catalog["teachers"] and catalog["audiences"] and self.args.day in catalog["dates"], "Catalog incomplete"
        assert specification["openapi"].startswith("3.1"), "Unexpected OpenAPI version"
        for path in ("/api/v2/resolve", "/api/v2/schedule", "/api/v2/coverage", "/api/v2/changes"):
            assert path in specification["paths"], "API absent from specification"
            assert path in paths and urllib.parse.urlsplit(paths[path]).query, "Clickable query example absent: " + path
            self.json(self.url(paths[path]))
        resolve_path = next(path for path, value in specification["paths"].items()
                            if value.get("get", {}).get("operationId") == "resolve_college_entity")
        schedule_path = next(path for path, value in specification["paths"].items()
                             if value.get("get", {}).get("operationId") == "get_structured_schedule")
        resolution = self.json(self.url(resolve_path, {"entity_type": "group", "query": self.args.group}))
        assert resolution["matches"] == [self.args.group] and not resolution["ambiguous"], "Exact group resolution failed"
        payload = self.json(self.url(schedule_path, {"entity_type": "group", "entity": resolution["matches"][0], "date": self.args.day}))
        day = payload["days"][0]
        assert day["date"] == self.args.day and day["status"] == "available", "Schedule missing"
        assert len(day["lessons"]) == 2 and [lesson["pair_number"] for lesson in day["lessons"]] == [1, 2], "Regression lesson count/numbers changed"
        assert "Гринь" in day["lessons"][0]["teacher"] and "Копылова" in day["lessons"][1]["teacher"], "Regression teachers changed"
        source = day["source"]
        assert source["revision"] >= 1 and "received_at" in source and "published_at" in source, "Revision metadata missing"
        status, headers, content = self.fetch(source["url"])
        assert status == 200 and hashlib.sha256(content).hexdigest() == source["id"], "Source SHA verification failed"
        assert headers.get("content-type", "").split(";", 1)[0] in ("application/pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel"), "Invalid source MIME"
        college_status, _, college_body = self.fetch(self.url(paths["/colleges/kkep"]))
        assert college_status == 200
        college = HTML(college_body)
        for kind in ("groups", "teachers", "audiences"):
            directory = next((href for href in college.links if urllib.parse.urlsplit(href).path.endswith("/" + kind)), None)
            assert directory, "Directory undiscoverable: " + kind
            status, _, body = self.fetch(self.url(directory))
            assert status == 200 and HTML(body).links, "Directory has no SSR links"
            if kind == "groups":
                group_url = next((href for href in HTML(body).links
                                  if urllib.parse.unquote(urllib.parse.urlsplit(href).path).endswith("/" + self.args.group)), None)
                assert group_url, "Group HTML link undiscoverable"
                status, _, body = self.fetch(self.url(group_url))
                day_url = next((href for href in HTML(body).links if href.endswith("/" + self.args.day)), None)
                assert status == 200 and day_url, "Day link undiscoverable"
                self.deep_response(urllib.parse.urlsplit(day_url).path, "GET", "urllib", QA_AGENT)
        return {"source_sha_verified": True, "lessons": len(day["lessons"]), "entity_urls_guessed": False}

    def double_encoded(self, encoded):
        status, _, _ = self.fetch(self.url(encoded.replace("%", "%25") + "/" + self.args.day))
        assert status == 404, "Double-encoded entity was silently decoded again or caused a server error"
        return {"http_status": status}

    def discovery_metadata(self):
        status, _, body = self.fetch(self.url("/robots.txt"))
        assert status == 200
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(body.decode("utf-8").splitlines())
        for agent in ("*", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "PerplexityBot", "Googlebot"):
            for path in ("/colleges/kkep/groups/" + urllib.parse.quote(self.args.group, safe=""), "/agents", "/api/v2/catalog", "/api/v2/schedule"):
                assert parser.can_fetch(agent, self.url(path)), "Robots denies public path for " + agent
            assert not parser.can_fetch(agent, self.url("/api/me")), "Robots private exclusion lost"
        sitemap_url = next(line.partition(":")[2].strip() for line in body.decode().splitlines() if line.lower().startswith("sitemap:"))
        status, _, body = self.fetch(sitemap_url)
        assert status == 200
        urls = [element.text for element in ET.fromstring(body).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        assert len(urls) == len(set(urls)) and len(urls) <= 45000, "Sitemap duplicate or unbounded"
        wanted = self.url("/colleges/kkep/groups/" + urllib.parse.quote(self.args.group, safe="") + "/" + self.args.day)
        assert wanted in urls and self.url("/agents") in urls and self.url('/') in urls, "Regression day/docs/root missing from sitemap"
        assert self.url('/indexnow-key.txt') not in urls, "Ownership key is not an indexable page"
        assert all(urllib.parse.urlsplit(value).netloc == urllib.parse.urlsplit(self.origin).netloc for value in urls), "Foreign sitemap URL"
        status, _, body = self.fetch(self.url("/llms.txt"))
        text = body.decode("utf-8")
        assert status == 200 and all(value in text for value in ("/agents", "/api/v2/catalog", "/api/v2/openapi.json", "Europe/Moscow", "missing")), "llms metadata incomplete"
        markdown_urls = set(re.findall(r'\[[^\]\n]+\]\((https://[^)\n]+)\)', text))
        api_examples = {value for value in markdown_urls if urllib.parse.urlsplit(value).path.startswith('/api/v2/')
                        and urllib.parse.urlsplit(value).query}
        assert len(api_examples) == 7 and api_examples <= getattr(self, 'docs_example_urls', set()), "llms runnable API links differ from HTML examples"
        status, headers, key = self.fetch(self.url('/indexnow-key.txt'))
        assert status == 200 and headers.get('content-type', '').startswith('text/plain') and headers.get('x-robots-tag') == 'noindex', "Ownership file unavailable or indexable"
        assert re.fullmatch(rb'[A-Za-z0-9-]{8,128}\s*', key), "Ownership file format invalid"
        status, headers, body = self.fetch(self.url('/indexnow-key.txt'), method='HEAD')
        assert status == 200 and not body and headers.get('x-robots-tag') == 'noindex', "Ownership HEAD failed"
        return {"sitemap_entries": len(urls), "robots_public_allowed": True, "llms_linked_api_examples": 7,
                "ownership_get_head": 200}

    def api_contract(self):
        base = {"entity_type": "group", "entity": self.args.group}
        for params in ({**base, "date": "2026-02-31"}, {**base, "date": "09.10.2026"},
                       {**base, "date": self.args.day, "unknown": "x"},
                       {**base, "start": "2026-10-01", "end": "2026-11-01"},
                       {**base, "start": "2026-10-09", "end": "2026-10-08"},
                       {**base, "date": self.args.day, "start": self.args.day, "end": self.args.day},
                       {"entity_type": "student", "entity": self.args.group}, {"entity_type": "group"}):
            payload = self.json(self.url("/api/v2/schedule", params), expected=400)
            assert payload.get("code") == "invalid_request" and payload.get("error"), "Invalid parameter error contract"
        catalog = getattr(self, "catalog", None) or self.json(self.url("/api/v2/catalog"))
        missing_date = "9999-12-30"
        assert missing_date not in catalog["dates"], "Missing-date regression fixture is present in catalog"
        payload = self.json(self.url("/api/v2/schedule", {**base, "date": missing_date}))
        missing = payload["days"][0]
        assert missing["status"] == "missing" and missing["lessons"] == [] and missing["source"] is None, "Missing source confused with no lessons"
        unknown = self.json(self.url("/api/v2/schedule", {"entity_type": "group", "entity": "NONEXISTENT-QA-GROUP", "date": self.args.day}))
        assert unknown["days"][0]["status"] == "entity_not_found" and not unknown["days"][0]["lessons"], "Unknown entity invented lessons"
        resolution = self.json(self.url("/api/v2/resolve", {"entity_type": "group", "query": "103"}))
        assert resolution["ambiguous"] and len(resolution["matches"]) > 1, "Ambiguous group silently selected"
        before = datetime.now(ZoneInfo("Europe/Moscow")).date()
        relative = self.json(self.url("/api/v2/schedule", {**base, "date": "tomorrow"}))
        after = datetime.now(ZoneInfo("Europe/Moscow")).date()
        assert relative["timezone"] == "Europe/Moscow" and relative["range"]["start"] in {(value + timedelta(days=1)).isoformat() for value in (before, after)}, "Tomorrow timezone incorrect"
        week = self.json(self.url("/api/v2/schedule", {**base, "start": "2026-10-05", "end": "2026-10-11"}))
        assert [value["date"] for value in week["days"]] == [f"2026-10-{value:02}" for value in range(5, 12)], "Range omits calendar days"
        assert week["coverage"]["complete"] == all(value["status"] in ("available", "no_lessons") for value in week["days"]), "Coverage suppresses missing days"
        changes = self.json(self.url("/api/v2/changes", {**base, "date": self.args.day}))
        assert changes["history_complete"] is False and changes["order_basis"] == "observation_order_not_publication", "History caveat lost"
        specification = self.json(self.url("/api/v2/openapi.json"))
        statuses = specification["components"]["schemas"]["Day"]["properties"]["status"]["enum"]
        assert set(statuses) == {"available", "no_lessons", "missing", "invalid_source", "entity_not_found"}, "Day status specification changed"
        assert self.fetch(self.url("/api/me"))[0] == 401, "Private route became public"
        return {"invalid_requests": 8, "timezone": "Europe/Moscow", "missing_distinct": True,
                "missing_date": missing_date, "unknown_entity_status": "entity_not_found", "private_route": 401}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://kkepik.rub1kub.ru")
    parser.add_argument("--group", default="103-Д9-3ИНС")
    parser.add_argument("--day", default="2026-10-09", help="Known two-lesson regression fixture date")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--pace", type=float, default=0.2, help="Seconds between bounded sequential requests (min 0.2)")
    args = parser.parse_args()
    parser.error("pace must be >= 0.2 and timeout 1..30") if args.pace < 0.2 or not 1 <= args.timeout <= 30 else None
    return Runner(args).run()


if __name__ == "__main__":
    sys.exit(main())
